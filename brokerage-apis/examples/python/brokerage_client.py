"""
HTTP client for the Brokerage REST API.

Standard library only. Requires Python 3.9+.

Three decisions worth calling out, because getting them wrong is expensive in
this domain:

  * Money is Decimal, never float. The API sends prices and quantities as JSON
    strings precisely so they survive the round trip. `json.loads` would turn
    "412.50" into a float if the API sent a number; because it sends a string,
    we can parse straight into Decimal and keep every digit.

  * Retries are scoped by method. GET is safe and always retryable. POST is
    retryable only because every order carries an Idempotency-Key -- without
    one, retrying a timed-out order is how a customer ends up with two
    positions instead of one.

  * A 401 triggers exactly one refresh-and-retry. Looping on 401 turns a
    revoked grant into an infinite request storm against the auth server.
"""

from __future__ import annotations

import gzip
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal
from typing import Any, Callable, Iterator, Optional

from pkce_auth import OAuthError, TokenStore, Tokens, get_valid_tokens, refresh_tokens

API_BASE = "https://api.sandbox.brokerage.example.com/v1"
USER_AGENT = "brokerage-python-example/1.0"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """An RFC 9457 problem response.

    Branch on `.type`, never on `.title` or `.detail` -- those are human-facing
    prose and are reworded without notice. `.trace_id` is what support will ask
    for, so log it on every failure.
    """

    def __init__(self, status: int, problem: dict) -> None:
        self.status = status
        self.problem = problem
        self.type = problem.get("type", "about:blank")
        self.title = problem.get("title", "Unknown error")
        self.detail = problem.get("detail", "")
        self.trace_id = problem.get("traceId")
        self.errors = problem.get("errors", [])
        super().__init__(f"HTTP {status} {self.title}: {self.detail} (trace {self.trace_id})")

    @property
    def is_retryable(self) -> bool:
        return self.status in (429, 502, 503, 504)


class RateLimited(ApiError):
    def __init__(self, status: int, problem: dict, retry_after: int) -> None:
        super().__init__(status, problem)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Decimal-preserving JSON
# ---------------------------------------------------------------------------


def parse_json(raw: bytes) -> Any:
    """Decode JSON with every number as Decimal.

    The API sends monetary values as strings, so this mainly guards against a
    future field arriving as a bare number and silently becoming a float.
    Belt and braces, in a domain where a rounding error is a real defect.
    """
    return json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_int=int)


