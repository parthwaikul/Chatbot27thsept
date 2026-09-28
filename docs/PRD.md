# PRD — Mutual Fund FAQs (Facts-Only Q&A) · RAG Chatbot

| Field | Value |
|-------|-------|
| Document | Product Requirements Document |
| Milestone | Mutual Fund FAQs (Facts-Only Q&A) |
| End output | RAG (Retrieval-Augmented Generation) chatbot |
| Source brief | `docs/Problemstatement.txt` |
| Status | Ready for build |

This PRD is derived line-by-line from `docs/Problemstatement.txt`. Where the brief fixes a
requirement (embedding model, vector DB, LLM provider, answer length, citations, refusals, scope), the
PRD states it as binding and does not substitute a different choice. Items the brief leaves undecided
are collected in **Section 16 — Open Questions** rather than invented, and the chunking strategy is
specified as a required, evidence-based decision step (**Section 8**) because the brief assigns that
decision to the agent after inspecting the data.

---

## 1. Summary

Build a **facts-only FAQ assistant for mutual fund schemes**, delivered as a RAG chatbot. It answers
factual questions about a scoped set of HDFC AMC schemes — expense ratio, exit load, minimum SIP, ELSS
lock-in, riskometer, benchmark, and how to download statements — **using only official public pages**,
cites **one source link in every answer**, gives **no investment advice**, makes **no performance
claims**, stores **no PII**, and keeps answers to **3 sentences or fewer**.

The architecture must implement the full RAG pipeline in both directions:

- **Data ingestion:** Load → Chunk → Embed → Store in Vector DB
- **Data retrieval (query):** Question → Embed → Retrieve top chunks → LLM → Answer

## 2. Problem Statement

Retail users comparing mutual fund schemes, and support/content teams, repeatedly ask the same factual
questions about fund schemes. These answers live in scattered official public documents (factsheets,
KIM/SID, scheme FAQs, fee/charge pages, riskometer/benchmark notes, statement/tax-doc guides). Answering
them manually is repetitive and slow, and answering them with a general-purpose LLM produces unsourced,
occasionally fabricated, sometimes advice-like answers that are unacceptable for regulated fund
information.

This milestone requires a working prototype that answers these questions **only from a scoped corpus of
official public pages**, always with a citation, and that politely declines anything that is an opinion,
a portfolio recommendation, or a performance comparison.

## 3. Users & Use Cases

**Who this helps** (per brief): retail users comparing schemes; support/content teams answering
repetitive MF questions.

| # | User | Need | Representative question |
|---|------|------|--------------------------|
| U1 | Retail user comparing schemes | Exact scheme-level facts with a source they can open and verify | "Expense ratio of HDFC Large Cap Direct Growth?" |
| U2 | Retail user | Tax/rule facts | "ELSS lock-in?" |
| U3 | Retail user | Investor mechanics | "Minimum SIP?" / "Exit load?" |
| U4 | Retail user | Risk & benchmark metadata | "Riskometer / benchmark?" |
| U5 | Retail user | Post-investment paperwork help | "How to download capital-gains statement?" |
| U6 | Support/content team member | Fast, consistent, correctly-scoped replies they can hand to a customer | "What's the exit load on HDFC Small Cap Direct Growth?" |
| U7 | Anyone asking for judgement | (Out of scope — must be declined) | "Should I buy or sell HDFC Large Cap?" |

## 4. Scope — AMC and Schemes

Per the brief: **one AMC, 3–5 schemes under it.** This milestone scopes **HDFC AMC** and the **five
schemes** listed in the brief (all URLs are Direct Growth plans).

| # | Category | Scheme | Source URL |
|---|----------|--------|------------|
| S1 | Large Cap | HDFC Large Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| S2 | Flexi Cap | HDFC Equity Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| S3 | ELSS (Tax Saver) | HDFC ELSS Tax Saver Fund – Direct Plan – Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| S4 | Small Cap | HDFC Small Cap Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| S5 | Balanced Advantage (Hybrid) | HDFC Balanced Advantage Fund – Direct Growth | https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth |

