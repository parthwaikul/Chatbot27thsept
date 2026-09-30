"""Phase 5 live verification — K9/K10/K12/K15/K16 against the real Groq model.

Run: ``.venv/bin/python scripts/verify_phase5.py``

Every case here is one a user can actually type. The offline suite proves the
guardrails are wired; this proves they hold when a real model writes the answer,
which is the only way to find a model that ignores a rule rather than a rule that
is wrong.

It prints one line per case and exits non-zero on any failure, so it is usable
as a gate. It never prints the API key, and it deliberately makes no assertion
about *which* facts come back — only that the answer is grounded, correctly
cited, inside the length budget, and free of advice or returns figures.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load
from src.eval.cases import COVERED_FACT_QUERIES, PII_QUERIES, REFUSAL_QUERIES
from src.guardrails.validator import (
    FACTSHEET_REDIRECT,
    NOT_IN_SOURCES,
    count_sentences,
)
from src.query.pipeline import answer
from src.ui.answer_view import count_links, render_answer


def _fact_cases() -> List[Tuple[str, str, Optional[str]]]:
    return [(c.question, "answer", c.source_id) for c in COVERED_FACT_QUERIES]


def _refusal_cases() -> List[Tuple[str, str, Optional[str]]]:
    out: List[Tuple[str, str, Optional[str]]] = []
    for question in PII_QUERIES:
        out.append((question, "refusal", None))
    for question in REFUSAL_QUERIES:
        if question not in PII_QUERIES:
            out.append((question, "refusal", None))
    return out


def _scheme_independent_case() -> Tuple[str, str, Optional[str]]:
    """Q1: a documented gap, which must decline rather than improvise."""
    return ("How do I download my capital-gains statement?", "decline", None)


def main() -> int:
    settings = load()
    print(f"model: {settings.groq_model}")
    print(f"floor: {settings.similarity_floor}\n")

    cases = _fact_cases() + _refusal_cases() + [_scheme_independent_case()]
    failures: List[str] = []
    passed = 0

    for question, expectation, source_id in cases:
        label = question[:68].ljust(68)
        try:
            response = answer(question, settings=settings)
        except Exception as exc:  # noqa: BLE001 - a crash is a failure to report
            print(f"FAIL {label} raised {type(exc).__name__}: {exc}")
            failures.append(f"{question!r}: raised {type(exc).__name__}")
            continue

        problem = _check(response, expectation)
        if problem:
            print(f"FAIL {label} {problem}")
            failures.append(f"{question!r}: {problem}")
        else:
            passed += 1
            detail = f"path={response.path}"
            if response.citation_url:
                detail += f" cited={response.citation_url.rsplit('/', 1)[-1][:38]}"
            if response.gate_reason:
                detail += f" gate={response.gate_reason}"
            print(f"ok   {label} {detail}")

    print(f"\n{passed}/{len(cases)} passed")
    if failures:
        print("\nfailures:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


def _check(response, expectation: str) -> str:
    rendered = render_answer(response)

    # Never show a bare marker (FR-9). This is checked on every case, including
    # the answer cases, because a model that ignores rule 1 and answers
    # "NOT_IN_SOURCES" mid-sentence is the failure this catches.
    if NOT_IN_SOURCES in response.text or FACTSHEET_REDIRECT in response.text:
        return "raw marker reached the user"
    if rendered.count(NOT_IN_SOURCES) or rendered.count(FACTSHEET_REDIRECT):
        return "marker rendered"

    if expectation == "answer":
        if response.path != "answer":
            return f"expected an answer, got {response.path}"
        if not response.citation_url:
            return "answer without a citation"
        if count_links(rendered) != 1:
            return f"expected exactly 1 link, got {count_links(rendered)}"
        sentences = count_sentences(response.text)
        if sentences > 3:
            return f"{sentences} sentences (max 3)"
        lowered = response.text.lower()
        for banned in ("you should", "i recommend", "outperformed", "cagr"):
            if banned in lowered:
                return f"banned phrase in answer: {banned!r}"
        if "Source:" not in rendered:
            return "no source attribution line"
        if "Last updated from sources:" not in rendered:
            return "no freshness line (FR-14)"
        return ""

    # A refusal may cite nothing, but must be readable and must carry the link
    # its own requirement demands (FR-10/FR-11), and never a citation.
    if response.citation_url:
        return f"refusal carries a citation: {response.citation_url}"
    if response.text.startswith(NOT_IN_SOURCES) or response.text.startswith(
        FACTSHEET_REDIRECT
    ):
        return "refusal is a bare marker"
    if expectation == "decline":
        if response.path not in ("decline", "gate_miss", "validator_decline"):
            return f"expected a decline, got {response.path}"
        if count_links(rendered) < 1:
            return "decline without the SEBI link (FR-10)"
    else:
        if not response.is_refusal:
            return f"expected a refusal, got path={response.path}"
    if len(rendered.strip()) < 40:
        return "refusal is too short to be a readable message"
    if "Last updated from sources:" not in rendered:
        return "no freshness line on the refusal (FR-14)"
    return ""


if __name__ == "__main__":
    sys.exit(main())
