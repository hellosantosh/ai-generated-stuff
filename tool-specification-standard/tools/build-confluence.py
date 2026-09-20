#!/usr/bin/env python3
"""
Generate a Confluence page set from the standard's own sources.

The standard exists as print fragments in pdf-src/. Retyping 160 pages into a
wiki would guarantee drift within a quarter, so the Confluence space is
generated from the same files the PDFs are built from, with the same catalog
and rule-index expansions. Re-run this after any change to the standard and
re-import.

    tools/build-confluence.py                  write confluence/
    tools/build-confluence.py --title-prefix "TSS — "

Output
    confluence/pages/*.xhtml     one file per page, Confluence storage format
    confluence/attachments/      figures, schemas, descriptors, the PDFs
    confluence/manifest.json     page tree, titles, parents, attachments
    confluence/README.md         how to import, three ways

Standard library only.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "confluence"

SPACE_HOME = "Tool Specification Standard"

# --- callout and status mappings -------------------------------------

CALLOUT = {"note": "info", "tip": "tip", "warn": "note", "danger": "warning"}
LEVEL_COLOR = {"must": "Red", "should": "Yellow", "may": "Blue", "never": "Grey"}
RISK_COLOR = {"r0": "Green", "r1": "Blue", "r2": "Yellow", "r3": "Red"}
PILL_COLOR = {"ro": "Green", "dest": "Red", "idem": "Blue", "open": "Yellow",
              "scope": "Grey", "cls": "Grey"}


def esc(text: str) -> str:
    """Storage format is XML: only the five XML entities are legal."""
    return (html.unescape(text)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def macro(name: str, params: dict[str, str] | None = None,
          body: str = "", plain: str | None = None) -> str:
    out = [f'<ac:structured-macro ac:name="{name}">']
    for k, v in (params or {}).items():
        out.append(f'<ac:parameter ac:name="{k}">{esc(v)}</ac:parameter>')
    if plain is not None:
        safe = plain.replace("]]>", "]]]]><![CDATA[>")
        out.append(f"<ac:plain-text-body><![CDATA[{safe}]]></ac:plain-text-body>")
    elif body:
        out.append(f"<ac:rich-text-body>{body}</ac:rich-text-body>")
    out.append("</ac:structured-macro>")
    return "".join(out)


def status(title: str, color: str) -> str:
    return macro("status", {"colour": color, "title": title})


# --- inline conversion -----------------------------------------------

def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


class Inline:
    """Rewrites the inline vocabulary of the print fragments into storage format."""

    def __init__(self, section_pages: dict[str, str]):
        self.section_pages = section_pages

    def convert(self, text: str) -> str:
        text = re.sub(r'<span class="lvl lvl-(\w+)">([^<]*)</span>',
                      lambda m: status(strip_tags(m.group(2)) or m.group(1).upper(),
                                       LEVEL_COLOR.get(m.group(1), "Grey")), text)
        text = re.sub(r'<span class="risk (r\d)">([^<]*)</span>',
                      lambda m: status(strip_tags(m.group(2)),
                                       RISK_COLOR.get(m.group(1), "Grey")), text)
        text = re.sub(r'<span class="pill(?: (\w+))?">([^<]*)</span>',
                      lambda m: status(strip_tags(m.group(2)),
                                       PILL_COLOR.get(m.group(1) or "", "Grey")), text)
        # A heading's number needs the space the print layout supplied with margin.
        text = re.sub(r'<span class="num">([^<]*)</span>\s*', r"\1 ", text)
        # Spans that were purely typographic carry no meaning in a wiki.
        for cls in ("small", "tt", "lbl", "tnum", "lang"):
            text = re.sub(rf'<span class="{cls}[^"]*">(.*?)</span>', r"\1", text, flags=re.S)
        text = re.sub(r"<b>(.*?)</b>", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"<i>(.*?)</i>", r"<em>\1</em>", text, flags=re.S)
        text = text.replace("<br>", "<br/>")
        text = re.sub(r'<span[^>]*>|</span>', "", text)
        text = self.link_sections(text)
        return self.entities(text)

    def link_sections(self, text: str) -> str:
        """§7.3 becomes a link to the page that defines section 7."""
        def repl(m: re.Match) -> str:
            label, top = m.group(0), m.group(1)
            target = self.section_pages.get(top)
            if not target:
                return label
            return ('<ac:link><ri:page ri:content-title="' + html.escape(target, quote=True)
                    + '"/><ac:link-body>' + label + "</ac:link-body></ac:link>")
        return re.sub(r"§(\d+)(?:\.\d+)*", repl, text)

    @staticmethod
    def entities(text: str) -> str:
        """
        Named HTML entities break the storage-format parser, so every entity is
        resolved to its character and then re-escaped as XML. Tags pass through
        untouched, and CDATA is literal by definition — escaping inside it would
        corrupt the code samples.
        """
        out: list[str] = []
        for block in re.split(r"(<!\[CDATA\[.*?\]\]>)", text, flags=re.S):
            if block.startswith("<![CDATA["):
                out.append(block)
                continue
            for seg in re.split(r"(<[^>]+>)", block):
                if seg.startswith("<"):
                    out.append(seg)
                else:
                    out.append(html.unescape(seg)
                               .replace("&", "&amp;")
                               .replace("<", "&lt;")
                               .replace(">", "&gt;"))
        return "".join(out)


# --- block conversion -------------------------------------------------

class PageBuilder:
    def __init__(self, inline: Inline, figures: dict[str, str]):
        self.inline = inline
        self.figures = figures

    def convert(self, body: str) -> str:
        out = body
        out = self.figures_to_images(out)
        out = self.code_blocks(out)
        out = self.rule_blocks(out)
        out = self.tool_blocks(out)
        out = self.callouts(out)
        out = self.two_columns(out)
        out = self.task_lists(out)
        out = self.tables(out)
        out = self.lede(out)
        out = self.headings(out)
        out = re.sub(r"</?section[^>]*>", "", out)
        out = re.sub(r'<div class="pills">(.*?)</div>', r"<p>\1</p>", out, flags=re.S)
        out = re.sub(r"</?div[^>]*>", "", out)
        out = out.replace("<hr>", "<hr/>")
        return self.inline.convert(out).strip()

    def figures_to_images(self, body: str) -> str:
        def repl(m: re.Match) -> str:
            block = m.group(0)
            cap = re.search(r"<figcaption>(.*?)</figcaption>", block, re.S)
            key = re.search(r"Figure ([\d.]+)", strip_tags(cap.group(1) if cap else ""))
            name = self.figures.get(key.group(1) if key else "", "")
            if not name:
                return ""
            img = (f'<p><ac:image ac:align="center" ac:layout="center">'
                   f'<ri:attachment ri:filename="{name}"/></ac:image></p>')
            caption = f"<p><em>{cap.group(1)}</em></p>" if cap else ""
            return img + caption
        return re.sub(r"<figure>.*?</figure>", repl, body, flags=re.S)

    @staticmethod
    def code_blocks(body: str) -> str:
        def repl(m: re.Match) -> str:
            raw = re.sub(r"<[^>]+>", "", m.group(1))
            text = html.unescape(raw).strip("\n")
            lang = "json" if text.lstrip().startswith(("{", "[")) else "text"
            return macro("code", {"language": lang}, plain=text)
        return re.sub(r"<pre[^>]*>(?:<code>)?(.*?)(?:</code>)?</pre>", repl, body, flags=re.S)

    @staticmethod
    def rule_blocks(body: str) -> str:
        def repl(m: re.Match) -> str:
            inner = m.group(2)
            rid = re.search(r'<span class="rid">([^<]+)</span>', inner)
            lvl = re.search(r'<span class="lvl lvl-(\w+)">([^<]*)</span>', inner)
            level = (lvl.group(2).strip() if lvl else m.group(1).strip() or "MUST").upper()
            title = f"{rid.group(1)} — {level}" if rid else level
            text = re.sub(r'<span class="rid">[^<]+</span>', "", inner)
            text = re.sub(r'<span class="lvl lvl-\w+">[^<]*</span>', "", text, count=1)
            color = {"MUST": "#A32A2A", "MUST NOT": "#A32A2A", "NEVER": "#2C2F36",
                     "SHOULD": "#8A5A00", "MAY": "#1F4F8F"}.get(level, "#1F4F8F")
            return macro("panel", {"title": title, "borderColor": color,
                                   "borderStyle": "solid", "borderWidth": "2",
                                   "titleBGColor": "#F4F5F7", "bgColor": "#FFFFFF"},
                         body=text.strip())
        return re.sub(r'<div class="rule ?(\w*)">(.*?)</div>', repl, body, flags=re.S)

    @staticmethod
    def tool_blocks(body: str) -> str:
        def repl(m: re.Match) -> str:
            inner = m.group(1)
            name = re.search(r'<div class="tool-name">(.*?)</div>', inner, re.S)
            title = strip_tags(name.group(1)) if name else "Tool"
            rest = re.sub(r'<div class="tool-name">.*?</div>', "", inner, flags=re.S)
            return macro("panel", {"title": title, "borderColor": "#1F4F8F",
                                   "borderStyle": "solid", "borderWidth": "1",
                                   "titleBGColor": "#EEF3FA", "bgColor": "#FFFFFF"},
                         body=rest.strip())
        return re.sub(r'<div class="tool(?: tall)?">(.*?)</div>\s*(?=<h|<p|<table|<div|$)',
                      repl, body, flags=re.S)

    @staticmethod
    def callouts(body: str) -> str:
        def repl(m: re.Match) -> str:
            kind, inner = m.group(1), m.group(2)
            lbl = re.search(r'<span class="lbl">(.*?)</span>', inner, re.S)
            title = strip_tags(lbl.group(1)) if lbl else ""
            text = re.sub(r'<span class="lbl">.*?</span>', "", inner, flags=re.S)
            params = {"title": title} if title else {}
            return macro(CALLOUT[kind], params, body=text.strip())
        return re.sub(r'<div class="(note|tip|warn|danger)">(.*?)</div>',
                      repl, body, flags=re.S)

    @staticmethod
    def two_columns(body: str) -> str:
        def repl(m: re.Match) -> str:
            cells = re.findall(r"<div[^>]*>(.*?)</div>\s*(?=<div|$)", m.group(1), re.S)
            if len(cells) != 2:
                return m.group(1)
            return ("<table><tbody><tr>"
                    f"<td>{cells[0].strip()}</td><td>{cells[1].strip()}</td>"
                    "</tr></tbody></table>")
        return re.sub(r'<div class="dd">(.*?)</div>\s*(?=<h|<p|<table|$)',
                      repl, body, flags=re.S)

    @staticmethod
    def task_lists(body: str) -> str:
        def repl(m: re.Match) -> str:
            items = re.findall(r"<li>(.*?)</li>", m.group(1), re.S)
            tasks = "".join("<ac:task><ac:task-status>incomplete</ac:task-status>"
                            f"<ac:task-body>{i.strip()}</ac:task-body></ac:task>"
                            for i in items)
            return f"<ac:task-list>{tasks}</ac:task-list>"
        return re.sub(r'<ul class="check">(.*?)</ul>', repl, body, flags=re.S)

    @staticmethod
    def tables(body: str) -> str:
        body = re.sub(r'<table[^>]*>', "<table>", body)
        body = re.sub(r'<(th|td)[^>]*style="[^"]*"([^>]*)>', r"<\1\2>", body)
        return re.sub(r"<(th|td) >", r"<\1>", body)

    @staticmethod
    def lede(body: str) -> str:
        """The lede becomes the page excerpt, so child listings can show it."""
        return re.sub(r'<p class="lede">(.*?)</p>',
                      lambda m: macro("excerpt", {"hidden": "false",
                                                  "atlassian-macro-output-type": "BLOCK"},
                                      body=f"<p>{m.group(1).strip()}</p>"),
                      body, count=1, flags=re.S)

    @staticmethod
    def headings(body: str) -> str:
        body = re.sub(r"<h2[^>]*>(.*?)</h2>", r"<h2>\1</h2>", body, flags=re.S)
        body = re.sub(r"<h3[^>]*>(.*?)</h3>", r"<h3>\1</h3>", body, flags=re.S)
        body = re.sub(r"<h4[^>]*>(.*?)</h4>", r"<h4>\1</h4>", body, flags=re.S)
        return body


# --- splitting the book into pages ------------------------------------

def expanded_body() -> str:
    """The print fragments, marker-expanded exactly as the PDF build does."""
    fragments = sorted(p for p in (ROOT / "pdf-src").glob("*.html")
                       if p.name not in ("00-head.html", "99-close.html"))
    raw = "\n".join(p.read_text() for p in fragments)
    return subprocess.run([sys.executable, str(ROOT / "tools" / "embed-catalog.py")],
                          input=raw, capture_output=True, text=True, check=True).stdout


def split_pages(body: str) -> list[dict]:
    """One page per h1, plus one per Part divider. Cover and contents are dropped."""
    body = re.sub(r'<section class="cover">.*?</section>', "", body, flags=re.S)
    body = re.sub(r'<section class="toc">.*?</section>', "", body, flags=re.S)

    # A part divider contains its own h1, so lift the dividers out before
    # splitting on headings, and leave a marker where each one stood.
    parts: list[str] = []

    def stash(m: re.Match) -> str:
        parts.append(m.group(0))
        return f"<!--PART:{len(parts) - 1}-->"

    body = re.sub(r'<section class="part">.*?</section>', stash, body, flags=re.S)

    marks = [m.start() for m in re.finditer(r"<!--PART:\d+-->|<h1[^>]*>", body)]
    marks.append(len(body))

    pages = []
    for start, end in zip(marks, marks[1:]):
        chunk = body[start:end]
        part = re.match(r"<!--PART:(\d+)-->", chunk)
        if part:
            block = parts[int(part.group(1))]
            num = strip_tags(re.search(r'<div class="pnum">(.*?)</div>', block, re.S).group(1))
            title = strip_tags(re.search(r"<h1[^>]*>(.*?)</h1>", block, re.S).group(1))
            desc = re.search(r'<p class="pdesc">(.*?)</p>', block, re.S)
            pages.append({"kind": "part", "num": num, "title": f"{num} — {title}",
                          "intro": desc.group(1).strip() if desc else "", "body": ""})
            continue
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", chunk, re.S)
        if not h1:
            continue
        num_m = re.search(r'<span class="num">([^<]+)</span>', h1.group(1))
        num = num_m.group(1).strip() if num_m else ""
        title = strip_tags(re.sub(r'<span class="num">[^<]*</span>', "", h1.group(1)))
        rest = chunk[h1.end():]
        kind = "appendix" if num and num[0].isalpha() else "section"
        pages.append({"kind": kind, "num": num,
                      "title": f"{num}. {title}" if num else title, "body": rest})
    return pages


def export_figures() -> dict[str, str]:
    """Pull the inline SVGs out of the book and render them for attachment."""
    figdir = OUT / "attachments"
    figdir.mkdir(parents=True, exist_ok=True)
    chrome = next((c for c in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"), shutil.which("chromium")] if c and Path(c).exists()), None)

    figures: dict[str, str] = {}
    for src in sorted((ROOT / "pdf-src").glob("*.html")):
        text = src.read_text()
        for block in re.findall(r"<figure>.*?</figure>", text, re.S):
            cap = re.search(r"Figure ([\d.]+)", strip_tags(block))
            svg = re.search(r"<svg.*?</svg>", block, re.S)
            if not (cap and svg):
                continue
            key = cap.group(1)
            stem = "figure-" + key.replace(".", "-")
            (figdir / f"{stem}.svg").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + svg.group(0).replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))
            png = figdir / f"{stem}.png"
            if chrome:
                vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.group(0))
                w, h = (int(float(vb.group(1))), int(float(vb.group(2)))) if vb else (900, 400)
                holder = figdir / f"{stem}.holder.html"
                holder.write_text(f'<body style="margin:0;background:#fff">{svg.group(0)}</body>')
                subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                                "--hide-scrollbars", f"--screenshot={png}",
                                f"--window-size={w*2},{h*2}", "--force-device-scale-factor=2",
                                f"file://{holder}"], capture_output=True)
                holder.unlink(missing_ok=True)
            figures[key] = f"{stem}.png" if png.exists() else f"{stem}.svg"
    return figures


# --- purpose-built pages ----------------------------------------------

def home_page(prefix: str) -> str:
    t = lambda name: html.escape(prefix + name, quote=True)
    link = lambda name, label=None: (
        f'<ac:link><ri:page ri:content-title="{t(name)}"/>'
        f'<ac:link-body>{esc(label or name)}</ac:link-body></ac:link>')
    return f"""
{macro("info", {"title": "EA-STD-TOOL-001 · Draft for Architecture Review Board · v1.0.0-rc1"},
       body="<p>A normative standard for defining, describing, classifying, securing, and "
            "governing tools that a language model can invoke. It specifies a contract, not a "
            "wire protocol, so a conformant descriptor publishes over any tool-calling runtime "
            "without amendment.</p>")}