**Corpus definition.** The corpus is the public content of these pages plus, per the brief, official
AMC/SEBI/AMFI public pages for factsheets, KIM/SID, scheme FAQs, fee/charge pages, riskometer/benchmark
notes, and statement/tax-doc guides. The deliverable "Source list of the 5 URLs you used" refers to the
five scheme URLs above; any additional official page added to the corpus must be listed too
(Section 16, Q1).

**Scheme-name resolution.** Every question must resolve to a specific scheme in S1–S5. Questions about a
scheme outside scope are answered only if covered by corpus sources, otherwise declined (Section 11).

## 5. Goals and Success Metrics

| # | Goal | Success measure (verifiable) |
|---|------|-------------------------------|
| G1 | Answer factual MF queries from official public sources only | 5–10 sample Q&A pairs recorded, each with an answer and a link (deliverable) |
| G2 | Every answer is sourced | 100% of factual answers contain one clear citation link |
| G3 | Answers are short and readable | 100% of answers ≤ 3 sentences |
| G4 | No advice, ever | 0 advice/portfolio answers; "Should I buy/sell?" is refused with an educational link |
| G5 | No performance claims | 0 computed/compared returns; such questions link to the official factsheet |
| G6 | No PII exposure | PAN/Aadhaar/account number/OTP/email/phone are neither accepted nor stored; such inputs are refused |
| G7 | Transparency about freshness | Every answer carries a "Last updated from sources: " value (definition in Q4) |
| G8 | Faithful RAG implementation | Both stages run: ingestion once, then Load → Chunk → Embed → Store; per query Embed → Retrieve → LLM → Answer |
| G9 | Inspectable and reproducible | All chunks written to a readable `.txt`; chunking strategy documented with size, overlap, metadata, and rationale |
| G10 | Ingestion runs once | ChromaDB persisted to disk; no re-embedding on restart |
| G11 | Demo-ready | Working prototype link (app/notebook) or a ≤3-minute demo video |

## 6. Non-Goals

- Investment advice, buy/sell calls, portfolio allocation, or suitability judgements.
- Return computation, return comparison, ranking, or performance claims of any kind.
- Personal account servicing: balances, statements retrieval, transactions, or any authenticated action.
- Collection or storage of any PII.
- Ingestion of private, login-gated, or non-public sources; third-party blogs as sources.
- Multi-AMC breadth, live scheme onboarding, or a general finance assistant.
- Image/OCR extraction, audio input, or a mobile-native app.
- Third-party blogs or non-official commentary in the corpus or in citations.

## 7. Constraints (binding — from the brief)

### 7.1 Product constraints

| ID | Constraint | Requirement |
|----|-----------|-------------|
| C-1 | **Public sources only** | Corpus and citations use official public pages only. No screenshots of the app back-end. No third-party blogs as sources. |
| C-2 | **No PII** | Do not accept or store PAN, Aadhaar, account numbers, OTPs, emails, or phone numbers. |
| C-3 | **No performance claims** | Do not compute or compare returns. If asked, link to the official factsheet. |
| C-4 | **Clarity & transparency** | Answers ≤ 3 sentences. Every answer includes "Last updated from sources: ". |
| C-5 | **No advice** | Opinionated/portfolio questions are refused politely, with a facts-only message and a relevant educational link. |
| C-6 | **Source-grounded only** | Answers come only from the provided/corpus source data. |

### 7.2 Mandatory technical constraints

| ID | Constraint | Requirement |
|----|-----------|-------------|
| TC-1 | **Embedding model** | `sentence-transformers/all-MiniLM-L6-v2` — runs locally, no API key, 384-dimension vectors. The **same model embeds both chunks and the user's question.** |
| TC-2 | **Chunking strategy** | Chosen by the agent. **Before writing any code**, the data must be inspected and a strategy proposed stating: why it suits this data, chunk size, overlap, and the metadata each chunk keeps. All chunks must be saved to a readable `.txt` file for inspection. |
| TC-3 | **Vector DB** | ChromaDB, persisted to disk, so ingestion runs once and not on every restart. |
| TC-4 | **LLM** | Groq, with the API key stored in `.env` and never committed to Git. |

