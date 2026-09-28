"""K2 — fetch a registered public page and reduce it to clean, auditable text.

Two extraction paths are combined, because the P1 inspection found that the
scheme pages carry facts in two different places:

1. **Structured scheme data** embedded in the page's ``__NEXT_DATA__`` payload
   holds the precise values (expense ratio, exit load, minimum SIP, lock-in,
   riskometer, benchmark). Only an explicit whitelist of fields is rendered, so
   price and performance data can never enter the corpus.
2. **Server-rendered visible text** holds the surrounding prose and section
   headings (minimum investments, exit load, tax rules, investment objective).

Performance and price content is removed at load time (constraint C-3).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.config import Settings
from src.ingest.models import (
    FACT_SECTION_HEADING,
    DocumentSection,
    LoadReport,
    SourceRecord,
    normalise_text,
)
from src.ingest.registry import ALLOWED_HOST_SUFFIXES, SourceRegistry, SourceSpec

HEADING_TAGS: Tuple[str, ...] = ("h1", "h2", "h3", "h4", "h5", "h6")
JUNK_TAGS: Tuple[str, ...] = ("script", "style", "noscript", "svg", "iframe", "template")
JUNK_SELECTORS: Tuple[str, ...] = (
    "nav",
    "footer",
    "aside",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    "[aria-label*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='cookie' i]",
    "[class*='consent' i]",
    "[id*='consent' i]",
)

# Sections dropped wholesale: performance figures, rankings and comparisons.
DROP_SECTION_RE = re.compile(
    r"^\s*(return\s+calculator|returns?\s+and\s+rankings|returns?|annualis?ed\s+returns?|"
    r"absolute\s+returns?|historic\s+returns?|performance|returns?\s+overview|"
    r"risk[\s-]?reward|holdings?|fund\s+management|compare\s+similar\s+funds|"
    r"portfolio\s+holdings)\b",
    re.IGNORECASE,
)

PERFORMANCE_HEADER_TOKENS = frozenset(
    {
        "nav",
        "1d",
        "1w",
        "1m",
        "3m",
        "6m",
        "1y",
        "3y",
        "5y",
        "return",
        "returns",
        "cagr",
        "ytd",
        "since inception",
        "performance",
        "1 day",
        "1 week",
        "1 month",
        "3 months",
        "6 months",
        "1 year",
        "3 years",
        "5 years",
    }
)
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_RETURN_WORD_RE = re.compile(r"\b(return|cagr|nav|performance|since inception|historic)\b", re.IGNORECASE)
_NAV_LINE_RE = re.compile(r"(?im)^[^\n]{0,40}\bNAV\b[^\n]{0,60}$")
_RETURN_SENTENCE_RE = re.compile(
    r"(?im)^[^\n]*\b(?:1\s*day|1\s*week|1\s*month|1\s*year|3\s*year|5\s*year|since inception)\b"
    r"[^\n]*\d+(?:\.\d+)?\s*%[^\n]*$"
)
_TOKEN_CLEAN_RE = re.compile(r"[^a-z0-9 ]+")

# The scheme pages carry a market ticker line that mixes return figures, period
# labels and the current price with genuinely useful facts (minimum SIP, expense
# ratio, AUM). Signed returns, period-label runs and the price are removed while
# unsigned percentages such as the expense ratio are kept.
_PERIOD_LABEL = r"(?:1D|1W|1M|3M|6M|1Y|3Y|5Y|1\s*Day|1\s*Week|1\s*Month|1\s*Year|3\s*Years|5\s*Years)"
_SIGNED_RETURN_RE = re.compile(r"[+-]\s?\d+(?:\.\d+)?\s*%")
_PERIOD_RUN_RE = re.compile(rf"\b{_PERIOD_LABEL}\b(?:\s+\b{_PERIOD_LABEL}\b)+")
_PERIOD_HEADER_RUN_RE = re.compile(rf"\b{_PERIOD_LABEL}\b\s+\b{_PERIOD_LABEL}\b\s+\b{_PERIOD_LABEL}\b")
_NAV_PRICE_RE = re.compile(r"\bAll\s+₹[\d,]+(?:\.\d+)?")
_TICKER_SIGNATURE_RE = re.compile(
    r"min\.?\s*for\s*sip|fund\s+size|\bAUM\b", re.IGNORECASE
)
_RESIDUAL_LABEL_RE = re.compile(
    r"\b\d+[DMY]\s+annualis?ed\b|\bannualis?ed\b|\babsolute\b|\bsince\s+inception\b",
    re.IGNORECASE,
)
_BARE_NAV_WORD_RE = re.compile(r"(?i)\bNAV\b")
_PURE_PRICE_RE = re.compile(r"₹[\d,]+(?:\.\d+)?")
_MIN_CHARS = 40

# Structured fields that may be rendered. Anything not listed here (nav, nav_date,
# sip_return, simple_return, return_stats, analysis, holdings, peerComparison, …)
# is never written to the corpus, which is how C-3 is enforced for page data.
FACT_FIELDS: Dict[str, str] = {
    "scheme_name": "Scheme name",
    "fund_name": "Fund name",
    "plan_type": "Plan",
    "scheme_type": "Option",
    "amc": "Asset management company",
    "fund_house": "Fund house",
    "expense_ratio": "Expense ratio",
    "min_sip_investment": "Minimum SIP investment (INR)",
    "min_investment_amount": "Minimum investment amount (INR)",
    "min_lump_investment": "Minimum lumpsum investment (INR)",
    "exit_load": "Exit load",
    "nfo_risk": "Riskometer",
    "benchmark": "Benchmark",
    "benchmark_name": "Benchmark index",
    "stamp_duty": "Stamp duty",
    "isin": "ISIN",
}

PREAMBLE_HEADING = "(page preamble)"
_HEADING_MARK = "\ue000"
_NUMBER_RE = re.compile(r"^[\d,]+$")


class SourceLoadError(RuntimeError):
    """Raised when a source cannot be fetched or yields unusable text."""


@dataclass
class ExtractionStats:
    """Counters describing what the loader removed and kept."""

    headings: int = 0
    tables: int = 0
    tables_dropped: int = 0
    junk_nodes: int = 0
    sections_dropped: int = 0
    preamble_chars_dropped: int = 0
    fact_fields: int = 0
    visible_chars: int = 0


@dataclass
class Extraction:
    """Result of turning one HTML document into text plus ordered sections."""

    text: str
    sections: Tuple[DocumentSection, ...]
    stats: ExtractionStats = field(default_factory=ExtractionStats)


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def fetch(url: str, settings: Settings) -> Tuple[str, int, str]:
    """GET a public page and return ``(html, status, final_url)``."""
    headers = {
        "User-Agent": settings.http_user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=settings.http_timeout_s)
    except requests.RequestException as exc:
        raise SourceLoadError(f"request failed for {url}: {exc}") from exc

    if response.status_code != 200:
        raise SourceLoadError(f"unexpected HTTP {response.status_code} for {url}")
    if not _host_allowed(response.url):
        raise SourceLoadError(
            f"redirected off the allowed hosts: {response.url!r} (not in {ALLOWED_HOST_SUFFIXES})"
        )
    if "html" not in response.headers.get("Content-Type", "").lower():
        raise SourceLoadError(f"unexpected content type for {url}: {response.headers.get('Content-Type')!r}")
    return response.text, response.status_code, response.url


def _parse_scheme_facts(html: str) -> Dict[str, str]:
    """Render whitelisted structured scheme fields as ``label: value`` pairs."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("script", id="__NEXT_DATA__")
    if node is None or not node.string:
        return {}
    try:
        payload = json.loads(node.string)
    except (ValueError, TypeError):
        return {}

    data = payload.get("props", {}).get("pageProps", {}).get("mfServerSideData")
    if not isinstance(data, dict):
        return {}

    facts: Dict[str, str] = {}
    for key, label in FACT_FIELDS.items():
        value = data.get(key)
        if value is None or value == "":
            continue
        facts[label] = _render_value(value)

    lock_in = data.get("lock_in")
    if isinstance(lock_in, dict):
        rendered = _render_lock_in(lock_in)
        if rendered:
            facts["Lock-in period"] = rendered

    historic_expense = data.get("historic_fund_expense")
    if isinstance(historic_expense, list) and historic_expense:
        latest = historic_expense[0]
        if isinstance(latest, dict):
            base = latest.get("base_expense_ratio")
            as_on = latest.get("as_on_date")
            parts = []
            if base not in (None, ""):
                parts.append(f"base expense ratio {base}%")
            if as_on:
                parts.append(f"as on {str(as_on)[:10]}")
            if parts:
                facts["Expense ratio (base, from scheme data)"] = ", ".join(parts)

    for entry in data.get("historic_exit_loads") or []:
        if not isinstance(entry, dict):
            continue
        note = entry.get("note")
        as_on = entry.get("as_on_date")
        if not note:
            continue
        suffix = f" (as on {str(as_on)[:10]})" if as_on else ""
        facts[f"Exit load note{len([k for k in facts if k.startswith('Exit load note')]) + 1}"] = (
            f"{note}{suffix}"
        )

    rta = data.get("rta_details")
    if isinstance(rta, dict):
        parts = [f"{label}: {rta[key]}" for key, label in (("rta_name", "RTA"), ("custodian_name", "Custodian")) if rta.get(key)]
        if parts:
            facts["RTA and custodian"] = "; ".join(parts)

    return facts


