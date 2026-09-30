"""K13 — prompt assembly.

The system prompt is the seven hard rules from architecture.md §8.5, verbatim in
substance and in the two marker strings. The markers matter more than they look:
P5's validator routes on them, so ``NOT_IN_SOURCES`` and ``FACTSHEET_REDIRECT``
are a contract between this module and the next one, not decoration.

The citation is the other contract that matters. :func:`citation_for` reads the
URL out of *chunk metadata* and never out of model text (AD-3), so a hallucinated
or truncated URL in the model's answer cannot reach the UI (FR-9, E-1, NFR-6).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

#: Exact marker the model must emit when CONTEXT cannot answer the question (C-6).
NOT_IN_SOURCES = "NOT_IN_SOURCES"

#: Exact marker for a performance/returns question, which must be redirected to
#: the official factsheet rather than computed or compared (C-3, FR-11).
FACTSHEET_REDIRECT = "FACTSHEET_REDIRECT"

#: The seven hard rules, architecture.md §8.5. Kept as a tuple so a test can
#: assert all seven survive any future edit to the rendered string.
HARD_RULES: Sequence[str] = (
    "Use only facts present in CONTEXT. If CONTEXT does not contain the answer, "
    f"reply exactly: {NOT_IN_SOURCES}",
    "Answer in at most 3 sentences. No preamble, no closing pleasantries.",
    "End with exactly one source link, copied verbatim from the chunk's source_url. "
    "Never invent, shorten, or modify a URL.",
    "Never give advice. No buy/sell calls, no suitability opinions, no "
    '"should I", "better", "best for you" language.',
    "Never state, compute, compare, or rank returns or performance. If the question "
    f"asks for performance, reply exactly: {FACTSHEET_REDIRECT}",
    "Never request or repeat personal identifiers (PAN, Aadhaar, account number, "
    "OTP, email, phone).",
    "Do not use outside knowledge, even for facts you believe are true.",
)

SYSTEM_PROMPT = """You are a mutual fund FAQ assistant. You answer factual questions about the
schemes listed in the CONTEXT, using only that context.

Hard rules:
1. """ + HARD_RULES[0] + """
2. """ + HARD_RULES[1] + """
3. """ + HARD_RULES[2] + """
4. """ + HARD_RULES[3] + """
5. """ + HARD_RULES[4] + """
6. """ + HARD_RULES[5] + """
7. """ + HARD_RULES[6] + """\
"""


class PromptError(ValueError):
    """Raised when context chunks cannot produce a valid prompt."""


def _require_url(chunk) -> str:
    """Return a chunk's source_url, refusing to build a prompt without one.

    A context chunk with no URL would let the model reach for a link it has no
    authority to cite, and would break FR-9 downstream. Better to fail here.
    """
    url = str(getattr(chunk, "source_url", "") or "").strip()
    if not url:
        chunk_id = getattr(chunk, "chunk_id", "<unknown>")
        raise PromptError(
            f"context chunk {chunk_id!r} has no source_url; refusing to build a "
            "prompt that could not be cited (C-1, FR-9)"
        )
    return url


def _meta(chunk, key: str, default: str = "unknown") -> str:
    value = getattr(chunk, "metadata", None) or {}
    return str(value.get(key, default) or default)


def build_user_message(chunks: Iterable, question: str) -> str:
    """Render numbered context chunks, then the question.

    Each chunk carries ``source_url``, ``scheme``, ``section`` and ``fact_type``
    so the model can attribute a fact to a source without guessing, and so the
    validator can check the emitted link against a known set.
    """
    question = (question or "").strip()
    if not question:
        raise PromptError("question must not be empty")

    materialised: List = list(chunks)
    if not materialised:
        raise PromptError("cannot build a user message with no context chunks")

    lines: List[str] = ["CONTEXT:"]
    for index, chunk in enumerate(materialised, start=1):
        lines.append(f"\n[{index}]")
        lines.append(f"source_url: {_require_url(chunk)}")
        lines.append(f"scheme: {_meta(chunk, 'scheme')}")
        lines.append(f"section: {_meta(chunk, 'section')}")
        lines.append(f"fact_type: {_meta(chunk, 'fact_type', 'general')}")
        text = str(getattr(chunk, "text", "") or "").strip()
        lines.append(f"text: {text}")
    lines.append(f"\nQUESTION: {question}")
    return "\n".join(lines)


def citation_for(chunks: Sequence, prefer_index: int = 0) -> Optional[str]:
    """The single citation URL, taken from chunk metadata (AD-3).

    ``prefer_index`` picks which chunk supplies the link; the answer pipeline
    passes 0 so the top-ranked chunk wins. Falls back to the first chunk that has
    any URL, so a missing URL on the top chunk degrades to a weaker citation
    rather than no citation at all.
    """
    materialised = list(chunks)
    if not materialised:
        return None
    if 0 <= prefer_index < len(materialised):
        url = str(getattr(materialised[prefer_index], "source_url", "") or "").strip()
        if url:
            return url
    for chunk in materialised:
        url = str(getattr(chunk, "source_url", "") or "").strip()
        if url:
            return url
    return None


def is_marker(text: str, marker: str) -> bool:
    """True when the model emitted exactly ``marker`` and nothing else.

    Exact-match rather than substring: an answer that *discusses* declining is
    not a decline, and P5 routes on the bare token.
    """
    return (text or "").strip() == marker