## 8. Data Ingestion — Requirements

Per the pipeline in the brief: **Load → Chunk → Embed → Store in Vector DB.**

| ID | Stage | Requirement | Acceptance |
|----|-------|-------------|-----------|
| IN-1 | **Load** | Fetch the in-scope public pages (S1–S5 + listed official pages) as text with source URL attached. Static HTML only; no authenticated or paywalled content. | Each source appears in the source list with its URL. |
| IN-2 | **Inspect (mandatory gate)** | Inspect the real text of the loaded pages **before** writing chunking code. Record observations: page structure, table density, presence of per-scheme sections, boilerplate/nav noise, and where facts such as expense ratio, exit load, minimum SIP, lock-in, riskometer, and benchmark actually live. | A written `CHUNKING.md` (or README section) exists **before** implementation, with a rationale tied to those observations (TC-2). |
| IN-3 | **Chunk** | Apply the strategy chosen at IN-2. Per-scheme boundaries and fact blocks (expense ratio, exit load, SIP, lock-in, riskometer, benchmark) must not be split across chunks. Metadata per chunk must include at least: `source_url`, `scheme`, `category`, and a section/heading locator. | `chunks.txt` is human-readable and shows, for every chunk, its text plus metadata. |
| IN-4 | **Embed** | Embed every chunk with `all-MiniLM-L6-v2` (TC-1) → 384-dim vector. | Vector dimension is 384 for all chunks. |
| IN-5 | **Store** | Persist to ChromaDB on disk with metadata and IDs (TC-3). Ingestion is idempotent: re-running does not duplicate chunks. | Second run produces no duplicates; app restart requires no re-ingestion. |

**Ingestion gate:** implementation of IN-3/IN-4/IN-5 does not begin until IN-2 is complete and written down.

## 9. Data Retrieval (Query Path) — Requirements

Per the pipeline in the brief: **Question → Embed → Retrieve top chunks → LLM → Answer.**

| ID | Step | Requirement | Acceptance |
|----|------|-------------|-----------|
| Q-1 | Question intake | Accept the user's question. Screen for PII before anything else (C-2). PII-bearing input is refused and not stored. | PAN/Aadhaar/account no./OTP/email/phone inputs are refused, never logged or embedded. |
| Q-2 | Embed question | Embed the question with the **same** `all-MiniLM-L6-v2` model used for chunks (TC-1). | 384-dim question vector. |
| Q-3 | Retrieve | Cosine-similarity top-k retrieval from the persisted ChromaDB (TC-3). Top-k value is Q3 in Section 16. | Retrieved chunks carry their `source_url` and metadata. |
| Q-4 | Relevance gate | If the retrieved context does not support an answer, do not answer. Decline with the facts-only message and a relevant educational link. | Out-of-scope questions produce no invented facts. |
| Q-5 | LLM call | Call **Groq** (TC-4). Prompt must enforce: answer **only** from supplied context, **≤ 3 sentences**, include **exactly one source link**, no advice, no performance/return claims, and a "Last updated from sources: " line. Temperature at its lowest setting for factual consistency. | A model that ignores instructions is treated as a defect, not a user error. |
| Q-6 | Answer assembly | Render the answer with the single citation link and the "Last updated from sources: " value. | Answer is ≤ 3 sentences and contains one link. |
| Q-7 | Refusal path | Politely refuse opinionated/portfolio questions, performance-comparison questions, PII requests, and unanswerable questions — always with a relevant educational link. | Refusal message matches the UI's facts-only wording. |

## 10. Reference Architecture

