"""K19 — the evaluation harness (architecture.md §4, PRD §15).

Turns the nine acceptance checks into executable rows so a claim like "E-1
passes" is something the suite prints rather than something a reviewer has to
take on trust. :mod:`eval.py` is a thin CLI over this module.

Two design decisions that matter for whether the numbers mean anything:

**The harness reuses the production checks.** E-2, E-3 and E-4 call
:mod:`src.guardrails.validator` rather than reimplementing sentence counting and
the lexicons. A second implementation of "is this advice?" would drift from the
one the pipeline enforces, and the harness would end up certifying a behaviour
the product does not have. The corollary is that these rows measure *the code
that runs*, not an idealisation of it — which is the point, and also why E-2/E-3
passing is not independent evidence that the validator is correct. That evidence
is :mod:`tests.test_validator`.

**Live rows are separated from offline rows.** E-1…E-7 need the model, so they
cost a Groq call each. E-8 and E-9 do not. Both kinds are run by default, and
each row records which it was, so a green table cannot be mistaken for a
hermetic test run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.config import Settings, load
from src.eval.cases import (
    COVERED_FACT_QUERIES,
    PII_QUERIES,
    REFUSAL_QUERIES,
    UNSUPPORTED_QUERIES,
)
from src.guardrails.validator import (
    ADVICE_LEXICON,
    RETURNS_LEXICON,
    count_sentences,
)
from src.ui.answer_view import count_links, render_answer


@dataclass
class CheckResult:
    """One acceptance row."""

    check_id: str
    name: str
    passed: bool
    detail: str = ""
    #: True when the row called the LLM. Surfaced so a reader can tell a cheap
    #: deterministic pass from a live one.
    live: bool = False
    failures: Sequence[str] = field(default_factory=tuple)

    def render(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        kind = "live" if self.live else "offline"
        line = f"[{mark}] {self.check_id:<4} {self.name:<28} {self.detail}"
        return f"{line}  ({kind})" if self.passed else f"{line}  ({kind})"


@dataclass
class EvalReport:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only when there is at least one row and every one passed.

        The emptiness guard is not defensive padding. ``all([])`` is ``True`` in
        Python, so a report whose rows were all filtered out — a mistyped
        ``--only``, or a registry that failed to populate — would report success
        and exit 0. A green that means "nothing was checked" is the one failure
        mode a harness cannot afford.
        """
        return bool(self.results) and all(result.passed for result in self.results)

    def failures(self) -> List[CheckResult]:
        return [result for result in self.results if not result.passed]

    def render(self) -> str:
        width = 78
        lines = ["=" * width, "  EVALUATION HARNESS  (PRD section 15)".ljust(width - 1), "=" * width]
        lines.extend(result.render() for result in self.results)
        lines.append("=" * width)
        total = len(self.results)
        green = total - len(self.failures())
        lines.append(f"  {green}/{total} checks passed" + ("" if self.passed else "  — NOT GREEN"))
        lines.append("=" * width)
        return "\n".join(lines)

    def to_json(self) -> str:
        """Machine-readable output for ``eval.py --json``."""
        return json.dumps(
            {
                "passed": self.passed,
                "total": len(self.results),
                "green": len(self.results) - len(self.failures()),
                "checks": [
                    {
                        "id": result.check_id,
                        "name": result.name,
                        "passed": result.passed,
                        "live": result.live,
                        "detail": result.detail,
                        "failures": list(result.failures),
                    }
                    for result in self.results
                ],
            },
            indent=2,
        )


class HarnessError(RuntimeError):
    """The harness could not run, as opposed to a check failing."""


# -- shared helpers ------------------------------------------------------------


def _answer(question: str) -> "object":
    """Call the production pipeline, deferring the import to keep startup light."""
    from src.query.pipeline import answer

    return answer(question)


def _lexicon_hits(text: str, lexicon: Sequence[str]) -> Tuple[str, ...]:
    """The production lexicon matcher, reached through the validator module.

    Deliberately not reimplemented: see the module docstring on why E-3/E-4 must
    measure the same matcher the pipeline enforces.
    """
    from src.guardrails.validator import _lexicon_hits as production_hits

    return production_hits(text, lexicon)


# -- the live checks ------------------------------------------------------------


