#!/usr/bin/env python3
"""
Generate dev-portal/reference.html from openapi/brokerage-api.yaml.

The endpoint reference is derived rather than hand-written so it cannot drift
from the specification. Re-run after any change to the spec:

    python3 tools/build-reference.py

Requires PyYAML (stdlib otherwise).
"""

from __future__ import annotations

import html
import pathlib
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required:  pip install pyyaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "openapi" / "brokerage-api.yaml"
OUT = ROOT / "dev-portal" / "reference.html"

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace", "query")

# Which guide page documents each tag, so the reference can link onward.
GUIDE_FOR_TAG = {
    "discovery": "index.html",
    "accounts": "accounts.html",
    "account-list": "accounts.html#account-list",
    "balances": "accounts.html#balances",
    "positions": "accounts.html#positions",
    "order-status": "accounts.html#order-status",
    "transactions": "accounts.html#transactions",
    "trading": "trading.html",
    "stock-trading": "trading.html#stock",
    "options-trading": "trading.html#options",
    "instruments": "trading.html#chains",
    "streaming": "trading.html#streaming",
}

# Presentation order of the top-level navigation tags.
SECTION_ORDER = ["discovery", "accounts", "trading", "instruments", "streaming"]


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def first_line(text: str | None) -> str:
    """First paragraph of a CommonMark description, as plain escaped text."""
    if not text:
        return ""
    for para in text.strip().split("\n\n"):
        cleaned = " ".join(line.strip() for line in para.strip().splitlines())
        if cleaned:
            # Render `code` spans; everything else stays literal.
            out, parts = [], cleaned.split("`")
            for i, part in enumerate(parts):
                out.append(f"<code>{esc(part)}</code>" if i % 2 else esc(part))
            return "".join(out)
    return ""


def scopes_of(operation: dict, spec: dict) -> list[str]:
    security = operation.get("security", spec.get("security", []))
    found: list[str] = []
    for requirement in security:
        for scopes in requirement.values():
            for scope in scopes:
                if scope not in found:
                    found.append(scope)
    return found


def nav_tag(operation: dict, tags_by_name: dict) -> str:
    """The operation's top-level navigation tag, walking `parent` upward."""
    for name in operation.get("tags", []):
        tag = tags_by_name.get(name)
        if not tag or tag.get("kind") != "nav":
            continue
        seen = set()
        while tag.get("parent") and tag["parent"] not in seen:
            seen.add(tag["name"])
            parent = tags_by_name.get(tag["parent"])
            if not parent:
                break
            tag = parent
        return tag["name"]
    return "other"


def collect(spec: dict) -> dict[str, list[dict]]:
    tags_by_name = {t["name"]: t for t in spec.get("tags", [])}
    sections: dict[str, list[dict]] = {}

    for path, item in spec["paths"].items():
        shared = item.get("parameters", [])
        entries = [(m.upper(), op) for m, op in item.items() if m in HTTP_METHODS]
        for method, op in item.get("additionalOperations", {}).items():
            entries.append((method.upper(), op))

        for method, operation in entries:
            section = nav_tag(operation, tags_by_name)
            sections.setdefault(section, []).append({
                "method": method,
                "path": path,
                "operation": operation,
                "params": shared + operation.get("parameters", []),
            })

    return sections


