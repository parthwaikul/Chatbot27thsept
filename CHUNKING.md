# CHUNKING.md — Data inspection and chunking strategy

**Deliverable:** D-6 · **PRD:** TC-2, IN-2, IN-3 · **Architecture:** K3, K4 (§7.2)
**Status:** decided from the inspected corpus. Written **before** any chunking code, as TC-2 requires.
**Corpus inspected:** 5 HDFC scheme pages, ingested 2026-09-28 (`corpus/sources.csv`).

Reproduce the evidence in this document with:

```bash
python ingest.py --stage load      # fetch, extract, write corpus/raw/*.txt
python ingest.py --stage inspect   # print the inspection report used below (no network)
```

---

## 1. What the data actually looks like

| Source | Category | Extracted chars | Sections | Whitelisted fact fields |
|--------|----------|----------------:|---------:|------------------------:|
| `hdfc_large_cap_direct_growth` | large_cap | 13,215 | 15 | 20 |
| `hdfc_equity_flexi_cap` | flexi_cap | 12,922 | 15 | 18 |
| `hdfc_elss_tax_saver` | elss | 12,786 | 15 | 18 |
| `hdfc_small_cap` | small_cap | 13,750 | 15 | 21 |
| `hdfc_balanced_advantage` | balanced_advantage | 19,398 | 19 | 21 |

**Finding 1 — the facts live in two places, and only one of them is precise.**
The server-rendered text is ~13–19K characters and contains prose about exit loads, tax and the
investment objective, but the *values* the brief asks for are in the page's `__NEXT_DATA__` payload:
`expense_ratio: 1.03`, `min_sip_investment: 100`,
`exit_load: "Exit load of 1% if redeemed within 1 year"`, `nfo_risk: "Moderately High Riskometer"`,
`benchmark_name: "NIFTY 100 Total Return Index"`, `lock_in: {"years": 3}` (ELSS only).
The loader therefore emits a `Scheme facts` block from an explicit **field whitelist** and keeps the
prose as context. Anything not on the whitelist — `nav`, `nav_date`, `sip_return`, `simple_return`,
`return_stats`, `holdings`, `peerComparison` — can never reach the corpus, which is how C-3 is
enforced structurally rather than by prompt.

**Finding 2 — most sections are small and atomic; a few are very large.**
Section length distribution per source: 12 of 15 sections are **40–600 characters**; the exceptions are
a fund-manager bio (~1,950 chars) and a trailing `Fund house` block of **7,439 chars** that swallows
the "About", "Investment Objective" and "Fund house" content because those headings sit below the
heading depth that is split on. A single flat width-based splitter would either cut the 7,439-char
block into meaningless fragments or leave it as one oversized chunk.

**Finding 3 — fact lines are already one-claim-per-line.**
Every value in the facts block is a single `Label: value` line (expense ratio, minimum SIP, minimum
investment, exit load, riskometer, benchmark, stamp duty, ISIN, RTA/custodian, exit-load history
notes). These lines are semantically atomic and must never be split.

**Finding 4 — the same fact appears in up to three places on a page.**
For the Large Cap fund: `exit_load` appears in the facts block, in the "Exit load" glossary definition
under "Understand terms", and in the "Exit load" fee section. Chunking must keep them distinguishable
so the answer quotes the authoritative value, and retrieval must not return three near-duplicates for
one question.

**Finding 5 — the page contradicts itself on risk.**
`nfo_risk` = `Moderately High Riskometer`, while the visible text says the fund is "rated Very High
risk" (the older star-rating scale). Both are stripped-into the corpus. The schema labels the value
`Riskometer`, so the bot answers the *riskometer* and must not present the star rating as the
riskometer. Recorded as a known conflict, not silently merged.

**Finding 6 — performance and price content had to be removed at load time.**
Left in place it was: `+8.71 %`, `3Y annualised`, the run `1D 1M 6M 1Y 3Y 5Y`, the live price
`₹1,189.08`, the "Annualised returns" and "Absolute returns" sections, and 3 of 4 tables per page.
All are removed. A verification scan of `corpus/raw/*.txt` for signed returns, period runs, CAGR and
"annualised" now returns **0 matches** across all five files. Percentages that remain are fee, tax and
ratio facts only: `1%` exit load, `0.005%` stamp duty, `0.57–1.21%` expense ratios, exit-load slabs.

**Finding 7 — 1,829 characters per page (≈12%) were site-wide marketing chrome.**
Identical on all five pages ("Stocks / Intraday / ETF Screener / IPO / Pricing / Blog…"). It precedes
the first heading, carries no scheme fact, and would be retrieved by almost any question, so it is
dropped and the dropped count is recorded in `ExtractionStats.preamble_chars_dropped`.

---

## 2. Chunking strategy

