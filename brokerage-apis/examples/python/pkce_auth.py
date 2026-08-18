"""
OAuth 2.1 authorization code flow with PKCE, for the Brokerage REST API.

Standard library only -- no SDK is published for this API, and nothing here
needs one. Requires Python 3.9+.

What OAuth 2.1 changes, and why this file looks the way it does:

  * PKCE is mandatory for every client, not just public ones. The `state`
    parameter still defends against CSRF on the redirect; `code_verifier`
    defends against interception of the authorization code itself. They solve
    different problems, so we send both.
  * The implicit and resource-owner-password grants are gone. There is no
    supported way to exchange a username and password for a token, so never
    ask a customer for their brokerage credentials.
  * Refresh tokens rotate. Every refresh returns a new refresh token and
    invalidates the old one. Persist the new value atomically -- see
    TokenStore.save -- because losing it costs the user a re-authorization.
  * Replaying a rotated refresh token is treated as theft and revokes the
    whole token family. That is a feature: it bounds the damage from a stolen
    token to a single use.

Run directly to perform an interactive authorization:

    python3 pkce_auth.py
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, asdict
from typing import Optional

AUTH_BASE = os.environ.get("BROKERAGE_AUTH_BASE", "https://auth.brokerage.example.com")
CLIENT_ID = os.environ.get("BROKERAGE_CLIENT_ID", "your-client-id")
REDIRECT_URI = os.environ.get("BROKERAGE_REDIRECT_URI", "http://127.0.0.1:8723/callback")

SCOPES = [
    "accounts:read",
    "positions:read",
    "orders:read",
    "orders:write",
    "transactions:read",
    "instruments:read",
    "offline_access",
]


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------


@dataclass
class Tokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: float          # absolute UNIX time, not a duration
    scope: str
    token_type: str = "Bearer"

    @property
    def expired(self) -> bool:
        # Refresh 60s early. A token that passes this check locally can still be
        # rejected by the server -- clocks drift, and tokens can be revoked at
        # any time -- so the client must also handle a 401 on any request.
        return time.time() >= self.expires_at - 60

    @classmethod
    def from_response(cls, payload: dict, previous: Optional["Tokens"] = None) -> "Tokens":
        return cls(
            access_token=payload["access_token"],
            # A refresh response that omits refresh_token means the old one is
            # still current. Keep it rather than dropping to None.
            refresh_token=payload.get("refresh_token")
            or (previous.refresh_token if previous else None),
            expires_at=time.time() + int(payload.get("expires_in", 900)),
            scope=payload.get("scope", ""),
            token_type=payload.get("token_type", "Bearer"),
        )


class TokenStore:
    """File-backed token storage.

    Two details that matter more than they look:

      * Mode 0600. A refresh token is a 90-day bearer credential for someone's
        brokerage account; it must not be world-readable.
      * Atomic replace. Rotation means the old refresh token dies the moment
        the new one is issued. A partial write during rotation leaves the user
        with no usable token at all, so we write to a temp file and rename,
        which is atomic on POSIX filesystems.

    For anything multi-user, put these in a real secret manager instead.
    """

    def __init__(self, path: str = ".brokerage-tokens.json") -> None:
        self.path = path

    def load(self) -> Optional[Tokens]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return Tokens(**json.load(fh))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return None

    def save(self, tokens: Tokens) -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(tokens), fh)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    """Base64url without padding, per RFC 7636."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using the S256 method.

    32 random bytes yields a 43-character verifier, the shortest RFC 7636
    permits. Use `secrets`, never `random`: the verifier is the only thing
    stopping an attacker who intercepts the authorization code from redeeming
    it, so it has to be cryptographically unpredictable.

    The `plain` challenge method exists in the RFC but is not accepted by this
    authorization server, and should not be used anywhere.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorization_url(code_challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_BASE}/oauth2/authorize?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def _post_form(url: str, form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            raise OAuthError(
                parsed.get("error", "unknown_error"),
                parsed.get("error_description", detail),
            ) from exc
        except json.JSONDecodeError:
            raise OAuthError("http_error", f"{exc.code}: {detail}") from exc


class OAuthError(Exception):
    def __init__(self, code: str, description: str) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description


def exchange_code(code: str, code_verifier: str) -> Tokens:
    """Trade an authorization code for tokens.

    Note there is no client_secret. This is a public client, so PKCE does the
    work a secret would have done -- and shipping a secret in a mobile app or
    an SPA only creates the illusion of confidentiality. A confidential
    server-side client would add `private_key_jwt` client authentication here
    instead of a shared secret.
    """
    payload = _post_form(
        f"{AUTH_BASE}/oauth2/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        },
    )
    return Tokens.from_response(payload)


def refresh_tokens(tokens: Tokens) -> Tokens:
    """Exchange a refresh token for a fresh pair.

    The returned refresh token replaces the one you sent. Save it before you
    use the new access token for anything, so a crash between the two leaves
    you with the token that still works.
    """
    if not tokens.refresh_token:
        raise OAuthError("no_refresh_token", "Request the offline_access scope to receive one.")

    payload = _post_form(
        f"{AUTH_BASE}/oauth2/token",
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    return Tokens.from_response(payload, previous=tokens)


def revoke(token: str, token_type_hint: str = "refresh_token") -> None:
    """Revoke a token (RFC 7009). Call this on logout.

    Dropping tokens on the floor at logout leaves a live credential for the
    account sitting in whatever logs or backups happened to capture it.
    """
    _post_form(
        f"{AUTH_BASE}/oauth2/revoke",
        {"token": token, "token_type_hint": token_type_hint, "client_id": CLIENT_ID},
    )


# ---------------------------------------------------------------------------
# Interactive authorization
# ---------------------------------------------------------------------------


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != urllib.parse.urlparse(REDIRECT_URI).path:
            self.send_error(404)
            return

        query = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result = {k: v[0] for k, v in query.items()}

        ok = "code" in _CallbackHandler.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        message = "Authorization complete. You can close this tab." if ok else (
            f"Authorization failed: {_CallbackHandler.result.get('error', 'unknown')}"
        )
        self.wfile.write(f"<html><body><p>{message}</p></body></html>".encode("utf-8"))

    def log_message(self, *args) -> None:
        pass  # keep the console clean


def authorize_interactively(timeout: int = 300) -> Tokens:
    """Run the full browser-based flow and return tokens.

    Suitable for a desktop or CLI tool. A web application would instead
    redirect the user's browser and handle the callback in a normal route,
    keeping `state` and `code_verifier` in the session.
    """
    verifier, challenge = generate_pkce_pair()
    state = _b64url(secrets.token_bytes(16))

    redirect = urllib.parse.urlparse(REDIRECT_URI)
    server = http.server.HTTPServer((redirect.hostname, redirect.port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = build_authorization_url(challenge, state)
    print("Opening your browser to authorize...")
    print(f"  If it does not open, visit:\n  {url}\n")
    webbrowser.open(url)

    deadline = time.time() + timeout
    try:
        while not _CallbackHandler.result and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()

    result = _CallbackHandler.result
    if not result:
        raise OAuthError("timeout", f"No callback received within {timeout}s.")
    if "error" in result:
        raise OAuthError(result["error"], result.get("error_description", ""))

    # Constant-time compare. `state` is short and this check is cheap, but
    # comparing secrets with == is a habit worth not having.
    if not secrets.compare_digest(result.get("state", ""), state):
        raise OAuthError("state_mismatch", "Possible CSRF; discarding the authorization code.")

    return exchange_code(result["code"], verifier)


def get_valid_tokens(store: Optional[TokenStore] = None) -> Tokens:
    """Return usable tokens, refreshing or re-authorizing as needed.

    This is the function application code should call. It encapsulates the
    three-way branch every OAuth client needs: no tokens, stale tokens,
    good tokens.
    """
    store = store or TokenStore()
    tokens = store.load()

    if tokens is None:
        tokens = authorize_interactively()
        store.save(tokens)
        return tokens

    if tokens.expired:
        try:
            tokens = refresh_tokens(tokens)
        except OAuthError as exc:
            # invalid_grant means the refresh token is revoked, expired, or was
            # already used. There is no recovery except sending the user back
            # through authorization.
            if exc.code != "invalid_grant":
                raise
            print(f"Refresh failed ({exc.description}); re-authorizing.")
            tokens = authorize_interactively()
        store.save(tokens)

    return tokens


if __name__ == "__main__":
    tokens = get_valid_tokens()
    print("Access token acquired.")
    print(f"  scope   : {tokens.scope}")
    print(f"  expires : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tokens.expires_at))}")
    print(f"  refresh : {'yes' if tokens.refresh_token else 'no (offline_access not granted)'}")
