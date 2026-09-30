"""Guardrails: the Phase 5 layer that makes the PRD's constraints structural.

architecture.md §8.1 places these at steps 0, 1, 4 and 7 of Stage B:

* :mod:`src.guardrails.pii` — K9, the PII screen, before anything is stored.
* :mod:`src.guardrails.intent` — K10, the intent router.
* :mod:`src.rag.relevance_gate` — K12 (lives under ``rag`` beside the other
  retrieval concerns, per the file list in implementation.md §7).
* :mod:`src.guardrails.validator` — K15, the answer validator.
* :mod:`src.guardrails.messages` — every user-facing string, deliverable D-5.
"""

from src.guardrails.intent import Intent, IntentResult, route
from src.guardrails.messages import (
    ADVICE_REFUSAL,
    DISCLAIMER,
    FRESHNESS_PREFIX,
    NOT_IN_SOURCES_DECLINE,
    PERFORMANCE_REDIRECT,
    PII_REFUSAL,
)
from src.guardrails.pii import PIIHit, screen
from src.guardrails.validator import ValidationResult, validate
from src.rag.relevance_gate import GateResult
from src.rag.relevance_gate import evaluate as evaluate_relevance

__all__ = [
    "ADVICE_REFUSAL",
    "DISCLAIMER",
    "FRESHNESS_PREFIX",
    "GateResult",
    "Intent",
    "IntentResult",
    "NOT_IN_SOURCES_DECLINE",
    "PERFORMANCE_REDIRECT",
    "PIIHit",
    "PII_REFUSAL",
    "ValidationResult",
    "evaluate_relevance",
    "route",
    "screen",
    "validate",
]