def render_params(params: list, spec: dict) -> str:
    rows = []
    for param in params:
        if "$ref" in param:
            ref = param["$ref"].split("/")[-1]
            param = spec["components"]["parameters"][ref]

        schema = param.get("schema", {})
        type_name = schema.get("type", "")
        if isinstance(type_name, list):
            type_name = " | ".join(t for t in type_name if t != "null") + " | null"
        if schema.get("enum"):
            type_name = "enum"

        required = ' <span class="req">required</span>' if param.get("required") else ""
        rows.append(
            f"<tr><td><code>{esc(param['name'])}</code>{required}</td>"
            f"<td><code>{esc(param.get('in',''))}</code></td>"
            f"<td><code>{esc(type_name)}</code></td>"
            f"<td>{first_line(param.get('description'))}</td></tr>"
        )

    if not rows:
        return ""
    return (
        '<h5>Parameters</h5><div class="table-wrap"><table>'
        "<thead><tr><th>Name</th><th>In</th><th>Type</th><th>Description</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def status_class(code: str) -> str:
    if code.startswith("2"):
        return "s-2xx"
    if code.startswith("3"):
        return "s-3xx"
    if code.startswith("4"):
        return "s-4xx"
    if code.startswith("5"):
        return "s-5xx"
    return "s-3xx"


def render_responses(responses: dict, spec: dict) -> str:
    rows = []
    for code, response in responses.items():
        if "$ref" in response:
            ref = response["$ref"].split("/")[-1]
            response = spec["components"]["responses"][ref]

        summary = response.get("summary") or ""
        detail = first_line(response.get("description"))
        label = code if code != "default" else "default"
        rows.append(
            f'<tr><td><span class="status-pill {status_class(label)}">{esc(label)}</span></td>'
            f"<td><strong>{esc(summary)}</strong>{'<br>' if summary and detail else ''}{detail}</td></tr>"
        )

    return (
        '<h5>Responses</h5><div class="table-wrap"><table>'
        "<thead><tr><th style='width:14%'>Status</th><th>Meaning</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_operation(entry: dict, spec: dict) -> str:
    operation = entry["operation"]
    method = entry["method"]
    op_id = operation.get("operationId", "")

    scopes = scopes_of(operation, spec)
    scope_html = ""
    if scopes:
        pills = " ".join(f'<span class="pill">{esc(s)}</span>' for s in scopes)
        scope_html = f"<div class='ep-meta'>Requires {pills}</div>"
    else:
        scope_html = "<div class='ep-meta'>Any valid access token</div>"

    body_html = ""
    if operation.get("requestBody"):
        content = operation["requestBody"].get("content", {})
        media = ", ".join(f"<code>{esc(m)}</code>" for m in content)
        required = " (required)" if operation["requestBody"].get("required") else ""
        body_html = f"<h5>Request body{esc(required)}</h5><p>{media}</p>"

    return f"""
<div class="endpoint tall" id="{esc(op_id)}">
  <div class="ep-line"><span class="verb {method.lower()}">{esc(method)}</span>{esc(entry['path'])}</div>
  <h4 class="op-title">{esc(operation.get('summary', op_id))}</h4>
  <p>{first_line(operation.get('description'))}</p>
  {scope_html}
  {render_params(entry['params'], spec)}
  {body_html}
  {render_responses(operation.get('responses', {}), spec)}
  <p class="op-id"><code>operationId: {esc(op_id)}</code></p>
</div>
"""


def build() -> str:
    spec = yaml.safe_load(SPEC.read_text())
    sections = collect(spec)
    tags_by_name = {t["name"]: t for t in spec.get("tags", [])}

    ordered = [s for s in SECTION_ORDER if s in sections]
    ordered += [s for s in sections if s not in ordered]

    op_count = sum(len(v) for v in sections.values())

    nav_items, body_parts = [], []
    for section in ordered:
        tag = tags_by_name.get(section, {})
        title = tag.get("summary") or section.replace("-", " ").title()
        nav_items.append(f'<li><a href="#sec-{esc(section)}">{esc(title)}</a></li>')

        guide = GUIDE_FOR_TAG.get(section)
        guide_link = (
            f' <a class="guide-link" href="{esc(guide)}">Guide →</a>' if guide else ""
        )

        body_parts.append(
            f'<h2 id="sec-{esc(section)}">{esc(title)}{guide_link}</h2>'
            f"<p>{first_line(tag.get('description'))}</p>"
        )
        for entry in sections[section]:
            body_parts.append(render_operation(entry, spec))

    info = spec["info"]
    servers = "".join(
        f"<div><dt>{esc(s['description'].split('.')[0])}</dt><dd>{esc(s['url'])}</dd></div>"
        for s in spec["servers"]
    )

    return f"""<!doctype html>
<html lang="en-US">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Endpoint reference — Brokerage API</title>
<meta name="description" content="Every operation in the Brokerage REST API, generated from the OpenAPI 3.2.0 specification.">
<link rel="stylesheet" href="assets/portal.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%231f4f8f'/><text x='16' y='23' font-size='19' font-family='sans-serif' font-weight='700' fill='white' text-anchor='middle'>B</text></svg>">
<style>
  .op-title {{ margin: 10px 0 6px; font-size: 15px; text-transform: none; letter-spacing: -0.01em; color: var(--ink); font-weight: 650; }}
  .endpoint h5 {{ margin: 16px 0 6px; font-size: 11px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; color: var(--ink-faint); }}
  .endpoint table {{ font-size: 13px; }}
  .endpoint p {{ font-size: 14px; }}
  .req {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--red); margin-left: 5px; }}
  .op-id {{ margin: 14px 0 0; padding-top: 10px; border-top: 1px solid var(--rule); font-size: 12px; color: var(--ink-faint); }}
  .guide-link {{ font-size: 13px; font-weight: 500; margin-left: 10px; white-space: nowrap; }}
  .generated {{ font-size: 13px; color: var(--ink-faint); }}
</style>
</head>
<body>

<header class="topbar">
  <button class="icon-btn" id="menu-toggle" type="button" aria-label="Toggle navigation">☰</button>
  <a class="brand" href="index.html"><span class="mark">B</span> Brokerage API <span class="ver">v1</span></a>
  <nav class="topnav">
    <a href="index.html">Overview</a>
    <a href="authentication.html">Authentication</a>
    <a href="accounts.html">Accounts</a>
    <a href="trading.html">Trading</a>
    <a href="examples.html">Examples</a>
    <a href="reference.html" class="active">Reference</a>
  </nav>
  <span class="spacer"></span>
  <button class="icon-btn" id="theme-toggle" type="button" aria-label="Toggle theme">☾</button>
</header>

<div class="scrim"></div>

<div class="shell">
  <aside class="sidebar">
    <h4>Get started</h4>
    <ul><li><a href="index.html">Overview</a></li></ul>
    <h4>Guides</h4>
    <ul>
      <li><a href="authentication.html">Authentication</a></li>
      <li><a href="accounts.html">Accounts</a></li>
      <li><a href="trading.html">Trading</a></li>
      <li><a href="examples.html">Code examples</a></li>
    </ul>
    <h4>Reference</h4>
    <ul>
      <li><a href="reference.html" class="active">Endpoint reference</a></li>
      {"".join(nav_items)}
      <li><a href="errors.html">Errors &amp; status codes</a></li>
      <li><a href="openapi/brokerage-api.yaml">OpenAPI 3.2.0 (YAML)</a></li>
      <li><a href="openapi/brokerage-api.json">OpenAPI 3.2.0 (JSON)</a></li>
    </ul>
  </aside>

  <main>
    <h1>Endpoint reference</h1>
    <p class="page-lede">
      Every operation in the API — {op_count} across {len(spec['paths'])} paths.
      This page is generated from the OpenAPI 3.2.0 specification, which is the
      normative artifact: where the guides and the specification disagree, the
      specification wins.
    </p>

    <div class="kv-grid">{servers}</div>

    <p class="generated">
      Generated from <code>openapi/brokerage-api.yaml</code> · API version {esc(info['version'])} ·
      regenerate with <code>python3 tools/build-reference.py</code>
    </p>

    {"".join(body_parts)}

    <div class="next-prev">
      <a href="examples.html"><span class="dir">Previous</span><span class="ttl">← Code examples</span></a>
      <a class="nxt" href="errors.html"><span class="dir">Next</span><span class="ttl">Errors &amp; status codes →</span></a>
    </div>
  </main>

  <nav class="toc" aria-label="On this page"></nav>
</div>

<footer class="site">
  <div class="inner">
    <div><strong>Brokerage API</strong> · v{esc(info['version'])} · Developer Portal</div>
    <div><a href="openapi/brokerage-api.yaml">OpenAPI 3.2.0</a> · <a href="errors.html">Errors</a> · <a href="index.html">Overview</a></div>
  </div>
</footer>

<script src="assets/portal.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"Wrote {OUT.relative_to(ROOT)}")
