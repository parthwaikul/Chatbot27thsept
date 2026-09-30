# Architecture — Mutual Fund FAQs (Facts-Only Q&A) · RAG Chatbot

| Field | Value |
|-------|-------|
| Document | Software / System Architecture |
| Implements | `docs/PRD.md` (v. derived from `docs/Problemstatement.txt`) |
| Pipeline | Ingestion: Load → Chunk → Embed → Store · Query: Question → Embed → Retrieve → LLM → Answer |
| Mandatory stack (from brief) | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, local) · ChromaDB (persisted) · Groq LLM (key in `.env`) |
| Status | Design ready; `top-k`, similarity floor and "Last updated" derivation are PENDING (PRD §16) |

Every architectural decision below is traceable to a PRD requirement ID (Section 22). Where the PRD
leaves a decision open, this document names the **mechanism** and exposes the value as configuration
marked `PENDING`, rather than inventing an answer.

---

## 1. Purpose

This document describes *how* the system in the PRD is built: component boundaries, module layout, data
contracts, control flow, prompt and guardrail contracts, configuration, failure behaviour, and the test
strategy that proves the PRD's acceptance checks (E-1 … E-9).

It does **not** restate product requirements. Where this document adds an engineering mechanism not
named in the PRD, it is marked **[engineering decision]** and is justified by an acceptance check.

## 2. Architecture Drivers

Ranked by how much they shape the design.

| # | Driver | Source | Design consequence |
|---|--------|--------|--------------------|
| D1 | Answer only from official public sources; never improvise | C-6, E-6 | Retrieval + relevance gate are *preconditions* to any LLM call, not post-hoc filters. |
| D2 | Every answer ≤ 3 sentences with exactly one source link | C-4, E-1, E-2 | Enforced in the prompt **and** by a post-LLM validator that can retry or hard-fail. |
| D3 | No advice, no performance/return claims | C-3, C-5, E-3, E-4 | Two layers: a deterministic intent screen before the LLM, plus output validation after it. |
| D4 | No PII accepted or stored | C-2, E-5, NFR-5 | PII screen is stage 0 of the query path — before embedding, before logging, before any persistence. |
| D5 | Ingestion runs once; not on every restart | TC-3, G10, E-8 | ChromaDB is persistent and ingestion is idempotent (content-addressed IDs + upsert). |
| D6 | Same embedding model for chunks and questions, 384-dim, no API key | TC-1 | One shared `EmbeddingService` singleton; dimension asserted at startup. |
| D7 | Groq only; key in `.env`, never committed | TC-4, NFR-2 | Single `LLMClient` seam; all key access through config loader; no key in code/logs. |
| D8 | Chunking is decided after inspecting real data | TC-2, IN-2, FR-4 | The chunker is a **strategy interface** with a documented output contract; parameters are set at the M0 gate. |
| D9 | Every chunk auditable to a public URL; chunks human-inspectable | NFR-6, FR-5 | `source_url` is required metadata on every chunk; `chunks.txt` is a first-class build artifact. |
| D10 | Tiny UI | FR-15 | Thin presentation layer; all logic lives in a callable pipeline that a notebook can invoke without the UI. |

## 3. System Context

```
┌──────────────┐        HTTPS (public pages only)         ┌──────────────────────┐
│  Public      │◀───────────────────────────────────────▶│  Source Loader       │
│  scheme      │        S1..S5 + official AMC/SEBI/AMFI  │  (allowlist fetch)   │
│  pages       │                                          └───────────┬──────────┘
└──────────────┘                                                      │ raw text + source_url
                                                              ┌───────▼──────────┐
                                                              │  Corpus Store    │
                                                              │  corpus/raw/*.txt│
                                                              │  corpus/sources  │
                                                              │  .csv            │
                                                              └───────┬──────────┘
                                                                      │ offline
┌──────────────┐   question   ┌──────────────────────────────────────────▼───────────┐
│  User        │─────────────▶│  Query Pipeline                                            │
│  (retail     │              │  0 PII screen → 1 embed → 2 retrieve → 3 relevance gate │
│  user /      │◀─────────────│  4 LLM (Groq) → 5 answer validation → 6 render           │
│  support     │   answer +   └───────┬──────────────┬──────────────┬─────────────────┘
│  agent)      │   1 citation         │              │              │
└──────────────┘                       │              │              │
                                       ▼              ▼              ▼
                              ┌────────────┐ ┌────────────┐ ┌──────────────────┐
                              │ ChromaDB   │ │ MiniLM     │ │ Groq API         │
                              │ (persist)  │ │ (local)    │ │ (key from .env)  │
                              └────────────┘ └────────────┘ └──────────────────┘
```

