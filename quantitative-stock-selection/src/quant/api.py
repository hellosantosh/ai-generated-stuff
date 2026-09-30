"""HTTP API and web UI host (REQUIREMENTS 45).

Wraps the same service layer the CLI uses, so the UI can only do things the
CLI can do. Long operations - downloading data, running a backtest - are
submitted as background jobs and polled, because they take minutes.

Scope and safety. This server binds to localhost by default and has no
authentication, because it exposes one person's research data on their own
machine. It has no trading endpoint and never will without the explicit human
approval step described in REQUIREMENTS 48. Do not expose it to a network
without putting authentication in front of it.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .errors import QuantError
from .logging_config import configure_logging, get_logger
from .portfolio.holdings import Holdings
from .ranking.sector_ranker import GROWTH_SLEEVE, SECTOR_SLEEVE
from .service import AppService, ranking_rows

log = get_logger(__name__)


# --- request models --------------------------------------------------------
class PositionIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    shares: float = Field(ge=0)
    cost_basis: float = Field(default=0.0, ge=0)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class HoldingsIn(BaseModel):
    positions: list[PositionIn] = Field(default_factory=list)
    cash: float = Field(default=0.0, ge=0)
    last_rebalance: dt.date | None = None
    note: str = ""


class BacktestIn(BaseModel):
    weeks: int | None = Field(default=None, ge=4, le=2000)
    start: dt.date | None = None
    end: dt.date | None = None
    variants: list[str] | None = None
    run_id: str | None = Field(default=None, max_length=64)


class UpdateDataIn(BaseModel):
    force: bool = False
    skip_fundamentals: bool = False


class ApplyPlanIn(BaseModel):
    contribution: float = Field(gt=0)
    mode: Literal["contribute", "rebalance"] | None = None
    confirm: bool = Field(default=False, description="must be true; guards against accidents")


# --- app -------------------------------------------------------------------
def create_app(project_root: Path | None = None) -> FastAPI:
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
    configure_logging(level="INFO", log_file=root / "logs" / "app.log")
    service = AppService(root)

    app = FastAPI(
        title="Quant Stock Selector",
        version=__version__,
        description=(
            "Research and backtesting API. This service does not place trades "
            "and returns model output only, never investment advice."
        ),
    )
    app.state.service = service
    api = APIRouter(prefix="/api")

    def fail(exc: Exception, status: int = 400) -> HTTPException:
        return HTTPException(status_code=status, detail=f"{type(exc).__name__}: {exc}")

    # --- meta ------------------------------------------------------------
    @api.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @api.get("/status")
    def status() -> dict[str, Any]:
        try:
            return service.status()
        except QuantError as exc:
            raise fail(exc)

    @api.get("/config")
    def config() -> dict[str, Any]:
        cfg = service.config
        return {
            "strategy": cfg.to_dict()["strategy"],
            "sector_strategy": cfg.to_dict()["sector_strategy"],
            "growth_strategy": cfg.to_dict()["growth_strategy"],
            "scoring_weights": cfg.scoring.weights,
            "growth_weights": cfg.scoring.growth_weights,
            "backtest": cfg.to_dict()["backtest"],
            "transaction_cost": cfg.to_dict()["transaction_cost"],
            "risk_controls_active": cfg.risk_controls.any_active,
            "limits_enforced": cfg.limits.enforce,
        }

    # --- weekly ----------------------------------------------------------
    @api.get("/weekly")
    def weekly(
        as_of: dt.date | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        try:
            view = service.weekly(as_of, force_reload=refresh)
        except QuantError as exc:
            raise fail(exc)

        market = view.data.market
        today = dt.date.today()
        staleness = (today - view.as_of).days
        return {
            "as_of": view.as_of.isoformat(),
            "execution_date": view.execution_date.isoformat() if view.execution_date else None,
            "universe": market.universe.name,
            "universe_status": market.universe.status_line(),
            "universe_size": view.universe_size,
            "eligible": view.eligible,
            "budgets": view.budgets,
            "basket": [
                {
                    "ticker": ticker,
                    "weight": weight,
                    "sleeve": sleeve,
                    "sector": view.sectors.get(ticker),
                    "score": None if score != score else round(score, 1),
                }
                for ticker, weight, sleeve, score in view.basket
            ],
            "warnings": view.ranking.warnings,
            "rejected": len(view.ranking.rejections),
            "synthetic": view.data.synthetic,
            "synthetic_banner": view.data.banner,
            "stale_days": staleness,
            "partial_session": view.as_of == today and today.weekday() < 5,
            "sector_classification_point_in_time": market.sectors.point_in_time,
        }

    @api.get("/rankings")
    def rankings(
        sleeve: Literal["sector_leaders", "high_growth"] = SECTOR_SLEEVE,
        limit: int = Query(default=5, ge=1, le=50),
        as_of: dt.date | None = None,
    ) -> dict[str, Any]:
        try:
            view = service.weekly(as_of)
        except QuantError as exc:
            raise fail(exc)
        return {
            "as_of": view.as_of.isoformat(),
            "sleeve": sleeve,
            "rows": ranking_rows(view.ranking, sleeve, limit),
        }

    @api.get("/explain/{ticker}")
    def explain(ticker: str, as_of: dt.date | None = None) -> dict[str, Any]:
        try:
            view = service.weekly(as_of)
        except QuantError as exc:
            raise fail(exc)
        ticker = ticker.upper()
        for sleeve in (SECTOR_SLEEVE, GROWTH_SLEEVE):
            for selection in view.ranking.selections(sleeve):
                if selection.ticker == ticker:
                    return {
                        "ticker": ticker,
                        "company_name": selection.company_name,
                        "sector": selection.sector,
                        "sleeve": sleeve,
                        "rank": selection.rank,
                        "total_score": round(selection.total_score, 1),
                        "categories": {
                            k: round(v, 1) for k, v in selection.category_scores.items()
                        },
                        "strengths": selection.strengths,
                        "risks": selection.risks,
                        "text": selection.explanation(),
                    }
        raise HTTPException(status_code=404, detail=f"{ticker} is not in the current selection")

    # --- holdings --------------------------------------------------------
    @api.get("/holdings")
    def get_holdings() -> dict[str, Any]:
        holdings = service.holdings()
        prices: dict[str, float] = {}
        try:
            if holdings.positions:
                view = service.weekly()
                prices = service.prices_for(holdings.tickers(), view.as_of)
        except QuantError as exc:
            log.warning("could not price holdings: %s", exc)

        values = holdings.market_values(prices)
        total = sum(values.values())
        return {
            "updated_at": holdings.updated_at.isoformat() if holdings.updated_at else None,
            "last_rebalance": holdings.last_rebalance.isoformat() if holdings.last_rebalance else None,
            "cash": holdings.cash,
            "total_value": round(total, 2),
            "unpriced": holdings.unpriced(prices),
            "positions": [
                {
                    "ticker": lot.ticker,
                    "shares": lot.shares,
                    "cost_basis": lot.cost_basis,
                    "price": prices.get(lot.ticker),
                    "value": round(values.get(lot.ticker, 0.0), 2) if lot.ticker in values else None,
                    "weight": (values[lot.ticker] / total) if total > 0 and lot.ticker in values else None,
                    "unrealized": round(values[lot.ticker] - lot.cost_basis, 2)
                    if lot.ticker in values and lot.cost_basis
                    else None,
                }
                for lot in sorted(holdings.positions.values(), key=lambda l: -values.get(l.ticker, 0.0))
            ],
        }

    @api.put("/holdings")
    def put_holdings(payload: HoldingsIn) -> dict[str, Any]:
        holdings = Holdings(cash=payload.cash, note=payload.note,
                            last_rebalance=payload.last_rebalance, updated_at=dt.date.today())
        for position in payload.positions:
            holdings.set_position(position.ticker, position.shares, position.cost_basis)
        service.save_holdings(holdings)
        return {"saved": True, "positions": len(holdings.positions)}

    @api.post("/holdings/import")
    async def import_holdings(file: UploadFile = File(...)) -> dict[str, Any]:
        import csv
        import tempfile

        raw = await file.read()
        if len(raw) > 2_000_000:
            raise HTTPException(status_code=413, detail="file is larger than 2 MB")
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
            handle.write(raw)
            temp_path = Path(handle.name)
        try:
            holdings = Holdings.from_csv(temp_path)
        except QuantError as exc:
            raise fail(exc)
        finally:
            temp_path.unlink(missing_ok=True)
        existing = service.holdings()
        holdings.last_rebalance = existing.last_rebalance
        service.save_holdings(holdings)
        return {"imported": len(holdings.positions)}

    # --- trade plan ------------------------------------------------------
    @api.get("/trade-plan")
    def trade_plan(
        contribution: float = Query(default=None, gt=0),
        mode: Literal["contribute", "rebalance"] | None = None,
        as_of: dt.date | None = None,
    ) -> dict[str, Any]:
        try:
            view = service.weekly(as_of)
            if view.data.synthetic:
                # A trade plan is the one output someone might act on with real
                # money. It is never served from generated prices.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "this server is running on synthetic data, so no trade plan "
                        "will be produced. Restart without the synthetic provider."
                    ),
                )
            plan, meta = service.trade_plan(contribution, mode, as_of)
        except QuantError as exc:
            raise fail(exc)
        payload = plan.to_dict()
        payload["meta"] = meta
        payload["disclaimer"] = (
            "Proposed trades from a quantitative model. Nothing has been ordered. "
            "Review every line before acting."
        )
        return payload

    @api.post("/trade-plan/apply")
    def apply_trade_plan(payload: ApplyPlanIn) -> dict[str, Any]:
        if not payload.confirm:
            raise HTTPException(
                status_code=400,
                detail="set confirm=true to record these trades as executed",
            )
        try:
            plan, _ = service.trade_plan(payload.contribution, payload.mode)
            holdings = service.apply_trade_plan(plan)
        except QuantError as exc:
            raise fail(exc)
        return {
            "recorded": len(plan.buys) + len(plan.sells),
            "positions": len(holdings.positions),
            "note": "Holdings updated to reflect the plan. This records what you traded; "
                    "it does not place orders.",
        }

    # --- jobs ------------------------------------------------------------
    @api.post("/jobs/update-data")
    def job_update_data(payload: UpdateDataIn) -> dict[str, Any]:
        from .pipeline import persist_market_data

        def work(emit):
            emit("loading universe and prices...")
            data = service.market(force_reload=True)
            emit(f"loaded {len(data.market.prices)} price series")
            emit(f"loaded {len(data.market.fundamentals)} fundamental series")
            emit(data.market.universe.status_line())
            db = service.database()
            try:
                counts = persist_market_data(db, data, service.config)
            finally:
                db.close()
            for table, count in counts.items():
                emit(f"  {table}: {count:,} rows")
            service.invalidate()

            # Score the week here rather than making the UI wait for it. The
            # weekly view takes tens of seconds to build, and the person who
            # just pressed "download" is about to ask for exactly this.
            emit("scoring this week's basket...")
            weekly = service.weekly(force_reload=True)
            emit(
                f"basket ready for {weekly.as_of}: {len(weekly.basket)} positions "
                f"from {weekly.eligible} eligible companies"
            )
            for warning in weekly.ranking.warnings:
                emit(f"  warning: {warning}")
            emit("done")
            return {
                "price_series": len(data.market.prices),
                "failures": len(data.failures),
                "basket_as_of": weekly.as_of.isoformat(),
                "basket_positions": len(weekly.basket),
            }

        try:
            job = service.jobs.submit("update-data", work)
        except QuantError as exc:
            raise fail(exc, status=409)
        return job.to_dict()

    @api.post("/jobs/backtest")
    def job_backtest(payload: BacktestIn) -> dict[str, Any]:
        def work(emit):
            from .backtest.engine import BacktestEngine
            from .backtest.validation import build_provenance, next_run_id
            from .config import load_config
            from .pipeline import load_market_data, persist_backtest

            overrides: dict[str, Any] = {}
            if payload.weeks:
                overrides["num_weeks"] = payload.weeks
            if payload.start:
                overrides["start_date"] = payload.start
            if payload.end:
                overrides["end_date"] = payload.end
            if payload.variants:
                overrides["variants"] = payload.variants
            cfg = load_config(
                service.project_root / "config",
                overrides={"settings": {"backtest": overrides}} if overrides else None,
                project_root=service.project_root,
            )

            emit("loading the full historical universe (this is the slow part)...")
            data = load_market_data(cfg)
            emit(f"loaded {len(data.market.prices)} price series")
            emit(data.market.universe.status_line())

            db = service.database()
            try:
                existing = [r["run_id"] for r in db.query("SELECT run_id FROM backtest_runs")]
                run_id = payload.run_id or next_run_id(existing)
                engine = BacktestEngine(cfg, data.market, run_id=run_id, progress=emit)
                result = engine.run()
                provenance = build_provenance(run_id, cfg, data.market, warnings=result.warnings)
                persist_backtest(db, result, provenance.to_dict(), cfg)
            finally:
                db.close()

            emit(f"stored run {run_id}")
            return {"run_id": run_id, "variants": list(result.variants)}

        try:
            job = service.jobs.submit("backtest", work)
        except QuantError as exc:
            raise fail(exc, status=409)
        return job.to_dict()

    @api.get("/jobs")
    def list_jobs(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
        return {
            "busy": service.jobs.busy,
            "jobs": [job.to_dict(tail=3) for job in service.jobs.recent(limit)],
        }

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str, tail: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
        job = service.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no job {job_id}")
        return job.to_dict(tail=tail)

    # --- runs ------------------------------------------------------------
    @api.get("/runs")
    def runs(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return {"runs": service.runs(limit)}

    @api.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        try:
            return service.run_metrics(run_id)
        except QuantError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @api.get("/runs/{run_id}/series")
    def run_series(run_id: str) -> dict[str, Any]:
        return {"series": service.run_series(run_id)}

    # --- report artifacts ------------------------------------------------
    @api.get("/reports")
    def list_reports() -> dict[str, Any]:
        directory = service.config.path(service.config.reporting.output_dir)
        if not directory.exists():
            return {"files": []}
        files = [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
            for path in sorted(directory.glob("*"))
            if path.is_file() and not path.name.startswith(".")
        ]
        return {"files": files}

    def _safe_report_path(name: str) -> Path:
        """Resolve a report filename, refusing anything outside the directory."""
        directory = service.config.path(service.config.reporting.output_dir).resolve()
        candidate = (directory / name).resolve()
        if not str(candidate).startswith(str(directory)):
            raise HTTPException(status_code=400, detail="path traversal is not allowed")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"no report file {name!r}")
        return candidate

    @api.get("/reports/{name}")
    def get_report(name: str):
        return FileResponse(_safe_report_path(name))

    @api.get("/charts/{name}")
    def get_chart(name: str):
        directory = service.config.path(service.config.reporting.chart_dir).resolve()
        candidate = (directory / name).resolve()
        if not str(candidate).startswith(str(directory)) or not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"no chart {name!r}")
        return FileResponse(candidate, media_type="image/png")

    app.include_router(api)

    # --- static UI --------------------------------------------------------
    web_dir = root / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    else:  # pragma: no cover - only if the UI files are missing
        @app.get("/")
        def missing_ui() -> JSONResponse:
            return JSONResponse({"detail": "web/ not found; the API is still available at /api"})

    return app


def run_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(
        "quant.api:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