<h2>Start here</h2>
<table><tbody>
<tr><th>If you are</th><th>Read</th></tr>
<tr><td><strong>An architect</strong> assessing a proposal</td>
    <td>{link("How to use this standard")}, then {link("9. Behavioral annotations and risk tiers", "risk tiers")},
        {link("15. Security and trust", "security")}, and the {link("Review checklist")} you will use in the meeting.</td></tr>
<tr><td><strong>A tech lead</strong> designing a tool set</td>
    <td>Part II end to end, then {link("12. Granularity and catalog design", "granularity")} and
        {link("13. Payload economics", "payload economics")}. Part IV is a catalog you can copy structurally.</td></tr>
<tr><td><strong>A developer</strong> writing one tool</td>
    <td>{link("4. Descriptor anatomy", "the anatomy")}, {link("6. The description: the model-facing contract", "the description")},
        {link("7. Input schema", "input")} and {link("8. Output schema and the result envelope", "output")} schemas,
        {link("11. Errors", "errors")}, and the nearest analogue in Part IV.</td></tr>
<tr><td><strong>Risk, audit, or compliance</strong></td>
    <td>{link("10. Governance metadata", "governance metadata")}, {link("15. Security and trust", "security")},
        {link("16. Observability, audit, and supervision", "audit and supervision")},
        {link("27. Testing and certification", "certification evidence")}.</td></tr>