All model access is behind three interfaces — `EmbeddingService`, `VectorStore`, `LLMClient` — so the
pipeline is testable with in-memory fakes and no network.

## 4. Component Map

| # | Component | Responsibility | PRD |
|---|-----------|----------------|-----|
| K1 | `SourceRegistry` | The allowlist of public URLs (S1–S5 + approved official pages). Nothing not in it can enter the corpus. | FR-1, FR-3, C-1 |
| K2 | `SourceLoader` | Fetches a registered URL, extracts main text, strips nav/boilerplate/return blocks, records fetch metadata. | IN-1, C-1, C-3 |
| K3 | `CorpusInspector` | Renders the loaded corpus for human inspection; produces the observations that justify the chunking strategy. | IN-2, TC-2, FR-4 |
| K4 | `ChunkStrategy` (interface) | Splits one document into chunks with metadata. Implementation chosen at the M0 gate. | IN-3, TC-2 |
| K5 | `ChunkWriter` | Serialises every chunk + metadata to `chunks.txt` for inspection. | FR-5, D-7 |
| K6 | `EmbeddingService` | Wraps `all-MiniLM-L6-v2`; 384-dim; L2-normalised; used for both chunks and questions. | TC-1, IN-4, Q-2 |
| K7 | `VectorStore` | ChromaDB persistent collection; upsert-only writes; cosine top-k query. | TC-3, IN-5, Q-3 |
| K8 | `IngestionPipeline` | Load → Chunk → Embed → Store, idempotent, with a `already_indexed` short-circuit. | IN-1…IN-5, G10 |
| K9 | `PIIScreen` | Deterministic detection + refusal for PAN/Aadhaar/account no./OTP/email/phone. Runs first. | C-2, Q-1, E-5 |
| K10 | `IntentRouter` | Classifies the question: FACT / ADVICE / PERFORMANCE / PII / AMBIGUOUS / OUT_OF_SCOPE. | C-3, C-5, FR-10, FR-11 |
| K11 | `Retriever` | Embeds the question, queries ChromaDB, returns top-k chunks with scores + metadata. | Q-2, Q-3 |
| K12 | `RelevanceGate` | Declines when the best match is below the floor or the fact block is missing. | Q-4, E-6 |
| K13 | `PromptBuilder` | Renders the system prompt, context block, and question into a single request; injects one source link and the freshness line. | Q-5, FR-9, FR-14 |
| K14 | `LLMClient` | Groq call. Key read from `.env` via config only. Lowest temperature. | TC-4, Q-5, FR-16 |
| K15 | `AnswerValidator` | Post-LLM enforcement of ≤3 sentences, exactly one link, no advice lexicon, no returns lexicon, freshness line present. Retry once, then safe-decline. | E-1, E-2, E-3, E-4, E-7 |
| K16 | `ResponseRenderer` | Produces the answer block, the single citation link, and the refusal variants. | Q-6, Q-7, §11 |
| K17 | `FreshnessResolver` | Computes the value behind "Last updated from sources: ". `PENDING` Q4. | FR-14, G7 |
| K18 | `ChatUI` | Welcome line, 3 example questions, "Facts-only. No investment advice." note, chat + citations. | FR-15, FR-20 |
| K19 | `EvalHarness` | Runs the acceptance checks and the refusal test set; prints a pass/fail table. | E-1…E-9, FR-19 |
| K20 | `Config` | Typed settings from env + `.env`; single source for paths, model ids, thresholds. | TC-4, NFR-2 |

## 5. Repository Layout

