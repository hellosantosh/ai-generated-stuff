"""Exception hierarchy.

The application fails loudly for critical errors (REQUIREMENTS 41). Every
exception below is fatal unless a caller explicitly decides otherwise, and the
message is expected to name the offending ticker, date or field.
"""

from __future__ import annotations


class QuantError(Exception):
    """Base class for every error raised by this application."""


class ConfigError(QuantError):
    """Configuration is missing, malformed or internally inconsistent."""


class DataError(QuantError):
    """Requested data is missing, stale or fails validation."""


class ProviderError(DataError):
    """A data provider failed after exhausting its retry budget."""


class RateLimitError(ProviderError):
    """A provider signalled that we exceeded its rate limit."""


class MissingAPIKeyError(ConfigError):
    """A provider was selected but its credentials are not in the environment."""


class LookAheadError(QuantError):
    """Data dated after the decision point was requested.

    Raised by the point-in-time store in strict mode. This is the single most
    important guard in the system (REQUIREMENTS 22, 38).
    """


class EligibilityError(QuantError):
    """A stock fails the configured eligibility rules."""


class InsufficientDataError(DataError):
    """Not enough history to compute the requested quantity."""
