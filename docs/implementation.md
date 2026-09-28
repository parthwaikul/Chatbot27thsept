# Implementation Plan — Mutual Fund FAQs (Facts-Only Q&A) · RAG Chatbot

| Field | Value |
|-------|-------|
| Document | Phase-wise implementation guide (agent-executable) |
| Implements | `docs/architecture.md` (K1–K20) |
| Constrained by | `docs/PRD.md`, `docs/Problemstatement.txt` |
| Executed by | Cursor (or any coding agent), phase by phase, in order |
| Status | Ready to start at **Phase 0** |

---

## 1. How to Use This Document

Work the phases **in order**. Each phase is a closed loop:

```
OBJECTIVE → PREREQUISITES → TASKS (file by file) → CURSOR KICKOFF PROMPT
        → VERIFY (run these commands) → DEFINITION OF DONE (PRD IDs) → SIGN-OFF
```

Rules for the executing agent:

1. **Do not start a phase until the previous phase's Definition of Done is checked.** P1→P2 has a hard
   gate (TC-2).
2. **Do not implement ahead.** The chunker's size/overlap come from P1's inspection. Guessing them
   early is the most likely way to waste this milestone.
3. **Each phase's "Cursor kickoff prompt" is copy-pasteable.** Paste it, let the agent work, run the
   verification commands, then tick the sign-off.
4. **Keep the evidence.** Every Definition of Done item maps to a PRD acceptance check (E-1…E-9) or a
   deliverable (D-1…D-7). The evidence is what proves the milestone.
5. **If a phase disproves an assumption**, stop and amend `docs/architecture.md` or this document rather
   than silently diverging.

### 1.1 Ground rules (non-negotiable, all phases)

| # | Rule | Why |
|---|------|-----|
| R-1 | Embeddings: `sentence-transformers/all-MiniLM-L6-v2` only, 384-dim, **same model** for chunks and questions | Brief, TC-1 |
| R-2 | Vector DB: ChromaDB persisted to disk, writes via `upsert`, never auto-ingest on app start | Brief, TC-3, E-8 |
| R-3 | LLM: Groq only. Key read from `.env` at runtime; `.env` gitignored, `.env.example` committed, key never logged | Brief, TC-4, NFR-2 |
| R-4 | No LangChain / LlamaIndex / any RAG framework — direct SDK calls | AD-1 |
| R-5 | Corpus and citations: only URLs registered in `config/sources.yaml`. No third-party blogs, no login-gated content | C-1 |
| R-6 | Answers: ≤ 3 sentences, exactly one source link, plus `Last updated from sources: ` | C-4, FR-13, FR-14 |
| R-7 | Never compute, compare, or rank returns. Performance questions get a factsheet link | C-3 |
| R-8 | Never give advice. Opinionated/portfolio questions get a polite facts-only refusal + educational link | C-5 |
| R-9 | PII (PAN, Aadhaar, account number, OTP, email, phone) is screened **before** embedding, logging, or persistence, and is never stored | C-2, NFR-5 |
| R-10 | Citation URL is derived from chunk metadata, never from model output | AD-3, FR-9 |
| R-11 | No screenshots or back-end captures in any artifact produced for submission | FR-21 |
| R-12 | Type hints and docstrings on public functions; `logs/`, `chroma/`, `.env`, `corpus/raw/` never committed | NFR-1, NFR-2 |

### 1.2 Phase dependency map

```
P0 Scaffold ─▶ P1 Load + Inspect ─▶ [GATE: CHUNKING.md] ─▶ P2 Chunk/Embed/Store
                                                                    │
                                                                    ▼
                                            P3 Retrieve ─▶ P4 Answer ─▶ P5 Guardrails
                                                                               │
                                                                               ▼
                                                       P6 UI ─▶ P7 Eval + Package ─▶ P8 Clean run
```

| Phase | Milestone | Hard gate before starting |
|-------|-----------|---------------------------|
| P0 | — | none |
| P1 | M0 | none |
| P2 | M1 | **`CHUNKING.md` exists** with observations, strategy, size, overlap, metadata, rationale (IN-2, TC-2) |
| P3 | M2 | P2 DoD: `chunks.txt` written, 384-dim, re-run is a no-op |
| P4 | M3 | P3 DoD: correct chunks retrieved for the 7 FR-8 fact queries |
| P5 | M4 | P4 DoD: every factual answer carries a corpus-valid link |
| P6 | M5 | P5 DoD: refusals, PII screen, and validator all verified |
| P7 | M6 | P6 DoD: UI usable end to end |
| P8 | M7 | P7 DoD: eval table green, D-1…D-7 present |

### 1.3 PENDING decisions and the phase that must resolve them

| PRD §16 | Decision | Resolve by | Fallback if undecided |
|---------|----------|-----------|------------------------|
| Q1 | Corpus = 5 scheme pages only, or plus official AMC/SEBI/AMFI pages | **P1** (writing `config/sources.yaml`) | Start with the 5 listed scheme URLs; add official pages additively later |
| Q8 | Direct vs Regular plan distinction | **P2** (chunk metadata) | Label `plan_variant` on every chunk; answers state the plan explicitly |
| Q3 | `RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR` | **P3** (tune against the 7 fact queries) | Pick from the observed score separation in P3 and record the values + reason in `CHUNKING.md` |
| Q5 | Behaviour when no scheme is named | **P5** | Ask which of the five schemes (clarify) — safer than answering for all |
| Q2 | Educational link + per-scheme factsheet links | **P5** | Config placeholder; blocks release. Must be a public, non-blog source |
| Q4 | Derivation of `Last updated from sources: ` | **P4/P5** | Use the source fetch timestamp in `corpus/sources.csv`; document the choice in README known-limits |
| Q6 | Latency target; UI framework | **P6** | Streamlit for the tiny UI; record first end-to-end latency, set the target in the README |
| Q7 | Hosted link vs demo video | **P8** | Local clean run + recorded video (D-1 allows either) |

---

## 2. Phase 0 — Scaffold and Configuration

**Objective.** A runnable, importable skeleton with typed config and the source allowlist. No business
logic yet.

**Prerequisites.** None.

**Files to create**

```
mf-faq-rag/
├── .gitignore
├── .env.example
├── requirements.txt
├── config/sources.yaml
├── src/__init__.py
├── src/config.py
└── src/ingest/__init__.py
```

**Tasks**

1. `.gitignore` — ignore `.env`, `.venv/`, `chroma/`, `logs/`, `corpus/raw/`, `__pycache__/`, `*.pyc`.
   Do **not** ignore `chunks.txt`, `chunks.jsonl`, or `SOURCES.md` (deliverables D-7, D-2).