```
mf-faq-rag/
├── app.py                          # UI entrypoint (FR-15) — thin; calls QueryPipeline
├── ingest.py                       # CLI: python ingest.py  (runs Stage A once)
├── eval.py                         # CLI: python eval.py    (E-1 … E-9 table)
├── CHUNKING.md                     # M0 gate output: observations → strategy (D-6, FR-4)
├── README.md                       # setup, scope, known limits (D-3)
├── SOURCES.md                      # the source list of URLs used (D-2)
├── SAMPLE_QA.md                    # 5–10 queries + answers + links (D-4)
├── DISCLAIMER.txt                  # exact disclaimer snippet shown in UI (D-5)
├── .env                            # GROQ_API_KEY — gitignored, never committed (TC-4)
├── .env.example                    # committed template, no secrets (NFR-2)
├── .gitignore                      # .env, chroma/, logs/, .venv/
├── requirements.txt
├── config/
│   └── sources.yaml                # K1 allowlist: S1..S5 + approved official pages
├── corpus/
│   ├── raw/<source_id>.txt         # load-time snapshot (audit trail, NFR-6)
│   └── sources.csv                 # source_id,url,scheme,category,fetched_at,http_status
├── chroma/                         # persisted vector store (TC-3, E-8) — gitignored
├── chunks.txt                      # K5 output: every chunk + metadata (D-7, FR-5)
├── logs/                           # structured, PII-redacted (NFR-5) — gitignored
└── src/
    ├── config.py                   # K20
    ├── ingest/
    │   ├── registry.py             # K1
    │   ├── loader.py               # K2
    │   ├── inspector.py            # K3
    │   ├── chunker.py              # K4  (strategy interface + selected implementation)
    │   ├── chunk_writer.py         # K5
    │   ├── pipeline.py             # K8
    │   └── models.py               # Document, Chunk, SourceRecord dataclasses
    ├── rag/
    │   ├── embeddings.py           # K6
    │   ├── vector_store.py         # K7
    │   ├── retriever.py            # K11
    │   ├── relevance_gate.py       # K12
    │   └── prompts.py              # K13
    ├── guardrails/
    │   ├── pii.py                  # K9
    │   ├── intent.py               # K10
    │   ├── validator.py            # K15
    │   └── messages.py             # canonical refusal + disclaimer strings (D-5)
    ├── llm/
    │   └── groq_client.py          # K14
    ├── query/
    │   └── pipeline.py             # Stage B orchestration (Q-1 … Q-7)
    ├── ui/
    │   └── app_view.py             # K18
    └── eval/
        ├── harness.py              # K19
        └── cases.py                # factual + refusal test sets
```

Ingestion and query share `src/rag/embeddings.py` and the same on-disk collection. There is exactly one
embedding model instance in the process (D6).

## 6. Data Contracts

### 6.1 `SourceRecord` — output of K2

| Field | Type | Notes |
|-------|------|-------|
| `source_id` | str | Slug, e.g. `hdfc_large_cap_direct_growth`. Primary key for idempotency. |
| `url` | str | Must be in the K1 allowlist. Persisted into every chunk (D9). |
| `scheme` | str | One of S1–S5, or `GENERAL` for non-scheme official pages. |
| `category` | str | `large_cap` / `flexi_cap` / `elss` / `small_cap` / `balanced_advantage` / `general` |
| `plan_variant` | str | `direct_growth` — makes plan identity explicit in every answer path (PRD Q8). |
| `text` | str | Extracted main text, boilerplate and market/return blocks removed. |
| `fetched_at` | datetime | UTC; feeds the freshness line via K17. |
| `content_hash` | str | SHA-256 of normalised text → drives change detection and idempotency. |
| `sections` | list | Ordered `{heading, text, ordinal}` blocks, the unit the chunker splits. |

### 6.2 `Chunk` — output of K4, input to K5/K6/K7

| Field | Type | Required | Rationale |
|-------|------|----------|-----------|
| `chunk_id` | str | yes | `{source_id}::{section_ordinal}::{part}` — deterministic ⇒ upsert-safe (E-8). |
| `text` | str | yes | The fact block(s) the LLM may cite. |
| `metadata.source_url` | str | yes | Citation source; every answer's link comes from here, never from the model (FR-9, NFR-6). |
| `metadata.scheme` | str | yes | Scheme scoping (FR-1). |
| `metadata.category` | str | yes | Query routing. |
| `metadata.plan_variant` | str | yes | Prevents Direct/Regular confusion (PRD risk table, Q8). |
| `metadata.section` | str | yes | Heading locator. |
| `metadata.fact_type` | str | yes | `expense_ratio` / `exit_load` / `min_sip` / `lock_in` / `riskometer` / `benchmark` / `statement_guide` / `general`. Drives `IntentRouter` and the relevance gate. |
| `metadata.source_fetched_at` | str | yes | Freshness line value. |
| `metadata.content_hash` | str | yes | Audit + dedupe. |

PRD requires at least `source_url`, `scheme`, `category` and a section locator (IN-3); `fact_type` and
`plan_variant` are **[engineering decisions]** added because E-4 (no performance claims) and the
Direct-vs-Regular risk both depend on classifying what a chunk *is*.

### 6.3 ChromaDB collection schema (K7)

| Setting | Value | Reason |
|---------|-------|--------|
| client | `chromadb.PersistentClient(path=config.chroma_dir)` | TC-3, D5 |
| collection | `mf_faq` | isolated per project |
| `metadata` | `{"hnsw:space": "cosine"}` | matches Q-3 |
| `ids` | `chunk_id` from §6.2 | upsert ⇒ idempotent re-ingest (IN-5, E-8) |
| `documents` | `chunk.text` | inspectable via `chroma.txt` tooling |
| `metadatas` | `metadata` dict above | citation + routing |
| `embeddings` | 384-dim L2-normalised from MiniLM | TC-1 |