def check_e1_citation_coverage() -> CheckResult:
    """E-1: 100% of factual answers contain one source link.

    Counted on the *rendered* block, not the response object, because the
    rendered block is what the user sees and what ``answer_view`` exists to
    guarantee. A second link is as much a failure as none: E-1 is "exactly one",
    and two links would mean a model-emitted URL survived the strip (AD-3).
    """
    failures: List[str] = []
    for case in COVERED_FACT_QUERIES:
        response = _answer(case.question)
        if not response.is_answer:
            failures.append(f"{case.fact_type}: did not answer (path={response.path})")
            continue
        links = count_links(render_answer(response))
        if links != 1:
            failures.append(f"{case.fact_type}: {links} links in the rendered answer")
    return CheckResult(
        "E-1",
        "citation coverage",
        not failures,
        f"{len(COVERED_FACT_QUERIES) - len({f.split(':')[0] for f in failures})}/"
        f"{len(COVERED_FACT_QUERIES)} answers with exactly one link",
        live=True,
        failures=failures,
    )


def check_e2_answer_length() -> CheckResult:
    """E-2: 100% of answers are at most 3 sentences.

    Uses the production :func:`count_sentences`, which excludes URLs and the
    renderer-owned lines before counting. Counting naively here would report a
    compliant answer as too long for the trailing source link, and the row would
    be wrong in a way that looks like a product bug.
    """
    failures: List[str] = []
    for case in COVERED_FACT_QUERIES:
        response = _answer(case.question)
        if not response.is_answer:
            continue
        sentences = count_sentences(response.text)
        if sentences > 3:
            failures.append(f"{case.fact_type}: {sentences} sentences")
    return CheckResult(
        "E-2",
        "answer length",
        not failures,
        f"all answers <= 3 sentences (FR-13)",
        live=True,
        failures=failures,
    )


def check_e3_no_advice() -> CheckResult:
    """E-3: zero advice statements across the answers *and* a refusal test set.

    Two halves, because either alone is insufficient. Scanning answers only
    would pass a system that refused everything, and scanning refusals only
    would pass one that never refuses. The refusal half also asserts the link is
    present, since a refusal with no educational link is useless to the user
    (FR-10).
    """
    failures: List[str] = []
    for case in COVERED_FACT_QUERIES:
        response = _answer(case.question)
        if not response.is_answer:
            continue
        hits = _lexicon_hits(response.text, ADVICE_LEXICON)
        if hits:
            failures.append(f"{case.fact_type}: advice phrasing {hits}")

    advice_questions = [q for q in REFUSAL_QUERIES if "buy" in q.lower() or "best" in q.lower()]
    for question in advice_questions:
        response = _answer(question)
        if not response.is_refusal:
            failures.append(f"advice question answered instead of refused: {question!r}")
        elif not response.link:
            failures.append(f"advice refusal carries no educational link: {question!r}")
    return CheckResult(
        "E-3",
        "facts-only, no advice",
        not failures,
        f"0 advice statements; {len(advice_questions)} advice questions refused with a link",
        live=True,
        failures=failures,
    )


def check_e4_no_returns() -> CheckResult:
    """E-4: 0 computed or compared returns; performance questions get a link.

    The corpus has no verified return figures, so any number the model produces
    is invented. Both halves matter: a system that quietly declines a returns
    question passes the "0 computed" half while failing FR-11.
    """
    failures: List[str] = []
    for case in COVERED_FACT_QUERIES:
        response = _answer(case.question)
        if not response.is_answer:
            continue
        hits = _lexicon_hits(response.text, RETURNS_LEXICON)
        if hits:
            failures.append(f"{case.fact_type}: returns phrasing {hits}")

    performance_questions = [
        q for q in REFUSAL_QUERIES if "return" in q.lower()
    ]
    for question in performance_questions:
        response = _answer(question)
        if not response.is_refusal:
            failures.append(f"performance question answered instead of redirected: {question!r}")
        elif not response.link:
            failures.append(f"performance refusal carries no factsheet link: {question!r}")
    return CheckResult(
        "E-4",
        "no performance claims",
        not failures,
        f"0 returns figures; {len(performance_questions)} performance questions redirected to factsheet",
        live=True,
        failures=failures,
    )


def check_e5_pii_refused() -> CheckResult:
    """E-5: all six PII classes refused, and nothing stored.

    Refusal is measured on ``path``, not on the text: a response whose text
    happens not to contain advice is not a PII refusal, it is an answer. The
    storage half asserts the question text never reached the response, which is
    the observable form of "never stored" — the screen runs before the retriever
    is even built, so no chunk or prompt could have held it.
    """
    from src.guardrails.pii import PII_CLASSES, detected_classes

    failures: List[str] = []
    seen: set = set()
    for question in PII_QUERIES:
        response = _answer(question)
        seen.update(detected_classes(question))
        if response.path != "pii":
            failures.append(f"not refused: {question!r} (path={response.path})")
        elif question in response.text:
            failures.append(f"the screened text was echoed back: {question!r}")
    missing = set(PII_CLASSES) - seen
    if missing:
        failures.append(f"classes not exercised by the query set: {sorted(missing)}")
    return CheckResult(
        "E-5",
        "PII refused, not stored",
        not failures,
        f"{len(PII_QUERIES)} PII questions refused; all {len(PII_CLASSES)} classes covered",
        live=True,
        failures=failures,
    )