**`CHUNK_STRATEGY=section_atomic_recursive`** — two-level, boundary-aware splitting.

### Why this strategy suits this data

1. **Section boundaries are the natural unit.** The corpus is already section-structured and the facts
   the brief asks about (expense ratio, exit load, minimum SIP, lock-in, riskometer, benchmark) each
   live in their own small section (Finding 2). Splitting *on* those boundaries keeps a fact and the
   prose that qualifies it together, and keeps an exit-load slab or fee table intact — the specific
   failure the PRD risk table warns about.
2. **Most sections are already under the target size,** so the common case is one chunk per section
   and no fragmentation of short facts.
3. **The few oversized sections need splitting without losing context,** hence the recursive level,
   which repeats the parent heading in each part so a split chunk is still self-describing.
4. **`fact_type` can only be assigned reliably with boundary awareness** (Finding 3), and
   `RelevanceGate` (K12) depends on `fact_type` agreement to decide whether a retrieval is on-topic.
5. **Scheme boundaries are never crossed,** so a citation can never mix two funds (C-1, NFR-6).

### Parameters

| Parameter | Value | Why this value |
|-----------|-------|----------------|
| `CHUNK_SIZE` | **1200** characters | Above the largest real fact section (600 chars) so a fact block plus its heading stays in one chunk, and well below the 7,439-char `Fund house` block that must be split. Keeps prompts small enough that top-k context is unambiguous. |
| `CHUNK_OVERLAP` | **150** characters (≈12%) | Only used on the recursive level. Large enough to carry the section heading and the preceding sentence across a split; small enough that neighbouring chunks do not become near-duplicates that crowd out other results (Finding 4). |
| Minimum chunk | 60 characters | Observed smallest real section is 40 chars ("Exit load"); anything shorter is merged into its neighbour rather than embedded alone. |
| Split unit on the recursive level | line, then word | Never splits a `Label: value` line. |

### Rules

1. **Never cross a scheme boundary.** One document (one scheme) per chunk set.
2. **Never split an atomic fact line** (`Label: value`).
3. **Never split a table-derived block** (fee table, exit-load slab) — the loader has already converted
   each row to one `key: value` line and the whole table is one unit.
4. **A section ≤ `CHUNK_SIZE` becomes exactly one chunk**, with `is_atomic=true`.
5. **A section > `CHUNK_SIZE` is split recursively** on line boundaries with `CHUNK_OVERLAP`,
   `is_atomic=false`, and the parent heading recorded on every part.
6. **Empty or boilerplate-only sections are dropped** and counted.

### Metadata kept on every chunk

| Field | Example | Used by |
|-------|---------|---------|
| `source_url` | `https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth` | Citation (K16) — the UI link is derived from this, never from model text |
| `scheme` | `HDFC Large Cap Fund - Direct Growth` | Scheme scoping (FR-1) |
| `category` | `large_cap` | Intent routing (K10) |
| `plan_variant` | `direct_growth` | Prevents Direct/Regular confusion (PRD Q8) |
| `section` | `Expense ratio` | Heading locator (IN-3) |
| `fact_type` | `expense_ratio` | `RelevanceGate` agreement check (K12) |
| `authority` | `scheme_facts` \| `page_text` | Orders context so the precise value outranks prose that repeats it (Finding 4) |
| `source_fetched_at` | `2026-09-28T09:14:02+00:00` | `Last updated from sources:` value (K17) |
| `content_hash` | `5c117494f665…` | Provenance, change detection, idempotent re-ingest (E-8) |
| `chunk_ordinal` | `0`, `1`, … | Ordering within a section |
| `is_atomic` | `true` / `false` | Lets P3 distinguish whole fact blocks from split prose when reading `chunks.txt` |
| `char_len` | `203` | Cheap size guard in the eval harness |

`chunk_id` = `{source_id}::{section_ordinal}::{part}` — deterministic, which is what makes
`upsert` idempotent and re-ingestion a no-op (IN-5, E-8).

`fact_type` is assigned by matching the section heading first, then the chunk text against the
inspection patterns, defaulting to `general`.

---

## 3. Verification plan for P2 (must pass before P3)

1. `chunks.txt` (D-7) is complete: one block per chunk with all twelve metadata fields.
2. No chunk contains a fragment of an exit-load slab or a fee table row.
3. Every chunk's `fact_type` matches its content, and each of the six fact types resolves on the
   expected scheme.
4. Two consecutive ingestions leave the ChromaDB count unchanged (E-8).
5. A deliberate `content_hash` change rewrites only the affected chunks.

### Results (Phase 2, run against the five live sources)