Writes use `upsert`, never `add`. Re-running ingestion after a content-hash match is a no-op; after a
content change, only changed `chunk_id`s are rewritten.

## 7. Stage A — Data Ingestion

### 7.1 Flow

```
python ingest.py
      │
      ▼
 K8 IngestionPipeline
      │
      ├─(1) LOAD ── K1 registry ──▶ K2 loader ──▶ corpus/raw/*.txt + corpus/sources.csv
      │            allowlist only; strip nav/boilerplate/return blocks
      │
      ├─(2) INSPECT ── K3 ──▶ console report + CHUNKING.md inputs
      │            observations: table density, per-scheme section presence,
      │            where each fact type lives, boilerplate noise ratio
      │            ── GATE (IN-2, TC-2): if CHUNKING.md has no size/overlap/
      │               metadata/rationale recorded, STOP.
      │
      ├─(3) CHUNK ── K4 strategy ──▶ List[Chunk]  ──▶ K5 ──▶ chunks.txt
      │            scheme boundaries + fact blocks kept atomic
      │
      ├─(4) EMBED ── K6 MiniLM 384-dim (assert dim==384) ──▶ 5) STORE ── K7 upsert
      │
      └─▶ short-circuit on next run: unchanged content_hash ⇒ skip 3-5 (E-8)
```

### 7.2 K4 — the chunking strategy seam (TC-2)

The PRD assigns this decision to the agent after inspecting the data, so the architecture fixes the
**contract**, not the numbers.

```python
class ChunkStrategy(Protocol):
    def split(self, doc: SourceRecord) -> list[Chunk]: ...

class StructuralChunker:      # selected at M0; name and params recorded in CHUNKING.md
    def __init__(self, chunk_size: int, overlap: int, min_chunk_chars: int): ...
```

Selection criteria recorded in `CHUNKING.md` before implementation (IN-2 acceptance):

1. Scheme pages are section-structured with dense fact tables, so chunks must align to **section and
   fact-block boundaries**; a flat fixed-width splitter risks cutting an exit-load slab or a fee table
   in half (PRD risk table).
2. `fact_type` must be assignable at chunk time, which requires boundary awareness.
3. `chunk_size` / `overlap` are set from observed fact-block lengths after M1, and the choice is
   re-verified against the E-6 acceptance queries.
4. `chunks.txt` is regenerated on every ingestion run and is the artifact used to verify that no fact
   block is split.

### 7.3 Failure behaviour

| Failure | Behaviour |
|---------|-----------|
| URL unreachable / non-200 | Log, mark `SourceRecord` failed, continue; ingestion reports a non-zero exit if any source failed. |
| Extracted text below a floor length (JS-rendered page) | Fail loudly with the URL — the M0 mitigation. Do **not** silently index an empty page. |
| Out-of-allowlist URL encountered in a page | Not fetched. Only registered URLs enter the corpus (C-1). |
| Content hash unchanged | Skip re-embed (D5, E-8). |

## 8. Stage B — Query Pipeline

### 8.1 Control flow

```
question
   │
   ▼
(0) K9 PII SCREEN ──────────────── hit ──▶ refusal (PII) ──────────────▶ END
   │ no hit (input never stored or logged)
   ▼
(1) K10 INTENT ROUTE
   ├── ADVICE      ──▶ refusal (facts-only) + educational link ─────────▶ END
   ├── PERFORMANCE ──▶ factsheet redirect, no computation (C-3) ───────▶ END
   ├── AMBIGUOUS   ──▶ clarify: which of S1–S5? (PRD Q5) ─────────────▶ END
   └── FACT / OUT_OF_SCOPE
   │
   ▼
(2) K11 EMBED question (K6, same model, 384-dim) ──▶ (3) Chroma cosine top-k
   │
   ▼
(4) K12 RELEVANCE GATE ── below floor / wrong fact_type ──▶ decline + link ─▶ END
   │
   ▼
(5) K13 PROMPT: system rules + numbered context chunks + question
   │
   ▼
(6) K14 GROQ (lowest temperature) ── error ──▶ K16 safe error state (NFR-4)
   │
   ▼
(7) K15 VALIDATE: ≤3 sentences · exactly 1 link · no advice lexicon ·
   │                 no returns lexicon · freshness line present
   │      fail ──▶ one repair retry ──fail──▶ K16 safe decline (never render bad output)
   ▼
(8) K16 RENDER: answer + citation link + "Last updated from sources: <K17>"
```

