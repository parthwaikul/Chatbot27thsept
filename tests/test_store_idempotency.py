"""Tests for K6/K7/K8: embeddings, the persisted store, and idempotency.

Runs entirely offline against :class:`FakeEmbeddingService`, so there is no
model download and no network. What is being asserted is the behaviour the
architecture pins down: writes are ``upsert`` so a second run is a no-op, a
content change rewrites only the chunks that changed, and the collection
survives a restart.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import List, Tuple

import pytest

from src.config import Settings, load
from src.ingest.loader import extract
from src.ingest.models import SourceRecord
from src.ingest.pipeline import read_embed_index, read_hash_index, run_pipeline, source_id_of_id
from src.rag.embeddings import FakeEmbeddingService
from src.rag.vector_store import VectorStore
from tests.fixtures import ELSS_HTML, MINIMAL_HTML, SPECS


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Settings redirected into tmp_path so tests never touch the real corpus."""
    base = load()
    return dataclasses.replace(
        base,
        chroma_dir=tmp_path / "chroma",
        corpus_dir=tmp_path / "corpus",
        sources_csv_path=tmp_path / "corpus" / "sources.csv",
        chunks_txt_path=tmp_path / "chunks.txt",
        chunks_jsonl_path=tmp_path / "chunks.jsonl",
    )


def _record(spec, html: str) -> SourceRecord:
    extraction = extract(html)
    return SourceRecord(
        source_id=spec.source_id,
        url=spec.url,
        scheme=spec.scheme,
        category=spec.category,
        plan_variant=spec.plan_variant,
        text=extraction.text,
        fetched_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        content_hash=__import__("hashlib")
        .sha256(extraction.text.encode())
        .hexdigest(),
        sections=extraction.sections,
        http_status=200,
        text_chars=len(extraction.text),
    )


@pytest.fixture()
def records() -> List[SourceRecord]:
    return [
        _record(SPECS[0], MINIMAL_HTML),
        _record(SPECS[1], ELSS_HTML),
    ]


def _write_csv(settings: Settings, records: List[SourceRecord]) -> None:
    import csv

    settings.sources_csv_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.sources_csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].to_csv_row()))
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())


def _run(settings: Settings, records: List[SourceRecord], **kwargs):
    return run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=VectorStore(chroma_dir=settings.chroma_dir),
        **kwargs,
    )


# -- fake embedder ----------------------------------------------------------


def test_fake_embeddings_are_384_dim_and_normalised() -> None:
    service = FakeEmbeddingService()
    vectors = service.embed_documents(["a", "b"])
    assert service.dim == 384
    assert all(len(vector) == 384 for vector in vectors)
    for vector in vectors:
        assert abs(sum(value * value for value in vector) ** 0.5 - 1.0) < 1e-9


def test_fake_embeddings_are_deterministic() -> None:
    assert FakeEmbeddingService().embed_query("hello") == FakeEmbeddingService().embed_query("hello")


def test_fake_embeddings_separate_different_texts() -> None:
    service = FakeEmbeddingService()
    assert service.embed_query("exit load 1%") != service.embed_query("minimum SIP 100")


# -- persistence ------------------------------------------------------------


def test_store_persists_across_instances(settings: Settings, records) -> None:
    _run(settings, records)
    first = VectorStore(chroma_dir=settings.chroma_dir).count()
    second = VectorStore(chroma_dir=settings.chroma_dir).count()
    assert first == second > 0


def test_collection_name_and_space(settings: Settings, records) -> None:
    _run(settings, records)
    store = VectorStore(chroma_dir=settings.chroma_dir)
    assert store.collection_name == "mf_faq"
    assert store._collection.metadata.get("hnsw:space") == "cosine"


# -- idempotency ------------------------------------------------------------


def test_second_run_is_a_noop(settings: Settings, records) -> None:
    _write_csv(settings, records)
    first = _run(settings, records)
    assert first.ingested and not first.unchanged

    second = _run(settings, records)
    assert second.store_count == first.store_count, "re-ingestion changed the count"
    assert second.ingested == [] and second.rewritten == []
    assert sorted(second.unchanged) == sorted(record.source_id for record in records)
    assert second.vectors == 0


def test_repeated_runs_never_grow_the_collection(settings: Settings, records) -> None:
    _write_csv(settings, records)
    counts = [_run(settings, records).store_count for _ in range(3)]
    assert counts[0] == counts[1] == counts[2]


