"""Backtesting engine, metrics and integrity checks."""

from .drawdown import DrawdownEpisode, drawdown_series, drawdown_table, max_drawdown_detail
from .metrics import PerformanceMetrics, compute_metrics, xirr
from .engine import BacktestEngine, BacktestResult, VariantResult
from .validation import ProvenanceRecord, make_run_id, verify_point_in_time

__all__ = [
    "DrawdownEpisode",
    "drawdown_series",
    "drawdown_table",
    "max_drawdown_detail",
    "PerformanceMetrics",
    "compute_metrics",
    "xirr",
    "BacktestEngine",
    "BacktestResult",
    "VariantResult",
    "ProvenanceRecord",
    "make_run_id",
    "verify_point_in_time",
]