Steps 0, 1, 4 and 7 are what make the PRD's constraints *structural* rather than prompt-suggested: a
non-compliant output is not rendered, it is replaced by a safe decline.

### 8.2 K9 — PII screen (C-2)

Runs before embedding, before logging, before any persistence. Detection is deterministic regex plus
context keywords; on a hit the pipeline returns a refusal and the raw text is never written to
`logs/`, `chunks.txt`, or the vector store (NFR-5, E-5).

| Class | Detection |
|-------|-----------|
| PAN | `[A-Z]{5}[0-9]{4}[A-Z]` |
| Aadhaar | 12 digits with optional separators, plus Aadhaar context keyword |
| Account number | 8–18 digits with an account/folio/holder context keyword |
| OTP | 4–6 digits with an OTP/verification context keyword |
| Email | standard address pattern |
| Phone | `+91…` or 10-digit Indian mobile pattern |

Context keywords are required for the numeric classes precisely to avoid refusing benign factual
questions that contain numbers (e.g. a minimum SIP amount).

### 8.3 K10 — Intent router

Deterministic first (keyword/pattern based, ordered), LLM-assisted only as a fallback for ambiguous
input. Categories and their handling follow PRD §11.

| Category | Trigger examples | Handling |
|----------|-------------------|----------|
| `ADVICE` | should I buy, should I sell, which is better, is it right for me, suggest | Refusal + educational link (C-5). Educational link source: `PENDING` Q2. |
| `PERFORMANCE` | return, returns, performance, CAGR, which performed better, best performing | Factsheet redirect; no computation, no comparison (C-3). |
| `PII` | handled upstream by K9 | Refusal (C-2). |
| `AMBIGUOUS` | fact question with no scheme named | Clarify which of S1–S5 (Q5). |
| `FACT` | expense ratio, exit load, minimum SIP, lock-in, riskometer, benchmark, how to download statement | RAG path. |
| `OUT_OF_SCOPE` | anything else | Retrieval attempted, then declined by K12 if unsupported. |

### 8.4 K12 — Relevance gate

Two conditions must both hold to proceed to the LLM:

1. **Score floor** — best cosine similarity ≥ `SIMILARITY_FLOOR` (`PENDING` Q3).
2. **Fact-type agreement** — at least one retrieved chunk's `fact_type` matches the intent's expected
   `fact_type` (e.g. a `lock_in` question must hit a `lock_in` chunk).

If either fails, the system declines with the facts-only message and an educational link. This is the
component that satisfies E-6 ("no invented facts on out-of-scope questions").

### 8.5 K13 / K14 — Prompt contract

System prompt, rendered once and reused for all queries:

```
You are a mutual fund FAQ assistant. You answer factual questions about the
schemes listed in the CONTEXT, using only that context.

Hard rules:
1. Use only facts present in CONTEXT. If CONTEXT does not contain the answer,
   reply exactly: NOT_IN_SOURCES
2. Answer in at most 3 sentences. No preamble, no closing pleasantries.
3. End with exactly one source link, copied verbatim from the chunk's source_url.
   Never invent, shorten, or modify a URL.
4. Never give advice. No buy/sell calls, no suitability opinions, no
   "should I", "better", "best for you" language.
5. Never state, compute, compare, or rank returns or performance. If the
   question asks for performance, reply exactly: FACTSHEET_REDIRECT
6. Never request or repeat personal identifiers (PAN, Aadhaar, account number,
   OTP, email, phone).
7. Do not use outside knowledge, even for facts you believe are true.
```

User message: numbered context chunks (each carrying its `source_url`, `scheme`, `section`,
`fact_type`) followed by the question. The link the model may emit is pre-supplied by K13 and must be
echoed verbatim — the renderer additionally recomputes the citation from chunk metadata so a hallucinated
URL cannot reach the UI (FR-9, D9).

### 8.6 K15 — Answer validator

| Check | Rule | On failure |
|-------|------|------------|
| Grounding marker | Output is `NOT_IN_SOURCES` | Route to decline path (no LLM output shown). |
| Performance marker | Output is `FACTSHEET_REDIRECT` | Route to factsheet redirect. |
| Length | ≤ 3 sentences after stripping the citation and freshness lines | One repair retry, then safe decline. |
| Citation | Exactly one URL, and it must be a member of the retrieved chunks' `source_url` set | One repair retry, then re-render with the citation taken from the top chunk's metadata. |
| Advice lexicon | No matches from the advice lexicon (`should`, `recommend`, `best for you`, `I would suggest`, `suitable for`, …) | Safe decline. |
| Returns lexicon | No matches from the returns lexicon (`% return`, `CAGR`, `performed better`, `outperformed`, …) | Factsheet redirect. |
| Freshness | `Last updated from sources: …` present | Renderer appends it from K17. |