class DecimalEncoder(json.JSONEncoder):
    """Serialize Decimal back out as a JSON string, matching the API contract."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BrokerageClient:
    def __init__(
        self,
        base_url: str = API_BASE,
        store: Optional[TokenStore] = None,
        max_retries: int = 4,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.store = store or TokenStore()
        self.max_retries = max_retries
        self._tokens: Optional[Tokens] = None

    # -- auth ---------------------------------------------------------------

    def _access_token(self) -> str:
        if self._tokens is None or self._tokens.expired:
            self._tokens = get_valid_tokens(self.store)
        return self._tokens.access_token

    def _force_refresh(self) -> None:
        """Refresh after a 401, even if the token looked locally valid.

        A token can be revoked server-side -- the customer disconnects the app,
        or the security team invalidates a family -- long before it expires.
        """
        if self._tokens and self._tokens.refresh_token:
            try:
                self._tokens = refresh_tokens(self._tokens)
                self.store.save(self._tokens)
                return
            except OAuthError:
                pass
        self._tokens = get_valid_tokens(self.store)

    # -- transport ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        headers: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        if_match: Optional[str] = None,
        content_type: str = "application/json",
    ) -> tuple[Any, dict]:
        """Issue one request. Returns (parsed_body, response_headers).

        `path` may be a path relative to the API base or an absolute URL -- so a
        HATEOAS link can be passed straight through without any parsing.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        if params:
            # doseq so repeated parameters (status=NEW&status=FILLED) work.
            cleaned = {k: v for k, v in params.items() if v is not None}
            if cleaned:
                url = f"{url}?{urllib.parse.urlencode(cleaned, doseq=True)}"

        payload = (
            json.dumps(body, cls=DecimalEncoder).encode("utf-8") if body is not None else None
        )

        attempt = 0
        refreshed = False

        while True:
            attempt += 1

            request_headers = {
                "Authorization": f"Bearer {self._access_token()}",
                "Accept": "application/json, application/problem+json",
                "Accept-Encoding": "gzip",
                "User-Agent": USER_AGENT,
                **(headers or {}),
            }
            if payload is not None:
                request_headers["Content-Type"] = content_type
            if idempotency_key:
                request_headers["Idempotency-Key"] = idempotency_key
            if if_match:
                request_headers["If-Match"] = if_match

            request = urllib.request.Request(
                url, data=payload, method=method, headers=request_headers
            )

            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    parsed = parse_json(raw) if raw else None
                    return parsed, dict(response.headers)

            except urllib.error.HTTPError as exc:
                raw = exc.read()
                if exc.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                try:
                    problem = parse_json(raw) if raw else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    problem = {"title": "Non-JSON error body", "detail": raw[:400].decode("utf-8", "replace")}

                if exc.code == 304:
                    return None, dict(exc.headers)  # conditional GET, unchanged

                # One refresh-and-retry on 401, then give up.
                if exc.code == 401 and not refreshed:
                    refreshed = True
                    self._force_refresh()
                    continue

                if exc.code == 429:
                    retry_after = int(exc.headers.get("Retry-After", "5"))
                    error = RateLimited(exc.code, problem, retry_after)
                else:
                    error = ApiError(exc.code, problem)

                if not self._should_retry(method, error, attempt, idempotency_key):
                    raise error

                time.sleep(self._backoff(attempt, error))

            except (urllib.error.URLError, TimeoutError) as exc:
                # A transport failure is the dangerous case for writes: the
                # order may well have been accepted. Only retry when an
                # Idempotency-Key makes that safe.
                if method in ("GET", "HEAD") or idempotency_key:
                    if attempt <= self.max_retries:
                        time.sleep(self._backoff(attempt, None))
                        continue
                raise ApiError(0, {"title": "Network error", "detail": str(exc)}) from exc

    def _should_retry(
        self, method: str, error: ApiError, attempt: int, idempotency_key: Optional[str]
    ) -> bool:
        if attempt > self.max_retries or not error.is_retryable:
            return False
        if method in ("GET", "HEAD", "DELETE"):
            return True
        # POST/PATCH are only safe to retry behind an idempotency key.
        return idempotency_key is not None

    @staticmethod
    def _backoff(attempt: int, error: Optional[ApiError]) -> float:
        """Exponential backoff with full jitter.

        Full jitter (random between 0 and the cap) rather than fixed backoff:
        when a venue hiccups, every client retries at once, and synchronized
        retries are how a brief blip becomes an outage.
        """
        if isinstance(error, RateLimited):
            return error.retry_after + random.uniform(0, 1)
        return random.uniform(0, min(2 ** attempt * 0.5, 20.0))

    # -- verbs --------------------------------------------------------------

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)[0]

    def get_with_headers(self, path: str, **kwargs) -> tuple[Any, dict]:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: dict, **kwargs) -> Any:
        return self.request("POST", path, body=body, **kwargs)[0]

    def patch(self, path: str, body: dict, **kwargs) -> Any:
        return self.request(
            "PATCH", path, body=body, content_type="application/merge-patch+json", **kwargs
        )[0]

    def delete(self, path: str, **kwargs) -> Any:
        return self.request("DELETE", path, **kwargs)[0]

    # -- hypermedia ---------------------------------------------------------

    @staticmethod
    def link(resource: dict, rel: str) -> Optional[str]:
        """Read one link relation, or None if the action is not available.

        This is the intended way to navigate. A missing `cancel` link means the
        order is not cancellable right now -- more reliable than inspecting
        `status`, because new statuses can be added without a version bump but
        the link contract holds.
        """
        return (resource.get("_links") or {}).get(rel, {}).get("href")

    @staticmethod
    def can(resource: dict, rel: str) -> bool:
        return rel in (resource.get("_links") or {})

    def follow(self, resource: dict, rel: str, **kwargs) -> Any:
        href = self.link(resource, rel)
        if href is None:
            raise KeyError(f"No '{rel}' link on this resource; the action is unavailable.")
        return self.get(href, **kwargs)

    def paginate(self, path: str, **kwargs) -> Iterator[dict]:
        """Yield every item across pages by following `next` links.

        Note that it follows the server's `next` href verbatim rather than
        constructing cursors. The cursor encoding is explicitly not part of the
        contract and will change.
        """
        page = self.get(path, **kwargs)
        while True:
            yield from page.get("items", [])
            next_href = self.link(page, "next")
            if not next_href:
                return
            page = self.get(next_href)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def new_idempotency_key() -> str:
        """One key per order *intent*.

        Generate it before the first attempt and reuse it across every retry of
        that same intent. Generating a fresh key inside a retry loop defeats the
        entire mechanism.
        """
        return str(uuid.uuid4())


def with_idempotency(fn: Callable[[str], Any]) -> Any:
    """Run `fn` with a stable idempotency key across retries.

        order = with_idempotency(lambda key: client.post(path, body, idempotency_key=key))
    """
    return fn(BrokerageClient.new_idempotency_key())
