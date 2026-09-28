"""Tests for K2/K3: extraction must be clean, auditable and free of market data.

These lock down the constraint C-3 guarantee at the load stage. If extraction
regresses, a performance figure can reach the corpus and the bot can be asked
to compare returns, which the brief forbids.
"""

from __future__ import annotations

import re

from src.ingest.inspector import inspect_record, locate_facts
from src.ingest.loader import extract
from src.ingest.models import DocumentSection, SourceRecord
from tests.fixtures import ELSS_HTML, MINIMAL_HTML, SPECS

_SIGNED_RETURN = re.compile(r"[+-]\s?\d+(?:\.\d+)?\s*%")
_PERIOD_RUN = re.compile(r"\b1D\s+1M\s+6M\b")


def _record(html: str = MINIMAL_HTML):
    extraction = extract(html)
    return (
        SourceRecord(
            source_id="hdfc_large_cap_direct_growth",
            url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
            scheme="HDFC Large Cap Fund - Direct Growth",
            category="large_cap",
            plan_variant="direct_growth",
            text=extraction.text,
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            content_hash="deadbeef",
            sections=extraction.sections,
            http_status=200,
            text_chars=len(extraction.text),
        ),
        extraction.stats,
    )


def test_whitelisted_facts_are_rendered() -> None:
    extraction = extract(MINIMAL_HTML)
    assert "Expense ratio: 1.03" in extraction.text
    assert "Minimum SIP investment (INR): 100" in extraction.text
    assert "Riskometer: Moderately High Riskometer" in extraction.text
    assert "Benchmark index: NIFTY 100 Total Return Index" in extraction.text
    assert "Exit load: Exit load of 1% if redeemed within 1 year" in extraction.text


def test_non_whitelisted_fields_never_enter_the_corpus() -> None:
    text = extract(MINIMAL_HTML).text
    for leaked in ("1189.079", "25-Sep-2026", "Some Company"):
        assert leaked not in text, f"performance/price field leaked into corpus: {leaked}"


def test_performance_sections_are_dropped() -> None:
    extraction = extract(MINIMAL_HTML)
    headings = [section.heading for section in extraction.sections]
    assert "Annualised returns" not in headings
    assert not any(h.startswith("Holdings") for h in headings)
    assert "12.4%" not in extraction.text


def test_market_ticker_is_stripped_but_facts_survive() -> None:
    text = extract(MINIMAL_HTML).text
    assert _SIGNED_RETURN.search(text) is None
    assert _PERIOD_RUN.search(text) is None
    assert "1,189.08" not in text
    assert "Min. for SIP" in text
    assert "Expense ratio 1.03%" in text
    assert "Fund size (AUM)" in text


def test_exit_load_and_stamp_duty_percentages_are_preserved() -> None:
    text = extract(MINIMAL_HTML).text
    assert "1% if redeemed within 1 year" in text
    assert "Stamp duty: 0.005%" in text


def test_navigation_chrome_is_removed() -> None:
    extraction = extract(MINIMAL_HTML)
    assert "site chrome" not in extraction.text
    assert "Home Products Pricing" not in extraction.text
    assert extraction.stats.preamble_chars_dropped > 0


def test_sections_are_ordered_and_have_content() -> None:
    extraction = extract(MINIMAL_HTML)
    ordinals = [section.ordinal for section in extraction.sections]
    assert ordinals == list(range(len(ordinals)))
    assert all(not section.is_empty() for section in extraction.sections)


def test_table_rows_become_key_value_lines() -> None:
    text = extract(MINIMAL_HTML).text
    assert "Minimum SIP: ₹100" in text
    assert "Minimum lumpsum: ₹100" in text


def test_elss_lock_in_is_extracted_and_non_elss_is_absent() -> None:
    elss_text = extract(ELSS_HTML).text
    assert "Lock-in period: 3 years" in elss_text
    assert "Lock-in period" not in extract(MINIMAL_HTML).text


def test_fact_location_covers_expected_types() -> None:
    record, _ = _record()
    hits, missing = locate_facts(record)
    for fact_type in ("expense_ratio", "exit_load", "min_sip", "riskometer", "benchmark"):
        assert fact_type in hits, f"{fact_type} not located"
    assert "statement_guide" in missing


def test_lock_in_absent_on_non_elss_is_not_a_corpus_gap() -> None:
    record, _ = _record()
    inspection = inspect_record(SPECS[0], record)
    assert "lock_in" in inspection.unexpected_absent_fact_types
    assert "lock_in" not in inspection.missing_fact_types


def test_drop_preamble_can_be_disabled() -> None:
    extraction = extract(MINIMAL_HTML, drop_preamble=False)
    assert "site chrome" in extraction.text