</tbody></table>

<h2>The four things most people come here for</h2>
<table><tbody>
<tr><td>{link("Rule index")}</td><td>All 113 normative rules, with level and section. Cite these in reviews.</td></tr>
<tr><td>{link("Review checklist")}</td><td>A tickable checklist for an architecture review. Copy it onto your review page.</td></tr>
<tr><td>{link("Machine-readable artifacts")}</td><td>The descriptor schema, the shared type library, the reference catalog, and the CI linter.</td></tr>
<tr><td>{link("5. Identity and naming")}</td><td>The naming grammar and the closed operation verb vocabulary.</td></tr>
</tbody></table>

<h2>Conformance levels</h2>
<table><tbody>
<tr><th>Level</th><th>Name</th><th>Requires</th><th>Permitted exposure</th></tr>
<tr><td>{status("L0", "Green")}</td><td><strong>Registered</strong></td>
    <td>Parses against the descriptor schema.</td><td>Dev and test only.</td></tr>
<tr><td>{status("L1", "Blue")}</td><td><strong>Conformant</strong></td>
    <td>Every MUST satisfied; linter clean; named owner; structured errors; audit events.</td>
    <td>Internal agents, non-customer data, tier 0–1.</td></tr>
<tr><td>{status("L2", "Yellow")}</td><td><strong>Certified</strong></td>
    <td>L1 plus security review, evaluation evidence, entitlement sign-off, SLO, kill switch, retention.</td>
    <td><strong>Mandatory</strong> for customer data, money, or positions.</td></tr>