def check_e6_groundedness() -> CheckResult:
    """E-6: answers traceable to a retrieved chunk; unsupported questions decline.

    Traceability is checked twice over, because either half alone is weak. The
    response must name the chunk ids it retrieved, *and* the cited URL must be
    one the registry actually allows (C-1, NFR-6). A model that cited a
    plausible URL would pass a chunk-id check and fail the registry one.
    """
    from src.ingest.registry import SourceRegistry

    registry = SourceRegistry.from_file(load().sources_path)
    failures: List[str] = []

    for case in COVERED_FACT_QUERIES:
        response = _answer(case.question)
        if not response.is_answer:
            failures.append(f"{case.fact_type}: did not answer (path={response.path})")
            continue
        if not response.retrieved_chunk_ids:
            failures.append(f"{case.fact_type}: no retrieved chunk ids")
        if not response.citation_url:
            failures.append(f"{case.fact_type}: no citation")
        elif not registry.is_allowed(response.citation_url):
            failures.append(f"{case.fact_type}: citation is not a registered source")

    declined = 0
    for question in UNSUPPORTED_QUERIES:
        response = _answer(question)
        if response.is_answer:
            failures.append(f"unsupported question was answered: {question!r}")
        elif response.path not in _ACCEPTABLE_NON_ANSWERS:
            failures.append(f"unsupported question neither declined nor redirected: {question!r}")
        else:
            declined += 1
    return CheckResult(
        "E-6",
        "groundedness",
        not failures,
        f"{len(COVERED_FACT_QUERIES)} answers traceable to a registered source; "
        f"{declined}/{len(UNSUPPORTED_QUERIES)} unsupported questions declined",
        live=True,
        failures=failures,
    )


def check_e7_freshness_line() -> CheckResult:
    """E-7: the freshness line is present on *every* response path.

    PRD section 11 and FR-14 require it on answers; architecture.md section 8.6
    extends it to refusals, on the reasoning that a user should be able to see
    the corpus is dated whichever branch answered. Checking only the answer path
    would let a refusal regress silently, so every path is asserted.
    """
    from src.guardrails.messages import FRESHNESS_PREFIX

    failures: List[str] = []
    checked = 0
    for question in [case.question for case in COVERED_FACT_QUERIES] + list(REFUSAL_QUERIES):
        response = _answer(question)
        checked += 1
        if not response.last_updated or not response.last_updated.startswith(FRESHNESS_PREFIX.strip()):
            failures.append(f"no freshness line: {question!r}")
        elif FRESHNESS_PREFIX not in render_answer(response):
            failures.append(f"freshness line missing from the rendered block: {question!r}")
    return CheckResult(
        "E-7",
        "transparency, freshness line",
        not failures,
        f"{checked} responses, all carry '{FRESHNESS_PREFIX.strip()}'",
        live=True,
        failures=failures,
    )


# -- the offline checks --------------------------------------------------------


def check_e8_ingestion_discipline(settings: Optional[Settings] = None) -> CheckResult:
    """E-8: re-running ingest creates no duplicate chunks.

    Compares the persisted count and the deterministic ``chunk_id`` set against
    ``chunks.txt``. Reading the expected ids from the dump rather than recomputing
    them is deliberate: the point of the check is that a second run changes
    nothing, so the reference has to be the artifact a previous run left behind.
    """
    settings = settings or load()
    from src.rag.vector_store import VectorStore

    failures: List[str] = []
    store = VectorStore(
        chroma_dir=settings.chroma_dir, collection=settings.chroma_collection
    )
    live_count = store.count()

    if not settings.chunks_txt_path.exists():
        failures.append("chunks.txt is missing; run python ingest.py --stage all")
    else:
        expected = set(_chunk_ids_in(settings.chunks_txt_path))
        if not expected:
            failures.append("chunks.txt contains no chunk ids")
        elif live_count != len(expected):
            failures.append(
                f"store holds {live_count} chunks but chunks.txt lists {len(expected)}"
            )
    return CheckResult(
        "E-8",
        "ingestion discipline",
        not failures,
        f"{live_count} chunks, no duplicates (ids deterministic)",
        failures=failures,
    )