2. `.env.example` — every variable from architecture.md §9, values empty or placeholder, including
   `GROQ_API_KEY=`.
3. `requirements.txt` — **pin exact versions** for `sentence-transformers`, `chromadb`, `groq`,
   `beautifulsoup4`, `requests`, `pyyaml`, `python-dotenv`, `streamlit`, `pytest`. Pinning is what makes
   a teammate's run reproduce yours.
4. `src/config.py` — a `Settings` dataclass with a field for every architecture.md §9 variable; `load()`
   reads `os.environ` after `load_dotenv()`; `PENDING` values default to `None` and raise a clear
   `ConfigError` naming the missing variable when accessed. Expose `chroma_dir`, `corpus_dir`,
   `sources_path`, `chunks_txt_path`, `chunks_jsonl_path`, `sources_csv_path`, `logs_dir`,
   `embedding_dim`.
5. `config/sources.yaml` — the K1 allowlist: the five scheme URLs from PRD §4 (S1–S5), each entry with
   `source_id`, `url`, `scheme`, `category`, `plan_variant: direct_growth`. Resolves **Q1**.
6. `git init` if needed, then an initial commit. Confirm `.env` is untracked.

**Cursor kickoff prompt (P0)**

```
Create the scaffold for a Python 3.11 mutual-fund FAQ RAG project exactly as described in
docs/architecture.md Section 5 (repository layout) and Section 9 (configuration). Read those
sections first. Do exactly this and nothing more:

- .gitignore covering .env, .venv/, chroma/, logs/, corpus/raw/, __pycache__/, *.pyc — but NOT
  chunks.txt, chunks.jsonl, or SOURCES.md
- .env.example listing every variable from architecture.md Section 9 with empty or placeholder
  values, including an empty GROQ_API_KEY=
- requirements.txt with pinned exact versions of sentence-transformers, chromadb, groq,
  beautifulsoup4, requests, pyyaml, python-dotenv, streamlit, pytest
- src/config.py: a typed Settings dataclass covering every Section 9 variable; load() reads env
  after load_dotenv(); PENDING values default to None and raise a clear ConfigError naming the
  missing variable when accessed; expose chroma_dir, corpus_dir, sources_path, chunks_txt_path,
  chunks_jsonl_path, sources_csv_path, logs_dir, embedding_dim=384
- src/__init__.py and src/ingest/__init__.py (empty)
- config/sources.yaml: a K1 allowlist of the five scheme URLs from docs/PRD.md Section 4, each
  entry with source_id, url, scheme, category, plan_variant=direct_growth
  (hdfc_large_cap_direct_growth, hdfc_equity_flexi_cap, hdfc_elss_tax_saver, hdfc_small_cap,
  hdfc_balanced_advantage)

Constraints: no LangChain or any RAG framework. No API key value anywhere except the empty
placeholder in .env.example. Type hints and docstrings on all public functions. No screenshots
or image output of any kind. Do not implement ingestion, chunking, embedding, or querying yet.
```

**Verify**

```bash
python -c "from src.config import load; print(load().embedding_dim)"
python -c "import yaml; print(len(yaml.safe_load(open('config/sources.yaml'))['sources']))"
git check-ignore -v .env
```

**Definition of done.** `embedding_dim` prints `384`; the registry holds 5 entries; `.env` is ignored;
`requirements.txt` installs into a fresh venv; no business logic exists yet.

**Sign-off.** ☐ P0 complete

---

## 3. Phase 1 — Load the Corpus and Inspect It (Milestone M0, hard gate)

**Objective.** Fetch the registered public pages as clean text, persist an audit trail, and produce the
observations that justify a chunking strategy. **No chunking code in this phase** (IN-2, TC-2).

**Prerequisites.** P0 done.

**Files to create**

```
src/ingest/models.py      # SourceRecord, DocumentSection, Chunk (architecture §6.1, §6.2)
src/ingest/registry.py    # K1
src/ingest/loader.py      # K2
src/ingest/inspector.py   # K3
ingest.py                 # CLI entry
corpus/sources.csv        # generated
corpus/raw/*.txt          # generated
```

**Tasks**

1. `models.py` — dataclasses exactly per architecture.md §6.1 and §6.2, including `content_hash`,
   `sections`, and all chunk metadata: `source_url`, `scheme`, `category`, `plan_variant`, `section`,
   `fact_type`, `source_fetched_at`.
2. `registry.py` — loads `config/sources.yaml`; exposes `all_sources()`; **refuses any URL not in the
   registry**. This is the C-1 enforcement point.
3. `loader.py` — for each registered URL: HTTP GET with an explicit User-Agent and a timeout; parse with
   BeautifulSoup; drop `script`, `style`, `nav`, `header`, `footer`, `aside`, and consent/cookie blocks;
   drop any block whose text matches a returns/performance pattern (C-3 defence at load time); convert
   `<table>` rows to newline-separated `key: value` lines so fee tables and exit-load slabs survive as
   readable text; split remaining text into ordered `DocumentSection` records from heading elements;
   compute `content_hash` (SHA-256) over normalised text; write `corpus/raw/<source_id>.txt`; append a
   row to `corpus/sources.csv` (`source_id,url,scheme,category,plan_variant,fetched_at,http_status,
   content_hash,text_chars`). **Fail loudly** when extracted text is below a floor length — that signals
   a JS-rendered page, and silently indexing it is the failure mode the risk table warns about.
4. `inspector.py` (K3) — prints an inspection report and returns a structured summary: per source, char
   count, section count, table/block density, boilerplate noise ratio, JS-rendered flag, and **for each
   target fact type** (`expense_ratio`, `exit_load`, `min_sip`, `lock_in`, `riskometer`, `benchmark`,
   `statement_guide`) the section heading and surrounding text where it appears.
5. `ingest.py` — CLI with `--stage load|inspect|all`; summary table; exit non-zero if any source failed.
6. **Human step.** Read the report and write **`CHUNKING.md`** (deliverable D-6) containing the four things
   TC-2 demands — **why** the strategy suits this data, **chunk size**, **overlap**, and **the metadata
   each chunk keeps** — plus the inspection observations that justify it. Name the selected
   `CHUNK_STRATEGY` id. Do not proceed to P2 without this file.

**Cursor kickoff prompt (P1)**

