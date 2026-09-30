"""K15 — answer validator tests (implementation.md Phase 5 task 7, fourth clause).

The validator is the last control before text reaches a user, so these tests are
about what it *rejects* as much as what it accepts. Three obligations from
architecture.md §8.6:

* FR-9: markers never render as bare wire format.
* FR-10: no answer without a citation to a retrieved chunk.
* FR-11: no advice, and no returns/performance figures.

Plus the documented failure ladder: one repair attempt for a *fixable* problem,
and a safe decline for a problem that rewriting cannot fix.

Note on the marker contract: ``validate`` returns ``ok=True`` **with** a marker.
A marker is the model's compliant way of declining, so it is a successful
validation whose ``marker`` routes the pipeline. The pipeline, not the validator,
is what turns a marker into a readable message.
"""

from __future__ import annotations

import pytest

from src.eval.cases import REFUSAL_QUERIES
from src.guardrails.messages import (
    ADVICE_REFUSAL,
    FRESHNESS_PREFIX,
    NOT_IN_SOURCES_DECLINE,
    PERFORMANCE_REDIRECT,
)
from src.guardrails.validator import (
    ADVICE_LEXICON,
    FACTSHEET_REDIRECT,
    NOT_IN_SOURCES,
    REPAIR_INSTRUCTION,
    RETURNS_LEXICON,
    ValidationResult,
    count_sentences,
    urls_in,
    validate,
)

CHUNK_URL = "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"


# -- the accept path -----------------------------------------------------------


