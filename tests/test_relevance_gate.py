"""K12 — relevance gate tests (implementation.md Phase 5 task 7, third clause).

The gate is the only thing standing between a retrieved chunk and a user-visible
answer, and it is deterministic: no LLM, no network. So these tests assert the
exact ``reason`` rather than just "declines" — a silent change from
``below_floor`` to ``wrong_fact_type`` is a different bug and needs a different
fix.
"""

from __future__ import annotations

import pytest

from src.config import load
from src.eval.cases import COVERED_FACT_QUERIES, UNSUPPORTED_QUERIES
from src.ingest.registry import SourceRegistry
from src.rag.relevance_gate import (
    BELOW_FLOOR,
    NO_CHUNKS,
    WRONG_FACT_TYPE,
    evaluate,
)
from src.rag.retriever import Retriever


@pytest.fixture(scope="module")
def retriever():
    return Retriever()


FLOOR = Retriever().similarity_floor
REGISTRY = SourceRegistry.from_file(load().sources_path)


# -- the accept side -----------------------------------------------------------


@pytest.mark.parametrize("case", COVERED_FACT_QUERIES, ids=lambda c: c.fact_type)
def test_every_covered_fact_passes_the_gate(retriever, case):
    """FR-8, verified against the real 166-chunk corpus rather than a stub.

    Each of the six answerable fact types is retrieved for its own type, and the
    floor must sit below the score. This is the test that caught the Phase 3
    floor of 0.42 rejecting the ``benchmark`` fact at 0.344.
    """
    hits = retriever.retrieve_for_fact_type(case.question, case.fact_type, k=3)
    result = evaluate(hits, case.fact_type, FLOOR)
    assert result.passed, f"{case.fact_type} declined: {result.reason}"
    assert result.reason == ""
    assert result.best_similarity > 0


def test_the_benchmark_fact_is_admitted_by_the_floor(retriever):
    """The specific regression: 0.344 vs the old 0.42 floor."""
    assert FLOOR < 0.344
    assert retriever.above_floor(
        "What benchmark does HDFC Equity Fund Direct Growth track?", k=3
    )


# -- the reject side -----------------------------------------------------------


def test_no_hits_is_rejected():
    result = evaluate([], "expense_ratio", FLOOR)
    assert not result.passed
    assert result.reason == NO_CHUNKS


def test_no_expected_fact_type_skips_condition_two(retriever):
    """OUT_OF_SCOPE carries no expected fact type, so condition 2 is vacuous.

    The gate must not invent one, or every out-of-scope question would be
    rejected for a reason that has nothing to do with the floor.
    """
    hits = retriever.retrieve("What is the best laptop under 50000?", k=3)
    result = evaluate(hits, None, FLOOR)
    assert result.reason != WRONG_FACT_TYPE
    assert result.passed, "an out-of-scope question above the floor is admitted to the LLM"


def test_a_weak_retrieval_is_reported_as_a_weak_retrieval(retriever):
    """``min_similarity`` is checked first so the reason is the useful one.

    Reporting "wrong fact type" for a retrieval that found nothing relevant
    sends the debugging effort in the wrong direction.
    """
    # "capital of France" scores 0.164, far below the floor, so the floor is what
    # fails here — not the fact type. (The laptop probe at 0.403 is above the
    # floor and is pinned separately in tests/test_retriever.py.)
    hits = retriever.retrieve("What is the capital of France?", k=3)
    result = evaluate(hits, "riskometer", FLOOR)
    assert not result.passed
    assert result.reason == BELOW_FLOOR
    assert result.best_similarity < FLOOR


def test_a_chunk_of_the_wrong_fact_type_is_rejected(retriever):
    """The riskometer conflict case: the wrong chunk must lose.

    All five schemes' header chunks carry ``fact_type=expense_ratio`` but
    contain a riskometer line saying "Very High", while the dedicated
    ``riskometer`` chunk says "Moderately High". Requiring agreement picks the
    dedicated chunk, so the contradictory header value never reaches a user.
    """
    question = "What is the riskometer category of HDFC Small Cap Fund?"
    hits = retriever.retrieve_for_fact_type(question, "expense_ratio", k=3)
    assert any("Very High" in h.text for h in hits), "fixture no longer has the conflict"
    result = evaluate(hits, "riskometer", FLOOR)
    assert not result.passed
    assert result.reason == WRONG_FACT_TYPE
    # The result reports what was retrieved instead, which explains the miss.
    assert "expense_ratio" in result.retrieved_fact_types
    assert result.failed_on_fact_type


