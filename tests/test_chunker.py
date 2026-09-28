"""Tests for K4: the chunker.

The guarantees asserted here are the ones a wrong chunker would break silently
and expensively, which is why they are stated as tests rather than comments:

* a scheme boundary is never crossed;
* a fact block is never split;
* the required metadata is always present;
* ``chunk_id`` is deterministic, so ``upsert`` stays idempotent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

import pytest

from src.config import load
from src.ingest.chunk_writer import read_chunks_jsonl, render_chunks
from src.ingest.chunker import (
    SectionAtomicRecursiveChunker,
    build_chunker,
    classify_fact_type,
    fact_type_for_label,
    split_oversized,
)
from src.ingest.models import (
    FACT_SECTION_HEADING,
    REQUIRED_CHUNK_METADATA,
    Chunk,
    SourceRecord,
)
from src.ingest.loader import read_records_cache
from tests.fixtures import MINIMAL_HTML, SPECS
from src.ingest.loader import extract

CHUNK_ID = re.compile(r"^[a-z0-9_]+::\d+::\d+$")

#: A fee/expense row that must never be cut in half. Both halves are meaningful
#: on their own, so a split changes the meaning of the fact.
FACT_ROW_PREFIXES = (
    "Expense ratio:",
    "Minimum SIP investment (INR):",
    "Exit load:",
    "Lock-in period:",
    "Riskometer:",
    "Benchmark index:",
    "Stamp duty:",
)


def _record_from(html: str = MINIMAL_HTML) -> SourceRecord:
    extraction = extract(html)
    return SourceRecord(
        source_id="hdfc_large_cap_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        scheme="HDFC Large Cap Fund - Direct Growth",
        category="large_cap",
        plan_variant="direct_growth",
        text=extraction.text,
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        content_hash="a" * 64,
        sections=extraction.sections,
        http_status=200,
        text_chars=len(extraction.text),
    )


@pytest.fixture(scope="module")
def chunker() -> SectionAtomicRecursiveChunker:
    return build_chunker(load())


# -- determinism ------------------------------------------------------------


def test_chunk_id_is_deterministic(chunker) -> None:
    record = _record_from()
    first = [chunk.chunk_id for chunk in chunker.split(record)]
    second = [chunk.chunk_id for chunk in chunker.split(record)]
    assert first == second
    assert len(first) == len(set(first)), "chunk ids must be unique within a document"


def test_chunk_id_matches_the_documented_format(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        assert CHUNK_ID.match(chunk.chunk_id), f"bad chunk id {chunk.chunk_id!r}"


# -- scheme boundaries ------------------------------------------------------


def test_no_chunk_crosses_a_scheme_boundary(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        assert chunk.metadata["source_url"] == record.url
        assert chunk.metadata["scheme"] == record.scheme
        assert chunk.chunk_id.split("::", 1)[0] == record.source_id


def test_chunks_from_two_sources_never_share_an_id(chunker) -> None:
    first = chunker.split(_record_from())
    second = chunker.split(
        SourceRecord(**{**_record_from().__dict__, "source_id": "hdfc_small_cap", "url": "https://groww.in/x"})
    )
    assert not {c.chunk_id for c in first} & {c.chunk_id for c in second}


# -- fact blocks ------------------------------------------------------------


def test_no_fact_block_is_split(chunker) -> None:
    """Every fact row must appear whole in exactly one chunk."""
    record = _record_from()
    chunks = chunker.split(record)
    facts = [
        chunk for chunk in chunks if chunk.metadata["section"] == FACT_SECTION_HEADING
    ]
    assert facts, "the facts block produced no chunks"

    for line in record.sections[0].text.split("\n"):
        if not line.strip():
            continue
        matches = [chunk for chunk in facts if line.strip() in chunk.text]
        assert matches, f"fact row disappeared: {line!r}"
        for chunk in matches:
            assert line.strip() in chunk.text, f"fact row was cut in half: {line!r}"


def test_fact_rows_are_never_truncated(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        text = chunk.text
        for prefix in FACT_ROW_PREFIXES:
            for line in text.split("\n"):
                if line.startswith(prefix):
                    assert text.count(line) == 1, f"duplicated fact row: {line!r}"


def test_atomic_fact_chunks_carry_one_fact_type(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        if chunk.metadata["authority"] != "scheme_facts":
            continue
        if chunk.metadata["fact_type"] == "general":
            continue
        lines = [line for line in chunk.text.split("\n") if line.strip()]
        labels = {fact_type_for_label(line.split(":", 1)[0]) for line in lines}
        assert labels == {chunk.metadata["fact_type"]}, (
            f"chunk labelled {chunk.metadata['fact_type']} mixes {labels}"
        )


def test_exit_load_slab_survives_whole(chunker) -> None:
    record = _record_from()
    exits = [
        chunk
        for chunk in chunker.split(record)
        if chunk.metadata["fact_type"] == "exit_load"
    ]
    assert exits
    assert any("if redeemed within" in chunk.text for chunk in exits)
    for chunk in exits:
        for line in chunk.text.split("\n"):
            if "%" in line:
                assert "redeemed" in line or "investment" in line, (
                    f"exit-load percentage lost its conditions: {line!r}"
                )


# -- metadata ---------------------------------------------------------------


def test_required_metadata_is_always_present(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        for key in REQUIRED_CHUNK_METADATA:
            assert chunk.metadata.get(key), f"{chunk.chunk_id} missing {key}"


def test_chunk_validate_rejects_missing_metadata() -> None:
    with pytest.raises(ValueError, match="source_url"):
        Chunk("x::0::0", "t", {"scheme": "s", "fact_type": "general"}).validate()


def test_chunk_validate_rejects_unknown_fact_type() -> None:
    bad = Chunk(
        "x::0::0",
        "t",
        {**{k: "v" for k in REQUIRED_CHUNK_METADATA}, "fact_type": "returns"},
    )
    with pytest.raises(ValueError, match="unknown fact_type"):
        bad.validate()


def test_metadata_agrees_with_the_source_record(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        metadata = chunk.metadata
        assert metadata["source_url"] == record.url
        assert metadata["scheme"] == record.scheme
        assert metadata["category"] == record.category
        assert metadata["plan_variant"] == record.plan_variant
        assert metadata["content_hash"] == record.content_hash
        assert metadata["source_fetched_at"] == record.fetched_at.isoformat(timespec="seconds")
        assert int(metadata["char_len"]) == len(chunk.text)


# -- size discipline --------------------------------------------------------


def test_no_chunk_exceeds_the_documented_size(chunker) -> None:
    record = _record_from()
    for chunk in chunker.split(record):
        assert len(chunk.text) <= chunker.chunk_size, (
            f"{chunk.chunk_id} is {len(chunk.text)} chars, over {chunker.chunk_size}"
        )


def test_oversized_section_is_split_on_line_boundaries(chunker) -> None:
    lines = [f"row {index:04d} lorem ipsum dolor sit amet" for index in range(400)]
    record = _record_from()
    record = SourceRecord(
        **{
            **record.__dict__,
            "sections": tuple(
                [
                    type(record.sections[0])(
                        heading="Fund house", text="\n".join(lines), ordinal=0
                    )
                ]
            ),
            "text": "\n".join(lines),
        }
    )
    chunks = chunker.split(record)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("Fund house\n")
        for line in chunk.text.split("\n")[1:]:
            assert line in lines, f"split cut a line: {line!r}"


def test_split_overlap_repeats_tail_content() -> None:
    lines = [f"line-{index}" for index in range(200)]
    parts = split_oversized("\n".join(lines), 400, 100, 60)
    assert len(parts) > 1
    for earlier, later in zip(parts, parts[1:]):
        assert earlier.split("\n")[-1] in later, "no overlap carried across the cut"


# -- configuration is not hardcoded ----------------------------------------


def test_chunker_rejects_a_strategy_it_does_not_implement() -> None:
    settings = load()
    wrong = type(settings)(
        **{**settings.__dict__, "chunk_strategy": "something_else"}
    )
    with pytest.raises(ValueError, match="no implementation"):
        build_chunker(wrong)


def test_chunker_rejects_overlap_not_smaller_than_size() -> None:
    settings = load()
    wrong = type(settings)(**{**settings.__dict__, "chunk_overlap": 5000})
    with pytest.raises(ValueError, match="must be smaller than"):
        SectionAtomicRecursiveChunker(wrong)


def test_configured_values_match_chunking_md() -> None:
    """CHUNKING.md is the contract; config must not drift from it."""
    doc = (Path(__file__).resolve().parent.parent / "CHUNKING.md").read_text()
    settings = load()
    assert settings.require("chunk_size") == int(
        re.search(r"CHUNK_SIZE`? \| \*\*(\d+)", doc).group(1)
    )
    assert settings.require("chunk_overlap") == int(
        re.search(r"CHUNK_OVERLAP`? \| \*\*(\d+)", doc).group(1)
    )
    assert settings.require("chunk_strategy") in doc


# -- fact_type assignment ---------------------------------------------------


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("Expense ratio", "expense_ratio"),
        ("Minimum investments", "min_sip"),
        ("Exit load", "exit_load"),
        ("Exit Load", "exit_load"),
        ("Stamp duty on investment: 0.005% (from July 1st, 2020)", "exit_load"),
        ("Lock-in", "lock_in"),
        ("Riskometer", "riskometer"),
        ("Benchmark", "benchmark"),
        ("Holdings", "general"),
        ("About HDFC Large Cap Fund Direct Growth", "general"),
    ],
)
def test_classify_fact_type_by_heading(heading: str, expected: str) -> None:
    assert classify_fact_type(heading) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Expense ratio", "expense_ratio"),
        ("Minimum SIP investment (INR)", "min_sip"),
        ("Minimum investment amount (INR)", "min_sip"),
        ("Exit load", "exit_load"),
        ("Exit load note1", "exit_load"),
        ("Lock-in period", "lock_in"),
        ("Riskometer", "riskometer"),
        ("Benchmark index", "benchmark"),
        ("ISIN", "general"),
        ("RTA and custodian", "general"),
    ],
)
def test_fact_type_for_label(label: str, expected: str) -> None:
    assert fact_type_for_label(label) == expected


# -- the writer -------------------------------------------------------------


def test_render_chunks_has_a_header_metadata_block_and_text(chunker) -> None:
    chunks = chunker.split(_record_from())
    rendered = render_chunks(chunks)
    assert rendered.count("=== CHUNK ") == len(chunks)
    for chunk in chunks:
        assert f"=== CHUNK {chunk.chunk_id} ===" in rendered
        assert f"source_url: {chunk.metadata['source_url']}" in rendered
    assert "---" in rendered


def test_chunks_txt_and_jsonl_agree(tmp_path, chunker) -> None:
    from src.ingest.chunk_writer import write_chunks

    chunks = chunker.split(_record_from())
    write_chunks(chunks, tmp_path / "chunks.txt", tmp_path / "chunks.jsonl")
    rows = read_chunks_jsonl(tmp_path / "chunks.jsonl")
    assert len(rows) == len(chunks)
    assert [row["chunk_id"] for row in rows] == [chunk.chunk_id for chunk in chunks]
    assert [row["text"] for row in rows] == [chunk.text for chunk in chunks]
    body = (tmp_path / "chunks.txt").read_text()
    for row in rows:
        assert row["text"] in body
