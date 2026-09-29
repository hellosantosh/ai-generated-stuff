"""Typed configuration loaded from YAML (REQUIREMENTS 32).

Everything the strategy depends on lives in ``config/``. Loading validates
aggressively: allocations and scoring weights must sum to 1.0, dates must
parse, enumerated fields must be members of their enum. A malformed config is
a fatal error, never a silent default.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .errors import ConfigError

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

REBALANCE_FREQUENCIES = {
    "weekly",
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
    "threshold",
}

WEIGHTING_METHODS = {"equal", "score", "market_cap", "volatility", "custom"}
EXECUTION_CONVENTIONS = {"next_open", "same_close"}
RETURN_MODES = {"total_return", "price_return"}
NORMALIZATION_METHODS = {"percentile", "zscore", "winsorized_zscore"}

_WEIGHT_TOLERANCE = 1e-6


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"missing required key {where}.{key}")
    return mapping[key]


def _as_date(value: Any, where: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{where}: {value!r} is not an ISO date (YYYY-MM-DD)") from exc
    raise ConfigError(f"{where}: expected a date, got {type(value).__name__}")


def _check_sums_to_one(weights: Mapping[str, float], where: str) -> None:
    total = float(sum(weights.values()))
    if abs(total - 1.0) > _WEIGHT_TOLERANCE:
        detail = ", ".join(f"{k}={v}" for k, v in weights.items())
        raise ConfigError(f"{where} must sum to 1.0, got {total:.6f} ({detail})")


def _check_enum(value: str, allowed: set[str], where: str) -> str:
    if value not in allowed:
        raise ConfigError(f"{where}: {value!r} is not one of {sorted(allowed)}")
    return value


def _check_non_negative(value: float, where: str) -> float:
    if value < 0:
        raise ConfigError(f"{where} must be >= 0, got {value}")
    return float(value)


@dataclass(frozen=True)
class StrategyConfig:
    version: str
    weekly_contribution: float
    ivv_allocation: float
    sector_allocation: float
    growth_allocation: float

    @property
    def allocations(self) -> dict[str, float]:
        return {
            "ivv": self.ivv_allocation,
            "sector_leaders": self.sector_allocation,
            "high_growth": self.growth_allocation,
        }

    def sleeve_contribution(self, sleeve: str) -> float:
        return self.weekly_contribution * self.allocations[sleeve]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StrategyConfig":
        cfg = cls(
            version=str(_require(raw, "version", "strategy")),
            weekly_contribution=float(_require(raw, "weekly_contribution", "strategy")),
            ivv_allocation=float(_require(raw, "ivv_allocation", "strategy")),
            sector_allocation=float(_require(raw, "sector_allocation", "strategy")),
            growth_allocation=float(_require(raw, "growth_allocation", "strategy")),
        )
        if cfg.weekly_contribution <= 0:
            raise ConfigError("strategy.weekly_contribution must be positive")
        _check_sums_to_one(cfg.allocations, "strategy allocations")
        for name, value in cfg.allocations.items():
            if value < 0:
                raise ConfigError(f"strategy allocation for {name} must be >= 0")
        return cfg


@dataclass(frozen=True)
class ThresholdConfig:
    max_weight_drift: float = 0.25
    max_rank: int = 10

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ThresholdConfig":
        raw = raw or {}
        return cls(
            max_weight_drift=float(raw.get("max_weight_drift", 0.25)),
            max_rank=int(raw.get("max_rank", 10)),
        )


@dataclass(frozen=True)
class SleeveConfig:
    """Shared shape of the sector-leader and high-growth sleeves."""

    name: str
    holdings: int
    rebalance_frequency: str
    weighting: str
    threshold: ThresholdConfig

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], name: str, holdings_key: str) -> "SleeveConfig":
        where = f"{name}_strategy"
        holdings = int(_require(raw, holdings_key, where))
        if holdings <= 0:
            raise ConfigError(f"{where}.{holdings_key} must be positive")
        return cls(
            name=name,
            holdings=holdings,
            rebalance_frequency=_check_enum(
                str(_require(raw, "rebalance_frequency", where)),
                REBALANCE_FREQUENCIES,
                f"{where}.rebalance_frequency",
            ),
            weighting=_check_enum(
                str(raw.get("weighting", "equal")), WEIGHTING_METHODS, f"{where}.weighting"
            ),
            threshold=ThresholdConfig.from_dict(raw.get("threshold")),
        )


@dataclass(frozen=True)
class BacktestConfig:
    start_date: dt.date | None
    end_date: dt.date
    num_weeks: int
    frequency: str
    contribution_day: int
    execution: str
    return_mode: str
    strict_point_in_time: bool
    variants: tuple[str, ...]
    risk_free_rate: float
    # False when end_date was resolved to today rather than read from config.
    end_date_pinned: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BacktestConfig":
        day_raw = str(_require(raw, "contribution_day", "backtest")).lower()
        if day_raw not in WEEKDAYS:
            raise ConfigError(f"backtest.contribution_day: {day_raw!r} is not a weekday name")
        # A null end_date means "the latest session available", so a weekly
        # habit picks up new data without anyone editing the config. A pinned
        # date is kept exactly as written, which is what reproducing an old
        # run needs.
        end_date = _as_date(raw.get("end_date"), "backtest.end_date")
        end_date_pinned = end_date is not None
        if end_date is None:
            end_date = dt.date.today()
        start_date = _as_date(raw.get("start_date"), "backtest.start_date")
        num_weeks = int(raw.get("num_weeks", 360))
        if start_date is None and num_weeks <= 0:
            raise ConfigError("backtest needs either start_date or a positive num_weeks")
        if start_date is not None and start_date >= end_date:
            raise ConfigError(
                f"backtest.start_date ({start_date}) must be before end_date ({end_date})"
            )
        frequency = str(raw.get("frequency", "weekly"))
        if frequency != "weekly":
            raise ConfigError(
                f"backtest.frequency: only 'weekly' is supported in v1, got {frequency!r}"
            )
        variants = tuple(raw.get("variants") or ["combined"])
        known = {"ivv", "sector_leaders", "high_growth", "combined"}
        unknown = set(variants) - known
        if unknown:
            raise ConfigError(f"backtest.variants: unknown variants {sorted(unknown)}")
        return cls(
            start_date=start_date,
            end_date=end_date,
            num_weeks=num_weeks,
            frequency=frequency,
            contribution_day=WEEKDAYS[day_raw],
            execution=_check_enum(
                str(raw.get("execution", "next_open")),
                EXECUTION_CONVENTIONS,
                "backtest.execution",
            ),
            return_mode=_check_enum(
                str(raw.get("return_mode", "total_return")),
                RETURN_MODES,
                "backtest.return_mode",
            ),
            strict_point_in_time=bool(raw.get("strict_point_in_time", True)),
            variants=variants,
            risk_free_rate=float(raw.get("risk_free_rate", 0.0)),
            end_date_pinned=end_date_pinned,
        )

    @property
    def contribution_day_name(self) -> str:
        return [k for k, v in WEEKDAYS.items() if v == self.contribution_day][0]


@dataclass(frozen=True)
class BenchmarkConfig:
    ticker: str = "IVV"
    additional: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "BenchmarkConfig":
        raw = raw or {}
        return cls(
            ticker=str(raw.get("ticker", "IVV")).upper(),
            additional=tuple(str(t).upper() for t in (raw.get("additional") or ())),
        )

    @property
    def all_tickers(self) -> tuple[str, ...]:
        seen: list[str] = [self.ticker]
        for ticker in self.additional:
            if ticker not in seen:
                seen.append(ticker)
        return tuple(seen)


@dataclass(frozen=True)
class TransactionCostConfig:
    commission_per_trade: float = 0.0
    slippage_bps: float = 5.0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "TransactionCostConfig":
        raw = raw or {}
        return cls(
            commission_per_trade=_check_non_negative(
                float(raw.get("commission_per_trade", 0.0)),
                "transaction_cost.commission_per_trade",
            ),
            slippage_bps=_check_non_negative(
                float(raw.get("slippage_bps", 5.0)), "transaction_cost.slippage_bps"
            ),
        )

    @property
    def slippage_fraction(self) -> float:
        return self.slippage_bps / 10_000.0


@dataclass(frozen=True)
class LimitsConfig:
    max_single_stock_weight: float = 0.05
    max_sector_weight: float = 0.20
    max_positions: int = 40
    enforce: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "LimitsConfig":
        raw = raw or {}
        return cls(
            max_single_stock_weight=float(raw.get("max_single_stock_weight", 0.05)),
            max_sector_weight=float(raw.get("max_sector_weight", 0.20)),
            max_positions=int(raw.get("max_positions", 40)),
            enforce=bool(raw.get("enforce", False)),
        )


@dataclass(frozen=True)
class RiskControlsConfig:
    skip_below_200dma: bool = False
    reduce_when_market_below_200dma: bool = False
    market_below_200dma_scale: float = 0.5
    min_average_dollar_volume: float = 0.0

    @property
    def any_active(self) -> bool:
        return (
            self.skip_below_200dma
            or self.reduce_when_market_below_200dma
            or self.min_average_dollar_volume > 0
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "RiskControlsConfig":
        raw = raw or {}
        return cls(
            skip_below_200dma=bool(raw.get("skip_below_200dma", False)),
            reduce_when_market_below_200dma=bool(
                raw.get("reduce_when_market_below_200dma", False)
            ),
            market_below_200dma_scale=float(raw.get("market_below_200dma_scale", 0.5)),
            min_average_dollar_volume=float(raw.get("min_average_dollar_volume", 0.0)),
        )


@dataclass(frozen=True)
class EligibilityConfig:
    min_price_history_weeks: int = 52
    min_price: float = 1.0
    max_stale_days: int = 10
    require_fundamentals: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "EligibilityConfig":
        raw = raw or {}
        return cls(
            min_price_history_weeks=int(raw.get("min_price_history_weeks", 52)),
            min_price=float(raw.get("min_price", 1.0)),
            max_stale_days=int(raw.get("max_stale_days", 10)),
            require_fundamentals=bool(raw.get("require_fundamentals", False)),
        )


@dataclass(frozen=True)
class UniverseConfig:
    name: str = "sp500"
    source: str = "wikipedia"
    custom_tickers: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "UniverseConfig":
        raw = raw or {}
        source = _check_enum(
            str(raw.get("source", "wikipedia")),
            {"wikipedia", "static", "synthetic", "custom"},
            "universe.source",
        )
        return cls(
            name=str(raw.get("name", "sp500")),
            source=source,
            custom_tickers=tuple(str(t).upper() for t in (raw.get("custom_tickers") or ())),
        )


@dataclass(frozen=True)
class DataConfig:
    provider: str = "yfinance"
    fallback_providers: tuple[str, ...] = ()
    fundamentals_provider: str = "sec"
    cache_dir: Path = Path("data/cache")
    raw_dir: Path = Path("data/raw")
    processed_dir: Path = Path("data/processed")
    database_path: Path = Path("data/database/quant.sqlite")
    cache_ttl_days: int = 1
    max_retries: int = 4
    backoff_base_seconds: float = 1.5
    request_timeout_seconds: float = 30.0

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "DataConfig":
        raw = raw or {}
        providers = {"yfinance", "alphavantage", "synthetic"}
        return cls(
            provider=_check_enum(str(raw.get("provider", "yfinance")), providers, "data.provider"),
            fallback_providers=tuple(
                _check_enum(str(p), providers, "data.fallback_providers")
                for p in (raw.get("fallback_providers") or ())
            ),
            fundamentals_provider=_check_enum(
                str(raw.get("fundamentals_provider", "sec")),
                {"sec", "yfinance", "synthetic", "none"},
                "data.fundamentals_provider",
            ),
            cache_dir=Path(raw.get("cache_dir", "data/cache")),
            raw_dir=Path(raw.get("raw_dir", "data/raw")),
            processed_dir=Path(raw.get("processed_dir", "data/processed")),
            database_path=Path(raw.get("database_path", "data/database/quant.sqlite")),
            cache_ttl_days=int(raw.get("cache_ttl_days", 1)),
            max_retries=int(raw.get("max_retries", 4)),
            backoff_base_seconds=float(raw.get("backoff_base_seconds", 1.5)),
            request_timeout_seconds=float(raw.get("request_timeout_seconds", 30.0)),
        )


@dataclass(frozen=True)
class ReportingConfig:
    output_dir: Path = Path("reports")
    chart_dir: Path = Path("reports/charts")
    generate_charts: bool = True
    generate_excel: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ReportingConfig":
        raw = raw or {}
        return cls(
            output_dir=Path(raw.get("output_dir", "reports")),
            chart_dir=Path(raw.get("chart_dir", "reports/charts")),
            generate_charts=bool(raw.get("generate_charts", True)),
            generate_excel=bool(raw.get("generate_excel", True)),
        )


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    file: Path | None = Path("logs/app.log")
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "LoggingConfig":
        raw = raw or {}
        file_value = raw.get("file", "logs/app.log")
        return cls(
            level=str(raw.get("level", "INFO")).upper(),
            file=Path(file_value) if file_value else None,
            max_bytes=int(raw.get("max_bytes", 10 * 1024 * 1024)),
            backup_count=int(raw.get("backup_count", 5)),
        )


@dataclass(frozen=True)
class NormalizationConfig:
    method: str = "percentile"
    group_by: str = "sector"
    winsorize_lower: float = 0.02
    winsorize_upper: float = 0.98
    min_group_size: int = 5

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "NormalizationConfig":
        raw = raw or {}
        lower = float(raw.get("winsorize_lower", 0.02))
        upper = float(raw.get("winsorize_upper", 0.98))
        if not 0.0 <= lower < upper <= 1.0:
            raise ConfigError(
                f"normalization winsorize bounds must satisfy 0 <= lower < upper <= 1, "
                f"got lower={lower}, upper={upper}"
            )
        return cls(
            method=_check_enum(
                str(raw.get("method", "percentile")),
                NORMALIZATION_METHODS,
                "normalization.method",
            ),
            group_by=_check_enum(
                str(raw.get("group_by", "sector")),
                {"sector", "universe"},
                "normalization.group_by",
            ),
            winsorize_lower=lower,
            winsorize_upper=upper,
            min_group_size=int(raw.get("min_group_size", 5)),
        )


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, float]
    growth_weights: dict[str, float]
    normalization: NormalizationConfig
    factor_weights: dict[str, dict[str, float]]
    lower_is_better: frozenset[str]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScoringConfig":
        weights = {str(k): float(v) for k, v in _require(raw, "weights", "scoring").items()}
        _check_sums_to_one(weights, "scoring.weights")
        required = {"growth", "momentum", "quality", "valuation", "risk"}
        if set(weights) != required:
            raise ConfigError(
                f"scoring.weights must contain exactly {sorted(required)}, got {sorted(weights)}"
            )

        growth_weights = {
            str(k): float(v) for k, v in _require(raw, "growth_weights", "scoring").items()
        }
        _check_sums_to_one(growth_weights, "scoring.growth_weights")

        factor_weights: dict[str, dict[str, float]] = {}
        for category, mapping in (raw.get("factors") or {}).items():
            inner = {str(k): float(v) for k, v in mapping.items()}
            if not inner:
                raise ConfigError(f"scoring.factors.{category} is empty")
            _check_sums_to_one(inner, f"scoring.factors.{category}")
            factor_weights[str(category)] = inner

        missing = (required | set(growth_weights)) - set(factor_weights)
        missing.discard("risk_free")
        if missing:
            raise ConfigError(
                f"scoring.factors is missing definitions for categories {sorted(missing)}"
            )

        return cls(
            weights=weights,
            growth_weights=growth_weights,
            normalization=NormalizationConfig.from_dict(raw.get("normalization")),
            factor_weights=factor_weights,
            lower_is_better=frozenset(str(x) for x in (raw.get("lower_is_better") or ())),
        )


@dataclass(frozen=True)
class SectorConfig:
    canonical: tuple[str, ...]
    aliases: dict[str, str]
    excluded: frozenset[str]

    def normalize(self, raw_sector: str | None) -> str | None:
        """Map a provider's sector label onto a canonical GICS sector."""
        if raw_sector is None:
            return None
        key = str(raw_sector).strip()
        if not key:
            return None
        return self.aliases.get(key.lower())

    @property
    def active(self) -> tuple[str, ...]:
        return tuple(s for s in self.canonical if s not in self.excluded)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SectorConfig":
        entries = _require(raw, "sectors", "sectors")
        canonical: list[str] = []
        aliases: dict[str, str] = {}
        for entry in entries:
            name = str(_require(entry, "canonical", "sectors[]"))
            canonical.append(name)
            aliases[name.lower()] = name
            for alias in entry.get("aliases") or ():
                aliases[str(alias).lower()] = name
        return cls(
            canonical=tuple(canonical),
            aliases=aliases,
            excluded=frozenset(str(s) for s in (raw.get("excluded") or ())),
        )


