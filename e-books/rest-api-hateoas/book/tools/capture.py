#!/usr/bin/env python3
"""
Capture the HTTP exchanges printed in the book from the running application.

Every request and response in the book comes from here, recorded against freshly started
servers, so the examples are what the code actually does. The script:

  1. starts the authorization server (:9000) and the API (:8080) from their built jars,
  2. runs a scripted session: tokens, reads, the order lifecycle, errors, versions, the
     browser (PKCE) sign-in, server-sent events, and a second API instance with a tiny
     rate limit (:8081) to show a 429,
  3. writes each exchange to captures/<name>.http, and stops everything it started.

Build the jars first (in brokerage-apis: ./mvnw -q -DskipTests package), then:

  tools/capture.py            # all captures
  tools/capture.py --keep     # leave the servers running afterwards

Java 27 is needed to run the jars: set JAVA_HOME, or put it first on the PATH.
"""
import base64
import hashlib
import html
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOOK = HERE.parent
OUT = BOOK / "captures"
CODE = BOOK.parent.parent.parent / "brokerage-apis"
AUTH_JAR = CODE / "auth-server/target/auth-server-1.0.0.jar"
API_JAR = CODE / "brokerage-api/target/brokerage-api-1.0.0.jar"
AGENT_JAR = CODE / "brokerage-agent/target/brokerage-agent-1.0.0.jar"

ALL = "accounts:read market-data:read orders:read orders:write"

# Response headers worth printing, in the order they are printed. Everything else
# (Date, Vary, X-Content-Type-Options, ...) is noise in a book.
SHOW = ["Location", "Content-Location", "ETag", "Content-Type", "Cache-Control", "Idempotent-Replayed",
        "API-Version", "Deprecation", "Sunset", "Link", "Retry-After", "WWW-Authenticate",
        "RateLimit-Policy", "RateLimit", "X-Request-Id"]
DEFAULT_SHOW = {"Location", "Content-Location", "ETag", "Content-Type", "Idempotent-Replayed",
                "Deprecation", "Sunset", "Link", "Retry-After", "WWW-Authenticate"}
CACHING = ["ETag", "Content-Type", "Cache-Control", "Deprecation", "Sunset", "Link"]

started = []


# ----------------------------------------------------------------------------- servers

def java():
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin/java").exists():
        return str(Path(home) / "bin/java")
    return shutil.which("java") or sys.exit("java not found; set JAVA_HOME to a Java 27 JDK")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(jar, port, *args):
    if port_open(port):
        sys.exit(f"port {port} is busy; stop whatever is running there first")
    log = open(OUT / f".{jar.stem}-{port}.log", "w")
    proc = subprocess.Popen([java(), "-jar", str(jar), f"--server.port={port}", *args],
                            stdout=log, stderr=subprocess.STDOUT)
    started.append(proc)
    for _ in range(120):
        if proc.poll() is not None:
            sys.exit(f"{jar.name} exited; see {log.name}")
        if port_open(port):
            time.sleep(1.0)
            return proc
        time.sleep(0.5)
    sys.exit(f"{jar.name} did not start on port {port}")


def stop_all():
    for proc in started:
        proc.terminate()
    for proc in started:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


# ----------------------------------------------------------------------------- HTTP

class Exchange:
    """One request and its response, as raw parts."""

    def __init__(self, method, target, req_headers, req_body, status, resp_headers, resp_body):
        self.method, self.target = method, target
        self.req_headers, self.req_body = req_headers, req_body
        self.status, self.resp_headers, self.resp_body = status, resp_headers, resp_body

    def header(self, name):
        for k, v in self.resp_headers:
            if k.lower() == name.lower():
                return v
        return None

    def json(self):
        return json.loads(self.resp_body)