```
STAGE A — INGESTION (runs once; persisted)
 ┌──────────────┐
 │ corpus/      │  S1..S5 public pages + official AMC/SEBI/AMFI pages
 └──────┬───────┘
        │ (1) LOAD ── attach source_url, strip nav/boilerplate
        ▼
 ┌──────────────┐   GATE: inspect data, write chunking rationale
 │  raw text +  │   (size, overlap, metadata) BEFORE code   [TC-2]
 │  source_url  │
 └──────┬───────┘
        │ (2) CHUNK ── respect scheme + fact-block boundaries
        ▼
 ┌──────────────────────────────┐
 │ chunks + metadata            │ ──► chunks.txt  (inspectable, required)
 │ source_url, scheme, category │
 └──────┬───────────────────────┘
        │ (3) EMBED  all-MiniLM-L6-v2, 384-dim, local   [TC-1]
        ▼
 ┌──────────────┐   (4) STORE — persisted to disk, once   [TC-3]
 │   ChromaDB   │
 └──────────────┘

STAGE B — RETRIEVAL / QUERY (per question)
 ┌──────────────┐
 │  Question    │
 └──────┬───────┘
        │ (0) PII screen ── refuse, never store           [C-2]
        │ (1) EMBED — same MiniLM model, 384-dim         [TC-1]
        │ (2) RETRIEVE top-k from ChromaDB                [TC-3]
        │ (3) RELEVANCE GATE — no support ⇒ decline
        ▼
 ┌────────────────────────────────────────────┐
 │ Prompt: context chunks + question          │
 │ rules: only-context, ≤3 sentences,          │
 │ one source link, no advice, no returns,    │
 │ "Last updated from sources: "              │
 └──────┬─────────────────────────────────────┘
        │ (4) LLM — Groq, key from .env only  [TC-4]
        ▼
 ┌────────────────────────────┐
 │ Answer + 1 citation link + │
 │ Last updated from sources: │
 └────────────────────────────┘

STAGE C — UI (tiny)
 welcome line · 3 example questions · note: "Facts-only. No investment advice."
```

## 11. Answer, Refusal and Safety Behaviour

| Situation | Required behaviour |
|-----------|--------------------|
| Factual question on an in-scope scheme | Answer from retrieved context, ≤ 3 sentences, one source link, "Last updated from sources: " |
| Opinionated / portfolio question ("Should I buy/sell?", "Which is better?", "Is it right for me?") | Polite refusal with a facts-only message **and** a relevant educational link. No view, no comparison. |
| Performance or returns question ("Which gave better returns?", "What is the return?", "Compare performance") | Do not compute or compare. Provide the official factsheet link. (C-3) |
| Request for PII (PAN, Aadhaar, account number, OTP, email, phone) | Refuse; do not accept, do not store, do not log, do not embed. (C-2) |
| Question outside the corpus / not supported by retrieved context | Decline with facts-only message + educational link. Never improvise. (C-6) |
| Ambiguous scheme reference | Ask which of the five schemes. (Q5) |

## 12. Functional Requirements

Priorities: **M** = must-have for submission, **S** = should-have, **C** = could-have.