<tr><td>{status("L3", "Red")}</td><td><strong>Externally exposed</strong></td>
    <td>L2 plus egress assessment, contractual controls, tenant isolation proof, annual red team.</td>
    <td>Agents the firm does not operate.</td></tr>
</tbody></table>

{macro("warning", {"title": "The gating rule"},
       body="<p><strong>TS-GEN-01.</strong> Every tool reachable from a production agent must hold "
            "at least Level 1. Every tool that reads customer data, moves money, changes an order, "
            "or writes to a system of record must hold Level 2.</p>")}

<h2>Downloads</h2>
<p>The full standard and the quick reference are attached to this page.</p>
<p>{macro("attachments", {"upload": "false", "old": "false"})}</p>

<h2>Contents</h2>
{macro("children", {"all": "true", "excerpt": "true", "depth": "2"})}

{macro("info", {"title": "How this space is maintained"},
       body="<p>Every page here is generated from the standard's source repository by "
            "<code>tools/build-confluence.py</code> and re-imported. <strong>Edit the repository, "
            "not the page</strong> — a wiki edit is overwritten on the next import, and a "
            "descriptor rule that exists only in Confluence is not a rule anyone's CI enforces "
            "(TS-GEN-03).</p>")}
""".strip()


def how_to_use_page(prefix: str) -> str:
    t = lambda name: html.escape(prefix + name, quote=True)
    link = lambda name, label=None: (
        f'<ac:link><ri:page ri:content-title="{t(name)}"/>'
        f'<ac:link-body>{esc(label or name)}</ac:link-body></ac:link>')
    return f"""
{macro("excerpt", {"hidden": "false", "atlassian-macro-output-type": "BLOCK"},
       body="<p>What the standard binds, how a tool gets approved, and how to raise an "
            "exception.</p>")}