def request(method, url, headers=None, body=None, form=None):
    u = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
    target = u.path + (("?" + u.query) if u.query else "")
    headers = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form, doseq=True, safe=":/ ").replace(" ", "+")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif body is not None:
        data = body if isinstance(body, str) else json.dumps(body)
        headers.setdefault("Content-Type", "application/json")
    conn.request(method, target, body=data.encode() if data else None, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode()
    ex = Exchange(method, target, list(headers.items()), data, resp.status, resp.getheaders(), raw)
    conn.close()
    return ex


def token(client, secret, scope, extra=None):
    basic = base64.b64encode(f"{client}:{secret}".encode()).decode()
    ex = request("POST", "http://localhost:9000/oauth2/token",
                 headers={"Authorization": f"Basic {basic}"},
                 form={"grant_type": "client_credentials", "scope": scope, **(extra or {})})
    assert ex.status == 200, ex.resp_body
    return ex.json()["access_token"], ex


# ----------------------------------------------------------------------------- formatting

def pretty(text, trim=None, redact=()):
    """Pretty-print JSON; optionally shorten long arrays and secret values."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return text
    for path, keep in (trim or {}).items():
        node, keys = doc, path.split(".")
        for key in keys[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        items = node.get(keys[-1]) if isinstance(node, dict) else None
        if isinstance(items, list) and len(items) > keep:
            node[keys[-1]] = items[:keep] + [f"@@MORE@@{len(items) - keep}"]
    for key in redact:
        _redact(doc, key)
    out = json.dumps(doc, indent=2, ensure_ascii=False)
    return re.sub(r'"@@MORE@@(\d+)"', lambda m: f"/* {m.group(1)} more */", out)


def _redact(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, str) and len(v) > 28:
                node[k] = v[:24] + "…"
            else:
                _redact(v, key)
    elif isinstance(node, list):
        for v in node:
            _redact(v, key)


def abbreviate_auth(value):
    if value.startswith("Bearer ") and len(value) > 30:
        return value[:27] + "…"
    return value


def render(ex, show=None, hide_request_body=False, trim=None, redact=(), host=None, note=None):
    lines = [f"{ex.method} {ex.target} HTTP/1.1"]
    lines.append(f"Host: {host or 'localhost:8080'}")
    for k, v in ex.req_headers:
        if k.lower() == "content-length":
            continue
        lines.append(f"{k}: {abbreviate_auth(v)}")
    if ex.req_body and not hide_request_body:
        lines.append("")
        lines.append(pretty(ex.req_body) if ex.req_body.lstrip().startswith("{") else ex.req_body)
    lines.append("")
    reason = HTTPStatus(ex.status).phrase if ex.status in HTTPStatus._value2member_map_ else ""
    if ex.status == 422:
        reason = "Unprocessable Content"
    lines.append(f"HTTP/1.1 {ex.status} {reason}".rstrip())
    wanted = set(show) if show is not None else DEFAULT_SHOW
    for name in SHOW:
        if name in wanted:
            for k, v in ex.resp_headers:
                if k.lower() == name.lower():
                    lines.append(f"{name}: {v}")
    if ex.resp_body.strip():
        lines.append("")
        lines.append(pretty(ex.resp_body, trim, redact))
    text = "\n".join(lines) + "\n"
    return (f"# {note}\n" if note else "") + text


def save(name, text):
    (OUT / f"{name}.http").write_text(text)
    first = text.splitlines()[0][:70]
    print(f"  {name:28s} {first}")


def api(method, path, tok=None, headers=None, body=None, base="http://localhost:8080", pin=True):
    """A request to the API. Like a well-behaved client, it pins API version 2 unless told not to."""
    h = {}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if pin:
        h["API-Version"] = "2"
    h.update(headers or {})
    return request(method, base + path, headers=h, body=body)


def claims(jwt):
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


# ----------------------------------------------------------------------------- the session

def session():
    cli, cli_ex = token("brokerage-cli", "cli-secret", ALL)
    save("token-cli", render(cli_ex, show=["Content-Type", "Cache-Control"], redact=("access_token",),
                              host="localhost:9000"))
    (OUT / "claims-cli.json").write_text(json.dumps(claims(cli), indent=2) + "\n")
    agent, _ = token("brokerage-agent", "agent-secret", ALL)
    (OUT / "claims-agent.json").write_text(json.dumps(claims(agent), indent=2) + "\n")
    readonly, _ = token("brokerage-cli", "cli-secret", "accounts:read market-data:read")
    market, _ = token("brokerage-cli", "cli-secret", "market-data:read")

    ex = request("GET", "http://localhost:9000/.well-known/oauth-authorization-server")
    save("as-metadata", render(ex, show=["Content-Type"], host="localhost:9000",
                               trim={}))

    # --- discovery
    save("root", render(api("GET", "/", headers={"Accept": "application/hal+json"})))
    save("prm", render(api("GET", "/.well-known/oauth-protected-resource")))
    save("no-token", render(api("GET", "/accounts"), show=["Content-Type", "WWW-Authenticate"]))
    save("wrong-scope", render(api("GET", "/accounts", market),
                               show=["Content-Type", "WWW-Authenticate"]))

    # --- accounts
    save("accounts", render(api("GET", "/accounts", cli), trim={"_embedded.bk:accounts": 1}))
    save("accounts-readonly", render(api("GET", "/accounts/ACC-1001", readonly)))
    save("account", render(api("GET", "/accounts/ACC-1001", cli)))
    save("account-hal-forms", render(api("GET", "/accounts/ACC-1001", cli,
                                         headers={"Accept": "application/prs.hal-forms+json"})))
    save("foreign-account", render(api("GET", "/accounts/ACC-2001", cli), show=["Content-Type"]))
    save("balances", render(api("GET", "/accounts/ACC-1001/balances", cli)))
    save("positions", render(api("GET", "/accounts/ACC-1001/positions", cli),
                             trim={"_embedded.bk:positions": 2}))
    page1 = api("GET", "/accounts/ACC-1001/transactions?limit=2", cli)
    save("transactions-page1", render(page1))
    nxt = page1.json()["_links"]["next"]["href"].replace("http://localhost:8080", "")
    save("transactions-page2", render(api("GET", nxt, cli)))
    save("transactions-dividends", render(api("GET", "/accounts/ACC-1001/transactions?type=DIVIDEND", cli)))
    save("bad-limit", render(api("GET", "/accounts/ACC-1001/transactions?limit=500", cli),
                             show=["Content-Type"]))

    # --- market data and versions
    save("instruments-search", render(api("GET", "/instruments?symbol=AAPL", cli)))
    q = api("GET", "/instruments/EQ-AAPL/quote", cli)
    save("quote-v2", render(q, show=CACHING))
    save("quote-304", render(api("GET", "/instruments/EQ-AAPL/quote", cli,
                                 headers={"If-None-Match": q.header("ETag")}), show=CACHING))
    save("quote-v1", render(api("GET", "/instruments/EQ-AAPL/quote", cli, pin=False), show=CACHING))
    save("quote-v9", render(api("GET", "/instruments/EQ-AAPL/quote", cli, headers={"API-Version": "9"}),
                            show=["Content-Type"]))
    save("option-chain", render(api("GET", "/instruments/EQ-AAPL/option-chain", cli),
                                trim={"_embedded.bk:instruments": 1}))

    # --- previews and orders
    ticket = {"instrumentId": "EQ-MSFT", "side": "BUY", "type": "LIMIT", "quantity": "10",
              "limitPrice": "450.00", "timeInForce": "GTC", "clientOrderId": "rebalance-2026-09-28"}
    save("preview-ok", render(api("POST", "/accounts/ACC-1001/order-previews", cli, body=ticket)))
    save("preview-bad", render(api("POST", "/accounts/ACC-1001/order-previews", cli,
                                   body={"instrumentId": "EQ-AMZN", "side": "SELL", "type": "MARKET",
                                         "quantity": "40"})))
    key = "7d0e4c9a-3b1f-4e6d-8a52-9c0f1e2b3a4d"
    placed = api("POST", "/accounts/ACC-1001/orders", cli, headers={"Idempotency-Key": key}, body=ticket)
    save("place", render(placed))
    save("place-replay", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                    headers={"Idempotency-Key": key}, body=ticket)))
    save("place-reused-key", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                        headers={"Idempotency-Key": key},
                                        body={**ticket, "quantity": "12"}), show=["Content-Type"]))
    save("place-no-key", render(api("POST", "/accounts/ACC-1001/orders", cli, body=ticket),
                                show=["Content-Type"]))
    save("place-invalid", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                     headers={"Idempotency-Key": "b1c2d3e4-0000-4000-8000-000000000001"},
                                     body={"instrumentId": "", "side": "BUY", "type": "LIMIT",
                                           "quantity": "-5", "limitPrice": "231.123456"}),
                                 show=["Content-Type"]))
    save("place-invalid-json", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                          headers={"Idempotency-Key": "b1c2d3e4-0000-4000-8000-000000000009"},
                                          body={"instrumentId": "EQ-AAPL", "side": "HOLD", "type": "LIMIT",
                                                "quantity": "5", "limitPrice": "230.00"}),
                                      show=["Content-Type"]))
    save("place-too-big", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                     headers={"Idempotency-Key": "b1c2d3e4-0000-4000-8000-000000000002"},
                                     body={"instrumentId": "ETF-SPY", "side": "BUY", "type": "LIMIT",
                                           "quantity": "500", "limitPrice": "660.00"}),
                                 show=["Content-Type"]))
    save("place-limit-missing", render(api("POST", "/accounts/ACC-1001/orders", cli,
                                           headers={"Idempotency-Key": "b1c2d3e4-0000-4000-8000-000000000003"},
                                           body={"instrumentId": "EQ-AAPL", "side": "BUY", "type": "LIMIT",
                                                 "quantity": "5"}), show=["Content-Type"]))
    save("agent-limit", render(api("POST", "/accounts/ACC-1001/orders", agent,
                                   headers={"Idempotency-Key": "c0ffee00-0000-4000-8000-000000000004"},
                                   body={"instrumentId": "EQ-MSFT", "side": "BUY", "type": "MARKET",
                                         "quantity": "20"}), show=["Content-Type"]))

    url = placed.header("Location").replace("http://localhost:8080", "")
    etag = placed.header("ETag")
    save("order", render(api("GET", url, cli)))
    save("order-304", render(api("GET", url, cli, headers={"If-None-Match": etag})))
    save("order-hal-forms", render(api("GET", url, cli, headers={"Accept": "application/prs.hal-forms+json"})))
    save("amend-no-if-match", render(api("PATCH", url, cli, headers={"Content-Type": "application/merge-patch+json"},
                                         body={"limitPrice": "455.00"}), show=["Content-Type"]))
    amended = api("PATCH", url, cli, headers={"Content-Type": "application/merge-patch+json", "If-Match": etag},
                  body={"limitPrice": "455.00"})
    save("amend", render(amended))
    save("amend-stale", render(api("PATCH", url, cli,
                                   headers={"Content-Type": "application/merge-patch+json", "If-Match": etag},
                                   body={"quantity": "8"}), show=["Content-Type"]))
    save("cancel", render(api("POST", url + "/cancellation", cli)))
    time.sleep(2.5)                                   # the matching engine completes the cancel
    save("order-cancelled", render(api("GET", url, cli)))
    save("cancel-again", render(api("POST", url + "/cancellation", cli), show=["Content-Type"]))

    # a market order that fills, and an option order
    market_order = api("POST", "/accounts/ACC-1001/orders", cli,
                       headers={"Idempotency-Key": "a11ce000-0000-4000-8000-000000000005"},
                       body={"instrumentId": "EQ-AAPL", "side": "BUY", "type": "MARKET", "quantity": "3"})
    save("place-market", render(market_order))
    time.sleep(2.5)
    save("order-filled", render(api("GET", market_order.header("Location").replace("http://localhost:8080", ""),
                                    cli)))
    chain = api("GET", "/instruments/EQ-AAPL/option-chain", cli).json()
    call = next(c for c in chain["_embedded"]["bk:instruments"]
                if c["option"]["right"] == "CALL" and c["option"]["strike"] == "240")
    save("option-contract", render(api("GET", f"/instruments/{call['id']}", cli)))
    option_ticket = {"instrumentId": call["id"], "side": "BUY", "type": "LIMIT", "quantity": "2",
                     "limitPrice": "3.10", "timeInForce": "DAY"}
    save("preview-option", render(api("POST", "/accounts/ACC-1001/order-previews", cli, body=option_ticket)))
    save("orders-open", render(api("GET", "/accounts/ACC-1001/orders?status=OPEN&status=PARTIALLY_FILLED", cli),
                               trim={"_embedded.bk:orders": 1}))

    # --- server-sent events: subscribe, place an order, collect what arrives
    save("events", sse_capture(cli, place=True))
    save("events-replay", sse_capture(cli, last_event_id=events_first_id))

    # --- the contract
    ex = api("HEAD", "/openapi.yaml")
    save("openapi", render(ex, show=["Content-Type", "Cache-Control"]))
    save("root-cache", render(api("GET", "/instruments/EQ-AAPL", cli), show=["Content-Type", "Cache-Control"]))

    # --- the browser sign-in: authorization code with PKCE, then a refresh
    try:
        pkce()
    except Exception as failure:              # the flow depends on the server's HTML pages
        print(f"  (PKCE capture skipped: {failure})")

    # --- rate limiting, on a second API instance with a quota of 2 per minute
    start(API_JAR, 8081, "--brokerage.rate-limit.requests-per-minute=2",
          "--brokerage.simulator.enabled=false")
    seen = []
    for _ in range(8):
        ex = api("GET", "/accounts", cli, base="http://localhost:8081")
        seen.append(ex)
        if ex.status == 429:
            break
    ok = next(e for e in seen if e.status == 200)
    limited = next((e for e in seen if e.status == 429), None)
    rl = ["Content-Type", "RateLimit-Policy", "RateLimit", "Retry-After"]
    save("rate-limit-ok", render(ok, show=rl, host="localhost:8081", trim={"_embedded.bk:accounts": 0}))
    if limited:
        save("rate-limited", render(limited, show=rl, host="localhost:8081"))


events_first_id = ""


def sse_capture(tok, place=False, last_event_id=None):
    """Subscribe to the order event stream, optionally place an order, record what arrives."""
    global events_first_id
    lines, done = [], threading.Event()
    headers = {"Authorization": f"Bearer {tok}", "Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    def listen():
        conn = http.client.HTTPConnection("localhost", 8080, timeout=4)
        conn.request("GET", "/accounts/ACC-1001/order-events", headers=headers)
        resp = conn.getresponse()
        lines.append(f"HTTP/1.1 {resp.status} OK")
        lines.append(f"Content-Type: {resp.getheader('Content-Type')}")
        lines.append("")
        try:
            while not done.is_set():
                raw = resp.readline()
                if not raw:
                    break
                lines.append(raw.decode().rstrip("\n"))
        except (socket.timeout, TimeoutError, OSError):
            pass
        conn.close()

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(1.0)
    if place:
        api("POST", "/accounts/ACC-1001/orders", tok,
            headers={"Idempotency-Key": "e7e7e7e7-0000-4000-8000-000000000006"},
            body={"instrumentId": "EQ-NVDA", "side": "BUY", "type": "MARKET", "quantity": "4"})
    time.sleep(3.0)
    done.set()
    t.join(timeout=10)
    ids = [l.split(":", 1)[1] for l in lines if l.startswith("id:")]
    if place and ids:
        events_first_id = ids[0]
    head = ["GET /accounts/ACC-1001/order-events HTTP/1.1", "Host: localhost:8080",
            f"Authorization: Bearer {tok[:20]}…", "Accept: text/event-stream"]
    if last_event_id:
        head.append(f"Last-Event-ID: {last_event_id}")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(head) + "\n\n" + "\n".join(lines) + "\n"


def pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = "af0ifjsldkj"
    scope = "openid accounts:read market-data:read orders:read"
    params = {"response_type": "code", "client_id": "brokerage-web", "scope": scope,
              "redirect_uri": "http://127.0.0.1:3000/callback", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    authorize = "/oauth2/authorize?" + urllib.parse.urlencode(params, safe=":/", quote_via=urllib.parse.quote)
    jar = {}

    def call(method, target, form=None):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items())} if jar else {}
        ex = request(method, "http://localhost:9000" + target, headers=headers, form=form)
        for k, v in ex.resp_headers:
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                jar[name] = rest.split(";")[0]
        return ex

    first = call("GET", authorize)
    login = call("GET", "/login")
    csrf = re.search(r'name="_csrf"[^>]*value="([^"]+)"', login.resp_body).group(1)
    call("POST", "/login", form={"username": "alice", "password": "wonderland", "_csrf": csrf})
    consent = call("GET", authorize)
    if consent.status == 200 and "consent" in consent.resp_body.lower():
        fields = {}
        for m in re.finditer(r'<input[^>]+>', consent.resp_body):
            tag = m.group(0)
            name = re.search(r'name="([^"]+)"', tag)
            value = re.search(r'value="([^"]*)"', tag)
            if name and value:
                fields.setdefault(html.unescape(name.group(1)), []).append(html.unescape(value.group(1)))
        form = {k: v for k, v in fields.items()}
        redirect = call("POST", "/oauth2/authorize", form=form)
    else:
        redirect = consent
    location = redirect.header("Location")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    code = query["code"][0]
    assert query["state"][0] == state

    auth_text = (f"GET {authorize} HTTP/1.1\nHost: localhost:9000\n\n"
                 f"HTTP/1.1 302 Found\nLocation: http://localhost:9000/login\n")
    save("pkce-authorize", auth_text)
    save("pkce-callback", f"HTTP/1.1 302 Found\nLocation: {location[:60]}…{location[-24:]}\n")

    web = {"Authorization": "Basic " + base64.b64encode(b"brokerage-web:web-secret").decode()}
    ex = request("POST", "http://localhost:9000/oauth2/token", headers=web,
                 form={"grant_type": "authorization_code", "code": code,
                       "redirect_uri": "http://127.0.0.1:3000/callback", "code_verifier": verifier})
    assert ex.status == 200, ex.resp_body
    ex.req_body = ex.req_body.replace(code, code[:10] + "…").replace(verifier, verifier[:12] + "…")
    save("pkce-token", render(ex, show=["Content-Type", "Cache-Control"], host="localhost:9000",
                              redact=("access_token", "refresh_token", "id_token")))
    tokens = ex.json()
    (OUT / "claims-alice.json").write_text(json.dumps(claims(tokens["access_token"]), indent=2) + "\n")
    refresh = request("POST", "http://localhost:9000/oauth2/token", headers=web,
                      form={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
    refresh.req_body = refresh.req_body.replace(tokens["refresh_token"], tokens["refresh_token"][:12] + "…")
    save("pkce-refresh", render(refresh, show=["Content-Type"], host="localhost:9000",
                                redact=("access_token", "refresh_token", "id_token")))
    save("alice-accounts", render(api("GET", "/accounts", tokens["access_token"]),
                                  trim={"_embedded.bk:accounts": 1}))
    del first


def agent_status():
    if not AGENT_JAR.exists():
        return
    env = dict(os.environ, ANTHROPIC_API_KEY="")
    run = subprocess.run([java(), "-jar", str(AGENT_JAR)], input="/status\n/quit\n", text=True,
                         capture_output=True, timeout=120, env=env)
    typed = iter(["/status", "/quit"])
    text = re.sub(r"(?m)^> ", lambda m: "> " + next(typed, "") + "\n", run.stdout).strip()
    (OUT / "agent-status.txt").write_text(text + "\n")
    print("  agent-status.txt")


def demo_output():
    """The hypermedia client's guided tour, run through Maven as a reader would run it."""
    mvn = shutil.which("mvn")
    if not mvn:
        return
    run = subprocess.run([mvn, "-q", "-f", str(CODE / "pom.xml"), "-pl", "brokerage-client", "compile",
                          "exec:java", "-Dexec.mainClass=com.example.brokerage.client.Demo"],
                         capture_output=True, text=True, timeout=300)
    (OUT / "demo-output.txt").write_text(run.stdout.strip() + "\n")
    print("  demo-output.txt")


def main():
    for jar in (AUTH_JAR, API_JAR):
        if not jar.exists():
            sys.exit(f"missing {jar}; build it first (./mvnw -q -DskipTests package)")
    OUT.mkdir(exist_ok=True)
    keep = "--keep" in sys.argv
    try:
        start(AUTH_JAR, 9000)
        start(API_JAR, 8080)
        print("capturing:")
        session()
        agent_status()
        demo_output()
    finally:
        if not keep:
            stop_all()
        for log in OUT.glob(".*.log"):
            log.unlink()


if __name__ == "__main__":
    main()