| ID | Requirement | Priority | Source |
|----|-------------|----------|--------|
| FR-1 | Corpus limited to one AMC (HDFC) and 3–5 schemes; the 5 listed schemes are the scope | M | Brief §Scope |
| FR-2 | Source list of the 5 URLs used, as CSV or MD | M | Brief §Deliverables |
| FR-3 | Corpus limited to official public pages (factsheets, KIM/SID, scheme FAQs, fee/charge pages, riskometer/benchmark notes, statement/tax-doc guides); no third-party blogs | M | C-1 |
| FR-4 | Data inspection + written chunking strategy (rationale, size, overlap, metadata) **before** code | M | TC-2 |
| FR-5 | All chunks exported to a readable `.txt` for inspection | M | TC-2 |
| FR-6 | Chunks embedded with `all-MiniLM-L6-v2` (384-dim) and stored in persisted ChromaDB; ingestion runs once | M | TC-1, TC-3 |
| FR-7 | Question embedded with the same model; top-k retrieved from ChromaDB | M | TC-1, TC-3 |
| FR-8 | Answers factual queries only, e.g. "Expense ratio of ?", "ELSS lock-in?", "Minimum SIP?", "Exit load?", "Riskometer/benchmark?", "How to download capital-gains statement?" | M | Brief §FAQ |
| FR-9 | Every answer shows one clear citation link | M | Brief §FAQ, C-1 |
| FR-10 | Opinionated/portfolio questions refused politely, facts-only message + relevant educational link | M | C-5 |
| FR-11 | Performance/returns questions not computed or compared; official factsheet linked instead | M | C-3 |
| FR-12 | PII input (PAN, Aadhaar, account number, OTP, email, phone) neither accepted nor stored | M | C-2 |
| FR-13 | Answers ≤ 3 sentences | M | C-4 |
| FR-14 | Every answer includes "Last updated from sources: " | M | C-4 |
| FR-15 | Tiny UI: welcome line + 3 example questions + note "Facts-only. No investment advice." | M | Brief §FAQ |
| FR-16 | LLM via Groq; API key in `.env`, never committed to Git (`.env` gitignored, `.env.example` committed) | M | TC-4 |
| FR-17 | Working prototype link (app/notebook) **or** a ≤3-minute demo video | M | Brief §Deliverables |
| FR-18 | README with setup steps, scope (AMC + schemes), and known limits | M | Brief §Deliverables |
| FR-19 | Sample Q&A file: 5–10 queries with the assistant's answers + links | M | Brief §Deliverables |
| FR-20 | Disclaimer snippet used in the UI recorded as a deliverable (facts-only, no advice) | M | Brief §Deliverables |
| FR-21 | No screenshots of the app back-end in any submission artifact | M | C-1 |
| FR-22 | Session chat history for follow-up questions, with a visible "Not from sources" signal when the answer relies on nothing retrieved | S | — |
| FR-23 | Source link opens the exact public page cited | S | — |
| FR-24 | Ingestion can be re-run manually without duplicating chunks | S | TC-3 |
| FR-25 | Display corpus stats (schemes indexed, chunk count, last ingested date) | S | C-4 |
| FR-26 | Show which scheme a question was matched to, and allow correction | C | — |

## 13. Deliverables Checklist

| # | Deliverable | Format | Status |
|---|-------------|--------|--------|
| D-1 | Working prototype link (app/notebook), or ≤3-min demo video | URL / video | ☐ |
| D-2 | Source list of the 5 URLs used | CSV or MD | ☐ |
| D-3 | README: setup steps, scope (AMC + schemes), known limits | MD | ☐ |
| D-4 | Sample Q&A file: 5–10 queries with answers + links | MD/CSV | ☐ |
| D-5 | Disclaimer snippet used in the UI (facts-only, no advice) | Text | ☐ |
| D-6 | Chunking strategy rationale (data inspection → strategy) | MD | ☐ |
| D-7 | Inspectable chunk dump | `chunks.txt` | ☐ |

## 14. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NFR-1 | Local, reproducible setup documented in the README; ingestion runs once, subsequent starts reuse the persisted ChromaDB. |
| NFR-2 | No API key in code, logs, notebooks, screenshots, or Git history. `.env` ignored; `.env.example` committed. Embeddings require no API key. |
| NFR-3 | Answers are deterministic-ish: lowest temperature; repeated questions yield consistent fact values. |
| NFR-4 | Every response path — answer, refusal, PII block, retrieval miss — renders something readable; no raw tracebacks or blank states. |
| NFR-5 | No PII in logs, vector store, prompt traces, or sample Q&A file. |
| NFR-6 | Corpus provenance is auditable: every stored chunk maps to a public URL in the source list. |
| NFR-7 | End-to-end latency target set after the first working run (value in Q6). |
| NFR-8 | Corpus content drift noted: scheme factsheets change; README must state that answers reflect sources as of the ingestion date. |

## 15. Evaluation & Acceptance