<h2>What is normative</h2>
<table><tbody>
<tr><th>Subject</th><th>Position</th></tr>
<tr><td>Descriptor content and structure</td><td><strong>Normative.</strong> The schema is published; the linter enforces it.</td></tr>
<tr><td>Result envelope and error taxonomy</td><td><strong>Normative.</strong></td></tr>
<tr><td>Gateway obligations</td><td><strong>Normative</strong> for platform teams.</td></tr>
<tr><td>Governance metadata and lifecycle</td><td><strong>Normative.</strong></td></tr>
<tr><td>Tool set design and granularity</td><td><strong>Advisory with review teeth</strong> — departures must be justified at review.</td></tr>
<tr><td>Wire protocol, transport, framing</td><td><strong>Out of scope.</strong> Any transport that carries the descriptor and envelope without loss.</td></tr>
<tr><td>Model choice, prompting, agent loops</td><td><strong>Out of scope</strong> — governed separately.</td></tr>
</tbody></table>

<h2>Conformance keywords</h2>
<table><tbody>
<tr><td>{status("MUST", "Red")}</td><td>Gating. Fails the build and the review.</td></tr>
<tr><td>{status("SHOULD", "Yellow")}</td><td>Departure needs a recorded reason.</td></tr>
<tr><td>{status("MAY", "Blue")}</td><td>Permitted, no expectation.</td></tr>
<tr><td>{status("NEVER", "Grey")}</td><td>MUST NOT, with no exception path.</td></tr>
</tbody></table>
<p>Rule identifiers are permanent and are the currency of a review: "fails <code>TS-INP-07</code>"
means the same thing in five years as it does today. The full list is on {link("Rule index")}.</p>

<h2>Getting a tool approved</h2>
<table><tbody>
<tr><th>Stage</th><th>Owner</th><th>Passes when</th></tr>
<tr><td><strong>Design review</strong> — before code</td><td>Domain architect</td>
    <td>The tool set derives from real utterances; boundaries and risk tiers agreed; the knowledge test discharged. Cheapest place to fix a granularity error, by an order of magnitude.</td></tr>
<tr><td><strong>Automated conformance</strong> — every commit</td><td>CI</td>
    <td>Linter clean, descriptors validate, size budgets hold.</td></tr>
<tr><td><strong>Security review</strong> — before L2</td><td>Security architecture</td>
    <td>Resource-level authorization demonstrated by a negative test, not asserted.</td></tr>
<tr><td><strong>Data review</strong> — before L2</td><td>Data owner, privacy</td>
    <td>Classification, PII mapping, minimization, retention.</td></tr>
<tr><td><strong>Evaluation</strong> — before release and on every change</td><td>Owning team</td>
    <td>Selection, refusal, argument, and interpretation thresholds met.</td></tr>
<tr><td><strong>Architecture Review Board</strong> — tier 3, L3, exceptions</td><td>ARB</td>
    <td>The capability is one the firm is willing to expose, to that audience, under that supervision.</td></tr>
