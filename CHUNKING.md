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
| `RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR` (Q3) | **done in P3, re-opened and corrected in P5** — `RETRIEVAL_TOP_K=3` unchanged; `SIMILARITY_FLOOR` lowered 0.42 → **0.30** in `.env` and `.env.example` once K12 made the floor binding. The measured distribution, why 0.42 was wrong, and the noise it now admits are recorded in §6 "Retrieval tuning" |
| Corpus reflects sources as of the ingestion date | note in README known limits (NFR-8) |
| Holdings inside the `Fund house` blob | names only, no weights, because the loader splits on headings down to h4 and this content sits below that. Answers about "top holdings" are therefore out of scope until an official holdings source is added; the `RelevanceGate` should decline them rather than quote an unweighted list |

## 6. Retrieval tuning (Phase 3, Q3 — re-opened in Phase 5)

**Chosen values: `RETRIEVAL_TOP_K=3`, `SIMILARITY_FLOOR=0.30`** — set in `.env` and `.env.example`.
`tests/test_retriever.py` asserts these numbers appear in this section, so the file cannot drift from the config.

### Q3 re-opened in Phase 5, and why

Phase 3 set the floor at **0.42**. Phase 5 wired that floor into K12 as a binding condition
(architecture.md §8.4 requires *both* the floor and fact-type agreement before any LLM call), and
the 0.42 value immediately proved to have negative precision and negative recall against the
fact-typed retrieval that K10/K11 now use:

| | best score | 0.42 floor |
|---|---|---|
| `benchmark` — a genuine FR-8 fact | **0.344** | **rejected** |
| all 5 in-domain unsupported questions | 0.587 – 0.801 | all admitted |

So 0.42 discarded one of six true facts and rejected none of the unsupported questions. The floor
was not the control; **fact-type agreement is** — it rejects every unsupported question, because
those questions either route to `PERFORMANCE` or name a `fact_type` the corpus does not carry.
The floor is now set just below the weakest genuine fact, at **0.30**, so its job is the
achievable one: a sanity check that retrieval found something plausible at all.

This is a deliberate re-opening of a closed decision, taken with the user's agreement, and the
consequence is recorded rather than hidden:

* **12 of 15** off-domain control questions are still rejected by the floor.
* **3 now pass** — "best laptop under 50000" (0.403), "price of gold today" (0.351), "VAT in the
  UK" (0.333). These route to `OUT_OF_SCOPE`, for which K12 condition 2 is vacuous (no expected
  `fact_type`), so they now reach the LLM. They still do not get answered: the context contains
  nothing about laptops, gold or VAT, so the model emits `NOT_IN_SOURCES` and the decline path
  renders. `tests/test_validator.py::test_an_off_domain_question_still_declines` asserts that
  end-to-end, so "reaches the LLM" is never mistaken for "gets answered".
* Raising the floor above 0.344 would restore the old noise filtering at the cost of FR-8's
  benchmark fact. `test_floor_rejects_off_domain_without_discarding_real_matches` now pins the
  floor *below the weakest genuine fact* so neither trade-off can be made silently.

### Score distribution (measured, Phase 3; unchanged)

Chosen from the measured distribution below, produced by `python scripts/inspect_scores.py`
against the 166 stored chunks using the same MiniLM service as ingestion (cosine).

### Score distribution

Supported questions, best correctly-typed chunk:

| fact_type | top score | chunk |
|-----------|-----------|-------|
| `expense_ratio` | 0.770 | `hdfc_large_cap_direct_growth::1::0` |
| `exit_load` | 0.472 | `hdfc_balanced_advantage::0::3` |
| `min_sip` | 0.668 | `hdfc_elss_tax_saver::0::1` |
| `lock_in` | 0.543 | `hdfc_elss_tax_saver::0::7` |
| `riskometer` | 0.522 | `hdfc_small_cap::0::4` |
| `benchmark` | 0.344 | `hdfc_equity_flexi_cap::0::6` |

In-domain but unsupported (score of the single best hit, no fact-type filter):

| top | question |
|-----|----------|
| 0.801 | What is the current NAV of HDFC Large Cap Fund Direct Growth? |
| 0.647 | What is the exit load on the Regular plan of HDFC Large Cap Fund? |
| 0.641 | What are the top ten holdings and their weights in HDFC Small Cap Fund? |
| 0.587 | What is the average expense ratio across all five HDFC schemes? |
| 0.799 | What is the AUM or fund size of HDFC Balanced Advantage Fund? |

Off-domain control set, 30 questions with no possible answer in the corpus (highest 6 shown):

