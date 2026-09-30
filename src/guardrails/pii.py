"""K9 — the PII screen (C-2, FR-12, E-5).

Runs at step 0, before embedding, before logging, before any persistence
(architecture.md §8.1). On a hit the caller returns a refusal and *drops the
text*: the question is never embedded, never written to ``logs/``, never stored
(NFR-5, E-5). That ordering is the entire security property — a screen that ran
after retrieval would already have persisted the text to embed.

Detection is deterministic regex, never an LLM. Two reasons, in order: a
detector that can be talked into a false negative is not a control, and
architecture.md §8.3 forbids an LLM from ever handling PII.

The numeric classes — Aadhaar, account number, OTP, phone — additionally
require a **context keyword** in the same message. Without that requirement the
screen is useless in this domain rather than merely noisy: a question about
facts is full of bare numbers, and "What is the minimum SIP amount?" would be
refused for containing 500. The keyword requirement is what separates a
*financial figure* from an *identifier*, and
``tests/test_pii.py::test_a_minimum_sip_question_is_not_pii`` pins that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Pattern, Sequence, Tuple


class PIIScreenError(ValueError):
    """Raised when a detection pattern is malformed."""


@dataclass(frozen=True)
class PIIHit:
    """One detected PII class, and where it was found.

    Carries the *class* and the character span, never the matched value. A hit
    object ends up in validator verdicts and test output, and a log line that
    echoed the PAN would defeat the screen (NFR-5). ``redacted`` is what may
    safely be written anywhere.
    """

    #: One of :data:`PII_CLASSES`.
    pii_class: str
    #: The keyword or pattern label that matched, for auditability.
    matcher: str
    start: int
    end: int

    @property
    def redacted(self) -> str:
        """A safe-to-log description carrying no PII value."""
        return f"{self.pii_class} via {self.matcher}"


#: The six classes of architecture.md §8.2, in detection-priority order.
#: PAN leads because it is the most specific pattern, and Aadhaar precedes the
#: account-number class because a 12-digit Aadhaar is also a valid 8-18 digit
#: account number: without this order every Aadhaar would be reported as an
#: account number and the refusal would name the wrong class.
PII_CLASSES: Tuple[str, ...] = (
    "pan",
    "aadhaar",
    "account_number",
    "otp",
    "email",
    "phone",
)


@dataclass(frozen=True)
class _Rule:
    """One class's pattern plus the keywords that must accompany a bare number.

    ``context_keywords`` is empty for the pattern classes (PAN, email), which
    are unambiguous on their own: `[A-Z]{5}[0-9]{4}[A-Z]` cannot occur by
    accident in a mutual-fund fact, and an address is not a fact.
    """

    pii_class: str
    pattern: Pattern[str]
    context_keywords: Sequence[str]
    label: str


def _compile(pattern: str, label: str) -> Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:  # pragma: no cover - guards a typo in the table
        raise PIIScreenError(f"bad PII pattern {label!r}: {exc}") from exc


#: The auditable table. architecture.md §8.2 asks for one home for the rules so
#: a reviewer can read the whole screen at once; this is it.
#:
#: Phone and email are pattern-only even though they are "numeric" in the
#: §8.2 table, because the table lists their detection as a pattern ("standard
#: address pattern", "+91… or 10-digit Indian mobile pattern") and a bare
#: 10-digit phone pattern is specific enough not to collide with an amount. The
#: context-keyword requirement is reserved for Aadhaar, account number and OTP,
#: whose patterns genuinely are ambiguous against financial figures.
RULES: Tuple[_Rule, ...] = (
    _Rule(
        pii_class="pan",
        pattern=_compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "pan"),
        context_keywords=(),
        label="pan_pattern",
    ),
    _Rule(
        pii_class="aadhaar",
        # 12 digits, optionally grouped in 4s or 3-4-5. Separators are stripped
        # before the check so "1234 5678 9012" and "1234-5678-9012" both hit.
        pattern=_compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b|\b\d{3}[\s-]?\d{4}[\s-]?\d{5}\b", "aadhaar_digits"),
        context_keywords=("aadhaar", "aadhar", "uidai", "aadhaar number", "aadhar number"),
        label="aadhaar_12_digit",
    ),
    _Rule(
        pii_class="account_number",
        pattern=_compile(r"\b\d{8,18}\b", "account_digits"),
        context_keywords=(
            "account number",
            "account no",
            "account no.",
            "acct",
            "folio",
            "folio number",
            "holder",
            "demat",
            "dp id",
        ),
        label="account_8_18_digit",
    ),
    _Rule(
        pii_class="otp",
        pattern=_compile(r"\b\d{4,6}\b", "otp_digits"),
        context_keywords=("otp", "one time password", "verification code", "verify code", "passcode"),
        label="otp_4_6_digit",
    ),
    _Rule(
        pii_class="email",
        pattern=_compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "email"),
        context_keywords=(),
        label="email_address",
    ),
    _Rule(
        pii_class="phone",
        # +91 with optional separators, or a bare 10-digit Indian mobile that
        # starts 6-9. The leading-digit constraint is what keeps this from
        # matching a 10-digit account number or a fund figure.
        pattern=_compile(r"(?:\+?91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b", "phone"),
        context_keywords=(),
        label="phone_indian_mobile",
    ),
)


def _digits_only(text: str) -> str:
    return re.sub(r"[\s-]", "", text or "")


def _has_context(text: str, keywords: Sequence[str]) -> Optional[str]:
    """Return the first matching context keyword, or ``None``.

    Matching is substring-based on a whitespace-collapsed, lowercased copy so
    "Aadhaar" and "aadhaar number" both match, and so a keyword split across a
    line break still matches.
    """
    haystack = " ".join((text or "").lower().split())
    for keyword in keywords:
        if keyword.lower() in haystack:
            return keyword
    return None


# -- identifier *requests* ----------------------------------------------------

#: PRD §15 requires "What is my account number / OTP?" to be refused, and that
#: sentence contains no identifier to pattern-match: it asks for one. Hard rule 6
#: of the system prompt is "never *request* or repeat personal identifiers", so a
#: request is the same privacy event as a disclosure and is screened at the same
#: place, before the text is ever embedded.
#:
#: A request needs *both* an identifier noun and a request verb, so the rule
#: cannot fire on a factual question that merely mentions one: "What is the
#: minimum SIP amount?" asks for something and names nothing sensitive, and
#: "The OTP page is in my account" neither asks nor names an identifier in the
#: request sense.
REQUEST_VERBS: Tuple[str, ...] = (
    "what is",
    "what's",
    "what are",
    "give me",
    "share",
    "send",
    "tell me",
    "provide",
    "email me",
    "call me",
    "link it",
    "save it",
    "update my",
    "verify my",
    "help me find",
    "where is",
    "do you have my",
    "can you see my",
    "need my",
)

#: Identifier nouns, grouped by the class they imply.
IDENTIFIER_NOUNS: Dict[str, Tuple[str, ...]] = {
    "pan": ("pan", "pan number", "pan card", "permanent account number"),
    "aadhaar": ("aadhaar", "aadhar", "uidai", "aadhaar number", "aadhar number"),
    "account_number": (
        "account number",
        "account no",
        "account no.",
        "acct number",
        "folio number",
        "folio",
        "demat id",
        "dp id",
    ),
    "otp": ("otp", "one time password", "verification code", "verify code", "passcode"),
    "email": ("email id", "email address", "my email", "e-mail"),
    "phone": ("phone number", "mobile number", "my phone", "contact number"),
}


def _detect_request(text: str) -> Optional[PIIHit]:
    """A request for a personal identifier, even with no identifier present."""
    haystack = " ".join((text or "").lower().split())
    verb = _matches_word(haystack, REQUEST_VERBS)
    if verb is None:
        return None
    for pii_class, nouns in IDENTIFIER_NOUNS.items():
        noun = _matches_word(haystack, nouns)
        if noun is not None:
            return PIIHit(
                pii_class=pii_class,
                matcher=f"request:{noun}",
                start=0,
                end=len(text or ""),
            )
    return None


def _matches_word(haystack: str, phrases: Sequence[str]) -> Optional[str]:
    """First phrase present in ``haystack`` on a whole-word boundary.

    Same boundary rule as the intent router: hyphen is a word boundary, which
    is why "capital-gains" matches "gains" but "aadhaar" does not match inside
    "aadhaarcard".
    """
    for phrase in phrases:
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        if re.search(pattern, haystack):
            return phrase
    return None


def _detect_by_pattern(text: str) -> Optional[PIIHit]:
    """First PII class matched by its value pattern, in class-priority order."""
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            if rule.context_keywords:
                # A numeric class only fires when the message also names what
                # the number is. This is the false-positive guard.
                if not _has_context(text, rule.context_keywords):
                    continue
                # A bare digit run that only looks long because of separators is
                # still a digit run; require the separator-stripped length to be
                # in the class's own range.
                stripped = _digits_only(match.group(0))
                if rule.pii_class == "aadhaar" and len(stripped) != 12:
                    continue
                if rule.pii_class == "account_number" and not 8 <= len(stripped) <= 18:
                    continue
                if rule.pii_class == "otp" and not 4 <= len(stripped) <= 6:
                    continue
            return PIIHit(
                pii_class=rule.pii_class,
                matcher=rule.label,
                start=match.start(),
                end=match.end(),
            )
    return None


def screen(text: str) -> Optional[PIIHit]:
    """Return the first :class:`PIIHit` in ``text``, or ``None``.

    First by *class priority* (:data:`PII_CLASSES` via :data:`RULES`), not by
    position in the string, so a message carrying both a PAN and an email is
    reported as a PAN: the PAN is the class whose handling matters most, and
    the refusal names one class rather than leaking a second value.

    A value pattern is tried first and an identifier *request* second, so
    "my PAN is ABCDE1234F" is reported as a PAN rather than as a request for
    one.
    """
    if not text or not text.strip():
        return None
    return _detect_by_pattern(text) or _detect_request(text)


def contains_pii(text: str) -> bool:
    """Convenience predicate for callers that only need the yes/no."""
    return screen(text) is not None


def detected_classes(text: str) -> Tuple[str, ...]:
    """Every class present, not just the first.

    Used by the test that asserts all six classes are detected, and useful in
    an eval report where "which classes does this test set exercise" is the
    question being asked.
    """
    found = []
    for rule in RULES:
        for match in rule.pattern.finditer(text or ""):
            if rule.context_keywords:
                if not _has_context(text, rule.context_keywords):
                    continue
                stripped = _digits_only(match.group(0))
                if rule.pii_class == "aadhaar" and len(stripped) != 12:
                    continue
                if rule.pii_class == "account_number" and not 8 <= len(stripped) <= 18:
                    continue
                if rule.pii_class == "otp" and not 4 <= len(stripped) <= 6:
                    continue
            found.append(rule.pii_class)
            break
    request = _detect_request(text or "")
    if request is not None and request.pii_class not in found:
        found.append(request.pii_class)
    return tuple(found)