</tbody></table>

<h2>Running a review</h2>
<p>Copy {link("Review checklist")} onto your review page — the items are Confluence tasks, so they
can be ticked, assigned, and reported on. Then ask these six out loud:</p>
<ol>
<li><strong>"Read me the description."</strong> Unreadable aloud, ninety seconds long, or no sentence beginning "Do not use" — not ready.</li>
<li><strong>"What is the nearest tool to this one, and what sentence tells them apart?"</strong> A pause is a confusability problem.</li>
<li><strong>"What does the customer see if this tool is wrong?"</strong> The answer should be a specific wrong sentence, not "an error".</li>
<li><strong>"Show me the negative test where the customer asks for someone else's account."</strong> Not the code — the test.</li>
<li><strong>"Who is paged at 2am, and what do they turn off?"</strong></li>
<li><strong>"What did the evaluation say about the cases where it should refuse?"</strong> Selection accuracy is easy to score well on. Refusal accuracy is where the risk lives.</li>
</ol>

<h2>Raising an exception</h2>
{macro("note", {"title": "TS-CAT-05"},
       body="<p>An exception to a MUST rule must be recorded against the tool in the catalog with "
            "the rule identifier, the rationale, the compensating control, the approver, and an "
            "expiry of no more than twelve months. <strong>An exception without an expiry is a "
            "silent amendment to the standard.</strong></p>")}
<p>Raise one through the Architecture Review Board intake, not by editing a page here.</p>

<h2>Proposing a change to the standard</h2>
<p>Open a pull request against the standard's repository. Prose, schemas, the reference catalog,
the linter, and this space are all built from it, so a merged change reaches every surface at
once. Changes to a rule's meaning need ARB ratification; clarifications that do not change
meaning do not.</p>
""".strip()


def artifacts_page(prefix: str) -> str:
    descriptors = sorted(p.name for p in (ROOT / "catalog").glob("*.json"))
    rows = "".join(f"<tr><td><code>{n}</code></td><td>Attached to this page.</td></tr>"
                   for n in descriptors)
    return f"""
{macro("excerpt", {"hidden": "false", "atlassian-macro-output-type": "BLOCK"},
       body="<p>The schema, the type library, the reference catalog, and the CI linter — the "
            "parts of the standard a build can enforce.</p>")}

{macro("info", {"title": "These are the enforceable half of the standard"},
       body="<p>Prose describes intent; these files decide whether a build passes. Where the two "
            "disagree about a structural rule, the files govern; where they disagree about intent, "
            "the prose governs.</p>")}

<h2>Schemas</h2>
<table><tbody>
<tr><th>File</th><th>What it is</th></tr>
<tr><td><code>tool-descriptor.schema.json</code></td>
    <td>The normative shape of a descriptor — Appendix A. A descriptor that does not validate against it is not a descriptor.</td></tr>
<tr><td><code>common-types.schema.json</code></td>
    <td>The shared type library — Appendix B. Decimal, currency, identifiers, completeness, the error payload, the result envelope. Paste from here rather than reinventing, so a concept means the same thing in every tool.</td></tr>
</tbody></table>

<h2>Reference catalog</h2>
<p>Seven conformant brokerage descriptors covering accounts, positions, balances, and order
status, plus a preview/place pair for contrast. All pass the linter with no findings.</p>
<table><tbody><tr><th>Descriptor</th><th>Where</th></tr>{rows}</tbody></table>

<h2>The conformance linter</h2>
<p>Standard library only, so it runs anywhere CI runs. Exit status 1 on any MUST violation;
SHOULD findings are warnings. Every finding names the rule identifier the standard defines.</p>
{macro("code", {"language": "bash"}, plain=
       "python3 tools/lint-descriptors.py catalog/\n"
       "python3 tools/lint-descriptors.py catalog/brokerage_balances_get.json\n\n"
       "# 7 descriptor(s) · 0 error(s) · 0 warning(s)")}

<h2>The CI gate</h2>
{macro("code", {"language": "bash"}, plain=
       "tools/lint-descriptors.py catalog/                       # rules, by identifier\n"
       "validate catalog/*.json  against schemas/tool-descriptor.schema.json\n"
       "validate examples/*.json against each tool's own outputSchema\n"
       "check     rendered descriptor size <= 1200 tokens\n"
       "check     no descriptor name changed since the last release\n"
       "register  descriptor content hashes with the catalog     # TS-SEC-09\n"
       "run       evaluation suite; fail below threshold")}

<h2>Files</h2>
<p>{macro("attachments", {"upload": "false", "old": "false"})}</p>
""".strip()


def write_readme(manifest: list[dict], prefix: str) -> None:
    tree = []
    for page in manifest:
        depth = 0 if not page["parent"] else (2 if page["parent"] not in
                                              (prefix + SPACE_HOME,) else 1)
        tree.append("  " * depth + "- " + page["title"])
    attachments = sorted(p.name for p in (OUT / "attachments").glob("*"))
    (OUT / "README.md").write_text(f"""# Confluence page set — EA-STD-TOOL-001