| top | question |
|-----|----------|
| 0.403 | What is the best laptop under 50000? |
| 0.351 | What is the price of gold today? |
| 0.333 | What is VAT in the UK? |
| 0.290 | How do I open a bank account? |
| 0.259 | Who is the president of India? |
| 0.255 | What is inflation? |

| set | min | median | max |
|-----|-----|--------|-----|
| supported | 0.344 | 0.533 | 0.770 |
| unsupported, in-domain | 0.587 | 0.647 | 0.801 |
| off-domain control | 0.061 | 0.160 | 0.403 |

### The floor: what it does and does not do

| candidate | off-domain admitted | unsupported admitted | supported kept |
|-----------|--------------------|----------------------|----------------|
| 0.40 | 1/30 | 5/5 | 5/6 |
| **0.42** | **0/30** | 5/5 | 5/6 |
| 0.45 | 0/30 | 5/5 | 5/6 |
| 0.48 | 0/30 | 5/5 | 4/6 |

`0.42` is chosen because it is the first value that rejects every off-domain probe while
keeping every supported question it can. Raising it to 0.48 starts discarding genuine
matches (`benchmark`) for no measured benefit.

One caveat, stated precisely: the weakest genuine match (`benchmark`, 0.344) does sit *below*
the strongest off-domain probe (0.403), so the two sets are not strictly disjoint. 0.42 is
still the right operating point because that single query is the one that overlaps — it is
admitted or rejected on the strength of its `fact_type` and scheme metadata, not its score,
and 0.42 keeps 5/6 supported while admitting 0/15 off-domain. The floor is a coarse filter;
metadata is the precise one.

**A cosine floor does not separate supported from unsupported in-domain questions.** The
two distributions overlap completely — every unsupported query scores *higher* than most
supported ones (0.587–0.801 versus 0.344–0.770). There is no threshold that rejects the
first set without also rejecting the second, so the floor cannot be chosen from score alone.
This is recorded rather than tuned around, and `test_unsupported_queries_are_recorded_as_unsupported`
fails if a future change makes the floor sufficient on its own.

The reason is structural. An unsupported question still names a real fund, so it lands on
that fund's `About ...` blob, which is a strong lexical and semantic match. Meanwhile short
fact fragments like `Benchmark: NIFTY 500 TRI` match a 9-word question weakly. Score measures
topical closeness to the corpus, not whether the answer is present.

Consequences for the design, consistent with architecture.md K12:

- The floor's real job is rejecting **off-domain** input — weather, cooking, general finance.
  That is the separation it provides, and it is clean.
- Rejecting **in-domain unsupported** questions is the job of the `fact_type` filter, not the
  floor. `retrieve_for_fact_type` returns `fact_type_match=False` hits when the corpus holds
  nothing of the asked type, which is the decline signal the gate should act on. That is the
  "or the fact block is missing" half of K12.
- Anything needing more than a scheme-page fact — NAV, AUM, holdings weights, Regular-plan
  figures — must be declined or redirected, never answered from a neighbouring chunk.

### Why `RETRIEVAL_TOP_K=3`

Across all 30 scheme x fact_type pairs, the correct chunk is **rank 1 within its own
`fact_type`** in 30/30 cases. So `k=1` would suffice for a single fact. `k=3` is chosen to
cover the one real exception in the corpus: benchmark and exit-load facts are published
twice, as an abbreviated form and a spelled-out form
(`Benchmark: NIFTY 500 TRI` and `Benchmark index: NIFTY 500 Total Return Index`).
`k=3` returns both plus a runner-up, so a future prompt builder can quote the fuller
wording without a second query. Larger k only adds weaker, wrong-scheme candidates.

### Two corrections this phase found in Phase 2's chunks

Both were found by retrieval and fixed in the retriever, not by re-chunking, so no
Phase 2 artefact changed:

1. **Short fact chunks are unrankable on their own.** `Benchmark: NIFTY 500 TRI` carries no
   scheme name, so ranking benchmark chunks by similarity returned *HDFC Balanced Advantage's*
   benchmark for a question about *HDFC Equity Fund*. The fix is a Chroma `where` filter on
   `fact_type`, applied server-side; over-fetching and re-ranking in Python did not help
   (the correct chunk sat at rank 80 of 100).
2. **Scheme scoping is required for correctness, not just precision.** The retriever now
   detects a named scheme in the question and filters on `scheme`. The filter must use the
   string actually stored in chunk metadata, not the registry: `config/sources.yaml` says
   `HDFC ELSS Tax Saver Fund - Direct Plan - Growth` while the ingested metadata says
   `HDFC ELSS Tax Saver Fund - Direct Growth`, so filtering on the registry text silently
   matched nothing and returned another fund's facts. Worth fixing in the registry for
   consistency, but the retriever does not depend on it.