def _render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if not isinstance(item, (dict, list)))
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items() if not isinstance(v, (dict, list)))
    return str(value)


def _render_lock_in(lock_in: Dict[str, Any]) -> Optional[str]:
    parts = []
    for key, unit in (("years", "year"), ("months", "month"), ("days", "day")):
        value = lock_in.get(key)
        if value:
            parts.append(f"{value} {unit}{'s' if value != 1 else ''}")
    return ", ".join(parts) if parts else None


def _is_performance_table(table: Any) -> bool:
    rows = table.find_all("tr")
    if not rows:
        return False
    header_cells = rows[0].find_all(["th", "td"])
    tokens = {
        _TOKEN_CLEAN_RE.sub(" ", cell.get_text(" ", strip=True).lower()).strip()
        for cell in header_cells
    }
    if tokens & PERFORMANCE_HEADER_TOKENS:
        return True
    if len(header_cells) < 3:
        return False
    body = table.get_text(" ", strip=True).lower()
    return bool(_PERCENT_RE.search(body)) and bool(_RETURN_WORD_RE.search(body))


def _table_to_text(table: Any) -> str:
    lines: List[str] = []
    for row in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if len(cells) == 2:
            lines.append(f"{cells[0]}: {cells[1]}")
        else:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _strip_junk(soup: Any) -> int:
    removed = 0
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()
        removed += 1
    for selector in JUNK_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
            removed += 1
    return removed