def test_changed_content_rewrites_only_affected_ids(settings: Settings, records) -> None:
    _write_csv(settings, records)
    _run(settings, records)
    store = VectorStore(chroma_dir=settings.chroma_dir)
    before_ids = set(store.all_chunk_ids())
    before = store.get_chunks(sorted(before_ids))

    # The load stage rewrites sources.csv before the pipeline runs on
    # --stage all, so change detection must come from the embed index, not the
    # CSV. Only the record passed in changes here.
    changed = dataclasses.replace(
        records[0],
        text=records[0].text + "\nStamp duty: 0.005% (updated)",
        content_hash="b" * 64,
    )
    updated = [changed, records[1]]
    report = _run(settings, updated)

    assert report.rewritten == [changed.source_id], "only the changed source is rewritten"
    assert sorted(report.unchanged) == [records[1].source_id]

    after_ids = set(store.all_chunk_ids())
    untouched = [
        chunk_id
        for chunk_id in before_ids
        if chunk_id.split("::", 1)[0] == records[1].source_id
    ]
    assert untouched, "the unchanged source contributed chunks"
    after = store.get_chunks(untouched)
    for chunk_id in untouched:
        assert after[chunk_id]["text"] == before[chunk_id]["text"], (
            f"{chunk_id} was rewritten even though its source did not change"
        )
    assert after_ids >= before_ids, "changed source should not lose ids"


def test_stale_chunk_ids_are_deleted_when_a_source_shrinks(
    settings: Settings, records
) -> None:
    _write_csv(settings, records)
    _run(settings, records)
    store = VectorStore(chroma_dir=settings.chroma_dir)
    before = len(store.all_chunk_ids())

    tiny = extract("<html><body><h1>Expense ratio</h1><p>Expense ratio: 0.57</p></body></html>")
    shrunk = SourceRecord(
        **{
            **dataclasses.replace(records[0], content_hash="c" * 64).__dict__,
            "text": tiny.text,
            "sections": tiny.sections,
            "text_chars": len(tiny.text),
        }
    )
    report = _run(settings, [shrunk, records[1]])

    assert report.deleted > 0, "orphaned chunk ids were not removed"
    remaining = store.all_chunk_ids()
    assert len(remaining) == before - report.deleted
    assert shrunk.source_id in {source_id_of_id(cid) for cid in remaining}


def test_wiping_the_store_triggers_a_full_reingest(settings: Settings, records) -> None:
    """chroma/ is gitignored, so a missing collection must not be treated as 'done'."""
    _write_csv(settings, records)
    first = _run(settings, records)
    assert first.store_count > 0

    # ChromaDB caches one client per path per process, so rmtree alone would not
    # be observed; emptying the collection is the equivalent real-world case.
    store = VectorStore(chroma_dir=settings.chroma_dir)
    store.delete_ids(store.all_chunk_ids())
    assert store.count() == 0

    report = _run(settings, records)
    assert report.store_count == first.store_count, "store was not rebuilt after being wiped"
    assert sorted(report.ingested) == sorted(record.source_id for record in records)


# -- write safety -----------------------------------------------------------


def test_upsert_rejects_a_length_mismatch(settings: Settings, records) -> None:
    from src.ingest.chunker import build_chunker
    from src.ingest.models import Chunk

    store = VectorStore(chroma_dir=settings.chroma_dir)
    chunks = build_chunker(settings).split(records[0])[:2]
    with pytest.raises(Exception):
        store.upsert_chunks(chunks, [[0.0] * 384])


def test_query_returns_similarity_as_one_minus_distance(
    settings: Settings, records
) -> None:
    _run(settings, records)
    store = VectorStore(chroma_dir=settings.chroma_dir)
    service = FakeEmbeddingService(dim=settings.embedding_dim)
    hits = store.query(service.embed_query("Exit load: 1% if redeemed within 1 year"), k=3)
    assert hits
    for hit in hits:
        assert set(hit) == {"chunk_id", "text", "metadata", "similarity"}
        assert -1.0001 <= hit["similarity"] <= 1.0001
    similarities = [hit["similarity"] for hit in hits]
    assert similarities == sorted(similarities, reverse=True), "hits not ordered"


def test_exact_match_scores_near_one(settings: Settings, records) -> None:
    _run(settings, records)
    store = VectorStore(chroma_dir=settings.chroma_dir)
    stored = store.get_chunks(store.all_chunk_ids())
    target = next(c for c in stored.values() if "Lock-in period" in c["text"])
    service = FakeEmbeddingService(dim=settings.embedding_dim)
    hits = store.query(service.embed_query(target["text"]), k=1)
    assert hits[0]["text"] == target["text"]
    assert hits[0]["similarity"] > 0.999


# -- hash index -------------------------------------------------------------


def test_hash_index_reads_the_csv(settings: Settings, records) -> None:
    assert read_hash_index(settings.sources_csv_path) == {}
    _write_csv(settings, records)
    index = read_hash_index(settings.sources_csv_path)
    assert index[records[0].source_id] == records[0].content_hash