E-2 and E-3 are therefore measured by the validator itself, so `eval.py` reports them directly.

### 8.7 K17 — Freshness value (`PENDING` Q4)

`FreshnessResolver` is an interface with candidate strategies — ingestion timestamp
(`source_fetched_at`), page-declared date, or factsheet date — selected once Q4 is answered. The
renderer emits the mandated string `Last updated from sources: <value>` regardless of strategy, so no
downstream component changes when Q4 is decided.

## 9. Configuration

| Variable | Used by | Default | Notes |
|----------|---------|---------|-------|
| `GROQ_API_KEY` | K14 | — | **Secret.** `.env` only, gitignored, never logged (TC-4, NFR-2). |
| `GROQ_MODEL` | K14 | configurable | Groq model id; the brief fixes the provider, not the model. |
| `GROQ_TEMPERATURE` | K14 | lowest supported | NFR-3. |
| `GROQ_TIMEOUT_S` / `GROQ_MAX_RETRIES` | K14 | 30 / 2 | Keeps a rate-limited demo responsive (NFR-4). |
| `GROQ_MAX_TOKENS` | K14 | `300` | Generation ceiling. An answer is ≤3 sentences plus one URL and the freshness line, so this is generous; it bounds runaway output, it does not shape the answer. |
| `EMBEDDING_MODEL` | K6 | `sentence-transformers/all-MiniLM-L6-v2` | TC-1. Must not be changed without re-ingesting (E-8). |
| `EMBEDDING_DIM` | K6 | `384` | Asserted at startup; mismatch aborts. |
| `CHROMA_DIR` | K7 | `./chroma` | TC-3. |
| `CHROMA_COLLECTION` | K7 | `mf_faq` | |
| `CORPUS_DIR` | K2/K5 | `./corpus` | |
| `CHUNK_SIZE` | K4 | `PENDING` M0 | Set in `CHUNKING.md` at the inspection gate. |
| `CHUNK_OVERLAP` | K4 | `PENDING` M0 | Idem. |
| `CHUNK_STRATEGY` | K4 | `PENDING` M0 | Idem. |
| `RETRIEVAL_TOP_K` | K11 | `PENDING` Q3 | |
| `SIMILARITY_FLOOR` | K12 | `PENDING` Q3 | |
| `ANSWER_MAX_SENTENCES` | K15 | `3` | C-4, binding. |
| `EDUCATIONAL_LINK` | K10/K16 | `PENDING` Q2 | Must be a public, non-blog source. |
| `FACTSHEET_LINK_MAP` | K10 | `PENDING` Q2 | Per scheme, for C-3 redirects. |
| `APP_ENV` | K20 | `local` | Switches verbose logging off in any shared/demo run. |

`.env.example` ships every key with an empty or placeholder value (NFR-2).

## 10. Failure Behaviour and Degradation

| Stage | Failure | Behaviour | PRD |
|-------|--------|-----------|-----|
| Loader | URL down / JS-only page | Record failure, report, exit non-zero; never index empty text | IN-1, risk table |
| Loader | Out-of-allowlist link | Ignored | C-1 |
| Chunker | Chunk missing required metadata | Raise — a chunk without `source_url` is a defect | IN-3, NFR-6 |
| Embeddings | Dimension ≠ 384 | Abort with a clear message | TC-1 |
| Vector store | `chroma/` missing at query time | Prompt to run `python ingest.py`; do not auto-ingest on app start | TC-3, D5 |
| LLM | Key missing | Clear setup message referencing `.env`; no traceback | NFR-2, NFR-4 |
| LLM | Rate limit / timeout | Retry per config, then safe error state | NFR-4 |
| LLM | Non-compliant output | Validator retry, then safe decline | E-1…E-4, E-7 |
| Any | Unhandled exception | Caught at the pipeline boundary; readable message; no raw traceback in the UI | NFR-4 |

## 11. Observability

Structured, line-oriented logs to `logs/` (gitignored): stage timings, retrieved `chunk_id`s and
similarity scores, chosen `fact_type`, validator verdicts, intent category, token usage.

Hard rules: **no question text that matched the PII screen, no API key, no PII in any form** (NFR-5).
The retrieval trace is the artefact used to debug E-6 failures.

Per C-1 / FR-21, observability is text logs only; no screenshots or captures of the app back-end are
produced for submission.

## 12. Latency Budget