def _convert_tables(soup: Any, stats: ExtractionStats) -> None:
    for table in soup.find_all("table"):
        stats.tables += 1
        if _is_performance_table(table):
            table.decompose()
            stats.tables_dropped += 1
            continue
        converted = _table_to_text(table)
        if not converted.strip():
            table.decompose()
            continue
        pre = soup.new_tag("pre")
        pre.string = converted
        table.replace_with(pre)


def _split_sections(body: Any) -> List[DocumentSection]:
    headings = body.find_all(HEADING_TAGS)
    for index, heading in enumerate(headings):
        heading.insert_before(f"{_HEADING_MARK}{index}{_HEADING_MARK}")

    text = body.get_text("\n", strip=True)
    parts = re.split(f"{_HEADING_MARK}(\\d+){_HEADING_MARK}", text)
    if len(parts) < 3:
        return []

    preamble = normalise_text(parts[0])
    sections: List[DocumentSection] = []
    if preamble:
        sections.append(DocumentSection(heading="(page preamble)", text=preamble, ordinal=0))

    for index in range(1, len(parts) - 1, 2):
        heading_index = int(parts[index])
        heading = headings[heading_index].get_text(" ", strip=True)
        content = normalise_text(parts[index + 1])
        if heading and content.lower().startswith(heading.lower()):
            content = normalise_text(content[len(heading) :])
        sections.append(
            DocumentSection(heading=heading, text=content, ordinal=len(sections))
        )
    return sections