@dataclass(frozen=True)
class AppConfig:
    """The whole validated configuration tree."""

    strategy: StrategyConfig
    sector_strategy: SleeveConfig
    growth_strategy: SleeveConfig
    backtest: BacktestConfig
    benchmark: BenchmarkConfig
    transaction_cost: TransactionCostConfig
    limits: LimitsConfig
    risk_controls: RiskControlsConfig
    eligibility: EligibilityConfig
    universe: UniverseConfig
    data: DataConfig
    reporting: ReportingConfig
    logging: LoggingConfig
    scoring: ScoringConfig
    sectors: SectorConfig
    config_dir: Path = field(default_factory=lambda: Path("config"))
    project_root: Path = field(default_factory=Path.cwd)

    # --- convenience -----------------------------------------------------
    def path(self, value: str | os.PathLike[str]) -> Path:
        """Resolve a configured relative path against the project root."""
        p = Path(value)
        return p if p.is_absolute() else self.project_root / p

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot, stored with every backtest for reproducibility."""
        return _to_plain(self)


def _to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (frozenset, set)):
        return sorted(str(v) for v in value)
    if isinstance(value, Mapping):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if data is None:
        raise ConfigError(f"configuration file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"configuration file must contain a mapping at the top level: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(
    config_dir: str | os.PathLike[str] = "config",
    overrides: Mapping[str, Any] | None = None,
    project_root: str | os.PathLike[str] | None = None,
) -> AppConfig:
    """Load and validate settings.yaml, scoring.yaml and sectors.yaml.

    ``overrides`` is a nested mapping merged over the file contents; the CLI
    uses it to apply flags such as ``--start`` without mutating the files.
    """
    config_path = Path(config_dir)
    root = Path(project_root) if project_root is not None else config_path.resolve().parent

    settings = _read_yaml(config_path / "settings.yaml")
    scoring = _read_yaml(config_path / "scoring.yaml")
    sectors = _read_yaml(config_path / "sectors.yaml")

    if overrides:
        settings = _deep_merge(settings, overrides.get("settings", overrides))
        if "scoring" in overrides:
            scoring = _deep_merge(scoring, overrides["scoring"])

    return AppConfig(
        strategy=StrategyConfig.from_dict(_require(settings, "strategy", "settings")),
        sector_strategy=SleeveConfig.from_dict(
            _require(settings, "sector_strategy", "settings"), "sector", "stocks_per_sector"
        ),
        growth_strategy=SleeveConfig.from_dict(
            _require(settings, "growth_strategy", "settings"), "growth", "number_of_stocks"
        ),
        backtest=BacktestConfig.from_dict(_require(settings, "backtest", "settings")),
        benchmark=BenchmarkConfig.from_dict(settings.get("benchmark")),
        transaction_cost=TransactionCostConfig.from_dict(settings.get("transaction_cost")),
        limits=LimitsConfig.from_dict(settings.get("limits")),
        risk_controls=RiskControlsConfig.from_dict(settings.get("risk_controls")),
        eligibility=EligibilityConfig.from_dict(settings.get("eligibility")),
        universe=UniverseConfig.from_dict(settings.get("universe")),
        data=DataConfig.from_dict(settings.get("data")),
        reporting=ReportingConfig.from_dict(settings.get("reporting")),
        logging=LoggingConfig.from_dict(settings.get("logging")),
        scoring=ScoringConfig.from_dict(scoring),
        sectors=SectorConfig.from_dict(sectors),
        config_dir=config_path,
        project_root=root,
    )


def load_env(project_root: str | os.PathLike[str] = ".") -> None:
    """Load ``.env`` if python-dotenv is installed; never fail if it is not."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return
    env_path = Path(project_root) / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def require_env(name: str, provider: str) -> str:
    from .errors import MissingAPIKeyError

    value = os.environ.get(name)
    if not value:
        raise MissingAPIKeyError(
            f"{provider} requires the {name} environment variable. "
            f"Copy .env.example to .env and set it, or choose another data.provider."
        )
    return value