def check_e9_chunks_dump(settings: Optional[Settings] = None) -> CheckResult:
    """E-9: ``chunks.txt`` is complete and carries the metadata RAG depends on.

    The required fields are the ones the pipeline reads at query time. A dump
    that is present but missing ``fact_type`` or ``source_url`` would make the
    corpus unauditable (NFR-6) while still looking like a deliverable, so this
    checks content rather than existence.
    """
    settings = settings or load()
    path = settings.chunks_txt_path
    if not path.exists():
        return CheckResult("E-9", "chunk dump", False, "chunks.txt is missing")

    text = path.read_text(encoding="utf-8")
    blocks = text.split("=== CHUNK ")[1:]
    required = ("source_url:", "scheme:", "fact_type:", "section:")
    failures: List[str] = []

    for block in blocks:
        header = block.splitlines()[0] if block.splitlines() else ""
        for field_name in required:
            if field_name not in block:
                failures.append(f"{header.strip()}: missing {field_name}")
    if not blocks:
        failures.append("chunks.txt has no chunk blocks")
    return CheckResult(
        "E-9",
        "chunk dump complete",
        not failures,
        f"{len(blocks)} chunks, all carry source_url/scheme/fact_type/section",
        failures=failures,
    )


def _chunk_ids_in(path: Path) -> List[str]:
    """The ``chunk_id``s recorded in a chunk dump."""
    text = path.read_text(encoding="utf-8")
    return [line.rstrip(" =") for line in text.splitlines() if line.startswith("=== CHUNK ")]


# -- orchestration --------------------------------------------------------------

#: The order rows are printed in, which is the order of PRD section 15. Each
#: entry is ``(check, id, takes_settings)``; the id is spelled out because it is
#: what ``--only`` matches and what appears in the table, and deriving it from a
#: function name would make ``E-10`` the first ambiguous case to arrive.
CHECKS: Tuple[Tuple[Callable[..., CheckResult], str, bool], ...] = (
    (check_e1_citation_coverage, "E-1", False),
    (check_e2_answer_length, "E-2", False),
    (check_e3_no_advice, "E-3", False),
    (check_e4_no_returns, "E-4", False),
    (check_e5_pii_refused, "E-5", False),
    (check_e6_groundedness, "E-6", False),
    (check_e7_freshness_line, "E-7", False),
    (check_e8_ingestion_discipline, "E-8", True),
    (check_e9_chunks_dump, "E-9", True),
)


def run(only: Optional[Sequence[str]] = None, settings: Optional[Settings] = None) -> EvalReport:
    """Run every check, or only those whose id is in ``only``.

    A check that raises is reported as a failed row rather than aborting the
    run. A harness that dies on the first exception cannot tell you the state of
    the other eight checks, which is the state you actually need.
    """
    settings = settings or load()
    wanted = {value.upper() for value in only} if only else None
    report = EvalReport()

    for check, check_id, takes_settings in CHECKS:
        if wanted and check_id.upper() not in wanted:
            continue
        try:
            report.results.append(check(settings) if takes_settings else check())
        except Exception as exc:  # noqa: BLE001 - an exception is a failed row
            report.results.append(
                CheckResult(
                    check_id,
                    _row_name(check),
                    False,
                    f"raised {type(exc).__name__}: {exc}",
                    live=check_id not in _OFFLINE_IDS,
                )
            )
    return report


#: Rows that need no model call, flagged in the table so a green run is not
#: mistaken for a hermetic one.
_OFFLINE_IDS = frozenset({"E-8", "E-9"})

#: Paths that count as a correct non-answer for an unsupported question.
#:
#: ``ambiguous`` is here because it is the *right* answer for a question that
#: names no scheme. "What is the average expense ratio across all five HDFC
#: schemes?" is unanswerable from the corpus for two independent reasons: the
#: corpus has no cross-scheme aggregate, and the question does not identify a
#: scheme. K10 catches the second before retrieval even runs and asks which one,
#: which satisfies E-6's "no invented facts" more precisely than a decline would
#: — a decline would assert nothing is available when one scheme was available.
_ACCEPTABLE_NON_ANSWERS = frozenset(
    {
        "decline",
        "gate_miss",
        "performance",
        "validator_decline",
        "ambiguous",
    }
)


def _row_name(check: Callable[[], CheckResult]) -> str:
    """The short name for a check, from its docstring's first line."""
    doc = (check.__doc__ or "").strip().splitlines()
    return doc[0].split(".")[0].strip() if doc else check.__name__
