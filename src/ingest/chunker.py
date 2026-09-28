"""K4 — chunking strategies.

Implements the strategy recorded in ``CHUNKING.md``
(``section_atomic_recursive``):

* the whitelisted-facts block is split into **one chunk per fact**, so an
  expense-ratio row, an exit-load slab, a minimum-SIP line, a lock-in statement,
  a riskometer/benchmark line and a statement-guide step are each atomic and can
  never be cut in half;
* every other section becomes one chunk, prefixed with its heading so the text
  is self-describing out of context;
* only sections larger than ``CHUNK_SIZE`` are recursively split, on line
  boundaries, with ``CHUNK_OVERLAP`` carried across the cut.

Size, overlap and strategy id are read from :mod:`src.config` so the values in
``CHUNKING.md`` and the values used here cannot drift apart.
"""

from __future__ import annotations

import re
from typing import List, Optional, Protocol, Sequence, Tuple

from src.config import Settings, load
from src.ingest.models import (
    FACT_SECTION_HEADING,
    Chunk,
    DocumentSection,
    SourceRecord,
)

#: Label prefix in the facts block -> fact type. Longest prefix wins, so
#: "Minimum investment amount" resolves to min_sip and "Expense ratio (base…)"
#: still resolves to expense_ratio.
_FACT_LABEL_TYPES: Tuple[Tuple[str, str], ...] = (
    ("expense ratio", "expense_ratio"),
    ("minimum sip", "min_sip"),
    ("minimum investment", "min_sip"),
    ("exit load note", "exit_load"),
    ("exit load", "exit_load"),
    ("lock-in period", "lock_in"),
    ("lock in period", "lock_in"),
    ("riskometer", "riskometer"),
    ("benchmark", "benchmark"),
)

#: Heading text -> fact type, for the visible page sections.
_HEADING_FACT_TYPES: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"minimum investment", re.I), "min_sip"),
    (re.compile(r"expense ratio", re.I), "expense_ratio"),
    (re.compile(r"^(exit load|stamp duty)\b", re.I), "exit_load"),
    (re.compile(r"lock[- ]?in", re.I), "lock_in"),
    (re.compile(r"riskometer|rated .{0,24}risk", re.I), "riskometer"),
    (re.compile(r"benchmark", re.I), "benchmark"),
    (
        re.compile(
            r"capital gains statement|tax statement|statement download|"
            r"computation of income|how to (?:download|get)",
            re.I,
        ),
        "statement_guide",
    ),
)

#: Body signals, used when a heading is generic.
_BODY_FACT_TYPES: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"^Expense ratio", re.M), "expense_ratio"),
    (re.compile(r"^Minimum (?:SIP|investment)", re.M), "min_sip"),
    (re.compile(r"^Exit load", re.M), "exit_load"),
    (re.compile(r"^Lock-in period:", re.M), "lock_in"),
    (re.compile(r"^Riskometer:", re.M), "riskometer"),
    (re.compile(r"^Benchmark", re.M), "benchmark"),
)


class ChunkStrategy(Protocol):
    """The chunking interface every strategy implements."""

    name: str

    def split(self, doc: SourceRecord) -> List[Chunk]:
        """Split one source document into embeddable chunks.

        Implementations must never cross a scheme boundary and must never split
        a fact block.
        """
        ...


def fact_type_for_label(label: str) -> str:
    """Return the fact type a facts-block label carries."""
    lowered = label.strip().lower()
    for prefix, fact_type in _FACT_LABEL_TYPES:
        if lowered.startswith(prefix):
            return fact_type
    return "general"


def classify_fact_type(heading: str, body: str = "") -> str:
    """Return the fact type of a visible page section.

    Heading match first (most reliable), then a body signal, then ``general``.
    """
    for pattern, fact_type in _HEADING_FACT_TYPES:
        if pattern.search(heading or ""):
            return fact_type
    for pattern, fact_type in _BODY_FACT_TYPES:
        if pattern.search(body or ""):
            return fact_type
    return "general"


def _lines(text: str) -> List[str]:
    return [line for line in text.split("\n") if line.strip()]


def _label_of(line: str) -> str:
    return line.split(":", 1)[0] if ":" in line else line


