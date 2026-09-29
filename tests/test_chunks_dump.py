"""Tests for the ``chunks.txt`` embedding dump (D-7).

``chunks.txt`` must show, for every chunk, the metadata, the text, and the
384-dim vector that chunk was embedded to. The vectors are read back out of the
persisted collection, so these tests check the dump against the store rather
than against a second embedding, which is what makes "the file shows what
retrieval will search" a verifiable claim.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path
from typing import Dict, List

import pytest

from src.config import Settings, load
from src.ingest.chunk_writer import (
    EMBEDDING_MISSING,
    VECTOR_PRECISION,
    format_vector,
    read_chunks_jsonl,
    render_chunks,
    write_chunks,
)
from src.ingest.chunker import build_chunker
from src.ingest.loader import extract
from src.ingest.models import SourceRecord
from src.ingest.pipeline import run_pipeline
from src.rag.embeddings import FakeEmbeddingService
from src.rag.vector_store import VectorStore
from tests.fixtures import ELSS_HTML, MINIMAL_HTML, SPECS

import datetime as dt
import hashlib

BLOCK = re.compile(r"^=== CHUNK (.+?) ===$", re.M)
VECTOR_LINE = re.compile(r"^embedding: \[([^\]]*)\]$", re.M)


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
        content_hash=hashlib.sha256(extraction.text.encode()).hexdigest(),
        sections=extraction.sections,
        http_status=200,
        text_chars=len(extraction.text),
    )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    base = load()
    return dataclasses.replace(
        base,
        chroma_dir=tmp_path / "chroma",
        corpus_dir=tmp_path / "corpus",
        sources_csv_path=tmp_path / "corpus" / "sources.csv",
        chunks_txt_path=tmp_path / "chunks.txt",
        chunks_jsonl_path=tmp_path / "chunks.jsonl",
    )


@pytest.fixture()
def records() -> List[SourceRecord]:
    return [_record(SPECS[0], MINIMAL_HTML), _record(SPECS[1], ELSS_HTML)]


def _parse_chunks_from_text(body: str) -> Dict[str, dict]:
    """Parse rendered chunk text, keeping the raw block for ordering checks."""
    parts = BLOCK.split(body)[1:]
    out: Dict[str, dict] = {}
    for chunk_id, rest in zip(parts[0::2], parts[1::2]):
        raw = f"=== CHUNK {chunk_id} ===\n{rest}"
        head, _, tail = rest.partition("\n---\n")
        text, _, _ = tail.partition("\n\nembedding_dim:")
        vector = VECTOR_LINE.search(tail)
        out[chunk_id] = {
            "raw": raw,
            "metadata": dict(
                line.split(": ", 1) for line in head.splitlines() if ": " in line
            ),
            "text": text.rstrip("\n"),
            "vector": [float(x) for x in vector.group(1).split(",")] if vector else None,
        }
    return out


def _parse_dump(path: Path) -> Dict[str, dict]:
    """Split ``chunks.txt`` into ``{chunk_id: {metadata, text, vector}}``."""
    return _parse_chunks_from_text(path.read_text(encoding="utf-8"))


# -- formatting -------------------------------------------------------------


def test_format_vector_is_numeric_and_fixed_precision() -> None:
    rendered = format_vector([1 / 3, -0.5, 0.0])
    assert rendered.startswith("[") and rendered.endswith("]")
    assert rendered == f"[{1/3:.{VECTOR_PRECISION}f}, -0.500000, 0.000000]"
    assert len(rendered.split(",")) == 3


def test_render_includes_metadata_text_then_embedding() -> None:
    chunker = build_chunker(load())
    chunks = chunker.split(_record(SPECS[0], MINIMAL_HTML))
    vectors = {c.chunk_id: [0.1] * 384 for c in chunks}
    body = render_chunks(chunks, vectors)

    dump = _parse_chunks_from_text(body)
    assert list(dump) == [chunk.chunk_id for chunk in chunks]
    for chunk in chunks:
        parsed = dump[chunk.chunk_id]
        # metadata block, then the separator, then the text, then the vector
        assert f"source_url: {chunk.metadata['source_url']}" in parsed["raw"]
        assert f"fact_type: {chunk.metadata['fact_type']}" in parsed["raw"]
        assert parsed["text"] == chunk.text
        assert parsed["vector"] == [0.1] * 384
        assert parsed["raw"].index("---") < parsed["raw"].index("embedding: [")
        assert parsed["raw"].index(chunk.text[:30]) < parsed["raw"].index("embedding: [")
        assert parsed["raw"].index("source_url:") < parsed["raw"].index("---")


def test_embedding_section_reports_its_dimension() -> None:
    chunker = build_chunker(load())
    chunks = chunker.split(_record(SPECS[0], MINIMAL_HTML))
    body = render_chunks(chunks[:1], {chunks[0].chunk_id: [0.0] * 384})
    assert "embedding_dim: 384" in body


def test_missing_vector_is_stated_explicitly() -> None:
    chunker = build_chunker(load())
    chunks = chunker.split(_record(SPECS[0], MINIMAL_HTML))
    body = render_chunks(chunks, {})
    assert body.count(EMBEDDING_MISSING) == len(chunks)
    assert "embedding: [" not in body


# -- end to end through the pipeline ---------------------------------------


def test_pipeline_dump_carries_every_stored_vector(settings: Settings, records) -> None:
    report = run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=VectorStore(chroma_dir=settings.chroma_dir),
    )
    assert report.embedded == report.chunks, "a chunk was written without its vector"

    dump = _parse_dump(settings.chunks_txt_path)
    assert len(dump) == report.chunks

    stored = VectorStore(chroma_dir=settings.chroma_dir).vectors_for(list(dump))
    assert len(stored) == report.chunks

    tolerance = 0.5 * 10 ** (-VECTOR_PRECISION) + 1e-12
    for chunk_id, parsed in dump.items():
        vector = parsed["vector"]
        assert vector is not None, f"{chunk_id} has no vector in the dump"
        assert len(vector) == 384
        assert abs(math.sqrt(sum(v * v for v in vector)) - 1.0) < 1e-3, "not normalised"
        reference = stored[chunk_id]
        assert len(reference) == len(vector)
        worst = max(abs(a - b) for a, b in zip(reference, vector))
        assert worst <= tolerance, f"{chunk_id} vector differs from the store by {worst}"


def test_dump_text_and_ids_match_the_jsonl(settings: Settings, records) -> None:
    run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=VectorStore(chroma_dir=settings.chroma_dir),
    )
    dump = _parse_dump(settings.chunks_txt_path)
    rows = read_chunks_jsonl(settings.chunks_jsonl_path)
    assert [row["chunk_id"] for row in rows] == list(dump), "chunk ids drifted"
    for row in rows:
        assert dump[row["chunk_id"]]["text"] == row["text"].strip(), (
            f"{row['chunk_id']} text differs between chunks.txt and chunks.jsonl"
        )


def test_jsonl_stays_vector_free(settings: Settings, records) -> None:
    run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=VectorStore(chroma_dir=settings.chroma_dir),
    )
    body = settings.chunks_jsonl_path.read_text()
    assert "embedding" not in body, "chunks.jsonl must stay lean; vectors live in chunks.txt"


def test_idempotent_rerun_keeps_every_vector(settings: Settings, records) -> None:
    store = VectorStore(chroma_dir=settings.chroma_dir)
    first = run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=store,
    )
    before = _parse_dump(settings.chunks_txt_path)

    second = run_pipeline(
        records,
        settings=settings,
        embeddings=FakeEmbeddingService(dim=settings.embedding_dim),
        store=VectorStore(chroma_dir=settings.chroma_dir),
    )
    assert second.vectors == 0, "a no-op run must not re-embed"
    assert second.embedded == first.embedded == second.chunks
    assert _parse_dump(settings.chunks_txt_path) == before, "dump changed on a no-op run"


def test_embed_false_writes_a_dump_without_touching_the_store(
    settings: Settings, records
) -> None:
    report = run_pipeline(
        records,
        settings=settings,
        store=VectorStore(chroma_dir=settings.chroma_dir),
        embed=False,
    )
    assert report.embedded == 0
    assert report.vectors == 0
    assert VectorStore(chroma_dir=settings.chroma_dir).count() == 0
    body = settings.chunks_txt_path.read_text()
    assert body.count(EMBEDDING_MISSING) == report.chunks


# -- the committed artefact -------------------------------------------------


def test_committed_chunks_txt_is_complete() -> None:
    """The real deliverable on disk: every chunk must carry a 384-dim vector."""
    path = Path(__file__).resolve().parent.parent / "chunks.txt"
    if not path.exists():
        pytest.skip("chunks.txt not generated yet; run ingest.py --stage all")
    dump = _parse_dump(path)
    assert dump, "chunks.txt has no chunk blocks"
    without = [cid for cid, parsed in dump.items() if parsed["vector"] is None]
    assert not without, f"{len(without)} chunk(s) missing a vector: {without[:5]}"
    for chunk_id, parsed in dump.items():
        assert len(parsed["vector"]) == 384, f"{chunk_id} is not 384-dim"
        assert parsed["metadata"]["fact_type"], f"{chunk_id} has no fact_type"
        assert parsed["text"].strip(), f"{chunk_id} has no text"
