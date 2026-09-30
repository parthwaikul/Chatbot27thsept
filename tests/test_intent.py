"""K10 — intent router tests (implementation.md Phase 5 task 7, second clause).

Obligation: precedence. PII first, then advice/performance. The rest of the file
pins the category of every case in the three eval sets, because a misroute is
the most expensive kind of bug in this layer: a refusal sent down the RAG path
invites the model to answer, and a fact question routed to a refusal denies the
user an answer the corpus can support.
"""

from __future__ import annotations

import pytest

from src.config import load
from src.ingest.registry import SourceRegistry
from src.eval.cases import (
    COVERED_FACT_QUERIES,
    FACT_QUERIES,
    PII_QUERIES,
    REFUSAL_QUERIES,
    UNSUPPORTED_QUERIES,
)
from src.guardrails.intent import (
    CATEGORIES,
    SCHEME_INDEPENDENT_FACT_TYPES,
    TERMINAL_CATEGORIES,
    IntentRouterError,
    detect_fact_type,
    names_scheme,
    route,
)

REGISTRY = SourceRegistry.from_file(load().sources_path)


# -- the one property the spec names: precedence -------------------------------


def test_pii_outranks_every_other_category():
    """A PII question must never be classified as something that reaches the LLM.

    Each PII case is written so it would otherwise match a later category, which
    is the point: "My PAN is ... link it to my account" contains no advice or
    performance trigger, but the ordering has to hold regardless of what a new
    keyword might be added later.
    """
    for query in PII_QUERIES:
        assert route(query).category == "PII", f"PII not first for {query!r}"


def test_advice_outranks_performance():
    """"Should I buy the one that gave higher returns?" is both.

    A refusal is the safer of the two answers: it declines without asserting
    anything about returns. Routing it to PERFORMANCE would hand back a
    factsheet link and imply the comparison is answerable.
    """
    result = route("Should I buy the fund that gave higher returns?")
    assert result.category == "ADVICE"


def test_performance_outranks_fact_and_ambiguous():
    """A performance keyword wins over a fact keyword in the same question."""
    result = route("What is the expense ratio of the best performing HDFC scheme?")
    assert result.category == "PERFORMANCE"


def test_advice_and_performance_both_end_the_request():
    for query in ("Should I buy HDFC Large Cap?", "Which fund gave higher returns?"):
        assert route(query).intent.is_terminal


# -- the eval sets ------------------------------------------------------------


@pytest.mark.parametrize("case", FACT_QUERIES, ids=lambda c: c.fact_type)
def test_every_fact_query_routes_to_fact_with_its_own_fact_type(case):
    """The router must supply the fact type the pipeline needs.

    This is the contract that makes fact-typed retrieval work: without the right
    ``expected_fact_type``, K11 returns the long ``general`` blobs and the model
    is asked a question its context cannot answer.
    """
    result = route(case.question, registered_schemes=REGISTRY.schemes())
    assert result.category == "FACT"
    assert result.expected_fact_type == case.fact_type
    assert not result.intent.is_terminal


@pytest.mark.parametrize(
    "case", [c for c in FACT_QUERIES if c.fact_type not in SCHEME_INDEPENDENT_FACT_TYPES],
    ids=lambda c: c.fact_type,
)
def test_a_per_fund_fact_query_names_its_scheme(case):
    result = route(case.question, registered_schemes=REGISTRY.schemes())
    assert result.intent.names_scheme
    assert case.source_id in result.intent.source_ids


def test_the_scheme_independent_case_needs_no_scheme():
    """statement_guide is the one FR-8 question that is not per-fund."""
    case = next(c for c in FACT_QUERIES if c.fact_type == "statement_guide")
    result = route(case.question, registered_schemes=REGISTRY.schemes())
    assert result.category == "FACT"
    assert not result.intent.names_scheme


@pytest.mark.parametrize("query", REFUSAL_QUERIES)
def test_no_prd_refusal_reaches_the_rag_path(query):
    """PRD §15: none of the refusal set may be answered from context."""
    assert route(query, registered_schemes=REGISTRY.schemes()).intent.is_terminal


def test_refusal_set_category_split():
    """Two advice, two performance, two PII — the documented spread."""
    categories = [route(q).category for q in REFUSAL_QUERIES]
    assert categories.count("ADVICE") == 2
    assert categories.count("PERFORMANCE") == 2
    assert categories.count("PII") == 2
    assert "FACT" not in categories


@pytest.mark.parametrize("query", PII_QUERIES)
def test_pii_query_set_is_entirely_pii(query):
    assert route(query).category == "PII"


