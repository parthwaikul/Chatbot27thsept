"""Data contracts for the ingestion stage.

These dataclasses are the boundary between the loader, the chunker, the
embedding service and the vector store. Field names and semantics follow
architecture.md Section 6.1 (SourceRecord) and Section 6.2 (Chunk).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

FACT_TYPES: Tuple[str, ...] = (
    "expense_ratio",
    "exit_load",
    "min_sip",
    "lock_in",
    "riskometer",
    "benchmark",
    "statement_guide",
    "general",
)

REQUIRED_CHUNK_METADATA: Tuple[str, ...] = (
    "source_url",
    "scheme",
    "category",
    "plan_variant",
    "section",
    "fact_type",
    "source_fetched_at",
    "content_hash",
)

CATEGORIES: Tuple[str, ...] = (
    "large_cap",
    "flexi_cap",
    "elss",
    "small_cap",
    "balanced_advantage",
    "general",
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalise_text(text: str) -> str:
    """Normalise whitespace and strip control characters for stable hashing."""
    cleaned = _CONTROL_CHARS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = "\n".join(_WHITESPACE_RUN.sub(" ", line).strip() for line in cleaned.split("\n"))
    return _BLANK_LINES.sub("\n\n", cleaned).strip()


@dataclass(frozen=True)
class DocumentSection:
    """One heading-delimited block of a source document."""

    heading: str
    text: str
    ordinal: int

    def is_empty(self) -> bool:
        return len(self.text.strip()) == 0


@dataclass(frozen=True)
class SourceRecord:
    """A fetched public page reduced to clean text plus its audit metadata."""

    source_id: str
    url: str
    scheme: str
    category: str
    plan_variant: str
    text: str
    fetched_at: datetime
    content_hash: str
    sections: Tuple[DocumentSection, ...]
    http_status: int
    text_chars: int

    def to_csv_row(self) -> Dict[str, Any]:
        """Return the row written to ``corpus/sources.csv``."""
        return {
            "source_id": self.source_id,
            "url": self.url,
            "scheme": self.scheme,
            "category": self.category,
            "plan_variant": self.plan_variant,
            "fetched_at": self.fetched_at.isoformat(timespec="seconds"),
            "http_status": self.http_status,
            "content_hash": self.content_hash,
            "text_chars": self.text_chars,
        }


@dataclass(frozen=True)
class Chunk:
    """An embeddable unit of text with the metadata every later stage relies on."""

    chunk_id: str
    text: str
    metadata: Mapping[str, str]

    def metadata_for_store(self) -> Dict[str, Any]:
        """Coerce metadata to the primitive types ChromaDB accepts.

        ChromaDB rejects ``None`` and container values, so every field is
        stringified or defaulted here rather than at the call site.
        """
        store: Dict[str, Any] = {}
        for key, value in self.metadata.items():
            if value is None:
                store[key] = ""
            elif isinstance(value, (str, int, float, bool)):
                store[key] = value
            else:
                store[key] = str(value)
        return store

    def validate(self) -> None:
        """Raise if a chunk is missing metadata the pipeline depends on."""
        missing = [key for key in REQUIRED_CHUNK_METADATA if not self.metadata.get(key)]
        if missing:
            raise ValueError(
                f"chunk {self.chunk_id!r} is missing required metadata: {', '.join(missing)}"
            )
        fact_type = self.metadata.get("fact_type")
        if fact_type and fact_type not in FACT_TYPES:
            raise ValueError(
                f"chunk {self.chunk_id!r} has unknown fact_type {fact_type!r}; "
                f"expected one of {', '.join(FACT_TYPES)}"
            )


@dataclass
class LoadReport:
    """Outcome of a load pass over the registry."""

    records: list = field(default_factory=list)
    failures: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def total(self) -> int:
        return len(self.records) + len(self.failures)