def split_oversized(
    body: str, chunk_size: int, chunk_overlap: int, min_chars: int
) -> List[str]:
    """Split an oversized body on line boundaries with a carried overlap.

    Line boundaries keep ``Label: value`` rows whole, which is the specific
    failure the PRD risk table warns about: an exit-load slab cut in half reads
    as a different fee.
    """
    rows = _lines(body)
    if not rows:
        return []
    parts: List[str] = []
    current: List[str] = []

    def carry_tail() -> List[str]:
        """Take rows from the end of the current part, up to the overlap budget."""
        tail: List[str] = []
        used = 0
        for row in reversed(current):
            cost = len(row) + (1 if tail else 0)
            if used + cost > chunk_overlap:
                break
            tail.insert(0, row)
            used += cost
        return tail

    for row in rows:
        projected = len("\n".join(current + [row]))
        if current and projected > chunk_size:
            parts.append("\n".join(current))
            current = carry_tail()
        current.append(row)
    if current:
        tail = "\n".join(current)
        if not parts or len(tail) >= min_chars:
            parts.append(tail)
        else:
            parts[-1] = f"{parts[-1]}\n{tail}"
    return parts


class SectionAtomicRecursiveChunker:
    """The ``section_atomic_recursive`` strategy from ``CHUNKING.md``."""

    name = "section_atomic_recursive"

    def __init__(
        self,
        settings: Optional[Settings] = None,
        min_chars: int = 60,
    ) -> None:
        settings = settings or load()
        self.chunk_size = settings.require("chunk_size")
        self.chunk_overlap = settings.require("chunk_overlap")
        strategy = settings.require("chunk_strategy")
        if strategy != self.name:
            raise ValueError(
                f"CHUNK_STRATEGY is {strategy!r} but this class implements {self.name!r}. "
                "Change CHUNKING.md and the implementation together."
            )
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be smaller than "
                f"CHUNK_SIZE ({self.chunk_size})"
            )
        self.min_chars = min_chars

    # -- public API ---------------------------------------------------------

    def split(self, doc: SourceRecord) -> List[Chunk]:
        """Split one document. Never crosses the scheme boundary, never splits a fact."""
        chunks: List[Chunk] = []
        page: List[Chunk] = []

        for section in doc.sections:
            if section.is_empty():
                continue
            if section.heading == FACT_SECTION_HEADING:
                chunks.extend(self._split_facts_block(doc, section))
            else:
                page.extend(self._split_section(doc, section))

        chunks.extend(self._merge_undersized(page))
        for chunk in chunks:
            chunk.validate()
        return chunks

    # -- internals ----------------------------------------------------------

    def _metadata(
        self,
        doc: SourceRecord,
        section: DocumentSection,
        fact_type: str,
        authority: str,
        text: str,
        is_atomic: bool,
    ) -> dict:
        return {
            "source_url": doc.url,
            "scheme": doc.scheme,
            "category": doc.category,
            "plan_variant": doc.plan_variant,
            "section": section.heading,
            "fact_type": fact_type,
            "source_fetched_at": doc.fetched_at.isoformat(timespec="seconds"),
            "content_hash": doc.content_hash,
            "authority": authority,
            "is_atomic": "true" if is_atomic else "false",
            "char_len": str(len(text)),
        }

    def _chunk(
        self,
        doc: SourceRecord,
        section: DocumentSection,
        part: int,
        text: str,
        fact_type: str,
        authority: str,
        is_atomic: bool,
    ) -> Chunk:
        text = text.strip()
        if not text:
            raise ValueError(
                f"refusing to emit an empty chunk for section {section.heading!r} "
                f"of {doc.source_id}"
            )
        return Chunk(
            chunk_id=f"{doc.source_id}::{section.ordinal}::{part}",
            text=text,
            metadata=self._metadata(doc, section, fact_type, authority, text, is_atomic),
        )

    def _split_facts_block(self, doc: SourceRecord, section: DocumentSection) -> List[Chunk]:
        """One chunk per fact line; the remaining identity lines share one chunk.

        The block is a list of ``Label: value`` rows, so each row is already an
        atomic fact. Grouping the unrecognised identity rows (ISIN, custodian,
        plan, AMC…) into a single ``general`` chunk avoids a dozen tiny chunks
        while keeping every recognised fact individually addressable, which is
        what the intent router and relevance gate rely on.
        """
        chunks: List[Chunk] = []
        general: List[str] = []
        history: List[str] = []
        part = 0

        for line in _lines(section.text):
            fact_type = fact_type_for_label(_label_of(line))
            if fact_type == "general":
                general.append(line)
                continue
            if _label_of(line).strip().lower().startswith("exit load note"):
                history.append(line)
                continue
            chunks.append(
                self._chunk(
                    doc, section, part, line, fact_type, "scheme_facts", True
                )
            )
            part += 1

        if history:
            chunks.append(
                self._chunk(
                    doc,
                    section,
                    part,
                    "\n".join(history),
                    "exit_load",
                    "scheme_facts",
                    True,
                )
            )
            part += 1
        if general:
            body = "\n".join(general)
            if len(body) <= self.chunk_size:
                chunks.append(
                    self._chunk(doc, section, part, body, "general", "scheme_facts", True)
                )
                part += 1
            else:
                for index, piece in enumerate(
                    split_oversized(body, self.chunk_size, self.chunk_overlap, self.min_chars)
                ):
                    chunks.append(
                        self._chunk(
                            doc, section, part, piece, "general", "scheme_facts", False
                        )
                    )
                    part += 1
        return chunks

    def _split_section(self, doc: SourceRecord, section: DocumentSection) -> List[Chunk]:
        """One chunk for a small section; recursive line split for a large one."""
        fact_type = classify_fact_type(section.heading, section.text)
        prefix = f"{section.heading}\n"
        headed = f"{prefix}{section.text}".strip()

        if len(headed) <= self.chunk_size:
            return [self._chunk(doc, section, 0, headed, fact_type, "page_text", True)]

        # The heading is repeated on every part, so it comes out of the budget.
        budget = self.chunk_size - len(prefix)
        if budget < self.chunk_overlap + self.min_chars:
            raise ValueError(
                f"heading {section.heading!r} leaves only {budget} chars of a "
                f"{self.chunk_size}-char chunk; raise CHUNK_SIZE"
            )
        chunks: List[Chunk] = []
        for part, piece in enumerate(
            split_oversized(section.text, budget, self.chunk_overlap, self.min_chars)
        ):
            text = f"{prefix}{piece}".strip()
            chunks.append(
                self._chunk(doc, section, part, text, fact_type, "page_text", False)
            )
        return chunks

    def _merge_undersized(self, chunks: Sequence[Chunk]) -> List[Chunk]:
        """Fold a chunk shorter than ``min_chars`` into the chunk before it.

        Only whole chunks are ever combined, so this cannot split a fact block.
        The merged chunk keeps the earlier chunk's id so ids stay deterministic,
        and records both headings in ``section``.
        """
        merged: List[Chunk] = []
        for chunk in chunks:
            if (
                merged
                and len(chunk.text) < self.min_chars
                and merged[-1].metadata["source_url"] == chunk.metadata["source_url"]
            ):
                previous = merged[-1]
                text = f"{previous.text}\n{chunk.text}"
                section = " | ".join(
                    dict.fromkeys(
                        [previous.metadata["section"], chunk.metadata["section"]]
                    )
                )
                metadata = dict(previous.metadata)
                metadata["section"] = section
                metadata["char_len"] = str(len(text))
                merged[-1] = Chunk(
                    chunk_id=previous.chunk_id, text=text, metadata=metadata
                )
                continue
            merged.append(chunk)
        return merged


def build_chunker(settings: Optional[Settings] = None) -> ChunkStrategy:
    """Return the strategy named by ``CHUNK_STRATEGY``.

    Reads the id from config rather than hardcoding it, so switching strategies
    is a configuration change with a clear error if the class is missing.
    """
    settings = settings or load()
    strategy = settings.require("chunk_strategy")
    if strategy == SectionAtomicRecursiveChunker.name:
        return SectionAtomicRecursiveChunker(settings)
    raise ValueError(
        f"CHUNK_STRATEGY={strategy!r} has no implementation in src/ingest/chunker.py. "
        "Add it there, or set CHUNK_STRATEGY to "
        f"{SectionAtomicRecursiveChunker.name!r}."
    )