```
Implement Phase 1 (Load + Inspect) of docs/implementation.md, following architecture.md K1, K2, K3
and the data contracts in architecture.md Sections 6.1 and 6.2. Read architecture.md Sections 3, 4,
5, 6.1, 6.2, 7.1, 7.3 and 10 before writing code.

Deliverables:
- src/ingest/models.py: SourceRecord, DocumentSection, Chunk dataclasses exactly as in
  architecture.md Sections 6.1 and 6.2, with no metadata field omitted
- src/ingest/registry.py (K1): loads config/sources.yaml; any URL not in the registry can be
  neither fetched nor cited
- src/ingest/loader.py (K2): fetch with an explicit User-Agent and a timeout; BeautifulSoup
  extraction; strip script/style/nav/header/footer/aside and consent boilerplate; strip any block
  whose text matches returns/performance patterns; convert HTML tables to newline-separated
  "key: value" lines so fee tables and exit-load slabs stay readable; build ordered
  DocumentSection records from headings; compute a SHA-256 content_hash over normalised text;
  write corpus/raw/<source_id>.txt; append a row to corpus/sources.csv with the columns from
  architecture.md Section 6.1; raise a clear error when extracted text is below a floor length
  (JS-rendered page) instead of indexing it
- src/ingest/inspector.py (K3): console report and structured summary — per source: char count,
  section count, table density, boilerplate noise ratio, JS-rendered flag, and for each fact type
  (expense_ratio, exit_load, min_sip, lock_in, riskometer, benchmark, statement_guide) the section
  heading and surrounding text where it occurs
- ingest.py CLI with --stage load|inspect|all, a summary table, and a non-zero exit when any source
  failed

Hard constraints: registered URLs only (no crawling, no third-party blogs, no login-gated content);
no LangChain; type hints and docstrings; no screenshots or image output; never log API keys.
Do NOT implement chunking, embedding, ChromaDB, or the query path in this phase.
```

**Verify**

```bash
python ingest.py --stage all
column -s, -t corpus/sources.csv
head -80 corpus/raw/hdfc_large_cap_direct_growth.txt
```

Then confirm **by hand**, from the report, that you can locate: expense ratio, exit load, minimum SIP,
ELSS lock-in, riskometer, benchmark, and the capital-gains statement guide. If any of the seven is
absent from the pages, record that in `CHUNKING.md` — it changes the retrieval design and possibly the
answer to Q1.

**Definition of done.** 5 sources fetched with HTTP 200; `corpus/sources.csv` complete; raw text
snapshots exist; the inspection report locates each fact type; **`CHUNKING.md` written with rationale,
chunk size, overlap, and metadata list** (IN-1, IN-2, TC-2, FR-4, D-6).

**Sign-off.** ☐ P1 complete ☐ `CHUNKING.md` written

---

## 4. Phase 2 — Chunk, Embed, Store (Milestone M1)

**Objective.** Turn inspected text into auditable chunks, embed with MiniLM, persist to ChromaDB once,
and prove idempotency.

**Prerequisites.** P1 done **and** `CHUNKING.md` exists (hard gate).

**Files to create**

```
src/ingest/chunker.py        # K4 — ChunkStrategy protocol + the strategy named in CHUNKING.md
src/ingest/chunk_writer.py   # K5
src/rag/__init__.py
src/rag/embeddings.py        # K6
src/rag/vector_store.py      # K7
src/ingest/pipeline.py       # K8
tests/test_chunker.py
tests/test_store_idempotency.py
```

**Tasks**

1. `chunker.py` (K4) — `ChunkStrategy` protocol with `split(doc: SourceRecord) -> list[Chunk]`. Implement
   the strategy named in `CHUNKING.md`, reading `CHUNK_SIZE` / `CHUNK_OVERLAP` / `CHUNK_STRATEGY` from
   config so the documented values and the used values cannot drift. Requirements: scheme boundaries are
   never crossed; a fact block (expense-ratio row, exit-load slab, SIP slab, lock-in statement,
   riskometer/benchmark note, statement-guide step) is never split; `fact_type` is assigned at chunk
   time; `chunk_id = "{source_id}::{section_ordinal}::{part}"`; raise if a chunk lacks `source_url`,
   `scheme`, or `fact_type`.
2. `chunk_writer.py` (K5) — write every chunk with full metadata to `chunks.txt` (deliverable D-7) as a
   `=== CHUNK <chunk_id> ===` header, a metadata block, then the text. Regenerated each run. Also emit
   `chunks.jsonl` for machine use in tests.
3. `embeddings.py` (K6) — one shared `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")`;
   `embed_documents(list[str])` and `embed_query(str)` both use `model.encode(...,
   normalize_embeddings=True)`; assert dimension == 384 at init with a clear error. Add
   `FakeEmbeddingService` (deterministic, hash-seeded 384-dim vectors) so tests run with no model
   download and no network.
4. `vector_store.py` (K7) — `chromadb.PersistentClient(path=chroma_dir)`; collection `mf_faq` with
   `{"hnsw:space": "cosine"}`; `upsert_chunks(chunks, vectors)`; `query(vector, k)` returning `chunk_id`,
   `text`, `metadata`, and **similarity = `1 - distance`**; `count()`. Writes are `upsert`, never `add`.
5. `pipeline.py` (K8) — chunk → embed → store, skipping sources whose `content_hash` matches
   `corpus/sources.csv`; print `ingested / unchanged / rewritten` counts.
6. Extend `ingest.py` so `--stage all` now means load → inspect → chunk → embed → store.
7. Tests — `test_chunker.py`: no chunk crosses a scheme boundary; no fact block split; required metadata
   present; `chunk_id` deterministic. `test_store_idempotency.py`: two consecutive ingests give identical
   `count()`; changed content rewrites only affected `chunk_id`s.

**Cursor kickoff prompt (P2)**

```
Implement Phase 2 (Chunk + Embed + Store) of docs/implementation.md, following architecture.md K4,
K5, K6, K7, K8 and Sections 6.2 and 6.3. Read CHUNKING.md first and implement exactly the strategy,
chunk size, and overlap recorded there — do not choose different values.

Deliverables:
- src/ingest/chunker.py (K4): ChunkStrategy protocol (split(doc) -> list[Chunk]) plus the selected
  implementation; reads CHUNK_SIZE/CHUNK_OVERLOP/CHUNK_STRATEGY from src/config.py; scheme
  boundaries never crossed; a fact block is never split; fact_type assigned per chunk;
  chunk_id = "{source_id}::{section_ordinal}::{part}"; raise if a chunk lacks source_url, scheme,
  or fact_type
- src/ingest/chunk_writer.py (K5): write chunks.txt with a "=== CHUNK <id> ===" header, the full
  metadata block, then the text; also chunks.jsonl
- src/rag/embeddings.py (K6): a single shared
  SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2") with embed_documents() and
  embed_query() using normalize_embeddings=True; assert dimension == 384 at init and raise a
  clear error otherwise; plus a deterministic hash-seeded FakeEmbeddingService for offline tests
- src/rag/vector_store.py (K7): chromadb.PersistentClient; collection mf_faq with
  hnsw:space=cosine; upsert_chunks(); query() returning chunk_id, text, metadata and cosine
  similarity computed as 1 - distance; count(). Use upsert, never add
- src/ingest/pipeline.py (K8): chunk -> embed -> store, skipping sources whose content_hash is
  unchanged versus corpus/sources.csv; print ingested/unchanged/rewritten counts
- extend ingest.py so --stage all runs load, inspect, chunk, embed, store
- tests/test_chunker.py and tests/test_store_idempotency.py exactly as specified in
  docs/implementation.md Phase 2

Hard constraints: all-MiniLM-L6-v2 only; ChromaDB persisted to disk; no LangChain; type hints and
docstrings; no screenshots or image output. Do not build the query path yet.
```