| Check | Result |
|-------|--------|
| Chunk count | **166** (large cap 31, flexi cap 31, ELSS 30, small cap 31, balanced advantage 38) |
| Atomic / split | 112 atomic fact chunks, 54 split parts |
| Size | min 14, median 217, p90 1196, max **1200** — **0 chunks over `CHUNK_SIZE`** |
| Undersized page chunks | **0**; the 36 sub-60-char chunks are all atomic fact rows, which rule 2 forbids merging |
| Fact rows truncated | **0** — every `Label: value` row appears whole, once, and still matches `corpus/raw/` |
| Fee percentages keeping their conditions | all — e.g. `1% if redeemed within 1 year`, `0.005% (from July 1st, 2020)` |
| C-3 leak scan over `chunks.txt` | **0** matches for CAGR, annualised, `1D 1M 6M`, absolute returns, NAV |
| Second full run | `ingested 0 / unchanged 5 / rewritten 0`, store stayed at 166 (E-8 proven) |
| Vectors | all 384-dim, `normalize_embeddings=True` |

One percentage deserves a note so nobody deletes it later:
`returns exceeding Rs 1.25 lakh in a financial year are taxed at 12.5%` is a **capital-gains tax
slab** from the Tax implication section, not a performance claim, and it is the answer to the ELSS
lock-in question. Every other percentage in the corpus is a fee, tax or ratio fact.

The `Fund house` section behaved exactly as Finding 2 predicted: 7,439 chars became 8 parts of
≤1200 chars, each repeating the heading, each carrying 150 chars of overlap — verified by
confirming the last line of part *n* reappears in part *n+1*.

### Change-detection design note

Change detection reads `corpus/embedded.json`, **not** `corpus/sources.csv`. On `--stage all` the
load stage rewrites `sources.csv` before the pipeline runs in the same invocation, so comparing
against it would mean every hash always matched and a genuine page change would never be detected.
`embedded.json` records `{source_id: {content_hash, chunk_ids}}` for what is actually in the store,
which also makes orphan deletion exact: a chunk id a source no longer produces is deleted rather
than left behind. If the collection is empty — because `chroma/` is gitignored and was wiped — the
run is forced to re-ingest instead of reporting a false no-op.

---

## 4. Corpus gap found during inspection — blocks one of the seven required query types

| Fact type | Status | Evidence |
|-----------|--------|----------|
| `expense_ratio` | available on all 5 | facts block, e.g. `Expense ratio: 1.03` |
| `exit_load` | available on all 5 | `Exit load: Exit load of 1% if redeemed within 1 year` + dated history notes |
| `min_sip` | available on all 5 | `Minimum SIP investment (INR): 100` (`500` for ELSS) |
| `riskometer` | available on all 5 | `Riskometer: Moderately High` / `… Riskometer` |
| `benchmark` | available on all 5 | `Benchmark index: NIFTY 100 Total Return Index` |
| `lock_in` | available on **ELSS only** — correct, lock-in is an ELSS feature | `Lock-in period: 3 years` |
| `statement_guide` | **MISSING on all 5** | no match for capital-gains statement, tax statement, or statement download anywhere in the extracted text |

**This is PRD open question Q1 and it needs a team decision before the sample Q&A is written.**
The brief requires the assistant to answer *"How to download capital-gains statement?"*, but the five
broker scheme pages contain no statement or tax-document guidance at all. The brief also explicitly
lists *"statement/tax-doc guides"* among the AMC/SEBI/AMFI public pages to collect, so the intended
resolution is to add an **official** source to `config/sources.yaml` — for example the AMC's
statement/tax-statement help page or the relevant AMFI/SEBI investor-education page.

Rules for whoever resolves this:

- The URL must be added to `config/sources.yaml` and re-verified with `python ingest.py --stage load`.
  It is not fetched or cited otherwise (C-1).
- It must be a public official page, not a third-party blog (C-1).
- No URL is guessed here on purpose: an unverified link in the registry is worse than a recorded gap.
- Until it is resolved, the bot must **decline** capital-gains-statement questions through
  `RelevanceGate` rather than improvise — which the architecture already does.

## 5. Other open items carried forward

| Item | Status |
|------|--------|
| `CHUNK_SIZE` / `CHUNK_OVERLAP` / `CHUNK_STRATEGY` | **done in P2** — set to 1200 / 150 / `section_atomic_recursive` in `.env` and `.env.example`; `chunker.py` reads them from config and a test asserts they still match §2, so the table above cannot go stale |
| Riskometer vs star-rating conflict (Finding 5) | answer the riskometer only; note in README known limits |
| `RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR` (Q3) | to be tuned during P3 from the observed score separation, then appended to this file under "Retrieval tuning" |
| Corpus reflects sources as of the ingestion date | note in README known limits (NFR-8) |
| Holdings inside the `Fund house` blob | names only, no weights, because the loader splits on headings down to h4 and this content sits below that. Answers about "top holdings" are therefore out of scope until an official holdings source is added; the `RelevanceGate` should decline them rather than quote an unweighted list |
