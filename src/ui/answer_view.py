"""K16 — the answer view.

A render function, not an app (architecture.md §5): the UI layer stays thin so
the same pipeline and the same renderer serve the chat app, ``eval.py`` and the
notebook deliverable (AD-5).

The citation is rendered as its own line, separate from the answer text. That
keeps the "exactly one link" property of E-1 checkable by counting links in the
rendered block, rather than trusting the model's prose.

Phase 5 changed what can arrive here. ``AnswerResponse.path`` now distinguishes
an answer from the five non-answer paths of architecture.md §8.1, and each gets
its own readable rendering:

* ``answer`` — the only path that carries a citation.
* ``pii`` / ``advice`` / ``performance`` / ``ambiguous`` / ``decline`` /
  ``gate_miss`` / ``validator_decline`` / ``llm_error`` — a message, plus the
  link the relevant refusal is required to carry (FR-10, FR-11).

A marker never reaches the UI as a bare token: the pipeline already converts
``NOT_IN_SOURCES`` and ``FACTSHEET_REDIRECT`` into their user-facing messages, so
rendering the token itself would leak the wire format into the product.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from src.guardrails.messages import DISCLAIMER, FRESHNESS_PREFIX
from src.query.pipeline import AnswerResponse

_URL_RE = re.compile(r"https?://\S+")
#: A markdown link whose target is a URL. Removed whole, before the bare-URL pass,
#: so a model that writes ``[Source](https://...)`` does not leave ``[Source](``
#: behind in the prose.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*https?://[^)]*\)")
#: The model's own citation label. Hard rule 3 tells it to end with the source
#: link and models frequently label that link "Source:", so removing the URL
#: alone used to strand the label and the answer rendered as
#: "Source: Source: <url>". Once the URL is gone, any label still in the text is
#: by definition orphaned, and ``render_answer`` supplies the authoritative one
#: from chunk metadata (AD-3), so stripping it here cannot lose a citation.
_ORPHAN_LABEL_RE = re.compile(
    r"(?i)(?:\*\*|__)?[ \t]*sources?[ \t]*(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?"
)


def strip_urls(text: str) -> str:
    """Remove URLs and their orphaned labels from model text.

    Rule 3 of the system prompt tells the model to end with the source link, and
    the renderer separately recomputes the citation from chunk metadata
    (architecture.md §8.5, AD-3). Keeping both would render two links and fail
    E-1, so the model's copy is dropped and the metadata one is authoritative.
    This is also what makes a hallucinated URL impossible to display: it is
    stripped here, not merely ignored.

    The label has to go with the link, not just the link itself: E-1 counts URLs
    rather than labels, so a stranded "Source:" passed every check while reading
    as a duplicated citation to the user.
    """
    cleaned = _MD_LINK_RE.sub("", text or "")
    cleaned = _URL_RE.sub("", cleaned)
    cleaned = _ORPHAN_LABEL_RE.sub("", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def render_answer(response: AnswerResponse) -> str:
    """Render an answer, or the readable message for any non-answer path."""
    if response.is_refusal:
        # A non-answer carries its own links, already composed by
        # guardrails.messages with the right label ("Learn more" vs "Official
        # factsheet"), and never a citation.
        return "\n".join(part for part in (response.text, response.last_updated) if part)

    parts = [strip_urls(response.text)]
    if response.citation_url:
        parts.append(f"Source: {response.citation_url}")
    else:
        # A factual answer with no link would fail E-1. Say so rather than
        # rendering a bare answer; the gate should have made this unreachable.
        parts.append("Source: [citation unavailable]")
    parts.append(response.last_updated)
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class BlockParts:
    """The answer body, its citation and its freshness line, kept apart."""

    answer: str
    citation_url: str
    freshness: str
    is_refusal: bool = False


def split_block(response: AnswerResponse) -> BlockParts:
    """Separate an answer's body, citation and freshness line.

    :func:`render_answer` joins them into one string, which is right for ``eval.py``
    and the notebook deliverable and wrong for a chat card: the product needs the
    answer in a reading pane and the citation and date in a muted metadata row.
    Rather than parse that string heuristically, this recomposes the three parts
    from the same fields :func:`render_answer` reads, using the same
    :func:`strip_urls` pass, so the two renderers cannot disagree.

    The citation is ``response.citation_url`` unchanged. A refusal is passed
    through as ``is_refusal`` with no citation, because its link is a different
    kind of thing: an educational resource, or an official factsheet (FR-10,
    FR-11), not a citation. E-1 still counts exactly one link on the answer path.
    """
    return BlockParts(
        answer=strip_urls(response.text).strip(),
        citation_url="" if response.is_refusal else (response.citation_url or ""),
        freshness=(response.last_updated or "").strip(),
        is_refusal=response.is_refusal,
    )


def source_label(url: str) -> str:
    """A short, readable name for a citation's host, for the UI only.

    "https://groww.in/mutual-funds/hdfc-small-cap" becomes "Groww" so the card can
    read "Source: Groww" instead of printing a long raw URL. This is derived from
    the citation the pipeline already chose, so it cannot introduce a source the
    registry does not allow — the link still points at the exact ``url``.

    Falls back to the host as-is when it cannot be prettified, and never returns an
    empty string, because a citation with no visible name is worse than an ugly one.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "Source page"
    label = host.split(".")[0]
    return label.capitalize() if label else host


def render_block(response: AnswerResponse) -> str:
    """The full user-facing block: disclaimer, answer, freshness line."""
    return f"{DISCLAIMER}\n\n{render_answer(response)}"


def count_links(text: str) -> int:
    """Count URLs in a rendered block, for the E-1 check."""
    return len(_URL_RE.findall(text or ""))


def freshness_line(value: str) -> str:
    """Normalise a freshness value to carry the mandated prefix exactly once."""
    text = (value or "").strip()
    if text.startswith(FRESHNESS_PREFIX):
        return text
    return f"{FRESHNESS_PREFIX}{text}"