**Generated. Do not edit these files, and do not edit the pages in Confluence.**
Everything here is produced from the standard's sources by
`tools/build-confluence.py` and overwritten on the next import. A rule that
exists only in Confluence is not a rule anyone's CI enforces (TS-GEN-03).

{len(manifest)} pages · {len(attachments)} attachments · Confluence storage format.

## Importing

### 1. The importer (recommended)

```bash
export CONFLUENCE_BASE_URL=https://example.atlassian.net/wiki   # DC: https://wiki.example.com
export CONFLUENCE_SPACE=EATOOLS
export CONFLUENCE_USER=you@example.com     # Cloud: your email
export CONFLUENCE_TOKEN=...                # Cloud: an API token · DC: a PAT, omit USER

python3 tools/import-confluence.py         # dry run, prints the plan
python3 tools/import-confluence.py --apply
```

Creates what is missing, updates what exists (matched on title, version bumped),
uploads each page's attachments. Safe to re-run. Nothing is ever deleted; pages
in the space that are no longer in the manifest are reported, not removed.

### 2. Paste one page at a time

For a space where API tokens are not available. In Confluence Cloud open the
page editor, then **⋯ → Advanced → Source editor**; in Data Center, **⋯ → Source
editor**. Paste the contents of the matching file in `pages/`. Create the page
tree by hand first, following the order below, and attach the files listed in
`manifest.json` for that page.

### 3. Land the PDFs only

If the space is meant purely as a distribution point, create one page, attach
`Enterprise-Tool-Specification-Standard.pdf` and
`Tool-Specification-Standard-Quick-Reference.pdf`, and skip the rest. You lose
search, linking, and the tickable review checklist.

## Space setup

| Setting | Recommendation |
| --- | --- |
| Space key | `EATOOLS`, or your architecture space with these pages under one parent |
| Read access | All engineering. This is a standard; hiding it defeats it |
| Edit access | **The platform team only.** Pages are generated; a wiki edit is lost on the next import |
| Labels | `ea-standard`, `tooling`, `agents` on every page, for cross-space search |
| Space home | Point it at *{prefix + SPACE_HOME}* |

## After any change to the standard

```bash
./tools/build-pdf.sh && ./tools/build-quickref.sh   # the PDFs
./tools/build-diagrams.sh                           # the sequence diagram
python3 tools/build-confluence.py                   # this page set
python3 tools/check-confluence.py                   # validate before importing
python3 tools/import-confluence.py --apply          # publish
```

