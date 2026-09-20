#!/usr/bin/env python3
"""
Conformance linter for the Tool Specification Standard (EA-STD-TOOL-001).

Checks tool descriptors against the normative rules in the standard and reports
findings by rule identifier, so that a review comment reading "fails TS-INP-09"
means the same thing in a pull request as it does in the document.

    tools/lint-descriptors.py catalog/*.json
    tools/lint-descriptors.py catalog            # a directory works too

Exit status is 1 if any MUST-level rule is violated; SHOULD-level findings are
reported as warnings and do not fail the build. Standard library only, so it
runs anywhere CI runs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# --- the closed vocabularies the standard defines ------------------------

TOP_LEVEL = {"name", "title", "description", "inputSchema", "outputSchema",
             "annotations", "icons", "_meta"}

READ_VERBS = {"get", "list", "search", "summarize", "check", "preview"}
WRITE_VERBS = {"create", "place", "update", "replace", "cancel", "delete", "export"}
VERBS = READ_VERBS | WRITE_VERBS

# Verbs that are not naturally idempotent and therefore need a key (TS-SEC-07).
NEEDS_IDEMPOTENCY_KEY = {"create", "place", "update", "replace"}

BANNED_PARAMS = {
    "userid": "TS-SEC-02", "customerid": "TS-SEC-02", "onbehalfof": "TS-SEC-02",
    "tenantid": "TS-SEC-02", "role": "TS-SEC-02", "principal": "TS-SEC-02",
    "apikey": "TS-SEC-03", "token": "TS-SEC-03", "password": "TS-SEC-03",
    "secret": "TS-SEC-03", "authorization": "TS-SEC-03",
    "sql": "TS-INP-17", "query": "TS-INP-17", "filterexpression": "TS-INP-17",
    "jsonpath": "TS-INP-17", "graphqlquery": "TS-INP-17",
    "action": "TS-NAM-06", "operation": "TS-NAM-06", "mode": "TS-NAM-06",
    "command": "TS-NAM-06",
    "force": "TS-INP-13", "skipvalidation": "TS-INP-13", "dryrun": "TS-INP-13",
    "override": "TS-INP-13",
    "env": "TS-NAM-05", "environment": "TS-NAM-05", "region": "TS-NAM-05",
    "debug": "TS-NAM-05", "usecache": "TS-NAM-05",
    "limit": "TS-INP-15", "offset": "TS-INP-15", "page": "TS-INP-15",
    "pagenumber": "TS-INP-15", "count": "TS-INP-15", "top": "TS-INP-15",
}

BANNED_KEYWORDS = ["if", "then", "else", "not", "anyOf", "allOf",
                   "dependentSchemas", "dependentRequired", "patternProperties",
                   "propertyNames", "$dynamicRef", "$dynamicAnchor", "nullable"]

ALLOWED_FORMATS = {"date", "date-time", "duration", "uuid", "uri"}

GOVERNANCE_REQUIRED = ["version", "lifecycle", "conformanceLevel", "riskTier",
                       "owner", "dataClassification", "containsPii",
                       "entitlements", "approval", "recordKeeping", "rateLimit"]

MONEYISH = re.compile(
    r"(price|amount|marketvalue|netliquidation|cost|pnl|balance|buyingpower"
    r"|equity|cash|value|liquidity|requirement)", re.I)

CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SECRETISH = re.compile(r"(https?://(?!schemas\.example\.com)|\.internal\b|jdbc:|"
                       r"BEGIN [A-Z ]*PRIVATE KEY|password\s*=)", re.I)


class Report:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str, str]] = []
        self.warnings: list[tuple[str, str, str]] = []

    def must(self, tool: str, rule: str, msg: str) -> None:
        self.errors.append((tool, rule, msg))

    def should(self, tool: str, rule: str, msg: str) -> None:
        self.warnings.append((tool, rule, msg))


def walk(schema, path="", depth=0):
    """Yield (path, subschema, depth) for every property subschema."""
    if not isinstance(schema, dict):
        return
    for key, sub in (schema.get("properties") or {}).items():
        yield f"{path}/{key}", key, sub, depth
        yield from walk(sub, f"{path}/{key}", depth + 1)
    items = schema.get("items")
    if isinstance(items, dict):
        yield from walk(items, f"{path}/*", depth)


def find_banned_keywords(node, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in BANNED_KEYWORDS:
                found.add(k)
            if k == "$ref" and isinstance(v, str) and not v.startswith("#"):
                found.add("$ref:remote")
            find_banned_keywords(v, found)
    elif isinstance(node, list):
        for v in node:
            find_banned_keywords(v, found)


def bounded_string(sub: dict) -> bool:
    return any(k in sub for k in ("maxLength", "enum", "const", "pattern", "format"))


def lint(doc: dict, source: str, r: Report) -> None:
    name = doc.get("name", f"<{source}>")

    # --- structure -------------------------------------------------------
    extra = set(doc) - TOP_LEVEL
    if extra:
        r.must(name, "TS-GEN-02", f"unknown top-level field(s): {', '.join(sorted(extra))}")
    for required in ("name", "title", "description", "inputSchema", "outputSchema",
                     "annotations", "_meta"):
        if required not in doc:
            r.must(name, "TS-GEN-02", f"missing required field '{required}'")

    # --- name ------------------------------------------------------------
    if not NAME_RE.match(doc.get("name", "")):
        r.must(name, "TS-NAM-01", "name must match ^[a-z][a-z0-9_]{2,63}$")
    segments = doc.get("name", "").split("_")
    verb = segments[-1] if segments else ""
    if verb not in VERBS:
        r.must(name, "TS-NAM-06", f"final segment '{verb}' is not in the operation vocabulary")
    if len(segments) < 2:
        r.must(name, "TS-NAM-01", "name must have at least a domain and an operation segment")
    if len(doc.get("name", "")) > 40:
        r.should(name, "TS-NAM-03", f"name is {len(doc['name'])} characters; 40 or fewer is preferred")
    for banned in ("uat", "dev", "prod", "emea", "apac", "test"):
        if banned in segments:
            r.must(name, "TS-NAM-05", f"name contains environment/region segment '{banned}'")

    # --- title -----------------------------------------------------------
    title = doc.get("title", "")
    if not title:
        r.must(name, "TS-NAM-07", "title is required")
    elif len(title) > 60:
        r.must(name, "TS-NAM-07", f"title is {len(title)} characters; the limit is 60")
    elif title.endswith("."):
        r.should(name, "TS-NAM-07", "title should not end with punctuation")

    # --- description -----------------------------------------------------
    desc = doc.get("description", "")
    if len(desc) > 2000:
        r.must(name, "TS-DSC-02", f"description is {len(desc)} characters; the limit is 2000")
    if len(desc) < 200:
        r.should(name, "TS-DSC-01", "description is very short; check all seven parts are present")
    if not re.search(r"\buse (it )?when\b", desc, re.I):
        r.must(name, "TS-DSC-01", "description has no selection trigger ('Use when ...')")
    if not re.search(r"\bdo not use\b", desc, re.I):
        r.must(name, "TS-DSC-01", "description has no exclusion ('Do not use ... call <tool>')")
    if re.search(r"(^|\n)\s*[-*#]|```|\*\*", desc):
        r.must(name, "TS-DSC-04", "description contains markdown; use plain prose")
    if SECRETISH.search(desc):
        r.must(name, "TS-GEN-08", "description appears to contain a URL, hostname, or credential")
    for phrase in ("this tool allows", "this function", "this api", "this endpoint"):
        if phrase in desc.lower():
            r.should(name, "TS-DSC-04", f"description opens with ceremony: '{phrase}'")

    # --- input schema ----------------------------------------------------
    ins = doc.get("inputSchema", {})
    if ins.get("type") != "object":
        r.must(name, "TS-INP-01", "inputSchema root must be type object")
    if "properties" not in ins:
        r.must(name, "TS-INP-01", "inputSchema must declare properties (possibly empty)")
    if "required" not in ins:
        r.must(name, "TS-INP-01", "inputSchema must declare an explicit required array")
    if ins.get("additionalProperties") is not False:
        r.must(name, "TS-INP-01", "inputSchema must set additionalProperties to false")
    if len(ins.get("properties") or {}) > 12:
        r.should(name, "TS-INP-02", f"{len(ins['properties'])} top-level parameters; 12 is the guidance")

    banned_found: set[str] = set()
    find_banned_keywords(ins, banned_found)
    for kw in sorted(banned_found):
        rule = "TS-GEN-07" if kw == "$ref:remote" else "TS-INP-01"
        r.must(name, rule, f"inputSchema uses prohibited construct '{kw}'")

    for ptr, key, sub, depth in walk(ins):
        if not isinstance(sub, dict):
            continue
        low = key.lower()
        if low == "query" and verb == "search":
            pass  # a relevance query is the whole point of a search tool
        elif low in BANNED_PARAMS:
            r.must(name, BANNED_PARAMS[low], f"prohibited parameter '{ptr}'")
        if not CAMEL.match(key):
            r.must(name, "TS-INP-03", f"parameter '{ptr}' is not lowerCamelCase")
        if depth > 1:
            r.should(name, "TS-INP-02", f"parameter '{ptr}' nests more than two levels deep")
        d = sub.get("description", "")
        if not d:
            r.must(name, "TS-INP-14", f"parameter '{ptr}' has no description")
        elif len(d) > 240:
            r.must(name, "TS-DSC-03", f"parameter '{ptr}' description is {len(d)} characters; the limit is 240")
        elif re.match(r"^(the\s+)?[\w ]+\(?(a\s+)?(string|integer|boolean|number|array|object)\)?\.?$",
                      d.strip(), re.I):
            r.must(name, "TS-INP-14", f"parameter '{ptr}' description merely restates the type")
        types = sub.get("type")
        types = [types] if isinstance(types, str) else (types or [])
        if "string" in types and not bounded_string(sub):
            r.must(name, "TS-INP-08", f"string parameter '{ptr}' declares no maxLength, pattern, enum, or format")
        if "array" in types and "maxItems" not in sub:
            r.must(name, "TS-INP-08", f"array parameter '{ptr}' declares no maxItems")
        if key.lower().endswith("id") and "pattern" not in sub and "enum" not in sub:
            r.must(name, "TS-INP-07", f"identifier '{ptr}' declares no pattern")
        fmt = sub.get("format")
        if fmt and fmt not in ALLOWED_FORMATS:
            r.must(name, "TS-INP-01", f"parameter '{ptr}' uses format '{fmt}', which is outside the profile")
        if key not in (ins.get("required") or []) and depth == 0 and "default" not in sub:
            if not re.search(r"\bomit|omitted|by default|defaults? to\b", d, re.I):
                r.should(name, "TS-INP-11", f"optional parameter '{ptr}' does not state its omission behavior")

    # --- output schema ---------------------------------------------------
    outs = doc.get("outputSchema", {})
    if outs.get("type") != "object":
        r.must(name, "TS-OUT-01", "outputSchema root must be type object")
    if outs.get("additionalProperties") is not False:
        r.must(name, "TS-OUT-01", "outputSchema must set additionalProperties to false")
    out_props = outs.get("properties") or {}

    banned_found = set()
    find_banned_keywords(outs, banned_found)
    for kw in sorted(banned_found):
        if kw == "$ref:remote":
            r.must(name, "TS-GEN-07", "outputSchema references a remote $ref")

    ann = doc.get("annotations") or {}
    read_only = ann.get("readOnlyHint") is True
    if read_only and "asOf" not in out_props:
        r.must(name, "TS-OUT-03", "read tool returns no asOf; every mutable figure needs one")
    if verb in ("list", "search") and "completeness" not in out_props:
        r.must(name, "TS-OUT-07", "collection tool returns no completeness block")

    for ptr, key, sub, _ in walk(outs):
        if not isinstance(sub, dict):
            continue
        d = sub.get("description", "")
        if not d and "$ref" not in sub:
            r.should(name, "TS-INP-14", f"output field '{ptr}' has no description")
        t = sub.get("type")
        t = [t] if isinstance(t, str) else (t or [])
        if MONEYISH.search(key) and ("number" in t or "integer" in t):
            r.must(name, "TS-OUT-13", f"monetary field '{ptr}' is numeric; use a decimal string")

    # --- annotations -----------------------------------------------------
    for a in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
        if not isinstance(ann.get(a), bool):
            r.must(name, "TS-ANN-01", f"annotation '{a}' is missing or not a boolean")
    if verb in READ_VERBS and ann.get("readOnlyHint") is not True:
        r.must(name, "TS-ANN-02", f"'{verb}' tools must declare readOnlyHint true")
    if verb in WRITE_VERBS - {"export"} and ann.get("readOnlyHint") is not False:
        r.must(name, "TS-ANN-02", f"'{verb}' tools must declare readOnlyHint false")
    if ann.get("readOnlyHint") is True and ann.get("destructiveHint") is True:
        r.must(name, "TS-ANN-02", "a read-only tool cannot be destructive")

    # --- idempotency -----------------------------------------------------
    in_props = ins.get("properties") or {}
    if verb in NEEDS_IDEMPOTENCY_KEY:
        if "idempotencyKey" not in in_props:
            r.must(name, "TS-SEC-07", f"'{verb}' tool does not require an idempotencyKey")
        elif "idempotencyKey" not in (ins.get("required") or []):
            r.must(name, "TS-SEC-07", "idempotencyKey is present but not required")

    # --- governance ------------------------------------------------------
    meta = doc.get("_meta") or {}
    gov_keys = [k for k in meta if k.endswith("/governance")]
    if not gov_keys:
        r.must(name, "TS-GOV-01", "_meta carries no namespaced governance block")
        return
    gov = meta[gov_keys[0]]
    for k in GOVERNANCE_REQUIRED:
        if k not in gov:
            r.must(name, "TS-GOV-01", f"governance block is missing '{k}'")
    tier = gov.get("riskTier")
    if tier not in (0, 1, 2, 3):
        r.must(name, "TS-ANN-03", "riskTier must be 0, 1, 2, or 3")
    if gov.get("lifecycle") not in ("draft", "active", "deprecated", "retired"):
        r.must(name, "TS-GOV-01", "lifecycle must be draft, active, deprecated, or retired")
    if not re.match(r"^\d+\.\d+\.\d+$", str(gov.get("version", ""))):
        r.must(name, "TS-VER-01", "version must be semantic (major.minor.patch)")
    owner = gov.get("owner") or {}
    for k in ("team", "contact", "escalation"):
        if k not in owner:
            r.must(name, "TS-GOV-01", f"owner is missing '{k}'")
    if not (gov.get("rateLimit") or {}).get("perPrincipalPerMinute"):
        r.must(name, "TS-GOV-04", "no per-principal rate limit declared")
    rk = gov.get("recordKeeping") or {}
    if "auditEvent" not in rk or "retention" not in rk:
        r.must(name, "TS-OBS-01", "recordKeeping must declare auditEvent and retention")
    elif not re.match(r"^P", str(rk["retention"])):
        r.must(name, "TS-OBS-01", "retention must be an ISO 8601 duration, e.g. P6Y")

    approval = gov.get("approval") or {}
    if tier == 3:
        if approval.get("mode") not in ("confirm", "dualControl", "humanExecution"):
            r.must(name, "TS-ANN-04", "tier 3 tools require confirm, dualControl, or humanExecution")
        if not approval.get("confirmationTemplate"):
            r.must(name, "TS-ANN-04", "tier 3 tools require a confirmationTemplate")
        if not approval.get("expiresInSeconds"):
            r.must(name, "TS-ANN-06", "tier 3 approvals must declare an expiry")
    if read_only and tier == 3:
        r.should(name, "TS-ANN-03", "a read-only tool at tier 3 is unusual; confirm the classification")
    if not read_only and tier < 2:
        r.must(name, "TS-ANN-03", "a state-changing tool cannot be below tier 2")
    if gov.get("conformanceLevel") in ("L2", "L3") and "evaluation" not in gov:
        r.must(name, "TS-GOV-01", "L2 and above require an evaluation record")
    if gov.get("dataClassification") == "restricted":
        r.must(name, "TS-GOV-02", "restricted data must not be returned by a tool")


def main(argv: list[str]) -> int:
    paths: list[Path] = []
    for arg in argv or ["catalog"]:
        p = Path(arg)
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    if not paths:
        print("no descriptors found", file=sys.stderr)
        return 2

    r = Report()
    for path in paths:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            r.must(path.name, "TS-GEN-05", f"not valid JSON: {exc}")
            continue
        lint(doc, path.name, r)

    for tool, rule, msg in r.errors:
        print(f"FAIL  {tool:32s} {rule:12s} {msg}")
    for tool, rule, msg in r.warnings:
        print(f"warn  {tool:32s} {rule:12s} {msg}")

    print(f"\n{len(paths)} descriptor(s) · {len(r.errors)} error(s) · {len(r.warnings)} warning(s)")
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
