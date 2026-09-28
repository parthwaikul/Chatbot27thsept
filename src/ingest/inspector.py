"""K3 — corpus inspection.

Phase 1 exists to produce evidence: where each fact type actually lives on each
page, how dense the tables are, how much boilerplate is present, and which
sections had to be dropped. The output of this module is the input to
``CHUNKING.md`` (deliverable D-6), which must be written before any chunking
code exists (TC-2, IN-2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.ingest.loader import ExtractionStats
from src.ingest.models import FACT_TYPES, SourceRecord
from src.ingest.registry import SourceSpec

# Patterns that locate each FR-8 fact type in the extracted text. The ELSS
# lock-in is served by the structured "Lock-in period" fact line, so the
# lock_in pattern deliberately matches either the fact line or lock-in prose.
FACT_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "expense_ratio": (r"expense\s*ratio", r"Expense ratio:"),
    "exit_load": (r"exit\s*load", r"Exit load:"),
    "min_sip": (r"min(?:imum)?\.?\s*for\s*sip", r"minimum\s+sip", r"Minimum SIP", r"min_sip"),
    "lock_in": (r"lock[\s-]?in", r"Lock-in period:"),
    "riskometer": (r"riskometer", r"Riskometer:", r"rated\s+\w+\s+risk"),
    "benchmark": (r"\bbenchmark\b", r"Benchmark:"),
    "statement_guide": (
        r"capital[\s-]gains\s+statement",
        r"tax\s+statement",
        r"download\s+(?:your\s+)?(?:statement|tax)",
        r"statement\s+download",
    ),
}

# Fact types that are only expected on some scheme categories. A lock-in period
# is an ELSS feature, so its absence on the other four schemes is correct and
# must not be reported as a corpus gap.
EXPECTED_ON_CATEGORIES: Dict[str, Optional[frozenset]] = {
    "lock_in": frozenset({"elss"}),
}

_EXCERPT_CHARS = 150
_BOILERPLATE_HINT_RE = re.compile(
    r"log\s*in|sign\s*up|download\s+app|copyright|terms\s+of\s+use|privacy\s+policy|"
    r"follow\s+us|newsletter|cookie",
    re.IGNORECASE,
)


@dataclass
class FactHit:
    """One place in a document where a fact type was found."""

    fact_type: str
    heading: str
    excerpt: str
    matched_text: str


@dataclass
class SourceInspection:
    """Everything observed about one source document."""

    spec: SourceSpec
    text_chars: int
    section_count: int
    section_headings: List[str]
    fact_hits: Dict[str, List[FactHit]]
    missing_fact_types: List[str]
    unexpected_absent_fact_types: List[str]
    boilerplate_ratio: float
    stats: Optional[ExtractionStats] = None


@dataclass
class InspectionReport:
    """Corpus-level inspection result."""

    sources: List[SourceInspection] = field(default_factory=list)
    missing_by_fact_type: Dict[str, List[str]] = field(default_factory=dict)
    unexpected_absent_by_fact_type: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing_by_fact_type

    @property
    def fully_ok(self) -> bool:
        return not self.missing_by_fact_type and not self.unexpected_absent_by_fact_type


def _excerpt(text: str, match: re.Match, width: int = _EXCERPT_CHARS) -> str:
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _heading_for_offset(record: SourceRecord, offset: int) -> str:
    for section in record.sections:
        end = offset + len(section.text)
        if offset < end:
            return section.heading
    return "(facts block)"


def locate_facts(record: SourceRecord) -> Tuple[Dict[str, List[FactHit]], List[str]]:
    """Find every fact type present in a record, with heading and excerpt."""
    hits: Dict[str, List[FactHit]] = {}
    for fact_type, patterns in FACT_PATTERNS.items():
        found: List[FactHit] = []
        seen: set = set()
        for pattern in patterns:
            for match in re.finditer(pattern, record.text, re.IGNORECASE):
                key = (fact_type, match.group(0).lower())
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    FactHit(
                        fact_type=fact_type,
                        heading=_heading_for_offset(record, match.start()),
                        excerpt=_excerpt(record.text, match),
                        matched_text=match.group(0),
                    )
                )
                if len(found) >= 4:
                    break
            if len(found) >= 4:
                break
        if found:
            hits[fact_type] = found

    missing = [fact_type for fact_type in FACT_TYPES if fact_type not in hits and fact_type != "general"]
    return hits, missing


def _boilerplate_ratio(text: str) -> float:
    if not text:
        return 0.0
    boiler = sum(len(m.group(0)) for m in _BOILERPLATE_HINT_RE.finditer(text))
    return round(boiler / len(text), 4)


def inspect_record(spec: SourceSpec, record: SourceRecord) -> SourceInspection:
    """Inspect a single loaded record."""
    hits, missing = locate_facts(record)
    expected = EXPECTED_ON_CATEGORIES.get("lock_in")
    unexpected: List[str] = []
    if expected is not None and spec.category not in expected:
        unexpected = [fact for fact in missing if fact in EXPECTED_ON_CATEGORIES]
        missing = [fact for fact in missing if fact not in unexpected]
    return SourceInspection(
        spec=spec,
        text_chars=record.text_chars,
        section_count=len(record.sections),
        section_headings=[section.heading for section in record.sections if section.heading],
        fact_hits=hits,
        missing_fact_types=missing,
        unexpected_absent_fact_types=unexpected,
        boilerplate_ratio=_boilerplate_ratio(record.text),
    )


def build_report(
    records: Sequence[SourceRecord],
    specs_by_id: Dict[str, SourceSpec],
    stats_by_id: Optional[Dict[str, ExtractionStats]] = None,
) -> InspectionReport:
    """Build the corpus-level report from loaded records."""
    report = InspectionReport()
    for record in records:
        spec = specs_by_id.get(record.source_id)
        if spec is None:
            continue
        inspection = inspect_record(spec, record)
        if stats_by_id and record.source_id in stats_by_id:
            inspection.stats = stats_by_id[record.source_id]
        report.sources.append(inspection)
        for fact_type in inspection.missing_fact_types:
            report.missing_by_fact_type.setdefault(fact_type, []).append(record.source_id)
        for fact_type in inspection.unexpected_absent_fact_types:
            report.unexpected_absent_by_fact_type.setdefault(fact_type, []).append(record.source_id)
    return report


def render_report(report: InspectionReport, records: Sequence[SourceRecord]) -> str:
    """Render the console report and the evidence needed for CHUNKING.md."""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("CORPUS INSPECTION REPORT  (input to CHUNKING.md — deliverable D-6)")
    lines.append("=" * 78)
    lines.append(f"sources loaded: {len(report.sources)}")
    lines.append("")

    for inspection in report.sources:
        record = next(r for r in records if r.source_id == inspection.spec.source_id)
        lines.append("-" * 78)
        lines.append(f"{inspection.spec.source_id}  [{inspection.spec.category}]")
        lines.append(f"  url            : {inspection.spec.url}")
        lines.append(f"  text chars     : {inspection.text_chars}")
        lines.append(f"  sections       : {inspection.section_count}")
        lines.append(f"  content hash   : {record.content_hash[:16]}")
        lines.append(f"  boilerplate    : {inspection.boilerplate_ratio:.2%} of text")
        if inspection.stats is not None:
            stats = inspection.stats
            lines.append(
                f"  stripped       : {stats.sections_dropped} sections "
                f"({stats.preamble_chars_dropped} chars site chrome), "
                f"{stats.tables_dropped}/{stats.tables} tables, {stats.junk_nodes} junk nodes"
            )
            lines.append(f"  fact fields    : {stats.fact_fields} whitelisted structured fields")
        lines.append(f"  headings       : {' | '.join(inspection.section_headings[:14])}")
        lines.append("  fact types found:")
        for fact_type, hits in inspection.fact_hits.items():
            first = hits[0]
            lines.append(f"    + {fact_type:<16} under [{first.heading}]")
            lines.append(f"        …{first.excerpt}…")
        if inspection.unexpected_absent_fact_types:
            lines.append(
                f"  n/a by design  : {', '.join(inspection.unexpected_absent_fact_types)} "
                f"(not applicable to {inspection.spec.category})"
            )
        if inspection.missing_fact_types:
            lines.append(f"  MISSING         : {', '.join(inspection.missing_fact_types)}")
        lines.append("")

    lines.append("=" * 78)
    lines.append("FACT TYPE COVERAGE ACROSS CORPUS")
    lines.append("=" * 78)
    for fact_type in FACT_TYPES:
        if fact_type == "general":
            continue
        missing = report.missing_by_fact_type.get(fact_type, [])
        expected_absent = report.unexpected_absent_by_fact_type.get(fact_type, [])
        if missing:
            lines.append(
                f"  ! {fact_type:<16} MISSING on {len(missing)} source(s): {', '.join(missing)}"
            )
        else:
            lines.append(f"  + {fact_type:<16} present on all sources")
        if expected_absent:
            lines.append(
                f"    (n/a by design on {len(expected_absent)}: {', '.join(expected_absent)})"
            )
    lines.append("")
    if not report.fully_ok:
        lines.append(
            "ACTION: a fact type marked MISSING cannot be answered from this corpus. Either add an "
            "official source for it (PRD open question Q1) or scope the FAQ to exclude it. Record "
            "the decision in CHUNKING.md before writing the chunker."
        )
        lines.append("")
    return "\n".join(lines)


def render_fact_samples(records: Sequence[SourceRecord]) -> str:
    """Print the exact fact lines per source — the values the bot must quote."""
    lines = ["", "EXTRACTED FACT LINES PER SOURCE (whitelisted fields only)", "-" * 78]
    for record in records:
        lines.append(f"{record.source_id}:")
        header = record.text.split("\n\n", 1)[0]
        for line in header.splitlines():
            if line.strip():
                lines.append(f"    {line}")
        lines.append("")
    return "\n".join(lines)
