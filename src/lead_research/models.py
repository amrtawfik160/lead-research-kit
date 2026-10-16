"""Small shared contracts. Secrets belong only in Request, never persisted records."""
from dataclasses import dataclass, field
from typing import Any, Mapping

@dataclass(frozen=True)
class Request:
    method: str
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    body: bytes | None = field(default=None, repr=False)
    timeout: float = 30.0
    # None keeps the transport's default cap; providers may request a bounded larger cap.
    max_response_bytes: int | None = None

@dataclass(frozen=True)
class Response:
    status: int
    data: Any = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    json_valid: bool = True

@dataclass(frozen=True)
class Outcome:
    assessment: str = "unknown"
    email: str | None = None
    risk_flags: tuple[str, ...] = ()
    fields: Mapping[str, Any] = field(default_factory=dict)
    job_url: str | None = None
    error: str | None = None

class ProviderError(RuntimeError):
    """Public-safe error code, without request URLs, bodies or server error text."""

class BusyError(RuntimeError):
    pass

class BudgetError(RuntimeError):
    pass

class PolicyError(RuntimeError):
    pass