| Step | Expected | Notes |
|------|----------|-------|
| PII screen | ~1 ms | Regex |
| Question embed (local MiniLM) | tens of ms | TC-1, no network |
| Chroma cosine query | single-digit to tens of ms | Corpus is small (5 schemes) |
| Prompt build | ~1 ms | |
| Groq generation | dominant term | Time-to-first-token + ≤3 sentences |
| Validate + render | ~1 ms | |

The NFR-7 target is set after the first end-to-end run (Q6). The corpus being small means retrieval cost
is negligible; end-to-end latency is dominated by the single Groq call.

## 13. Testing Strategy

| Level | Target | Method |
|-------|--------|--------|
| Unit | K9, K10, K12, K15, K17 | Table-driven tests per class; no network, no model. Assert PAN/email/phone detection, intent precedence, floor behaviour, sentence counting, link-count and lexicon rejection. |
| Contract | K6, K7 | Ingestion → query round-trip on a tiny fixture corpus; assert 384 dims, upsert idempotency, and that a second ingest run changes nothing (E-8). |
| Integration | K8, QueryPipeline | One full question per fact type, asserting a citation is returned and the URL is in the retrieved set. |
| Acceptance | K19 `eval.py` | Runs the E-1 … E-9 table plus the PRD §15 refusal test set; prints pass/fail per check. |
| Artifact | D-1…D-7 | Presence and content checks: `SOURCES.md` URLs match `config/sources.yaml`; `chunks.txt` non-empty and each chunk shows metadata; `DISCLAIMER.txt` string matches what the UI renders. |
| Security | NFR-2 | Assert `.env` is gitignored, no key appears in tracked files, and no PII appears in `logs/`. |

Fakes (`FakeEmbeddingService`, `InMemoryVectorStore`, `ScriptedLLMClient`) let the whole query pipeline be
tested without an API key, so the suite runs in CI and on a demo machine with no network.

## 14. Security and Compliance Posture

| Control | Implementation |
|---------|----------------|
| Source integrity | Corpus is an explicit allowlist; only registered public URLs are fetched or cited (C-1). |
| Citation integrity | The displayed link is derived from chunk metadata, not from model text, so a fabricated URL cannot be rendered. |
| PII minimisation | Detection precedes every other operation; PII-bearing text is never embedded, logged, or persisted (C-2, NFR-5). |
| Secret hygiene | One key, one loader, `.env` + `.gitignore` + `.env.example` (TC-4, NFR-2). |
| Content safety | Advice and returns paths are deterministic refusals/redirects, not model judgements (C-3, C-5). |
| Submission hygiene | Text artifacts only; no back-end screenshots (FR-21). |

Regulated-content note for the README: this is a prototype FAQ assistant, not an investment adviser;
the facts-only disclaimer is rendered in the UI and shipped as deliverable D-5.

## 15. Build Order

Mirrors the PRD milestones; the M0 gate is hard — nothing in stage A after inspection may be written
before `CHUNKING.md` exists (IN-2, TC-2).

| Milestone | Components | Gate |
|-----------|-----------|------|
| M0 Inspect | K1, K2, K3 | `CHUNKING.md` states observations, strategy, size, overlap, metadata, rationale |
| M1 Ingest | K4, K5, K6, K7, K8 | 384-dim; `chunks.txt` written; re-run is a no-op; no duplicate `chunk_id` |
| M2 Retrieve | K11 | Correct chunks for the seven FR-8 fact queries |
| M3 Answer | K13, K14, K16 | Every factual answer carries a corpus-valid link |
| M4 Guardrails | K9, K10, K12, K15, K17 | E-4, E-5, E-7 pass |
| M5 UI | K18 | Welcome line, 3 examples, disclaimer note, citation rendering |
| M6 Package | K19, K20 | D-1…D-7 complete; no secrets tracked |
| M7 Demo | all | Clean-clone end-to-end run |

## 16. Architectural Decisions

