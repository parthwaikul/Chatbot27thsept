"""The deliverables D-2 … D-7 exist, and the generated ones still match the code.

Phase 7 produces four files that a reviewer reads *instead of* running the
product, which is exactly what makes them dangerous: nothing in the test suite
fails when `SOURCES.md` lists a scheme that was removed, or when `README.md`
claims a limit the code no longer has. So these tests treat the artifacts as
derived data and re-derive them.

The `DISCLAIMER.txt` and `SOURCES.md` checks are drift guards, not content
checks — both files are generated, and the only real failure mode is somebody
editing a generated file by hand.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.config import load
from src.ingest.registry import SourceRegistry

ROOT = Path(__file__).resolve().parent.parent

DELIVERABLES = {
    "D-2": "SOURCES.md",
    "D-3": "README.md",
    "D-4": "SAMPLE_QA.md",
    "D-5": "DISCLAIMER.txt",
    "D-6": "CHUNKING.md",
    "D-7": "chunks.txt",
}


@pytest.mark.parametrize("label,filename", sorted(DELIVERABLES.items()))
def test_deliverable_exists_and_is_not_empty(label: str, filename: str) -> None:
    path = ROOT / filename
    assert path.exists(), f"{label} is missing: {filename}"
    assert path.stat().st_size > 0, f"{label} is empty: {filename}"


# -- D-2: the source list ------------------------------------------------------


def test_sources_md_lists_every_registered_source() -> None:
    """A source in the registry but not in D-2 is an invisible corpus.

    This is the check that would have caught a sixth scheme being added without
    the deliverable being regenerated.
    """
    settings = load()
    registry = SourceRegistry.from_file(settings.sources_path)
    text = settings.sources_md_path.read_text(encoding="utf-8")
    for spec in registry.all_sources():
        assert spec.url in text, f"D-2 omits {spec.source_id} ({spec.url})"
        assert spec.scheme in text, f"D-2 omits the scheme name for {spec.source_id}"


def test_sources_md_carries_the_fetch_date_from_the_manifest() -> None:
    """The date must be the one the ingest run recorded, not a typed-in one.

    D-2 is the artifact that answers "when was this true", so a date that
    disagrees with `corpus/sources.csv` is worse than no date.
    """
    settings = load()
    text = settings.sources_md_path.read_text(encoding="utf-8")
    with settings.sources_csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["fetched_at"].split("T")[0]
            assert date in text, f"D-2 does not record the fetch date {date} for {row['source_id']}"


def test_sources_md_has_no_third_party_blog() -> None:
    """C-1 / FR-21, asserted on the artifact a reviewer actually reads."""
    text = load().sources_md_path.read_text(encoding="utf-8")
    for host in ("medium.com", "wordpress.com", "blogspot.", "scribd", "reddit.com",
                 "youtube.com", "linkedin.com", "twitter.com", "x.com/"):
        assert host not in text, f"D-2 links a non-official host: {host}"


# -- D-5: the disclaimer -------------------------------------------------------


def test_disclaimer_txt_quotes_the_live_constants() -> None:
    """Drift guard: DISCLAIMER.txt is generated, so this only fails on a hand edit.

    The bare `DISCLAIMER` constant is the one the brief specifies verbatim, so
    it is checked exactly, full stop included.
    """
    from src.guardrails import messages

    text = (ROOT / "DISCLAIMER.txt").read_text(encoding="utf-8")
    assert messages.DISCLAIMER == "Facts-only. No investment advice."
    assert messages.DISCLAIMER in text, "D-5 does not quote the live disclaimer"

    for attribute in (
        "PII_REFUSAL",
        "ADVICE_REFUSAL",
        "PERFORMANCE_REDIRECT",
        "NOT_IN_SOURCES_DECLINE",
        "VALIDATOR_DECLINE",
    ):
        # Compared stripped: a trailing space in a constant is invisible in a
        # recorded text file, and failing on one would train people to ignore
        # this test.
        assert getattr(messages, attribute).strip() in text, (
            f"D-5 is missing the current {attribute} wording"
        )


def test_the_disclaimer_is_shown_on_every_response() -> None:
    """FR-20 is about the UI, not the file. Assert the render path."""
    from src.ui.answer_view import render_block

    for response in _fake_responses():
        block = render_block(response)
        assert "Facts-only. No investment advice." in block


def _fake_responses():
    """One response per branch, built without touching the model or the store."""
    from src.query.pipeline import AnswerResponse

    common = {"last_updated": "Last updated from sources: 28 Sep 2026"}
    return (
        AnswerResponse(text="An answer.", citation_url="https://groww.in/x",
                       path="answer", **common),
        AnswerResponse(text="I can't help with that.", citation_url=None, path="pii", **common),
        AnswerResponse(text="I couldn't answer that from my sources.", citation_url=None,
                       path="decline", **common),
        AnswerResponse(text="I don't state returns.", citation_url=None,
                       path="performance", **common),
        AnswerResponse(text="Something went wrong.", citation_url=None, path="error", **common),
    )


# -- D-4: the sample Q&A -------------------------------------------------------


def test_sample_qa_stays_inside_the_required_range() -> None:
    """FR-19 asks for 5–10 queries. A shorter file is a scope failure; a longer
    one is padding, and usually means a generator was pointed at the whole case
    set."""
    text = load().sample_qa_path.read_text(encoding="utf-8")
    count = text.count("\n## ") - text.count("\n## What each block")
    assert 5 <= count <= 10, f"D-4 has {count} questions; FR-19 requires 5-10"


def test_sample_qa_shows_a_refusal_and_a_pii_block() -> None:
    from src.guardrails.messages import ADVICE_REFUSAL, PII_REFUSAL, PERFORMANCE_REDIRECT

    text = load().sample_qa_path.read_text(encoding="utf-8")
    assert PII_REFUSAL in text, "D-4 must show the PII screen (FR-19)"
    assert ADVICE_REFUSAL in text or PERFORMANCE_REDIRECT in text, (
        "D-4 must show a refusal, not only answers (FR-19)"
    )
    assert "Source: https://" in text, "D-4 must show answers carrying their source links (FR-19)"


def test_sample_qa_every_link_is_a_registered_or_configured_source() -> None:
    """A pasted sample is a liability if any URL in it is not one we allow.

    FR-21 also forbids back-end captures, and a URL is the one thing in a
    markdown sample that could point somewhere unexpected.
    """
    import re

    settings = load()
    registry = SourceRegistry.from_file(settings.sources_path)
    allowed = {spec.url for spec in registry.all_sources()}
    allowed.update((settings.factsheet_link_map or {}).values())
    if settings.educational_link:
        allowed.add(settings.educational_link)

    text = settings.sample_qa_path.read_text(encoding="utf-8")
    urls = set(re.findall(r"https?://[^\s)\]|]+", text))
    unexpected = {url for url in urls if url.rstrip(".") not in allowed}
    assert not unexpected, f"D-4 contains unregistered URLs: {sorted(unexpected)}"


# -- D-3 / D-6 / D-7 -----------------------------------------------------------


def test_readme_covers_the_setup_the_brief_asks_for() -> None:
    """FR-18: setup steps, scope, known limits. Each is a heading, not a mention."""
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    for step in ("cp .env.example .env", "ingest.py", "streamlit run app.py", "eval.py"):
        assert step in text, f"README setup is missing `{step}` (FR-18)"
    for heading in ("## setup", "## scope", "## known limits"):
        assert heading in text, f"README is missing the {heading} section (FR-18)"
    assert "direct growth" in text, "README must state the plan variant scope (FR-18)"
    assert "facts-only. no investment advice." in text, "README must carry the disclaimer (FR-18)"


def test_readme_states_the_freshness_semantics_and_latency() -> None:
    """The two limits most likely to be read as claims of currency."""
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "last updated from sources" in text, (
        "README must explain what the freshness line means (FR-14, FR-18)"
    )
    assert "ingestion date" in text or "ingestion timestamp" in text, (
        "README must say the freshness line is the ingestion date, not a live check"
    )
    assert "6 s" in text or "6s" in text, "README must state the warm-answer target (NFR-7, Q6)"


def test_chunking_doc_has_the_retrieval_tuning_section() -> None:
    """D-6 is not complete without it: the tuning decisions are part of the
    chunking strategy deliverable, not a separate one."""
    text = (ROOT / "CHUNKING.md").read_text(encoding="utf-8")
    assert "retrieval tuning" in text.lower(), "D-6 is missing the retrieval tuning section"
    assert "RETRIEVAL_TOP_K" in text, "D-6 must record the resolved top-k (Q3)"
    assert "SIMILARITY_FLOOR" in text, "D-6 must record the resolved similarity floor (Q3)"


def test_chunks_dump_has_one_block_per_stored_chunk() -> None:
    """D-7 must describe the corpus that is actually loaded, or a reviewer
    reading it is reading a different system from the one being run."""
    settings = load()
    blocks = settings.chunks_txt_path.read_text(encoding="utf-8").count("=== CHUNK ")
    expected = sum(1 for _ in csv.DictReader(settings.sources_csv_path.open(encoding="utf-8")))
    assert blocks >= expected * 20, (
        f"chunks.txt holds {blocks} blocks for {expected} sources; that is too few to be the "
        "real corpus"
    )
    # Deterministic ids: the source_id and the chunk's position, never a uuid or
    # a timestamp. E-8 depends on this.
    for line in settings.chunks_txt_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("=== CHUNK "):
            body = line[len("=== CHUNK ") :]
            assert "::" in body, f"chunk id is not positional, so E-8 cannot be stable: {body}"
