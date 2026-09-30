"""Generate SAMPLE_QA.md (D-4) by actually running the pipeline.

The brief asks for "5–10 queries with the assistant's answers + links". The
tempting shortcut is to write those answers by hand, and it is the one thing
that would make this deliverable worthless: a hand-written sample is evidence
about the author, not about the product, and it drifts the first time a prompt
changes. So this script asks the real pipeline, renders the real block with
`src.ui.answer_view.render_answer`, and records what came back.

Run it after `python ingest.py --stage all` and with a live Groq key:

    python scripts/gen_sample_qa.py

It is deliberately *not* a test. Model output varies, so a diff here is not a
regression; `eval.py` is what asserts behaviour.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load  # noqa: E402
from src.eval.cases import COVERED_FACT_QUERIES, PII_QUERIES  # noqa: E402
from src.guardrails.messages import DISCLAIMER  # noqa: E402
from src.query.pipeline import answer  # noqa: E402
from src.ui.answer_view import render_answer  # noqa: E402

#: A representative spread rather than the whole case set: all six covered fact
#: types, then one branch per non-answer path.
#:
#: The two PII questions are chosen from the tail of ``PII_QUERIES`` on purpose.
#: The first six entries of that set carry synthetic identifiers (a PAN, an
#: Aadhaar number, a folio number) because that is what the screen has to be
#: tested against, but writing any of them into a submission artifact would
#: contradict NFR-5 in the same breath. The last two are the same classes —
#: email and OTP — with no identifier at all, so the branch is demonstrated
#: without a value being published. The screen is not weakened by the choice:
#: it refuses these because it sees the *class* cue, not the digits.
SAMPLE_QUESTIONS: Tuple[str, ...] = (
    *(case.question for case in COVERED_FACT_QUERIES),
    "Which HDFC fund is the best one for me to invest in?",
    "What is the 5-year return of the HDFC Large Cap Fund?",
    PII_QUERIES[6],
    PII_QUERIES[7],
)


def _escape(text: str) -> str:
    """Fence a rendered block without letting a stray fence break the document."""
    return text.replace("```", "`` `")


def registry_ids() -> Sequence[str]:
    """The registered source ids, for the corpus line in the header."""
    from src.ingest.registry import SourceRegistry

    return SourceRegistry.from_file(load().sources_path).source_ids()


def build() -> str:
    settings = load()
    now = datetime.now().astimezone()

    lines: List[str] = [
        "# SAMPLE_QA.md — deliverable D-4 (FR-19)",
        "",
        f"{len(SAMPLE_QUESTIONS)} real question-and-answer pairs, produced by running the",
        "pipeline against the persisted corpus. Every block below is the actual output of",
        "`src.query.pipeline.answer` rendered by `src.ui.answer_view.render_answer` — nothing",
        "is written by hand. Regenerate with:",
        "",
        "```bash",
        "python ingest.py --stage all",
        "python scripts/gen_sample_qa.py",
        "```",
        "",
        f"Corpus: {len(registry_ids())} schemes · captured {now.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "Model output is not deterministic, so a regenerated file will not be byte-identical.",
        "What must not change is the *shape* of each response: one link on an answer, a fixed",
        "refusal on an opinion question, a redirect on a performance question, and a screen on",
        "anything containing a personal identifier. `python eval.py` is what checks that.",
        "",
        "---",
        "",
    ]

    for number, question in enumerate(SAMPLE_QUESTIONS, start=1):
        response = answer(question)
        rendered = _escape(render_answer(response))
        lines += [
            f"## {number}. {question}",
            "",
            f"**Path taken:** `{response.path}`"
            + (f" · intent `{response.intent}`" if response.intent else "")
            + (f" · top match {response.top_similarity:.2f}" if response.top_similarity else ""),
            "",
            "```text",
            DISCLAIMER,
            "",
            rendered,
            "```",
            "",
        ]
        if response.sources:
            lines.append("Sources consulted:")
            lines.append("")
            for source in response.sources:
                lines.append(
                    f"- `{source.chunk_id}` — {source.section} (similarity {source.similarity:.2f})"
                )
            lines.append("")
        lines += ["---", ""]

    lines += [
        "## What each block demonstrates",
        "",
        "| # | Question | Expected branch | What it shows |",
        "|---|----------|-----------------|----------------|",
        "| 1–6 | covered facts | `answer` | one source link, ≤3 sentences, freshness line (E-1, E-2, E-7) |",
        "| 7 | \"best for me\" | refusal | no advice, no ranking, educational link (FR-10, E-3) |",
        "| 8 | 5-year return | performance | no figure stated or computed; official factsheet link (C-3, E-4) |",
        "| 9–10 | personal identifiers | `pii` | refused before retrieval; nothing stored (C-2, E-5) |",
        "",
        "The 5-year return question is absent from the corpus by design and takes the",
        "performance branch (C-3). Questions 9 and 10 name a *class* of identifier without",
        "carrying one, which is what lets this file demonstrate the PII screen while still",
        "containing no personal data (NFR-5). The corpus's one genuine gap is a cross-scheme",
        "aggregate — see \"Known limits\" in `README.md`.",
        "",
        "---",
        "",
        DISCLAIMER,
        "",
        "Full wording and the refusal messages: `DISCLAIMER.txt`. Source list: `SOURCES.md`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load()
    out = settings.sample_qa_path
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({len(SAMPLE_QUESTIONS)} questions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
