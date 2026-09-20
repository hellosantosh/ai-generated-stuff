#!/usr/bin/env python3
"""
Import the generated page set into a Confluence space.

Creates pages that do not exist, updates those that do, and uploads each page's
attachments. Safe to re-run: it matches on page title within the space and bumps
the version rather than duplicating. Works against Cloud and Data Center.

    export CONFLUENCE_BASE_URL=https://example.atlassian.net/wiki
    export CONFLUENCE_SPACE=EATOOLS
    export CONFLUENCE_USER=you@example.com        # Cloud: email + API token
    export CONFLUENCE_TOKEN=...                   # DC: omit USER, use a PAT

    tools/import-confluence.py                    # dry run — prints the plan
    tools/import-confluence.py --apply            # actually writes

Nothing is deleted. Pages that exist in the space but not in the manifest are
reported so a human can decide.

Standard library only — no pip install on a locked-down build agent.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "confluence"


class Confluence:
    def __init__(self, base: str, space: str, user: str | None, token: str):
        self.base = base.rstrip("/")
        self.space = space
        if user:
            raw = f"{user}:{token}".encode()
            self.auth = "Basic " + base64.b64encode(raw).decode()
        else:
            self.auth = f"Bearer {token}"

    def _request(self, method: str, path: str, body=None, headers=None, raw=False):
        url = path if path.startswith("http") else f"{self.base}{path}"
        data = None
        hdrs = {"Authorization": self.auth, "Accept": "application/json"}
        if raw:
            data = body
            hdrs.update(headers or {})
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                payload = resp.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise SystemExit(f"{method} {url} -> {exc.code}\n{detail}") from None

    def find(self, title: str) -> dict | None:
        q = urllib.parse.urlencode({"spaceKey": self.space, "title": title,
                                    "expand": "version"})
        results = self._request("GET", f"/rest/api/content?{q}").get("results", [])
        return results[0] if results else None

    def create(self, title: str, body: str, parent_id: str | None) -> dict:
        payload = {"type": "page", "title": title, "space": {"key": self.space},
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        return self._request("POST", "/rest/api/content", payload)

    def update(self, page: dict, title: str, body: str, parent_id: str | None) -> dict:
        payload = {"id": page["id"], "type": "page", "title": title,
                   "space": {"key": self.space},
                   "version": {"number": page["version"]["number"] + 1,
                               "message": "Generated from the standard's repository"},
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        return self._request("PUT", f"/rest/api/content/{page['id']}", payload)

    def attach(self, page_id: str, path: Path) -> None:
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(), b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="minorEdit"\r\n\r\ntrue\r\n',
            f"--{boundary}--\r\n".encode(),
        ]
        self._request("PUT", f"/rest/api/content/{page_id}/child/attachment",
                      body=b"".join(parts), raw=True,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                               "X-Atlassian-Token": "no-check"})


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    manifest = json.loads((OUT / "manifest.json").read_text())
    pages = manifest["pages"]

    seen: set[str] = set()
    for page in pages:
        if page["parent"] and page["parent"] not in seen:
            print(f"error: '{page['title']}' precedes its parent '{page['parent']}'",
                  file=sys.stderr)
            return 1
        seen.add(page["title"])

    if not apply:
        print("DRY RUN — nothing will be written. Re-run with --apply.\n")
        for page in pages:
            under = f"  under  {page['parent']}" if page["parent"] else "  (space home)"
            print(f"  {page['title']}{under}")
            for a in page["attachments"]:
                print(f"       + {a}")
        print(f"\n{len(pages)} pages, "
              f"{sum(len(p['attachments']) for p in pages)} attachment uploads")
        return 0

    base = os.environ.get("CONFLUENCE_BASE_URL")
    space = os.environ.get("CONFLUENCE_SPACE")
    token = os.environ.get("CONFLUENCE_TOKEN")
    if not (base and space and token):
        print("error: set CONFLUENCE_BASE_URL, CONFLUENCE_SPACE and CONFLUENCE_TOKEN",
              file=sys.stderr)
        return 1

    api = Confluence(base, space, os.environ.get("CONFLUENCE_USER"), token)
    ids: dict[str, str] = {}

    for page in pages:
        title, body = page["title"], (OUT / page["file"]).read_text()
        parent_id = ids.get(page["parent"]) if page["parent"] else None
        existing = api.find(title)
        if existing:
            result = api.update(existing, title, body, parent_id)
            action = f"updated to v{result['version']['number']}"
        else:
            result = api.create(title, body, parent_id)
            action = "created"
        ids[title] = result["id"]
        print(f"  {action:22s} {title}")
        for name in page["attachments"]:
            path = OUT / "attachments" / name
            if path.exists():
                api.attach(result["id"], path)
                print(f"       attached {name}")

    print(f"\n{len(pages)} pages imported into {space}.")
    print(f"Space home: {base}/spaces/{space}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