`check-confluence.py` is the gate: it parses every page as XML in the storage
namespaces, fails on print-only markup that survived conversion, on named HTML
entities (which break Confluence's parser), and on links or attachments that do
not resolve.

## Macros used

All built-in — no marketplace add-ons, so this imports into a vanilla space:
`info`, `note`, `warning`, `tip`, `panel`, `code`, `status`, `excerpt`,
`children`, `attachments`, and native task lists for the review checklist.

## Page tree

```
{chr(10).join(tree)}
```

## Attachments

{chr(10).join("- `" + a + "`" for a in attachments)}
""")


# --- driver ------------------------------------------------------------

CHECKLIST = "Review checklist"
RULE_INDEX = "Rule index"
ARTIFACTS = "Machine-readable artifacts"
HOWTO = "How to use this standard"
APPENDICES = "Appendices"


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def rule_index_page(inline: Inline) -> str:
    table = subprocess.run([sys.executable, str(ROOT / "tools" / "extract-rules.py"), "--html"],
                           capture_output=True, text=True, check=True).stdout
    table = PageBuilder.tables(table)
    table = re.sub(r'<tr class="grp"><td colspan="4">(.*?)</td></tr>',
                   r'<tr><th colspan="4">\1</th></tr>', table, flags=re.S)
    table = re.sub(r'<td class="sec">', "<td>", table)
    return (macro("excerpt", {"hidden": "false", "atlassian-macro-output-type": "BLOCK"},
                  body="<p>Every normative rule in the standard, with its level and the section "
                       "that defines it. Cite these identifiers in reviews and pull requests.</p>")
            + macro("info", {"title": "Rule identifiers are permanent"},
                    body="<p>A withdrawn rule is marked withdrawn and its number is never reused, "
                         "so a review comment reading \"fails TS-INP-07\" means the same thing in "
                         "five years as it does today. The linter reports findings by these "
                         "identifiers.</p>")
            + inline.convert(table))


def main(argv: list[str]) -> int:
    prefix = ""
    if "--title-prefix" in argv:
        prefix = argv[argv.index("--title-prefix") + 1]

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "pages").mkdir(parents=True)
    (OUT / "attachments").mkdir(parents=True, exist_ok=True)

    print("Exporting figures...")
    figures = export_figures()
    for key, name in figures.items():
        print(f"  Figure {key} -> {name}")

    print("Expanding the print fragments...")
    pages = split_pages(expanded_body())

    # Pass one: what page defines each top-level section, so §N can be a link.
    section_pages: dict[str, str] = {}
    for p in pages:
        if p["kind"] == "section" and p["num"]:
            section_pages[p["num"].split(".")[0]] = prefix + p["title"]

    inline = Inline(section_pages)
    builder = PageBuilder(inline, figures)

    manifest: list[dict] = []
    current_part = None

    def add(title: str, body: str, parent: str | None, order: int,
            attachments: list[str] | None = None) -> None:
        name = f"{order:02d}-{slug(title)}.xhtml"
        (OUT / "pages" / name).write_text(body.strip() + "\n")
        manifest.append({"title": prefix + title, "parent": prefix + parent if parent else None,
                         "file": f"pages/{name}", "order": order,
                         "attachments": attachments or []})

    order = 0
    add(SPACE_HOME, home_page(prefix), None, order,
        ["Enterprise-Tool-Specification-Standard.pdf",
         "Tool-Specification-Standard-Quick-Reference.pdf"])
    order += 1
    add(HOWTO, how_to_use_page(prefix), SPACE_HOME, order)
    order += 1

    for p in pages:
        if p["kind"] == "part":
            current_part = p["title"]
            body = (macro("excerpt", {"hidden": "false",
                                      "atlassian-macro-output-type": "BLOCK"},
                          body=f"<p>{inline.convert(p['intro'])}</p>")
                    + macro("children", {"all": "true", "excerpt": "true"}))
            order += 1
            add(current_part, body, SPACE_HOME, order)
            continue

        body = builder.convert(p["body"])
        attach = sorted(set(re.findall(r'ri:filename="([^"]+)"', body)))

        if p["title"].startswith("3."):
            body += (
                "<h2>3.2 as a sequence diagram</h2>"
                "<p>The same eleven steps, drawn. The PlantUML source is attached and is the "
                "editable original; steps marked ◆ are the ones where a descriptor defect "
                "cannot be recovered downstream.</p>"
                '<p><ac:image ac:align="center" ac:layout="center">'
                '<ri:attachment ri:filename="anatomy-of-an-invocation.png"/></ac:image></p>')
            attach += ["anatomy-of-an-invocation.png", "anatomy-of-an-invocation.puml",
                       "anatomy-of-an-invocation.svg"]

        if p["kind"] == "appendix":
            if p["num"] == "E":
                order += 1
                add(CHECKLIST, body, SPACE_HOME, order, attach)
                continue
            if not any(m["title"] == prefix + APPENDICES for m in manifest):
                order += 1
                add(APPENDICES, (
                    macro("excerpt", {"hidden": "false",
                                      "atlassian-macro-output-type": "BLOCK"},
                          body="<p>The normative schema, the shared type library, the error code "
                               "registry, the reserved enumerations, and the glossary.</p>")
                    + macro("children", {"all": "true", "excerpt": "true"})
                    + "<p>The review checklist, Appendix E in the printed standard, is a page of "
                      "its own so that it can be copied onto a review: see "
                      f'<ac:link><ri:page ri:content-title="{html.escape(prefix + CHECKLIST, quote=True)}"/>'
                      f"</ac:link>.</p>"), SPACE_HOME, order)
            order += 1
            add(p["title"], body, APPENDICES, order, attach)
            continue

        order += 1
        add(p["title"], body, current_part or SPACE_HOME, order, attach)

    order += 1
    add(RULE_INDEX, rule_index_page(inline), SPACE_HOME, order)
    order += 1
    add(ARTIFACTS, artifacts_page(prefix), SPACE_HOME, order,
        ["tool-descriptor.schema.json", "common-types.schema.json", "lint-descriptors.py"]
        + sorted(p.name for p in (ROOT / "catalog").glob("*.json")))

    print("Copying attachments...")
    for src in [ROOT / "Enterprise-Tool-Specification-Standard.pdf",
                ROOT / "Tool-Specification-Standard-Quick-Reference.pdf",
                ROOT / "schemas" / "tool-descriptor.schema.json",
                ROOT / "schemas" / "common-types.schema.json",
                ROOT / "tools" / "lint-descriptors.py",
                *sorted((ROOT / "catalog").glob("*.json")),
                *sorted((ROOT / "diagrams").glob("anatomy-of-an-invocation.*"))]:
        if src.exists():
            shutil.copy2(src, OUT / "attachments" / src.name)

    write_readme(manifest, prefix)

    (OUT / "manifest.json").write_text(json.dumps(
        {"space_home": prefix + SPACE_HOME, "title_prefix": prefix,
         "generated_from": "pdf-src/, catalog/, diagrams/",
         "pages": manifest}, indent=2) + "\n")

    missing = [a for m in manifest for a in m["attachments"]
               if not (OUT / "attachments" / a).exists()]
    if missing:
        print(f"warning: manifest references missing attachments: {sorted(set(missing))}",
              file=sys.stderr)

    print(f"\nWrote {len(manifest)} pages and "
          f"{len(list((OUT / 'attachments').glob('*')))} attachments to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