def test_the_gate_admits_the_dedicated_riskometer_chunk(retriever):
    """...and the deterministic answer is "Moderately High", never "Very High"."""
    question = "What is the riskometer category of HDFC Small Cap Fund?"
    hits = retriever.retrieve_for_fact_type(question, "riskometer", k=3)
    result = evaluate(hits, "riskometer", FLOOR)
    assert result.passed, result.reason
    assert any("Moderately High" in h.text for h in hits)
    assert not any("Very High" in h.text for h in hits)


def test_a_fact_type_absent_from_the_corpus_is_rejected(retriever):
    """FR-7 is enforced by refusing, not by answering from a stale value.

    NAV is not a Phase 2 ``fact_type`` at all, so this is the shape FR-7 takes:
    the router sends the question to PERFORMANCE (a terminal category) and the
    corpus is never consulted. If a future phase adds ``nav`` to ``FACT_TYPES``,
    this test starts failing and the FR-7 decision has to be re-made.
    """
    from src.guardrails.intent import route

    question = "What is the current NAV of HDFC Large Cap Fund Direct Growth?"
    intent = route(question, registered_schemes=REGISTRY.schemes())
    assert intent.category == "PERFORMANCE"
    assert intent.intent.is_terminal
    assert intent.expected_fact_type is None

    from src.ingest.models import FACT_TYPES

    assert "nav" not in FACT_TYPES


@pytest.mark.parametrize("question", UNSUPPORTED_QUERIES)
def test_unsupported_questions_never_reach_the_llm(retriever, question):
    """E-6: the gate is the control, because score alone is not.

    These questions score 0.587-0.801 — far above any floor worth having. What
    stops them is fact-type disagreement (condition 2) or a PERFORMANCE route.
    Each is routed the way the pipeline routes it, so the expected fact type is
    the one K10 actually supplies rather than one the test picks.
    """
    from src.guardrails.intent import route

    intent = route(question, registered_schemes=REGISTRY.schemes())
    expected = intent.expected_fact_type
    if intent.intent.category != "FACT":
        pytest.skip(f"{intent.category} is terminal before the gate")

    hits = retriever.retrieve_for_fact_type(question, expected, k=3)
    result = evaluate(hits, expected, FLOOR)
    if result.passed:
        # Permitted only when the question is answerable from the corpus; the
        # pipeline still requires the model to ground itself in the context.
        covered = {c.fact_type for c in COVERED_FACT_QUERIES}
        assert expected in covered, f"{expected} was admitted but is not in the corpus"
    else:
        assert result.reason, "a rejection must say why"


# -- wiring --------------------------------------------------------------------


def test_the_floor_is_reported_even_on_success(retriever):
    """The eval report needs the floor it judged against, not just the outcome."""
    hits = retriever.retrieve_for_fact_type(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        "expense_ratio",
        k=3,
    )
    result = evaluate(hits, "expense_ratio", FLOOR)
    assert result.floor == FLOOR
    assert result.expected_fact_type == "expense_ratio"
    assert result.retrieved_fact_types


def test_the_gate_is_a_pure_function_of_the_retrieval(retriever):
    """Architecture.md §8.4: the gate runs before, and independently of, the LLM."""
    question = "What is the expense ratio of HDFC Large Cap Fund Direct Growth?"
    hits = retriever.retrieve_for_fact_type(question, "expense_ratio", k=3)
    first = evaluate(hits, "expense_ratio", FLOOR)
    second = evaluate(hits, "expense_ratio", FLOOR)
    assert first == second


def test_a_chunk_exactly_at_the_floor_is_admissible(retriever):
    """``>=`` is the gate's rule; ``>`` is the retriever's ranking heuristic.

    They answer different questions, and conflating them would drop a chunk the
    gate is meant to allow.
    """
    hits = retriever.retrieve_for_fact_type(
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        "expense_ratio",
        k=1,
    )
    exact = hits[0].similarity
    assert evaluate(hits, "expense_ratio", exact).passed
    assert not evaluate(hits, "expense_ratio", exact + 1e-9).passed


def test_the_gate_handles_none_chunks():
    assert evaluate(None, "expense_ratio", FLOOR).reason == NO_CHUNKS
    assert evaluate((), None, FLOOR).reason == NO_CHUNKS