### Known limitations carried forward

- A metadata-filtered HNSW search is approximate and can return fewer than `k` rows, so
  `retrieve_for_fact_type` widens the filter (`k`, then `4k`, then `12k`) before concluding
  the corpus has nothing. Reproducibility is bounded by the index, not just the model.
- `benchmark` at 0.344 is the weakest genuine match and the reason the floor is 0.30 rather
  than higher. The `TRI` / `Total Return Index` duplication is a source-publication quirk
  (Finding 3) and inflates short-fact scores downward relative to the long `About` blobs. At
  the original 0.42 this fact was rejected, so the floor cost a real FR-8 fact.
- **PRD Q5 is for P4, not P3.** A question naming no scheme ("What is the exit load?") is
  deliberately *not* scheme-scoped, so it returns hits across funds (0.656 ELSS, 0.590 and
  0.586 Balanced Advantage). Retrieval does not guess which fund was meant; `K10 INTENT ROUTE`
  in P4 must ask which of S1–S5, per architecture.md §8.1.

### Findings from Phase 4 live verification

1. **`retrieve()` alone cannot answer a fact question, so the query pipeline must pass the
   fact type.** Measured on the persisted corpus: for "What benchmark does HDFC Equity Fund
   Direct Growth track?" the top three plain hits are all `general` blobs (`About`, `Scheme
   name`), and the chunk holding the answer, `Benchmark: NIFTY 500 TRI`, ranks **24th**. The
   model replied `NOT_IN_SOURCES` against that context, which is faithful behaviour on a
   context that does not contain the fact, not a hallucination. `answer()` now accepts
   `fact_type` and prefers `retrieve_for_fact_type`, falling back to plain retrieval when the
   filter matches nothing so the decline path stays intact. In production the value comes from
   the P5 intent router; P4 callers and tests pass it explicitly. This is the reason
   `retrieve_for_fact_type` was built in P3 but not called in P4 — worth noting that the
   server-side `fact_type` filter is what makes the difference; a "general" blob can still
   outrank a real fact on cosine similarity alone.
2. **The corpus contains a genuine risk-level conflict, so risk answers depend on the path
   taken.** Every fund has 2 chunks saying `Very High Risk` and 1 saying `Moderately High
   Riskometer`. The `Very High` text sits in the scheme header blob, which carries
   `fact_type: expense_ratio` because that is what the header table is labelled; the
   `Moderately High` text is the dedicated `riskometer` chunk. Plain retrieval surfaces the
   header and the model answers *Very High*; `retrieve_for_fact_type("riskometer")` returns
   the dedicated chunk and the model answers *Moderately High*. Both are grounded in retrieved
   text, which is exactly why the numeric/citation checks could not catch this. Flagging as
   **Finding 8** for the P5 validator: a question whose `fact_type` disagrees with the text
   of a higher-ranked chunk is a source conflict, and the user should be told both readings
   rather than silently getting one. Not fixed here — deduplicating or overriding a source's
   own contradictory fields is a P2 corpus decision.


### Phase 5 resolution of Finding 8 (riskometer conflict)

Phase 4's Finding 8 flagged the `Very High` (header, `fact_type: expense_ratio`) versus
`Moderately High Riskometer` (dedicated `riskometer` chunk) contradiction and suggested the
user be shown both readings. **Phase 5 resolved it differently: the gate shows one, and it is
deterministic.**

K12's condition 2 requires an exact `fact_type` match, so a riskometer question can only be
answered from a `riskometer` chunk. Verified live on 29 Sep 2026: *"What is the riskometer
category of HDFC Small Cap Fund?"* returns **"Moderately High Riskometer"**, and the
`Very High` text never reaches the model. The same question answered through plain retrieval
returns `Very High`, which is exactly the inconsistency the condition removes.

The alternative — surfacing both readings — was rejected because it would put a
self-contradictory source in front of a user who asked one narrow question, with no way to
rank the two. The `Very High` reading is not wrong; it is a different field (the scheme page's
risk label) filed under the wrong `fact_type` by the Phase 2 header chunker. Fixing that
mislabeling is a **Phase 2 corpus decision** and remains open; until then the gate is what
guarantees the answer is at least the reading the corpus files under the fact type asked for.

`tests/test_relevance_gate.py::test_the_gate_admits_the_dedicated_riskometer_chunk` and
`::test_a_chunk_of_the_wrong_fact_type_is_rejected` pin both directions.