def test_a_short_cited_answer_passes():
    result = validate(
        f"The expense ratio is 1.03%. Source: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert result.ok
    assert result.repairable is False
    assert result.advice_hits == ()
    assert result.returns_hits == ()


def test_a_missing_freshness_line_is_recorded_but_not_a_failure():
    """Freshness is K17's job and the renderer appends it (FR-14).

    Charging the model for a line it was never asked to emit would fail every
    answer; it is reported instead so E-7 can distinguish renderer-completed
    freshness from model-emitted freshness.
    """
    result = validate(
        f"The minimum SIP is INR 500. Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,)
    )
    assert result.ok
    assert result.freshness_missing
    assert result.repairable is False


def test_a_model_emitted_freshness_line_is_not_reported_as_missing():
    result = validate(
        f"The minimum SIP is INR 500. {FRESHNESS_PREFIX} 1 Jan 2026\nSource: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert not result.freshness_missing


def test_the_freshness_line_does_not_consume_a_sentence():
    """FR-13 allows 3 sentences of content; renderer lines are not content."""
    assert count_sentences(f"One. Two. Three.\n{FRESHNESS_PREFIX} 1 Jan 2026\nSource: {CHUNK_URL}") == 3


# -- FR-9: markers -------------------------------------------------------------


@pytest.mark.parametrize("marker", (NOT_IN_SOURCES, FACTSHEET_REDIRECT))
def test_a_marker_is_detected(marker):
    result = validate(marker, retrieved_urls=(CHUNK_URL,))
    assert result.marker == marker
    assert result.is_marker
    # A marker is compliant, so it never trips a formatting check.
    assert result.ok
    assert result.failed_checks == ()


def test_a_marker_is_matched_exactly_not_as_a_substring():
    """A word-boundary match would fire on an answer *quoting* the contract.

    "Reply exactly NOT_IN_SOURCES if..." is a compliant model behaviour that must
    not be mistaken for the marker itself.
    """
    result = validate(
        "Reply exactly NOT_IN_SOURCES if the context is insufficient.",
        retrieved_urls=(CHUNK_URL,),
    )
    assert result.marker is None


def test_a_normal_answer_has_no_marker():
    assert validate(f"Ratio 1.03%. Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,)).marker is None


def test_markers_carry_no_urls():
    assert urls_in(NOT_IN_SOURCES) == ()
    assert urls_in(FACTSHEET_REDIRECT) == ()


# -- FR-10: citation -----------------------------------------------------------


def test_an_answer_without_a_link_fails_citation():
    result = validate("The expense ratio is 1.03%.", retrieved_urls=(CHUNK_URL,))
    assert not result.ok
    assert "citation" in result.failed_checks
    assert result.repairable, "a missing link is fixable by one rewrite"


def test_a_link_to_a_source_that_was_not_retrieved_fails():
    """This is the rule that forces the model to cite the chunk it was given.

    A plausible but unretrieved URL is the failure mode that would let a
    hallucinated citation survive, so it is treated exactly like a missing one.
    """
    result = validate(
        "The expense ratio is 1.03%. Source: https://example.com/not-retrieved",
        retrieved_urls=(CHUNK_URL,),
    )
    assert not result.ok
    assert "citation" in result.failed_checks


def test_two_links_fail_the_exactly_one_rule():
    result = validate(
        f"Ratio 1.03%. Source: {CHUNK_URL} See also: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert "citation" in result.failed_checks


def test_citation_is_skipped_when_there_is_no_context_to_cite():
    """No retrieved URLs means no set to be a member of, not a guaranteed failure.

    The gate is what guarantees context exists; this module only checks
    membership. Failing here would make the validator reject a context-free
    repair attempt for the wrong reason.
    """
    result = validate("The expense ratio is 1.03%.", retrieved_urls=())
    assert result.ok


def test_a_trailing_period_does_not_break_citation_matching():
    result = validate(f"Ratio 1.03%. Source: {CHUNK_URL}.", retrieved_urls=(CHUNK_URL,))
    assert result.ok, result.failed_checks


# -- FR-11: advice and returns -------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "You should invest in this fund because it performs well.",
        "I recommend buying HDFC Large Cap Fund now.",
        "This is a better option than the others.",
    ],
)
def test_advice_is_rejected(sentence):
    result = validate(f"{sentence} Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,))
    assert not result.ok
    assert "advice" in result.failed_checks
    assert result.advice_hits
    # C-5: advice has no marker, so the pipeline declines safely.
    assert result.marker is None


@pytest.mark.parametrize(
    "sentence",
    [
        "HDFC Large Cap Fund outperformed its benchmark last year.",
        "The fund has returned 18.9% over 5 years.",
        "The CAGR was 14.2%.",
    ],
)
def test_returns_are_rejected(sentence):
    """FR-7/FR-11: the corpus has no verified return figures, so any are invented."""
    result = validate(f"{sentence} Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,))
    assert not result.ok
    assert "returns" in result.failed_checks
    assert result.returns_hits
    # FR-11: a returns statement redirects to the factsheet rather than declining.
    assert result.marker == FACTSHEET_REDIRECT


def test_advice_outranks_returns_when_both_are_present():
    """Both are unsafe, but the user must not be sent to a factsheet instead.

    The advice breach is the compliance failure; a redirect implies the question
    was answerable and merely lives elsewhere.
    """
    result = validate(
        f"You should invest; it outperformed its benchmark. Source: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert result.advice_hits and result.returns_hits
    assert result.marker is None


def test_the_lexicons_cover_the_refusal_set_vocabulary():
    """The words K10 refuses on must also be the words K15 catches.

    If the model slips past the router and states the figure anyway, these two
    lexicons are the last thing between it and the user.
    """
    blob = " ".join(ADVICE_LEXICON + RETURNS_LEXICON).lower()
    for word in ("recommend", "you should", "outperformed", "cagr", "returns of"):
        assert word in blob, f"{word} is neither routed nor validated"


def test_a_factual_answer_is_not_caught_by_the_lexicons():
    """The false-positive direction, which would be a product-breaking bug."""
    result = validate(
        f"The exit load is 0% after 12 months. The minimum SIP is INR 500. "
        f"Source: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert result.ok, result.failed_checks
    assert result.advice_hits == () and result.returns_hits == ()


def test_lexicon_matching_respects_word_boundaries():
    """"request" must not fire "rest", and "allocate" must not fire mid-word."""
    for benign in ("This is a request for information.", "Reallocation is not stated."):
        assert validate(f"{benign} Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,)).ok


# -- the repair ladder ---------------------------------------------------------


def test_length_and_citation_are_repairable():
    assert validate("Short.", retrieved_urls=(CHUNK_URL,)).repairable
    assert validate("One. Two. Three. Four. Source: " + CHUNK_URL,
                    retrieved_urls=(CHUNK_URL,)).repairable


def test_a_too_long_answer_is_rejected():
    result = validate(
        f"The expense ratio is 1.03%. " * 40 + f"Source: {CHUNK_URL}",
        retrieved_urls=(CHUNK_URL,),
    )
    assert not result.ok
    assert "length" in result.failed_checks
    assert result.repairable


def test_lexicon_failures_are_not_repairable_by_rewriting_once():
    """A second attempt at the same prompt is not a safety control.

    These failures mark the path unsafe, so the pipeline must decline or redirect
    rather than spend its one retry on them.
    """
    advice = validate(f"You should invest here. Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,))
    returns = validate(f"It outperformed. Source: {CHUNK_URL}", retrieved_urls=(CHUNK_URL,))
    assert not advice.repairable
    assert not returns.repairable


def test_an_advice_breach_is_not_repairable_even_with_a_citation_miss():
    """The lexicon decision outranks a formatting miss on the same answer.

    Otherwise an answer that was both non-compliant and mis-cited would get a
    retry, and the retry's only job would be to re-derive a less honest answer.
    """
    result = validate("You should invest in this fund.", retrieved_urls=(CHUNK_URL,))
    assert "citation" in result.failed_checks
    assert "advice" in result.failed_checks
    assert not result.repairable


def test_the_repair_instruction_does_not_offer_the_model_a_url():
    """The repair prompt must not tell the model to add a link.

    Model-emitted URLs are stripped at render (FR-9), so an instruction to "add
    the source link" would produce a link that is then removed, converting a
    citation-only answer into a decline for no reason.
    """
    assert "http" not in REPAIR_INSTRUCTION.lower()
    assert "link" not in REPAIR_INSTRUCTION.lower()
    assert "url" not in REPAIR_INSTRUCTION.lower()


def test_the_repair_instruction_names_the_marker_the_model_must_repeat():
    assert NOT_IN_SOURCES in REPAIR_INSTRUCTION


# -- helpers and wiring --------------------------------------------------------


def test_urls_in_finds_every_link():
    assert len(urls_in(f"a {CHUNK_URL} b https://x.test/c")) == 2
    assert urls_in("no links here") == ()


def test_count_sentences_ignores_urls_and_renderer_lines():
    assert count_sentences(f"See {CHUNK_URL} for details.") == 1
    assert count_sentences("A. B. C. D.") == 4
    assert count_sentences("") == 0


def test_an_empty_answer_is_rejected():
    result = validate("", retrieved_urls=(CHUNK_URL,))
    assert not result.ok


def test_the_result_is_immutable():
    result = validate("Short.", retrieved_urls=(CHUNK_URL,))
    assert isinstance(result, ValidationResult)
    with pytest.raises(Exception):
        result.ok = True  # type: ignore[misc]


def test_no_prd_refusal_question_reaches_validation_as_a_question():
    """No refusal-set question may be answered from context.

    The four non-PII ones are caught by the router before validation, so this
    pins that none of them slips through to be treated as a citable answer.
    """
    from src.guardrails.intent import route

    for question in REFUSAL_QUERIES:
        assert route(question).intent.is_terminal, f"{question!r} would reach the LLM"


def test_the_canonical_messages_exist_and_are_distinct():
    assert ADVICE_REFUSAL and PERFORMANCE_REDIRECT
    assert ADVICE_REFUSAL != PERFORMANCE_REDIRECT
    # The marker and the readable message are separate on purpose: the renderer
    # must never show the wire format, but the marker must survive for logs.
    assert NOT_IN_SOURCES_DECLINE != NOT_IN_SOURCES
    assert NOT_IN_SOURCES != FACTSHEET_REDIRECT