def _strip_market_data(text: str) -> str:
    """Remove return figures, period labels and prices from a ticker line."""
    if not _TICKER_SIGNATURE_RE.search(text):
        return text

    cleaned = _PERIOD_RUN_RE.sub(" ", text)
    cleaned = _SIGNED_RETURN_RE.sub(" ", cleaned)
    cleaned = _PERIOD_HEADER_RUN_RE.sub(" ", cleaned)
    cleaned = _RESIDUAL_LABEL_RE.sub(" ", cleaned)
    cleaned = _NAV_PRICE_RE.sub(" ", cleaned)
    if _BARE_NAV_WORD_RE.search(cleaned):
        cleaned = _BARE_NAV_WORD_RE.sub(" ", cleaned)
        cleaned = _PURE_PRICE_RE.sub(" ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _clean_performance_lines(text: str) -> str:
    cleaned = _NAV_LINE_RE.sub("", text)
    cleaned = _RETURN_SENTENCE_RE.sub("", cleaned)
    cleaned = _strip_market_data(cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def extract(html: str, drop_preamble: bool = True) -> Extraction:
    """Turn one page of HTML into ordered sections plus a whitelisted facts block.

    ``drop_preamble`` removes the text that precedes the first heading. On the
    scheme pages that block is site-wide marketing navigation (measured at 1829
    identical characters, about 12% of the document) and carries no scheme fact,
    so it is removed and counted in ``ExtractionStats.preamble_chars_dropped``.
    """
    facts = _parse_scheme_facts(html)

    soup = BeautifulSoup(html, "lxml")
    stats = ExtractionStats()
    stats.fact_fields = len(facts)
    stats.junk_nodes = _strip_junk(soup)
    _convert_tables(soup, stats)

    body = soup.find("body") or soup
    headings = body.find_all(HEADING_TAGS)
    stats.headings = len(headings)
    sections = _split_sections(body)

    kept: List[DocumentSection] = []
    if facts:
        # The facts block leads the document, so it is section 0. Keeping it as a
        # real section (rather than text-only) means `text` is exactly the
        # concatenation of `sections`, which is what lets the chunker address it
        # and keeps fact offsets attributed to the right heading.
        kept.append(
            DocumentSection(
                heading=FACT_SECTION_HEADING,
                text=normalise_text("\n".join(f"{k}: {v}" for k, v in facts.items())),
                ordinal=0,
            )
        )
    for section in sections:
        if section.heading == PREAMBLE_HEADING and drop_preamble:
            stats.preamble_chars_dropped += len(section.text)
            stats.sections_dropped += 1
            continue
        if DROP_SECTION_RE.match(section.heading or ""):
            stats.sections_dropped += 1
            continue
        content = _clean_performance_lines(section.text)
        if not content:
            continue
        kept.append(
            DocumentSection(heading=section.heading, text=content, ordinal=len(kept))
        )

    text = normalise_text(
        "\n\n".join(f"{s.heading}\n{s.text}" for s in kept if s.text.strip())
    )
    stats.visible_chars = len(text)
    return Extraction(text=text, sections=tuple(kept), stats=stats)


def load_source(
    spec: SourceSpec, settings: Settings, registry: SourceRegistry
) -> Tuple[SourceRecord, ExtractionStats]:
    """Fetch, extract and record one registered source."""
    registry.require_allowed(spec.url)
    html, status, _ = fetch(spec.url, settings)
    extraction = extract(html)

    if len(extraction.text) < settings.min_extracted_chars:
        raise SourceLoadError(
            f"only {len(extraction.text)} chars extracted from {spec.url} "
            f"(floor {settings.min_extracted_chars}); the page is probably rendered by "
            f"JavaScript. Do not index an empty page - fix extraction or change the source."
        )

    fetched_at = datetime.now(timezone.utc)
    content_hash = hashlib.sha256(extraction.text.encode("utf-8")).hexdigest()
    raw_path = settings.corpus_dir / "raw" / f"{spec.source_id}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(extraction.text, encoding="utf-8")

    record = SourceRecord(
        source_id=spec.source_id,
        url=spec.url,
        scheme=spec.scheme,
        category=spec.category,
        plan_variant=spec.plan_variant,
        text=extraction.text,
        fetched_at=fetched_at,
        content_hash=content_hash,
        sections=extraction.sections,
        http_status=status,
        text_chars=len(extraction.text),
    )
    return record, extraction.stats


SOURCES_CSV_FIELDS: Tuple[str, ...] = (
    "source_id",
    "url",
    "scheme",
    "category",
    "plan_variant",
    "fetched_at",
    "http_status",
    "content_hash",
    "text_chars",
)


def write_sources_csv(records: Sequence[SourceRecord], path: Path) -> Path:
    """Write (or rewrite) ``corpus/sources.csv`` for the given records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCES_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_csv_row())
    return path


def write_records_cache(records: Sequence[SourceRecord], path: Path) -> Path:
    """Cache loaded records so inspection can run offline without re-fetching."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "source_id": record.source_id,
            "url": record.url,
            "scheme": record.scheme,
            "category": record.category,
            "plan_variant": record.plan_variant,
            "text": record.text,
            "fetched_at": record.fetched_at.isoformat(timespec="seconds"),
            "content_hash": record.content_hash,
            "http_status": record.http_status,
            "text_chars": record.text_chars,
            "sections": [
                {"heading": s.heading, "text": s.text, "ordinal": s.ordinal}
                for s in record.sections
            ],
        }
        for record in records
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_records_cache(path: Path) -> List[SourceRecord]:
    """Read previously cached records written by :func:`write_records_cache`."""
    if not path.is_file():
        raise SourceLoadError(f"records cache not found: {path}; run --stage load first")
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: List[SourceRecord] = []
    for entry in payload:
        sections = tuple(
            DocumentSection(
                heading=item.get("heading", ""),
                text=item.get("text", ""),
                ordinal=int(item.get("ordinal", index)),
            )
            for index, item in enumerate(entry.get("sections", []))
        )
        records.append(
            SourceRecord(
                source_id=entry["source_id"],
                url=entry["url"],
                scheme=entry["scheme"],
                category=entry["category"],
                plan_variant=entry["plan_variant"],
                text=entry["text"],
                fetched_at=datetime.fromisoformat(entry["fetched_at"]),
                content_hash=entry["content_hash"],
                sections=sections,
                http_status=int(entry["http_status"]),
                text_chars=int(entry["text_chars"]),
            )
        )
    return records


def load_all(
    registry: SourceRegistry,
    settings: Settings,
    only: Optional[Sequence[str]] = None,
    verbose: bool = True,
) -> Tuple[LoadReport, List[Tuple[SourceSpec, ExtractionStats]]]:
    """Load every registered source, collecting failures instead of raising."""
    wanted = set(only) if only else None
    report = LoadReport()
    stats_rows: List[Tuple[SourceSpec, ExtractionStats]] = []
    for spec in registry.all_sources():
        if wanted and spec.source_id not in wanted:
            continue
        try:
            record, stats = load_source(spec, settings, registry)
        except SourceLoadError as exc:
            report.failures[spec.source_id] = str(exc)
            if verbose:
                print(f"  FAIL {spec.source_id}: {exc}")
            continue
        report.records.append(record)
        stats_rows.append((spec, stats))
        if verbose:
            print(
                f"  ok   {spec.source_id}: {record.text_chars} chars, "
                f"{len(record.sections)} sections, "
                f"{stats.fact_fields} fact fields, sha {record.content_hash[:12]}"
            )
    if report.records:
        write_sources_csv(report.records, settings.sources_csv_path)
        write_records_cache(report.records, settings.corpus_dir / "records.json")
    return report, stats_rows
