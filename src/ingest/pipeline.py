"""K8 — chunk, embed, store.

Runs the second half of Stage A. Sources whose ``content_hash`` already matches
``corpus/sources.csv`` are skipped, which is what makes a second run a no-op
(IN-5, E-8). When a hash does change, only that source's chunks are re-embedded
and upserted, and any chunk id it no longer produces is deleted so the
collection cannot accumulate orphans.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.config import Settings, load
from src.ingest.chunk_writer import write_chunks
from src.ingest.chunker import ChunkStrategy, build_chunker
from src.ingest.models import Chunk, SourceRecord
from src.rag.embeddings import EmbeddingService, build_embedding_service
from src.rag.vector_store import VectorStore

#: Name of the state file recording what was last *embedded*.
#:
#: This cannot reuse ``corpus/sources.csv``: that file is rewritten by the load
#: stage, which on ``--stage all`` runs before this pipeline in the same
#: invocation. Comparing against it would mean every hash always matched and a
#: genuine page change would never be detected. This file records the state of
#: the store, so it only changes when chunks are actually upserted.
EMBED_INDEX_NAME = "embedded.json"

CSV_HASH_COLUMN = "content_hash"
CSV_ID_COLUMN = "source_id"


def source_id_of(chunk: Chunk) -> str:
    """Recover the source id from ``{source_id}::{section_ordinal}::{part}``."""
    return chunk.chunk_id.split("::", 1)[0]


def source_id_of_id(chunk_id: str) -> str:
    """Recover the source id from a bare chunk id string."""
    return chunk_id.split("::", 1)[0]


@dataclass
class PipelineReport:
    """Counts printed by ``ingest.py`` so a run's effect is visible at a glance."""

    ingested: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    rewritten: List[str] = field(default_factory=list)
    deleted: int = 0
    chunks: int = 0
    vectors: int = 0
    store_count: int = 0

    def line(self) -> str:
        return (
            f"ingested {len(self.ingested)} / unchanged {len(self.unchanged)} / "
            f"rewritten {len(self.rewritten)} | chunks {self.chunks} "
            f"| deleted {self.deleted} | store {self.store_count}"
        )


def read_hash_index(csv_path: Path) -> Dict[str, str]:
    """Return ``{source_id: content_hash}`` from ``corpus/sources.csv``."""
    if not csv_path.exists():
        return {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        return {
            row[CSV_ID_COLUMN]: row[CSV_HASH_COLUMN]
            for row in csv.DictReader(handle)
            if row.get(CSV_ID_COLUMN) and row.get(CSV_HASH_COLUMN)
        }


def embed_index_path(settings: Settings) -> Path:
    return settings.corpus_dir / EMBED_INDEX_NAME


def read_embed_index(settings: Settings) -> Optional[Dict[str, Dict[str, object]]]:
    """Return ``{source_id: {content_hash, chunk_ids}}`` for the last embed.

    ``None`` means nothing has ever been embedded, so every source is pending.
    """
    path = embed_index_path(settings)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_embed_index(
    settings: Settings, records: Sequence[SourceRecord], chunks: Sequence[Chunk]
) -> None:
    """Record the hash and chunk ids now present in the store."""
    by_source: Dict[str, List[str]] = {}
    for chunk in chunks:
        by_source.setdefault(source_id_of(chunk), []).append(chunk.chunk_id)
    payload = {
        record.source_id: {
            "content_hash": record.content_hash,
            "chunk_ids": sorted(by_source.get(record.source_id, [])),
        }
        for record in records
    }
    path = embed_index_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _select(
    records: Sequence[SourceRecord],
    known: Optional[Dict[str, Dict[str, object]]],
    force: bool,
) -> Tuple[List[SourceRecord], List[str]]:
    """Split records into those to process and those already up to date."""
    if force or known is None:
        return list(records), []
    pending: List[SourceRecord] = []
    unchanged: List[str] = []
    for record in records:
        entry = known.get(record.source_id)
        if entry and entry.get("content_hash") == record.content_hash:
            unchanged.append(record.source_id)
        else:
            pending.append(record)
    return pending, unchanged


def run_pipeline(
    records: Sequence[SourceRecord],
    settings: Optional[Settings] = None,
    embeddings: Optional[EmbeddingService] = None,
    store: Optional[VectorStore] = None,
    chunker: Optional[ChunkStrategy] = None,
    force: bool = False,
    write_files: bool = True,
) -> PipelineReport:
    """Chunk, embed and store the given records, skipping unchanged sources."""
    settings = settings or load()
    report = PipelineReport()
    target = store if store is not None else VectorStore(chroma_dir=settings.chroma_dir)

    strategy = chunker or build_chunker(settings)
    all_chunks: List[Chunk] = []
    for record in records:
        all_chunks.extend(strategy.split(record))
    report.chunks = len(all_chunks)

    if write_files:
        write_chunks(all_chunks, settings.chunks_txt_path, settings.chunks_jsonl_path)

    known = read_embed_index(settings)
    is_initial = known is None
    # An empty collection means the store was wiped (chroma/ is gitignored), so a
    # matching hash must not be allowed to skip a re-ingest.
    if target.count() == 0:
        is_initial = True

    pending, unchanged = _select(records, known, force or is_initial)
    report.unchanged = unchanged
    pending_ids = {record.source_id for record in pending}
    for record in records:
        if record.source_id not in pending_ids:
            continue
        (report.ingested if is_initial else report.rewritten).append(record.source_id)

    if not pending:
        report.store_count = target.count()
        return report

    touched = [chunk for chunk in all_chunks if source_id_of(chunk) in pending_ids]
    service = embeddings or build_embedding_service(settings)
    vectors = service.embed_documents([chunk.text for chunk in touched])
    for vector in vectors:
        if len(vector) != settings.embedding_dim:
            raise ValueError(
                f"embedding has {len(vector)} dims, expected {settings.embedding_dim}; a "
                "mixed-model collection would return meaningless results"
            )

    if known is not None:
        # Chunk ids this source produced last time but not this time are orphans.
        expected = {chunk.chunk_id for chunk in touched}
        stale: List[str] = []
        for source_id in pending_ids:
            entry = (known.get(source_id) or {})
            stale.extend(
                chunk_id
                for chunk_id in entry.get("chunk_ids", [])  # type: ignore[union-attr]
                if chunk_id not in expected
            )
        target.delete_ids(sorted(set(stale)))
        report.deleted = len(set(stale))

    target.upsert_chunks(touched, vectors)
    write_embed_index(settings, records, all_chunks)
    report.vectors = len(vectors)
    report.store_count = target.count()
    return report


def source_id_of_id(chunk_id: str) -> str:
    """Recover the source id from a bare chunk id string."""
    return chunk_id.split("::", 1)[0]
