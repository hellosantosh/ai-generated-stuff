"""Read-only analytics that sit beside the strategy rather than inside it."""

from .indices import (
    COHORT_SIZES,
    MAJOR_INDICES,
    IndexProxy,
    cohort_performance,
    index_performance,
)

__all__ = [
    "COHORT_SIZES",
    "MAJOR_INDICES",
    "IndexProxy",
    "cohort_performance",
    "index_performance",
]