def test_unsupported_queries_never_reach_a_fact_fact_type():
    """E-6: an unsupported question must not be given a fact type to retrieve.

    A question that routes to FACT with an ``expected_fact_type`` will be
    retrieved for that type; if the corpus lacks it, K12 declines. That is the
    intended path. What must never happen is a *supported* fact type being
    claimed for a question the corpus cannot answer, because then the gate's
    condition 2 is satisfiable by the wrong chunks.
    """
    for query in UNSUPPORTED_QUERIES:
        result = route(query, registered_schemes=REGISTRY.schemes())
        if result.category == "FACT":
            assert result.expected_fact_type in (
                "expense_ratio",
                "exit_load",
                "min_sip",
                "lock_in",
                "riskometer",
                "benchmark",
            )


# -- Q5: the ambiguous category ----------------------------------------------


def test_a_fact_question_naming_no_scheme_is_ambiguous():
    """Q5: a per-fund fact with no fund named must ask which one."""
    result = route("What is the minimum SIP amount?")
    assert result.category == "AMBIGUOUS"
    assert result.expected_fact_type == "min_sip"
    assert not result.intent.names_scheme
    assert result.intent.is_terminal


def test_a_scheme_independent_how_to_is_not_ambiguous():
    """A how-to cannot differ between funds, so "which of the five?" is wrong.

    With no scheme-independent exemption this question would be asked to pick
    from a numbered list, which answers a question the user did not ask. It is
    routed to FACT instead and then declined by K12 for lack of a chunk, which
    is the correct outcome for the recorded Q1 corpus gap.
    """
    result = route("How do I download my capital-gains statement?")
    assert result.category == "FACT"
    assert result.expected_fact_type == "statement_guide"
    assert "statement_guide" in SCHEME_INDEPENDENT_FACT_TYPES


def test_a_bare_mention_of_the_amc_is_not_a_scheme_name():
    """"HDFC" alone names the AMC, not a fund.

    Treating it as a scheme name would make "What is the minimum SIP amount for
    HDFC?" look answered, and it would then retrieve across all five funds and
    mix their numbers.
    """
    result = route("What is the minimum SIP amount for HDFC?")
    assert result.category == "AMBIGUOUS"


# -- fact-type detection ------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is the expense ratio of HDFC Large Cap Fund?", "expense_ratio"),
        ("What is the exit load on HDFC Balanced Advantage Fund?", "exit_load"),
        ("What is the minimum SIP amount?", "min_sip"),
        ("What is the lock-in period for HDFC ELSS Tax Saver Fund?", "lock_in"),
        ("What is the riskometer category of HDFC Small Cap Fund?", "riskometer"),
        ("What benchmark does HDFC Equity Fund Direct Growth track?", "benchmark"),
        ("How do I download my capital-gains statement?", "statement_guide"),
    ],
)
def test_detect_fact_type(question, expected):
    assert detect_fact_type(question) == expected


def test_the_longest_matching_keyword_wins():
    """"minimum sip" must not be read as a bare "sip", and both are FR-8 types."""
    assert detect_fact_type("What is the minimum SIP for HDFC ELSS?") == "min_sip"
    assert detect_fact_type("What is the lock-in period?") == "lock_in"


def test_an_unrelated_question_has_no_fact_type():
    assert detect_fact_type("Who won the cricket match?") is None


def test_names_scheme_ignores_the_amc_alone():
    assert not names_scheme("HDFC")
    assert not names_scheme("Who won the cricket match?")
    assert names_scheme("HDFC Small Cap Fund")
    assert names_scheme("the ELSS one")


# -- table integrity ----------------------------------------------------------


def test_every_fact_type_keyword_maps_to_a_real_fact_type():
    from src.ingest.models import FACT_TYPES

    for fact_type in set(FACT_TYPE_KEYWORDS_FOR_TEST):
        assert fact_type in FACT_TYPES, f"{fact_type} is not a Phase 2 fact type"


FACT_TYPE_KEYWORDS_FOR_TEST = {
    "expense_ratio",
    "exit_load",
    "min_sip",
    "lock_in",
    "riskometer",
    "benchmark",
    "statement_guide",
}


def test_the_fr8_fact_types_are_all_routed():
    """FR-8 names seven fact questions; all seven must produce a fact type."""
    from src.ingest.models import FACT_TYPES

    covered = {c.fact_type for c in COVERED_FACT_QUERIES}
    assert covered | {"statement_guide"} == set(FACT_TYPES) - {"general"}


def test_terminal_categories_are_a_subset_of_the_documented_ones():
    assert set(TERMINAL_CATEGORIES) < set(CATEGORIES)
    assert "PII" in TERMINAL_CATEGORIES


def test_the_router_needs_a_question():
    with pytest.raises(IntentRouterError):
        route("")


def test_routing_works_without_a_registry():
    """Unit-level routing must not depend on a loaded corpus."""
    assert route("What is the expense ratio of HDFC Small Cap Fund?").category == "FACT"