**Verify**

```bash
python ingest.py --stage all
python -c "from src.rag.vector_store import VectorStore; print(VectorStore().count())"
grep -c "^=== CHUNK" chunks.txt
grep -A8 "^=== CHUNK" chunks.txt | head -40
python ingest.py --stage all && python -c "from src.rag.vector_store import VectorStore; print(VectorStore().count())"
pytest tests/ -q
```

Run ingestion **twice** and confirm the chunk count is identical (idempotency, E-8). Open `chunks.txt` and
confirm no expense-ratio row or exit-load slab is cut in half.

**Definition of done.** Every chunk carries `source_url`, `scheme`, `category`, `plan_variant`, `section`,
`fact_type`; all vectors are 384-dim; `chroma/` persists across restarts; a second run is a no-op;
`chunks.txt` is complete and readable (IN-3, IN-4, IN-5, FR-5, FR-6, G9, G10, E-8, E-9, D-7).

**Sign-off.** ☐ P2 complete ☐ idempotency proven ☐ `chunks.txt` verified by hand

---

## 5. Phase 3 — Retrieval (Milestone M2)

**Objective.** Return the right chunks with scores for a question. Resolve **Q3** with evidence.

**Prerequisites.** P2 done.

**Files to create**

```
src/eval/__init__.py
src/eval/cases.py               # fact + refusal + PII + unsupported query sets
src/rag/retriever.py            # K11
scripts/inspect_scores.py       # score distribution, used to set the floor
tests/test_retriever.py
```

**Tasks**

