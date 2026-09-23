# Understanding Jev

A briefing and a sequenced reading path on **Jev**, TypeSafe AI's decision-only model,
released in limited early access on 15 September 2026.

| Artifact | Path |
| --- | --- |
| **The document (PDF)** | [`Understanding-Jev.pdf`](Understanding-Jev.pdf) — 22 pages |
| Assembled HTML | [`Understanding-Jev.html`](Understanding-Jev.html) — build byproduct |
| Source fragments | [`pdf-src/`](pdf-src/) — concatenated in filename order |

```bash
./tools/build-pdf.sh          # pdf-src/ → Understanding-Jev.pdf
```

## What it covers

| § | Contents |
| --- | --- |
| 0 | How to read it, and how the evidence is marked |
| 1 | What Jev is · the System One framing · the three primitives · a request and response · how it differs from an LLM |
| 2 | Why it matters: the economic, structural and Jevons arguments · what practitioners report |
| 3 | When to use it: six demonstrated uses, and a decision table |
| 4 | When **not** to: the six documented failure modes, criteria as code, and decomposition |
| 5 | What it is replacing — and what it is not |
| 6 | Where it sits in an architecture |
| 7 | **The honest picture**: benchmark caveats, the independent calibration study, the moat question, adoption risks |
| 8 | Getting started |
| 9 | **The reading path** — twelve sources in four stages, each with what it adds and how far to trust it |
| 10 | Glossary and sources |

## A note on sourcing

Jev was released after this document's author had any training data about it, so everything
here comes from the public record of September 2026 and every claim is attributed. The
document distinguishes **vendor claims** (TypeSafe's own materials and benchmarks, which the
company describes as designed by its own team) from **independent findings** (third parties
reproducing or testing them), and where they conflict it gives both rather than resolving it.

The numbers have a short shelf life. §9 — the reading path — is the durable part.
