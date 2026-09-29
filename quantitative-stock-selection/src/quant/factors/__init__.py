"""Factor calculations (REQUIREMENTS 9-12).

Every function in this package takes a ``PointInTimeView`` and returns a
mapping of factor name to raw value. A factor that cannot be computed returns
``None``; it is never defaulted to zero, because zero is a real and very
different economic statement (REQUIREMENTS 16, 41).
"""

from .panel import FundamentalPanel
from .momentum import momentum_factors
from .growth import growth_factors
from .quality import quality_factors
from .valuation import valuation_factors
from .risk import risk_factors
from .engine import FactorSet, compute_factors, compute_universe_factors

__all__ = [
    "FundamentalPanel",
    "momentum_factors",
    "growth_factors",
    "quality_factors",
    "valuation_factors",
    "risk_factors",
    "FactorSet",
    "compute_factors",
    "compute_universe_factors",
]