1. `cases.py` — `FACT_QUERIES`: ≥ 7 entries, one per FR-8 fact type, each naming a specific scheme from
   S1–S5 (e.g. "What is the expense ratio of HDFC Large Cap Fund Direct Growth?", "What is the lock-in
   period for HDFC ELSS Tax Saver Fund?"). `REFUSAL_QUERIES`: the exact PRD §15 set. `PII_QUERIES`: PAN,
   Aadhaar, account number, OTP, email, phone variants. `UNSUPPORTED_QUERIES`: ≥ 3 in-domain but absent
   questions.
2. `retriever.py` (K11) — `retrieve(question, k)` embeds the question with the **same** `EmbeddingService`
   used at ingestion, queries ChromaDB by cosine, and returns `RetrievedChunk(chunk_id, text, metadata,
   similarity)`. Also `retrieve_for_fact_type(question, fact_type, k)` that prefers matching
   `fact_type`. **No LLM call in this phase.**
3. `scripts/inspect_scores.py` — print top similarity scores for every `FACT_QUERIES` and
   `UNSUPPORTED_QUERIES` entry. Use the observed separation to set `RETRIEVAL_TOP_K` and
   `SIMILARITY_FLOOR`; append a "Retrieval tuning" section to `CHUNKING.md` with the chosen values, the
   score distribution, and the reasoning (resolves **Q3**).
4. `tests/test_retriever.py` — each `FACT_QUERIES` entry retrieves ≥ 1 chunk with the expected
   `fact_type` and a `source_url` present in `config/sources.yaml`; each `UNSUPPORTED_QUERIES` entry
   returns nothing above the floor.

**Cursor kickoff prompt (P3)**

```
Implement Phase 3 (Retrieval) of docs/implementation.md, following architecture.md K11 and
Sections 6.3, 8.1, 9 and 13. Read those sections first.

Deliverables:
- src/eval/cases.py: FACT_QUERIES (>=7, one per FR-8 fact type, each naming a specific scheme from
  docs/PRD.md Section 4), REFUSAL_QUERIES (the exact set from PRD Section 15), PII_QUERIES (PAN,
  Aadhaar, account number, OTP, email, phone), UNSUPPORTED_QUERIES (>=3 plausible questions that
  are absent from the corpus)
- src/rag/retriever.py (K11): retrieve(question, k) using the SAME EmbeddingService instance and
  model as ingestion; returns chunk_id, text, metadata and similarity where similarity =
  1 - distance from ChromaDB; plus retrieve_for_fact_type(question, fact_type, k) preferring
  matching fact_type chunks. No LLM call in this phase
- scripts/inspect_scores.py: print top similarity scores for every FACT_QUERIES and
  UNSUPPORTED_QUERIES entry so RETRIEVAL_TOP_K and SIMILARITY_FLOOR can be chosen from the
  observed separation
- tests/test_retriever.py asserting each FACT_QUERIES entry returns a chunk with the expected
  fact_type and a source_url present in config/sources.yaml, and each UNSUPPORTED_QUERIES entry
  returns nothing above the floor

Then set RETRIEVAL_TOP_K and SIMILARITY_FLOOR as config defaults based on the observed separation and
append a "Retrieval tuning" section to CHUNKING.md recording the chosen values, the score
distribution, and why the floor separates supported from unsupported questions.

Hard constraints: all-MiniLM-L6-v2 on both sides; ChromaDB cosine; no LangChain; no LLM call in this
phase; type hints and docstrings; no screenshots or image output.
```

**Verify**

```bash
python scripts/inspect_scores.py
pytest tests/test_retriever.py -q
```

**Definition of done.** All 7 fact queries retrieve a correct-`fact_type` chunk from a registered URL;
unsupported queries fall below the floor; `RETRIEVAL_TOP_K` and `SIMILARITY_FLOOR` are set, recorded,
and justified (Q-3, FR-7, M2).

**Sign-off.** ☐ P3 complete ☐ top-k and floor recorded in `CHUNKING.md`

---

## 6. Phase 4 — Answer Generation (Milestone M3)

**Objective.** Produce a ≤3-sentence grounded answer with exactly one citation via Groq, and stand up the
freshness string (settles **Q4** for the prototype).

**Prerequisites.** P3 done.

**Files to create**

```
src/rag/prompts.py            # K13
src/llm/__init__.py
src/llm/groq_client.py        # K14
src/query/__init__.py
src/query/freshness.py        # K17
src/query/pipeline.py         # Stage B orchestration, first pass (no guardrails yet)
src/ui/__init__.py
src/ui/answer_view.py         # render function only, not the app
tests/test_prompts.py
```

**Tasks**

1. `prompts.py` (K13) — `SYSTEM_PROMPT` matching all seven hard rules in architecture.md §8.5, including
   the exact `NOT_IN_SOURCES` and `FACTSHEET_REDIRECT` markers. `build_user_message(chunks, question)`
   renders numbered context chunks each carrying `source_url`, `scheme`, `section`, `fact_type`, then the
   question. `citation_for(chunks)` returns the single citation from **chunk metadata** (AD-3, R-10) —
   never from model text.
2. `groq_client.py` (K14) — `Groq(api_key=settings.groq_api_key)`; `chat(messages)` calls
   `client.chat.completions.create(model=..., messages=..., temperature=<lowest>, max_tokens=...)`; key
   read only from config; retries per `GROQ_MAX_RETRIES`; typed `LLMUnavailable` on missing key, timeout,
   or exhausted retries. Add `ScriptedLLMClient` returning canned responses for tests.
3. `query/freshness.py` (K17) — `FreshnessResolver` interface with `ingestion_timestamp` (from
   `corpus/sources.csv` `fetched_at`) and `page_date` strategies; default `ingestion_timestamp`. Record
   the choice in README known-limits (**Q4**).
4. `query/pipeline.py` — first-pass `answer(question)`: retrieve → build prompt → LLM → return
   `AnswerResponse(text, citation_url, last_updated, retrieved_chunk_ids)`. **No guardrails yet** — those
   are P5.
5. `answer_view.py` — renders answer + exactly one citation link + `Last updated from sources: <value>`.
   This is the string documented in deliverable D-5.
6. `tests/test_prompts.py` — assert the system prompt contains all seven rules and both markers;
   `build_user_message` includes every retrieved chunk's `source_url`; `citation_for` returns a URL from
   the retrieved set.

**Cursor kickoff prompt (P4)**

```
Implement Phase 4 (Answer generation) of docs/implementation.md, following architecture.md K13,
K14, K16, K17 and Section 8.5. Read architecture.md Sections 4, 8.1, 8.5, 9 and 10 first.

Deliverables:
- src/rag/prompts.py (K13): SYSTEM_PROMPT matching all seven hard rules in architecture.md
  Section 8.5, including the exact NOT_IN_SOURCES and FACTSHEET_REDIRECT markers;
  build_user_message(chunks, question) rendering numbered context chunks that each carry
  source_url, scheme, section and fact_type; and citation_for(chunks) returning the single
  citation URL taken from CHUNK METADATA, never from model text
- src/llm/groq_client.py (K14): Groq client reading GROQ_API_KEY from config only; chat() via
  client.chat.completions.create with the lowest temperature and configured max_tokens; retries
  per config; a typed LLMUnavailable error for missing key, timeout, or exhausted retries; plus a
  ScriptedLLMClient for tests
- src/query/freshness.py (K17): FreshnessResolver interface with ingestion_timestamp (from
  corpus/sources.csv fetched_at) and page_date strategies; default ingestion_timestamp
- src/query/pipeline.py: first-pass answer(question) = retrieve -> build prompt -> LLM ->
  AnswerResponse(text, citation_url, last_updated, retrieved_chunk_ids). No guardrails yet
- src/ui/answer_view.py: render answer + exactly one citation link +
  "Last updated from sources: <value>"
- tests/test_prompts.py

Hard constraints: Groq only; temperature at its lowest; the key is never logged or printed; the
citation comes from chunk metadata; answers capped at 3 sentences by instruction; no LangChain;
type hints and docstrings; no screenshots or image output.
```

**Verify**

```bash
cp .env.example .env        # then paste your own GROQ_API_KEY
pytest tests/test_prompts.py -q
python -c "from src.query.pipeline import answer; print(answer('What is the expense ratio of HDFC Large Cap Fund Direct Growth?'))"
```

Read the printed answer: factual, ≤3 sentences, one link, and it must match the source page.

**Definition of done.** All 7 fact queries return a grounded answer with a citation drawn from the
retrieved set; both markers (`NOT_IN_SOURCES`, `FACTSHEET_REDIRECT`) work; the freshness string renders
(FR-8, FR-9, FR-13, FR-14, Q-4, TC-4, NFR-3, M3).

**Sign-off.** ☐ P4 complete

---

## 7. Phase 5 — Guardrails (Milestone M4)

**Objective.** Make every constraint structural rather than prompt-suggested. Resolve **Q2** and **Q5**.

**Prerequisites.** P4 done.

**Files to create**

```
src/guardrails/__init__.py
src/guardrails/messages.py   # canonical user-facing strings — source of deliverable D-5
src/guardrails/pii.py        # K9
src/guardrails/intent.py     # K10
src/rag/relevance_gate.py    # K12
src/guardrails/validator.py  # K15
tests/test_pii.py
tests/test_intent.py
tests/test_validator.py
tests/test_relevance_gate.py
```

**Tasks**

1. `messages.py` — one home for every user-facing string: the UI disclaimer `Facts-only. No investment
   advice.`, the `Last updated from sources: ` prefix, the opinionated-question refusal, the
   performance/factsheet redirect, the PII refusal, the not-in-sources decline, and the clarify-scheme
   prompt. This file is the source of **D-5**. Educational link and per-scheme factsheet links come from
   config (`EDUCATIONAL_LINK`, `FACTSHEET_LINK_MAP`) and must be public, non-blog sources (**Q2**).
2. `pii.py` (K9) — deterministic detection per architecture.md §8.2: PAN, Aadhaar, account number, OTP,
   email, phone. The numeric classes require their context keyword so a minimum-SIP amount is not falsely
   refused. `screen(text) -> PIIHit | None`. On a hit the pipeline returns the PII refusal and the text is
   never embedded, logged, or persisted.
3. `intent.py` (K10) — ordered deterministic categories `PII | ADVICE | PERFORMANCE | AMBIGUOUS | FACT |
   OUT_OF_SCOPE` per architecture.md §8.3, with all keyword sets in one auditable table. An LLM-assisted
   classifier may be a fallback for genuinely ambiguous input but must **never** handle PII.
   `AMBIGUOUS` = a fact question naming no scheme → clarify which of the five (**Q5**).
4. `relevance_gate.py` (K12) — two conditions, both required, before any LLM call: best similarity ≥
   `SIMILARITY_FLOOR` **and** ≥ 1 retrieved chunk whose `fact_type` matches the intent's expected type.
   Otherwise decline with the facts-only message and an educational link.
5. `validator.py` (K15) — the checks in architecture.md §8.6: grounding marker, performance marker,
   ≤3 sentences, exactly one URL that belongs to the retrieved set, advice lexicon, returns lexicon, and
   freshness-line presence. Behaviour: `NOT_IN_SOURCES` / `FACTSHEET_REDIRECT` route to their handlers;
   a length, citation, or lexicon failure triggers **one repair retry**, then a safe decline. The answer
   is never rendered raw if it fails.
6. Re-wire `query/pipeline.py` to the full Stage B order from architecture.md §8.1:
   `0 PII screen → 1 intent → 2 embed → 3 retrieve → 4 relevance gate → 5 prompt → 6 LLM →
   7 validate → 8 render`. Every non-answer path (PII, ADVICE, PERFORMANCE, AMBIGUOUS, gate-miss, LLM
   error) returns a readable message (NFR-4).
7. Tests — `test_pii.py` (all six classes detected; a minimum-SIP question **not** flagged),
   `test_intent.py` (precedence: PII first, then advice/performance), `test_relevance_gate.py`
   (below-floor and wrong-fact-type both decline), `test_validator.py` (4-sentence output rejected,
   off-corpus URL rejected, advice and returns lexicons rejected, missing freshness line repaired).

**Cursor kickoff prompt (P5)**

```
Implement Phase 5 (Guardrails) of docs/implementation.md, following architecture.md K9, K10, K12,
K15 and Sections 8.1, 8.2, 8.3, 8.6, 10 and 11. Read those sections first.

Deliverables:
- src/guardrails/messages.py: one module holding every user-facing string — the UI disclaimer
  "Facts-only. No investment advice.", the "Last updated from sources: " prefix, the
  opinionated-question refusal, the performance/factsheet redirect, the PII refusal, the
  not-in-sources decline, and the clarify-scheme prompt. Educational link and per-scheme factsheet
  links come from config (EDUCATIONAL_LINK, FACTSHEET_LINK_MAP) and must be public, non-blog URLs
- src/guardrails/pii.py (K9): deterministic detection per architecture.md Section 8.2 for PAN,
  Aadhaar, account number, OTP, email and phone; numeric classes require their context keyword so a
  minimum-SIP amount is not falsely refused; screen(text) -> PIIHit | None; a hit means the text is
  never embedded, logged or persisted
- src/guardrails/intent.py (K10): ordered deterministic categories PII | ADVICE | PERFORMANCE |
  AMBIGUOUS | FACT | OUT_OF_SCOPE per Section 8.3, with all keyword sets in one auditable table; an
  LLM-assisted classifier may be a fallback for ambiguous input but never handles PII; AMBIGUOUS
  (fact question naming no scheme) asks which of the five schemes
- src/rag/relevance_gate.py (K12): proceed only when best similarity >= SIMILARITY_FLOOR AND at least
  one retrieved chunk's fact_type matches the intent's expected fact_type; otherwise decline with
  the facts-only message and an educational link
- src/guardrails/validator.py (K15): the checks in architecture.md Section 8.6 — grounding marker,
  performance marker, <=3 sentences, exactly one URL belonging to the retrieved set, advice lexicon,
  returns lexicon, freshness-line presence; NOT_IN_SOURCES and FACTSHEET_REDIRECT route to their
  handlers; length/citation/lexicon failures trigger exactly one repair retry and then a safe decline,
  and a failing answer is never rendered raw
- re-wire src/query/pipeline.py to the full Stage B order: 0 PII screen, 1 intent, 2 embed, 3 retrieve,
  4 relevance gate, 5 prompt, 6 LLM, 7 validate, 8 render; every non-answer path returns a readable
  message with no raw traceback
- tests/test_pii.py, tests/test_intent.py, tests/test_relevance_gate.py, tests/test_validator.py
  exactly as specified in docs/implementation.md Phase 5

Hard constraints: Groq only; the key is never logged; PII never stored; the citation comes from chunk
metadata; no LangChain; type hints and docstrings; no screenshots or image output.
```

**Verify**

```bash
pytest tests/ -q
python -c "from src.query.pipeline import answer; print(answer('Should I buy HDFC Large Cap Fund?'))"
python -c "from src.query.pipeline import answer; print(answer('Which of these funds gave higher returns?'))"
python -c "from src.query.pipeline import answer; print(answer('My PAN is ABCDE1234F, please link it to my account'))"
python -c "from src.query.pipeline import answer; print(answer('What is the minimum SIP amount?'))"
```

The last call **must not** be PII-blocked — that is the false-positive regression check.

**Definition of done.** PII screen blocks all six classes and logs nothing; advice and performance
questions get refusals/redirects with links; below-floor retrieval declines; the validator rejects
4-sentence, off-corpus-link, advice, and returns outputs; all four response paths render readably
(FR-10, FR-11, FR-12, C-2, C-3, C-5, E-3, E-4, E-5, E-7, NFR-4, NFR-5, M4).

**Sign-off.** ☐ P5 complete ☐ refusal + PII tests green

---

## 8. Phase 6 — UI (Milestone M5)

**Objective.** The tiny UI the brief specifies: welcome line, 3 example questions, the facts-only note,
and answers with a clickable citation. Resolve **Q6**.

**Prerequisites.** P5 done.

**Files to create**

```
app.py                    # entrypoint; thin
src/ui/chat.py            # K18 state + render
```

**Tasks**

1. `src/ui/chat.py` (K18) — session state holding the message list; `render_welcome()` shows the welcome
   line, the note `Facts-only. No investment advice.`, and exactly **3** example-question buttons taken
   from the highest-confidence `FACT_QUERIES` in `src/eval/cases.py`; `render_answer(AnswerResponse)`
   shows the answer text, the single citation as a link to `citation_url`, and
   `Last updated from sources: <value>`; `render_refusal(...)` shows whatever `messages.py` returned.
2. `app.py` — Streamlit entrypoint that calls the pipeline and renders. **No logic here**: it may not
   contain retrieval, prompting, or guardrail code (AD-5).
3. Add a corpus-stats line (FR-25): schemes indexed, chunk count, last ingestion date, read from
   `VectorStore().count()` and `corpus/sources.csv`.
4. Add an optional collapsible "sources" disclosure under each answer listing the retrieved `chunk_id`,
   `section`, and similarity score — this is the retrieval trace that demonstrates the pipeline
   (FR-22). It is additive UI, not a second citation.
5. Record the first measured end-to-end latency and set the NFR-7 target in the README (**Q6**).

**Cursor kickoff prompt (P6)**

```
Implement Phase 6 (UI) of docs/implementation.md, following architecture.md K18 and the FR-15
requirements in docs/PRD.md Section 12. Read PRD.md Sections 11 and 12 and architecture.md
Sections 8.1 and 10 first.

Deliverables:
- src/ui/chat.py (K18): session state for the message list; render_welcome() showing the welcome
  line, the note "Facts-only. No investment advice.", and exactly 3 example-question buttons drawn
  from the highest-confidence FACT_QUERIES in src/eval/cases.py; render_answer(AnswerResponse)
  showing the answer text, the single citation as a link to citation_url, and
  "Last updated from sources: <value>"; render_refusal(...) showing whatever
  src/guardrails/messages.py returned
- app.py: a thin Streamlit entrypoint that calls src/query/pipeline.py and renders. It must contain
  no retrieval, prompting, or guardrail logic
- a corpus-stats line (schemes indexed, chunk count, last ingestion date) sourced from
  VectorStore().count() and corpus/sources.csv
- an optional collapsible "sources" disclosure per answer listing retrieved chunk_id, section and
  similarity score (retrieval trace; not a second citation)

Then measure end-to-end latency for a factual query and record the value plus the NFR-7 target in
the README known-limits section.

Hard constraints: facts-only; every answer shows exactly one citation link; the disclaimer note is
always visible; no screenshots, image output, or back-end captures anywhere; no LangChain; type
hints and docstrings.
```

**Verify**

```bash
streamlit run app.py
```

Manually: click all 3 example questions and confirm each yields an answer with one link; ask an advice
question and confirm the refusal renders with an educational link; reload and confirm no re-ingestion
occurs (no long delay, no log line about loading the model corpus).

**Definition of done.** Welcome line, 3 examples, and the disclaimer note render; factual answers show
one citation and the freshness line; refusals render; app start is fast because nothing re-embeds;
latency recorded (FR-15, FR-20, FR-22, FR-25, NFR-7, M5, D-5).

**Sign-off.** ☐ P6 complete

---

## 9. Phase 7 — Evaluation Harness and Packaging (Milestone M6)

**Objective.** Make the acceptance checks executable, then produce every deliverable.

**Prerequisites.** P6 done.

**Files to create**

```
eval.py                        # CLI
src/eval/harness.py            # K19
tests/test_no_secrets.py
SOURCES.md                     # D-2
SAMPLE_QA.md                   # D-4
DISCLAIMER.txt                 # D-5
README.md                      # D-3
```

**Tasks**

1. `src/eval/harness.py` (K19) — run each check and print a pass/fail row:
   E-1 citation coverage; E-2 answer ≤3 sentences; E-3 zero advice statements; E-4 zero computed
   returns; E-5 PII refused for all six classes; E-6 groundedness (every answer traceable to a
   retrieved chunk, and unsupported questions decline); E-7 freshness line present; E-8 second ingest
   is a no-op; E-9 `chunks.txt` complete with metadata. Reuse `validator.py` for E-2/E-3/E-4 rather
   than reimplementing checks, so the harness measures production behaviour.
2. `eval.py` — CLI wrapper; exit non-zero on any failure; `--json` for machine-readable output.
3. `tests/test_no_secrets.py` — assert `.env` is gitignored, no Groq key appears in any tracked file, and
   no PII pattern appears under `logs/` (NFR-2, NFR-5).
4. `SOURCES.md` (D-2) — the source list of the URLs used, generated from `config/sources.yaml` so it can
   never drift from the registry; include scheme, category, and fetch date.
5. `SAMPLE_QA.md` (D-4) — 5–10 queries with the assistant's actual answers and links, generated by
   running the app and pasting the real output. Include at least one refusal and one PII-block example.
6. `DISCLAIMER.txt` (D-5) — the exact disclaimer snippet rendered in the UI, copied from
   `src/guardrails/messages.py`, plus the refusal wording.
7. `README.md` (D-3) — setup steps (venv, install, `cp .env.example .env`, `python ingest.py`, `streamlit
   run app.py`, `python eval.py`), scope (HDFC AMC + the 5 schemes + plan variant), known limits
   (corpus reflects sources as of the ingestion date; `Last updated from sources:` semantics; Direct
   plan only; latency; no performance data by design), and the disclaimer. **No screenshots** (FR-21).
8. Extend `CHUNKING.md` with the "Retrieval tuning" section if P3 has not already done so, so D-6 is
   complete.

**Cursor kickoff prompt (P7)**

```
Implement Phase 7 (Evaluation harness + packaging) of docs/implementation.md, following
architecture.md K19 and the deliverables D-2 through D-7 in docs/PRD.md Section 13. Read
architecture.md Section 13 and PRD.md Section 13 first.

Deliverables:
- src/eval/harness.py (K19): runs and prints pass/fail rows for E-1 citation coverage, E-2 answers
  of at most 3 sentences, E-3 zero advice statements, E-4 zero computed returns, E-5 PII refused for
  all six classes, E-6 groundedness including declines for unsupported questions, E-7 freshness line
  present, E-8 a second ingest run is a no-op, E-9 chunks.txt complete with metadata. REUSE
  src/guardrails/validator.py for E-2/E-3/E-4 instead of reimplementing checks
- eval.py: CLI wrapper, non-zero exit on any failure, --json output
- tests/test_no_secrets.py: assert .env is gitignored, no Groq key appears in any tracked file, and
  no PII pattern appears under logs/
- SOURCES.md (D-2): the URL source list GENERATED from config/sources.yaml so it cannot drift from the
  registry; include scheme, category and fetch date
- SAMPLE_QA.md (D-4): 5-10 queries with the assistant's real answers and links, produced by actually
  running the pipeline, including at least one refusal and one PII-block example
- DISCLAIMER.txt (D-5): the exact disclaimer snippet rendered in the UI, copied from
  src/guardrails/messages.py, plus the refusal wording
- README.md (D-3): setup steps, scope (HDFC AMC + the 5 schemes + plan variant), known limits
  including that the corpus reflects sources as of the ingestion date, the "Last updated from
  sources:" semantics, Direct plan only, and measured latency
- complete the "Retrieval tuning" section of CHUNKING.md if it is missing

Hard constraints: no screenshots or back-end captures in any artifact (FR-21); no third-party blog URLs
in SOURCES.md; no PII in SAMPLE_QA.md; the Groq key must never appear in any committed file.
```

**Verify**

```bash
python eval.py
pytest tests/ -q
grep -r "gsk_\|GROQ_API_KEY=." --include="*.py" --include="*.md" . | grep -v ".env.example"
```

**Definition of done.** Eval table green for E-1…E-9; D-2, D-3, D-4, D-5, D-6, D-7 all present; no
secrets tracked (NFR-2, E-10, M6).

**Sign-off.** ☐ P7 complete ☐ D-2…D-7 present

---

## 10. Phase 8 — Clean-Clone Run and Demo (Milestone M7)

**Objective.** Prove it works from scratch, then demo it. Resolve **Q7**.

**Prerequisites.** P7 done.

**Tasks**

1. **Clean-room run.** In a fresh clone, without `chroma/` and with only `.env.example` copied to `.env`
   plus a real key:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env          # add GROQ_API_KEY
   python ingest.py --stage all
   python eval.py
   streamlit run app.py
   ```
   Confirm: ingestion populates the index, eval is green, the UI answers three questions. Record actual
   timings — this is the NFR-7 evidence.
2. **Restart check.** With `chroma/` present, `streamlit run app.py` must answer immediately with no
   re-ingestion (TC-3, E-8).
3. **Deliverable D-1.** Publish the prototype link if the team can host it, **or** record a ≤3-minute
   video covering: one example question, one refusal, one PII block, and the sources disclosure. Keep
   the video locally as the fallback if the venue network fails (**Q7**).
4. **Rehearse the three questions** from the UI's example buttons plus one adversarial question. Confirm
   the refusal behaviour looks intentional, not broken.
5. Confirm the README's setup steps are exactly what you just ran — fix any drift.

**Cursor kickoff prompt (P8)**

```
Finalize and verify the project per Phase 8 of docs/implementation.md and the deliverables in
docs/PRD.md Section 13. Do not add features.

Do exactly this:
- Perform a clean-room verification in a fresh clone with no chroma/ directory: create a venv,
  pip install -r requirements.txt, cp .env.example .env with a real GROQ_API_KEY, run
  python ingest.py --stage all, python eval.py, streamlit run app.py. Report the actual output of
  each command and the measured timings.
- Verify the restart path: with chroma/ already populated, confirm streamlit run app.py answers a
  question immediately without re-ingestion or re-embedding.
- Verify every deliverable exists and is consistent: D-1 prototype link or <=3-minute demo video
  script outline, D-2 SOURCES.md, D-3 README.md, D-4 SAMPLE_QA.md, D-5 DISCLAIMER.txt, D-6
  CHUNKING.md, D-7 chunks.txt. Report any that is missing or inconsistent.
- Cross-check that the README setup steps exactly match the commands that were just run, and fix
  any drift.
- Confirm no screenshots or back-end captures exist anywhere in the repo, and that no Groq key is
  tracked by git.

Hard constraints: no new features, no refactors, no screenshots, no third-party blog URLs, no PII in
any committed file. Report findings as a checklist.
```

**Definition of done.** Clean clone works end to end; restart needs no re-ingestion; D-1…D-7 complete;
`python eval.py` green; the three demo questions produce correct answers and the adversarial question
produces a clean refusal (E-10, E-11, M7).

**Sign-off.** ☐ P8 complete ☐ demo rehearsed

---

## 11. Deliverable Production Map

| Deliverable | Produced in | Source of truth |
|-------------|-------------|-----------------|
| D-1 prototype link / video | P8 | local run of `app.py` |
| D-2 source list | P7 | `config/sources.yaml` |
| D-3 README | P7 | setup steps verified in P8 |
| D-4 sample Q&A | P7 | real pipeline output |
| D-5 disclaimer snippet | P5 defined, P7 exported | `src/guardrails/messages.py` |
| D-6 chunking strategy | P1 written, P2/P3 appended | `CHUNKING.md` |
| D-7 chunk dump | P2 | `chunks.txt` |

## 12. Common Implementation Traps

| Trap | Why it bites | What to do |
|------|--------------|------------|
| ChromaDB returns a **distance**, not a similarity | With `hnsw:space=cosine`, distance = 1 − similarity; reading the raw value as a similarity inverts your floor logic | Compute `similarity = 1 - distance` in `VectorStore.query` |
| Sentence counting breaks on decimals and abbreviations | "The expense ratio is 1.5%." splits into two "sentences", so a 2-sentence answer looks like 3 | Guard the split regex against `\d\.\d` and known abbreviations before counting in `validator.py` |
| PII false positives on legitimate numbers | A blanket 10-digit rule would block "minimum SIP" and "exit load" answers | Require a context keyword for the numeric PII classes (architecture.md §8.2) |
| ChromaDB metadata rejects `None` and non-primitive values | A `None` `section` raises at write time and looks like a store corruption | Coerce all metadata to `str`/`int`/`float`/`bool` in `vector_store.py` before upsert |
| `add` instead of `upsert` breaks idempotency | Re-running ingestion duplicates chunks and silently corrupts E-8 | Use deterministic `chunk_id`s with `upsert`; keep the idempotency test |
| Letting the model emit the citation | A hallucinated URL reaches the UI and fails E-1/NFR-6 | Renderer always takes `citation_url` from chunk metadata |
| Model answering from memory | Violates C-6 and C-3 even with a good prompt | The relevance gate plus `NOT_IN_SOURCES` handling are the real control; test with `UNSUPPORTED_QUERIES` |
| Re-ingesting on app start | Violates TC-3 and makes the demo slow | The app never calls the ingestion pipeline; it only reads the persisted store |
| Leaking the key into logs or `SOURCES.md` | Fails NFR-2 | `test_no_secrets.py` in P7 |
| Committing `chroma/` or `logs/` | Bloats the repo and may contain PII | `.gitignore` from P0, verified in P7 |

## 13. Final Acceptance Checklist

Run this before submitting. Every line maps to a PRD check.

```bash
python eval.py --json > eval_results.json
```

- [ ] E-1 every factual answer carries one citation link
- [ ] E-2 every answer ≤ 3 sentences
- [ ] E-3 zero advice statements; advice questions refused with an educational link
- [ ] E-4 zero computed/compared returns; performance questions get a factsheet link
- [ ] E-5 PAN, Aadhaar, account number, OTP, email, phone all refused and never stored
- [ ] E-6 answers traceable to a retrieved chunk; unsupported questions decline
- [ ] E-7 `Last updated from sources:` present on every answer
- [ ] E-8 restart does not re-embed; re-running ingest is a no-op
- [ ] E-9 `chunks.txt` complete; `CHUNKING.md` written before implementation
- [ ] E-10 D-1 … D-7 present
- [ ] E-11 prototype link live, or ≤3-minute video recorded
- [ ] No third-party blog URL anywhere in the corpus or citations (C-1)
- [ ] No screenshots or back-end captures in any artifact (FR-21)
- [ ] `.env` gitignored; no key in any tracked file (NFR-2)
- [ ] README documents setup, scope, and known limits (D-3)
- [ ] All 8 open questions in PRD §16 either resolved or explicitly listed in README known limits