| # | Decision | Rationale | Alternatives rejected |
|---|----------|-----------|----------------------|
| AD-1 | Direct SDK usage (no LangChain/LlamaIndex) | The brief pins the three components; the pipeline is short enough to read end-to-end, which matters when the milestone is judged on the RAG stages being visibly correct. | Framework abstraction, hidden prompt assembly. |
| AD-2 | Deterministic guardrails + one LLM-backed path | Advice/performance/PII handling must be reproducible (E-3, E-4, E-5); a model-only classifier is not auditable. | Pure-prompt classification. |
| AD-3 | Citation recomputed from chunk metadata | Guarantees E-1 and NFR-6 even if the model mangles a URL. | Trusting the model's emitted link. |
| AD-4 | Content-addressed `chunk_id` + `upsert` | Makes re-ingestion idempotent, satisfying IN-5/E-8 without a separate manifest. | `add` + manual dedupe pass. |
| AD-5 | Pipeline as a callable function, UI as a thin view | The same pipeline serves the UI, `eval.py`, and a notebook deliverable (D-1). | Logic inside the UI layer. |
| AD-6 | Validator that can hard-fail to a safe decline | Renders constraints structural; guarantees E-1…E-4 are met by construction. | Prompt-only enforcement. |
| AD-7 | One shared embedding service + dimension assertion | TC-1 requires the same model on both paths; the assertion catches a silent model swap that would invalidate the persisted index. | Per-call model loads. |
| AD-8 | Corpus limited to registered public URLs | Directly implements C-1 at the boundary where it can be enforced. | Open web crawling. |

## 17. Explicitly Not Built

Aligned with PRD §6 non-goals: hybrid/BM25 retrieval, rerankers, query rewriting, agents or tool use,
auth/multi-user, a database, cross-AMC expansion, performance analytics or returns computation, image
extraction, audio, mobile UI. Each is a plausible extension but none is required by the brief, and
performance analytics in particular would violate C-3.

## 18. Open Decisions Carried In from the PRD

This architecture does not pre-empt any of these; each is a config value or a one-method change.

| PRD §16 | Open decision | Where it lands in the architecture |
|---------|---------------|---------------------------------|
| Q1 | Corpus = 5 scheme pages, or those plus official AMC/SEBI/AMFI pages | `config/sources.yaml` (K1) — additive entries; `SOURCES.md` regenerated from it |
| Q2 | Educational link and per-scheme factsheet links for refusals/redirects | `EDUCATIONAL_LINK`, `FACTSHEET_LINK_MAP` (K10/K16) |
| Q3 | `RETRIEVAL_TOP_K` and `SIMILARITY_FLOOR` | K11/K12; tuned against the E-6 acceptance queries |
| Q4 | Derivation of "Last updated from sources: " | `FreshnessResolver` strategy (K17) |
| Q5 | Behaviour when no scheme is named | `IntentRouter` `AMBIGUOUS` branch (K10) |
| Q6 | Latency target; UI framework | §12 budget; `ui/` layer is framework-agnostic behind `ChatUI` |
| Q7 | Hosted link vs demo video | Deployment is out of scope here; `app.py` must run locally from a clean clone |
| Q8 | Direct vs Regular plan distinction | `plan_variant` on every chunk; K15 refuses answers that do not match the chunk's variant |

## 19. Component → PRD Traceability

| Component | PRD requirements |
|-----------|------------------|
| K1 `SourceRegistry` | FR-1, FR-3, C-1, NFR-6 |
| K2 `SourceLoader` | IN-1, C-1, C-3 (strip return blocks) |
| K3 `CorpusInspector` | IN-2, TC-2, FR-4, D-6 |
| K4 `ChunkStrategy` | IN-3, TC-2, FR-4, D-6 |
| K5 `ChunkWriter` | FR-5, D-7, E-9 |
| K6 `EmbeddingService` | TC-1, IN-4, Q-2 |
| K7 `VectorStore` | TC-3, IN-5, Q-3, G10, E-8 |
| K8 `IngestionPipeline` | IN-1…IN-5, G8, G10, FR-24 |
| K9 `PIIScreen` | C-2, FR-12, Q-1, E-5, NFR-5 |
| K10 `IntentRouter` | C-3, C-5, FR-10, FR-11, Q-5 |
| K11 `Retriever` | Q-2, Q-3, FR-7, TC-1 |
| K12 `RelevanceGate` | Q-4, C-6, E-6 |
| K13 `PromptBuilder` | Q-5, FR-9, FR-13, FR-14 |
| K14 `LLMClient` | TC-4, FR-16, NFR-2, NFR-3 |
| K15 `AnswerValidator` | E-1, E-2, E-3, E-4, E-7 |
| K16 `ResponseRenderer` | Q-6, Q-7, FR-9, FR-14, §11 |
| K17 `FreshnessResolver` | FR-14, G7, Q4 |
| K18 `ChatUI` | FR-15, FR-20, FR-22, FR-23, FR-25, D-5 |
| K19 `EvalHarness` | E-1…E-9, FR-19, D-4 |
| K20 `Config` | TC-4, FR-16, NFR-1, NFR-2 |
| `guardrails/messages.py` | FR-20, D-5, §11 |
| Repository layout | NFR-1, NFR-4, D-2…D-7, FR-18 |
