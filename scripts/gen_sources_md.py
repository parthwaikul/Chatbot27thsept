"""Generate SOURCES.md (D-2) from the registry and the fetch manifest.

D-2 is a hand-written file in every other project, and it is wrong within a
month. This generates it from ``config/sources.yaml`` — the same file the
allowlist is loaded from — so a source added there appears here and a source
removed there disappears. The fetch dates come from ``corpus/sources.csv``,
which is written by the ingest run that actually retrieved the page, so the date
in the deliverable is the date the corpus was built, not a date someone typed.

Run after ``python ingest.py --stage all``:

    python scripts/gen_sources_md.py
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load  # noqa: E402
from src.ingest.registry import SourceRegistry  # noqa: E402

#: Links the product itself emits, not corpus sources. Listed separately so the
#: "sources I answer from" claim in the UI stays literally true.
EDUCATIONAL_LINKS = (
    (
        "SEBI mutual fund education",
        "https://investor.sebi.gov.in/",
        "refusals: opinionated and advice questions (FR-10)",
    ),
)


def _fetched_at() -> Dict[str, str]:
    """``source_id -> ISO fetch timestamp`` from the ingest manifest."""
    settings = load()
    if not settings.sources_csv_path.exists():
        return {}
    with settings.sources_csv_path.open(encoding="utf-8", newline="") as handle:
        return {row["source_id"]: row["fetched_at"] for row in csv.DictReader(handle)}


def _chunk_counts() -> Dict[str, int]:
    """``source_id -> chunk count`` from the persisted dump (D-7)."""
    settings = load()
    if not settings.chunks_txt_path.exists():
        return {}
    counts: Dict[str, int] = {}
    for line in settings.chunks_txt_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("=== CHUNK "):
            source_id = line[len("=== CHUNK ") :].split("::")[0].strip()
            counts[source_id] = counts.get(source_id, 0) + 1
    return counts


def _factsheet_rows(registry: SourceRegistry) -> List[str]:
    """The per-scheme factsheet links the product actually emits.

    Read from the live config rather than written here, for the same reason the
    source table is: a hand-maintained copy of a redirect target is a link that
    rots. A scheme with no configured factsheet gets an explicit "not configured"
    so the gap is visible in the deliverable rather than discovered when a user
    clicks through to the educational link instead.
    """
    links = load().factsheet_link_map or {}
    rows: List[str] = []
    for spec in registry.all_sources():
        rows.append(f"| {spec.scheme} | {links.get(spec.source_id) or '_(not configured)_'} |")
    return rows


def build(registry: SourceRegistry) -> str:
    fetched = _fetched_at()
    chunks = _chunk_counts()
    now = datetime.now().astimezone().strftime("%Y-%m-%d")

    lines: List[str] = [
        "# SOURCES.md — deliverable D-2 (FR-2)",
        "",
        "The complete list of public sources this assistant answers from. Generated from",
        "`config/sources.yaml` by `python scripts/gen_sources_md.py`, so it cannot drift from",
        "the allowlist the code enforces (C-1). Every URL below is a public scheme page;",
        "there are no third-party blogs and no paywalled material.",
        "",
        f"Generated: {now} · {len(registry)} sources · {sum(chunks.values())} chunks",
        "",
        "## Corpus sources",
        "",
        "| # | Scheme | Category | Plan | Fetched (UTC) | Chunks | URL |",
        "|---|--------|----------|------|---------------|--------|-----|",
    ]

    for index, spec in enumerate(registry.all_sources(), start=1):
        stamp = fetched.get(spec.source_id)
        date = stamp.split("T")[0] if stamp else "unknown"
        lines.append(
            f"| {index} | {spec.scheme} | `{spec.category}` | `{spec.plan_variant}` "
            f"| {date} | {chunks.get(spec.source_id, 0)} | {spec.url} |"
        )

    lines += [
        "",
        "All five schemes are from the same AMC (HDFC Mutual Fund), and all five are the",
        "**Direct Growth** plan variant. See \"Scope and known limits\" in `README.md` for",
        "why the corpus is deliberately that narrow.",
        "",
        "## Source links the assistant cites",
        "",
        "Not corpus sources — these are the official pages a refusal or redirect points to,",
        "so the user can go read the figure the assistant declined to state (FR-11, FR-20).",
        "",
        "| Purpose | URL |",
        "|---------|-----|",
    ]
    lines += [f"| {label} | {url} |" for label, url, _ in EDUCATIONAL_LINKS]
    lines += [
        "",
        "The official monthly factsheet is the destination for a performance question",
        "(FR-11). Which factsheet a scheme redirects to is configuration, not code:",
        "",
        "| Scheme | Official factsheet |",
        "|--------|-------------------|",
        *_factsheet_rows(registry),
        "",
        "> **Known limit.** As configured, all five schemes resolve to the same AMC-level",
        "> factsheet rather than a per-scheme one, so a user redirected here still has to",
        "> find their own scheme's figures. See \"Known limits\" in `README.md`.",
        "",
        "## Rules this file exists to make checkable",
        "",
        "- **C-1 / NFR-6** — only the URLs above may be cited. The registry rejects anything",
        "  else, and `src/eval/harness.py` E-6 re-asserts it against real answers.",
        "- **C-3** — the corpus contains no return, risk or performance figures, so the",
        "  assistant never states or compares one; it links the official factsheet instead.",
        "- **FR-2** — this file is the source list required by the brief, and is regenerated",
        "  rather than edited by hand.",
        "",
        "---",
        "",
        "Facts-only. No investment advice. (D-5 — see `DISCLAIMER.txt`.)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load()
    registry = SourceRegistry.from_file(settings.sources_path)
    out = settings.sources_md_path
    out.write_text(build(registry), encoding="utf-8")
    print(f"wrote {out} ({len(registry)} sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
