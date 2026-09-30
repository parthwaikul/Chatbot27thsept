"""K9 — PII screen tests (implementation.md Phase 5 task 7, first clause).

Two obligations:

* all six classes of architecture.md §8.2 are detected;
* a minimum-SIP question is **not** flagged.

The second is the one that matters in this domain. A fact assistant is asked
about numbers, so a screen that flags bare numbers refuses the entire product.
"""

from __future__ import annotations

import pytest

from src.eval.cases import PII_QUERIES, REFUSAL_QUERIES
from src.guardrails.pii import (
    PII_CLASSES,
    PIIHit,
    contains_pii,
    detected_classes,
    screen,
)


# -- the false-positive guard, first because it is the one that can ship a bug --


def test_a_minimum_sip_question_is_not_pii():
    """The documented regression check, verbatim from implementation.md §7 Verify.

    A bare "500" is a financial figure, not an identifier. The numeric classes
    require a context keyword precisely so this stays answerable.
    """
    assert screen("What is the minimum SIP amount?") is None
    assert screen("What is the minimum SIP amount for HDFC ELSS Tax Saver Fund?") is None
    assert screen("What is the minimum SIP investment (INR): 500?") is None


@pytest.mark.parametrize(
    "question",
    [
        "What is the exit load on HDFC Balanced Advantage Fund Direct Growth?",
        "The lock-in period is 3 years.",
        "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
        "What benchmark does HDFC Equity Fund Direct Growth track?",
        "How many days does the exit load apply for?",
        "What is the AUM of HDFC Small Cap Fund?",
    ],
)
def test_factual_questions_carrying_numbers_are_not_pii(question):
    """Numbers that are facts, not identifiers, stay answerable."""
    assert screen(question) is None, f"false positive on a factual question: {question!r}"


# -- all six classes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("My PAN is ABCDE1234F, please save it", "pan"),
        ("Update my Aadhaar number 1234 5678 9012", "aadhaar"),
        ("What is my account number 50100234567890?", "account_number"),
        ("Share the OTP 448291 with me", "otp"),
        ("Email me the statement at ravi.sharma@example.com", "email"),
        ("Call me on my registered phone number +91 98765 43210 about my folio", "phone"),
    ],
)
def test_each_pii_class_is_detected(text, expected):
    hit = screen(text)
    assert hit is not None, f"not detected: {text!r}"
    assert hit.pii_class == expected


def test_all_six_classes_are_exercised_by_the_pii_query_set():
    """E-5: the eval set must cover every class, not just a few."""
    found = set()
    for query in PII_QUERIES:
        found.update(detected_classes(query))
    assert found == set(PII_CLASSES), f"uncovered classes: {set(PII_CLASSES) - found}"


def test_every_pii_query_is_detected():
    for query in PII_QUERIES:
        assert contains_pii(query), f"missed a PII case: {query!r}"


def test_identifier_requests_are_refused_even_with_no_identifier_present():
    """PRD §15 lists "What is my account number / OTP?" in the refusal set.

    That sentence contains no identifier to match, so a pattern-only screen would
    let it through. Asking for an identifier is the same privacy event as
    disclosing one, and hard rule 6 forbids both.
    """
    assert screen("What is my account number / OTP?") is not None
    assert screen("Send the statement to my email id") is not None
    assert screen("What is the OTP on my phone?") is not None


# -- class priority and non-disclosure ----------------------------------------


def test_aadhaar_wins_over_account_number():
    """A 12-digit Aadhaar is also a valid 8-18 digit account number.

    Ordering by first match in the string would report whichever the regex found
    first, so a refusal could name the wrong class. Class priority fixes it.
    """
    hit = screen("Update my Aadhaar number 1234 5678 9012")
    assert hit is not None and hit.pii_class == "aadhaar"


def test_a_value_hit_wins_over_a_request_hit():
    hit = screen("My PAN is ABCDE1234F — link it to my account")
    assert hit is not None and hit.pii_class == "pan"
    assert "pan_pattern" in hit.matcher, "a disclosed value is not a request"


def test_a_hit_never_carries_the_pii_value():
    """NFR-5: a hit object reaches logs and test output, so it must be safe."""
    hit = screen("My PAN is ABCDE1234F, please save it")
    assert hit is not None
    redacted = hit.redacted
    assert "ABCDE1234F" not in redacted
    assert hit.pii_class in redacted


def test_the_pii_refusal_never_echoes_the_question():
    """The pipeline's PII response must not contain the screened text."""
    from src.guardrails.messages import PII_REFUSAL

    assert "ABCDE1234F" not in PII_REFUSAL
    assert "50100234567890" not in PII_REFUSAL


# -- PENDING / degenerate inputs ----------------------------------------------


def test_empty_and_whitespace_are_not_pii():
    assert screen("") is None
    assert screen("   \n  ") is None


def test_a_bare_digit_run_with_no_identifier_word_is_not_pii():
    """The context keyword is the whole mechanism; without it there is no hit."""
    assert screen("12345678") is None
    assert screen("123456789012") is None


def test_a_hit_is_immutable():
    hit = screen("My PAN is ABCDE1234F")
    assert isinstance(hit, PIIHit)
    with pytest.raises(Exception):
        hit.pii_class = "email"  # type: ignore[misc]


def test_prd_refusal_set_pii_cases_are_caught():
    """The two PII sentences of the PRD §15 refusal set are screened."""
    pii_cases = [q for q in REFUSAL_QUERIES if contains_pii(q)]
    assert len(pii_cases) == 2, f"expected 2 PII cases in the refusal set, got {pii_cases}"