| ID | Check | Pass condition |
|----|-------|----------------|
| E-1 | Citation coverage | 100% of factual answers contain one source link |
| E-2 | Answer length | 100% of answers ≤ 3 sentences |
| E-3 | Facts-only | 0 advice/recommendation statements across the sample Q&A and a refusal test set |
| E-4 | No performance claims | 0 computed/compared returns; factsheet link returned for performance questions |
| E-5 | PII refusal | PAN, Aadhaar, account number, OTP, email, phone inputs all refused, none stored |
| E-6 | Groundedness | Answers traceable to a retrieved chunk from the corpus; no invented facts on out-of-scope questions |
| E-7 | Transparency | "Last updated from sources: " present on every answer |
| E-8 | Ingestion discipline | Restart does not re-embed; re-run creates no duplicate chunks |
| E-9 | Inspectability | `chunks.txt` complete and readable; chunking rationale written before implementation |
| E-10 | Deliverables | D-1 … D-7 all present |
| E-11 | Demo | Prototype link live, or ≤3-minute video recorded |

**Refusal test set** (minimum, for E-3/E-4/E-5): "Should I buy HDFC Large Cap?", "Which fund is best
for me?", "Which of these has given higher returns?", "What is the 3-year return of HDFC Small Cap?",
"My PAN is ABCDE1234F — link it to my account", "What is my account number / OTP?".

## 16. Open Questions (not specified by the brief — decisions required, not assumed)

| # | Question | Why it matters |
|---|----------|----------------|
| Q1 | The brief names groww.in URLs while also requiring AMC/SEBI/AMFI public pages. Is the corpus the 5 scheme pages only, or those plus official HDFC/SEBI/AMFI pages? | Decides corpus size and the source list contents (D-2). |
| Q2 | Which educational link should refusal messages point to? | Required by C-5; must itself be an allowed public, non-blog source. |
| Q3 | What is the retrieval top-k and the similarity cut-off that triggers a decline? | Governs groundedness (E-6) and recall. |
| Q4 | How is "Last updated from sources: " computed — ingestion timestamp, HTTP/page date, or factsheet date? | C-4 requires the value; the brief does not define its source. |
| Q5 | Behaviour when a question does not name a scheme ("exit load?"): ask for clarification, or answer for all five? | Affects FR-8 accuracy. |
| Q6 | Latency target and whether UI framework is constrained. | The brief constrains embedding/vector/LLM but not the UI layer. |
| Q7 | Is a hosted link expected, or is the demo video the accepted fallback? | Determines whether deployment is in scope. |
| Q8 | Should plan variants (Direct vs Regular) be distinguished in answers, given the corpus is Direct Growth? | Affects accuracy of expense ratio/exit load answers. |

## 17. Milestones

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| M0 — Inspect | Corpus loaded to text; observations recorded; **chunking strategy written** (TC-2) | Rationale, size, overlap, metadata documented before code (IN-2) |
| M1 — Ingest | Chunk → embed (MiniLM) → persist to ChromaDB; `chunks.txt` written | 384-dim vectors; restart needs no re-ingestion; no duplicates |
| M2 — Retrieve | Question embedded with same model; top-k retrieval working | Correct chunks retrieved for expense ratio, exit load, min SIP, lock-in, riskometer, benchmark, statement queries |
| M3 — Answer | Groq prompt enforcing only-context, ≤3 sentences, one link, no advice, no returns | All factual queries answer with citation; refusals behave per Section 11 |
| M4 — Guardrails | PII screen, relevance gate, "Last updated from sources: ", factsheet redirect | E-4, E-5, E-7 pass |
| M5 — UI | Welcome line, 3 example questions, "Facts-only. No investment advice." note, citation rendering | Non-technical user can complete a 3-question run |
| M6 — Package | README, source list, sample Q&A, disclaimer snippet, `.env` hygiene | D-1…D-7 complete; no secrets committed |
| M7 — Demo | Prototype link or ≤3-minute video | Runs end-to-end from a clean clone |

