"""Report generation: charts, Excel, CSV, Markdown/HTML."""

from .charts import ChartSet, generate_charts
from .csv_export import export_csvs
from .excel import write_workbook
from .weekly import render_weekly_report, write_weekly_report
from .backtest_report import render_backtest_report, write_backtest_report

__all__ = [
    "ChartSet",
    "generate_charts",
    "export_csvs",
    "write_workbook",
    "render_weekly_report",
    "write_weekly_report",
    "render_backtest_report",
    "write_backtest_report",
]