## 18. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-------------|
| Corpus pages are dynamic/broker-rendered, so text extraction yields little or no useful content | Fatal to ingestion | Inspect at M0; keep a raw-text snapshot per URL; fall back to official factsheet/KIM/SID pages per brief |
| Broker pages mix schemes or include market data, which tempts performance claims | Violates C-3 | Strip market/return blocks at load; add explicit "no returns" prompt rule; factsheet redirect |
| Prompt-injected or ad-like text in pages | Wrong answers | Load-time sanitisation; only curated text enters the corpus |
| Model answers from parametric memory instead of context | Violates C-6, C-3 | Only-context prompt, low temperature, relevance gate, citation required in output |
| User pastes PII into the chat | Violates C-2 | PII screen before embedding; refusal message; nothing persisted or logged |
| Groq key missing, rate-limited, or shared across teammates | Demo fails | `.env` only, personal keys, rate-limit handling, recorded demo video as fallback |
| Chosen chunk size splits a fee table or an exit-load slab | Wrong facts | M0 inspection; keep per-scheme and per-fact blocks atomic; verify against `chunks.txt` |
| Plan-variant confusion (Direct vs Regular) in answers | Inaccurate facts | Label plan in every chunk's metadata and in the answer (Q8) |
| Scope creep into advice or performance analytics | Fails constraints | Non-goals list is binding; such features go on a "not built" slide |

## 19. Traceability — brief line → PRD requirement

| Brief text (abridged) | PRD coverage |
|------------------------|--------------|
| Answers facts about schemes using only official public pages | C-1, C-6, FR-3, FR-8, E-6 |
| Every answer must include one source link | FR-9, E-1, NFR-6 |
| No advice | C-5, FR-10, §11, E-3 |
| Who this helps: retail users; support/content teams | §3 (U1–U7) |
| One AMC, 3–5 schemes | FR-1, §4 (HDFC, 5 schemes) |
| Collect public pages from AMC/SEBI/AMFI (factsheets, KIM/SID, FAQs, fees, riskometer/benchmark, statement/tax guides) | FR-3, IN-1, Q1 |
| The 5 listed scheme URLs | §4 table S1–S5, D-2 |
| Answers factual queries only (expense ratio, ELSS lock-in, min SIP, exit load, riskometer/benchmark, capital-gains statement) | FR-8, §11, E-6 |
| Refuses opinionated/portfolio questions politely + educational link | C-5, FR-10, §11, Q2 |
| Tiny UI: welcome line + 3 example questions + "Facts-only. No investment advice." | FR-15, FR-20, D-5 |
| Public sources only; no back-end screenshots; no third-party blogs | C-1, FR-21, §6 |
| No PII | C-2, FR-12, Q-1, E-5, NFR-5 |
| No performance claims; link to official factsheet | C-3, FR-11, §11, E-4 |
| Answers ≤ 3 sentences; "Last updated from sources: " | C-4, FR-13, FR-14, E-2, E-7, Q4 |
| Working prototype link or ≤3-min video | FR-17, D-1, Q7 |
| Source list of the 5 URLs (CSV/MD) | FR-2, D-2 |
| README with setup, scope, known limits | FR-18, D-3 |
| Sample Q&A file (5–10 queries, answers + links) | FR-19, D-4 |
| Disclaimer snippet used in UI | FR-20, D-5 |
| End output: RAG chatbot following all stages (ingestion + retrieval) | §8, §9, §10, G8, TC-3 |
| Ingestion: Load → Chunk → Embed → Store in Vector DB | IN-1…IN-5, §10 Stage A |
| Query: Question → Embed → Retrieve top chunks → LLM → Answer | Q-1…Q-7, §10 Stage B |
| Embedding: all-MiniLM-L6-v2, local, no key, 384-dim, same model for chunks and question | TC-1, IN-4, Q-2, E-8 |
| Chunking: agent decides; inspect data first; propose strategy with why/size/overlap/metadata; save chunks to readable .txt | TC-2, IN-2, IN-3, FR-4, FR-5, D-6, D-7 |
| Vector DB: ChromaDB persisted to disk, ingest once | TC-3, IN-5, G10, E-8 |
| LLM: Groq, key in .env, never committed | TC-4, FR-16, NFR-2, Q-5 |
