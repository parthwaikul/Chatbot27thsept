# Mutual Fund FAQs — HDFC Scheme Facts (RAG Chatbot)

A facts-only FAQ assistant over five HDFC mutual fund scheme pages. It answers
factual questions from those pages, cites exactly one source per answer, refuses
advice and performance questions, stores no PII, and keeps answers to three
sentences or fewer.

**Facts-only. No investment advice.** ([`DISCLAIMER.txt`](DISCLAIMER.txt))

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add GROQ_API_KEY
python ingest.py --stage all  # one-time: writes chroma/ and chunks.txt
streamlit run app.py
```

To run the acceptance checks instead of the app:

```bash
python eval.py                # E-1 … E-9, exit 0 when green
python eval.py --json         # machine-readable
python -m pytest tests/ -q    # the full suite
```

`ingest.py` is a one-time step. It writes `chroma/` and `chunks.txt`; the app
only reads them, so restarting is immediate and re-running ingest is a no-op
(TC-3, E-8).

The API key is read from `.env` only, is never logged, and `.env` is gitignored.
`.env.example` is committed and contains no secrets (NFR-2, enforced by
`tests/test_no_secrets.py`).

Three settings are worth knowing about, all in `.env`:

| Setting | Value | Why |
|---|---|---|
| `RETRIEVAL_TOP_K` | `3` | Keeps the prompt small enough that three sentences are answerable (Q3) |
| `SIMILARITY_FLOOR` | `0.30` | Below this, the gate declines instead of asking the model (Q3) |
| `EDUCATIONAL_LINK` | SEBI investor-education page | Attached to every refusal (Q2, FR-10) |

## Scope

Deliberately narrow, because a narrow corpus is auditable and a broad one is not.

- **AMC:** HDFC Mutual Fund, only.
- **Schemes:** the five in [`SOURCES.md`](SOURCES.md) — Large Cap, Flexi Cap, ELSS
  Tax Saver, Small Cap, Balanced Advantage.
- **Plan variant:** Direct Growth, only. A "HDFC Large Cap Fund" answer is true
  of the Direct Growth plan; the Regular and Dividend-Handoff variants charge
  different fees, and a user who meant one of those will be misled.
- **Answerable fact types:** expense ratio, exit load, minimum SIP, ELSS lock-in,
  riskometer, benchmark. Six, not seven — see the gap below.
- **Not in scope:** any other AMC, any other plan variant, returns, NAV,
  holdings, tax computation, and portfolio-level questions.

## What it does

The note `Facts-only. No investment advice.` is visible for the whole session
(D-5, FR-20). Everything below the first row is handled without calling the
model:

| You ask | You get |
|---|---|
| A supported fact | An answer with one source link and a freshness line |
| A question naming no scheme | "Which scheme do you mean?" listing the five |
| Should I buy / which is better | A refusal with a SEBI investor-education link |
| Returns, NAV, AUM, holdings | A redirect to the official HDFC factsheet |
| A PAN, Aadhaar, account number, OTP, email, phone | A refusal, and nothing is stored |
| Anything the corpus does not contain | A decline with an educational link |

Ten worked examples, all real output, are in
[`SAMPLE_QA.md`](SAMPLE_QA.md).

## How it is checked

`eval.py` runs the nine acceptance checks from `docs/PRD.md` §15 and prints a
pass/fail row for each. Seven of them call the live model; E-8 and E-9 are
offline and the table labels which is which, so a green run is not mistaken for a
hermetic one.

```
[PASS] E-1  citation coverage      6/6 answers with exactly one link  (live)
[PASS] E-8  ingestion discipline  166 chunks, no duplicates            (offline)
```

Two properties are worth calling out because they are easy to fake:

- **E-2, E-3 and E-4 call the production validator** rather than reimplementing
  sentence counting or the advice/returns lexicons. A harness with its own copy
  of the rules would certify a behaviour the pipeline does not have.
- **E-6 asserts the citation is a registered source**, not merely that a chunk
  was retrieved. A model that cited a plausible URL passes a weaker check.

The suite also includes 5 skips that are deliberate, not accidental: three
performance rows are gated off by default, one ambiguous-query test
short-circuits before the gate can run, and the PII scan under `logs/` skips
because no `logs/` directory exists — absence is the passing state there, since
the screen runs before anything is logged. `python -m pytest tests/ -q` reports
them as skipped rather than hiding them.

## Latency

Measured on the development machine against the live Groq API, 29 Sep 2026
(NFR-7, Q6):

| | Measured |
|---|---|
| Cold first answer (loads the local embedding model) | 8.9 s |
| Warm answer, median of 3 | 4.0 s |
| Retrieval alone, after the model is loaded | 10–220 ms |
| App start to interactive, `chroma/` present | under 1 s |

**Target: under 6 s for a warm answer.** Latency is dominated by the single Groq
call; embedding and Chroma are negligible at this corpus size, as
`architecture.md` §12 predicts. Raising the target above ~8 s would be
misleading, because it would normalise a regression in the model call rather
than catch it.

## Known limits

- **The corpus reflects the sources as of the ingestion date** (28 Sep 2026). A
  figure that changes on the page will be stale until `ingest.py` is re-run.
  `Last updated from sources: 28 Sep 2026` is therefore the date the pages were
  read, not a claim that the figure is current. The date comes from the fetch
  manifest (`corpus/sources.csv`), not from the model, so it cannot drift.
- **No performance data by design.** The corpus contains no verified return
  figures, so the assistant states none and computes none. Performance questions
  redirect to the official factsheet (C-3, FR-11).
- **Latency is not cached or streamed.** Every question is a fresh Groq call.
- **No follow-up context.** Each question is answered independently; the chat
  keeps a transcript for display but does not resolve pronouns against earlier
  turns.
- **One of the seven required fact types is missing.** "How do I download my
  statement?" is documented in `CHUNKING.md` §4: no statement-guide URL was
  available as a public source, so rather than invent one the assistant declines.
  This is the single scope gap against `docs/Problemstatement.txt`.
- **Riskometer conflicts within the sources.** Each scheme page states both
  "Very High Risk" and "Moderately High Riskometer". Answers use the dedicated
  riskometer chunk, so the reading is deterministic, but the underlying source
  contradiction is unresolved (`CHUNKING.md`, Finding 8).
- **Three off-domain questions clear the similarity floor** (see `CHUNKING.md`
  §6). They reach the model and are declined there rather than by the gate.
- **Holdings are not available with weights**, so "top ten holdings" declines.
- **The factsheet link is AMC-level, not per-scheme.** All five schemes redirect
  to the same monthly HDFC factsheet, so a user must still find their own
  scheme's figures.
- **No hybrid retrieval, reranking, or query rewriting**, per PRD §6 non-goals.

## Deliverables

| # | Deliverable | File |
|---|---|---|
| D-2 | Source list | [`SOURCES.md`](SOURCES.md) |
| D-3 | This file | `README.md` |
| D-4 | Sample Q&A, real output | [`SAMPLE_QA.md`](SAMPLE_QA.md) |
| D-5 | Disclaimer and refusal wording | [`DISCLAIMER.txt`](DISCLAIMER.txt) |
| D-6 | Chunking strategy and tuning | [`CHUNKING.md`](CHUNKING.md) |
| D-7 | Chunk dump, text and vectors | `chunks.txt` |
| D-1 | Prototype link or ≤3-min video | *pending — see Q7* |

SOURCES.md, SAMPLE_QA.md, DISCLAIMER.txt and chunks.txt are generated. Do not
edit them by hand; run the generator in each file's header instead.

## Open decisions

`docs/PRD.md` §16 lists eight. Resolved: Q1 (five scheme pages), Q2 (SEBI +
official HDFC factsheet links), Q3 (`RETRIEVAL_TOP_K=3`, `SIMILARITY_FLOOR=0.30`),
Q4 (freshness = ingestion timestamp), Q5 (ask which scheme), Q6 (under 6 s warm,
measured at 4.0 s), Q8 (plan labelled in metadata). Still open: **Q7**, whether
to publish a hosted link or record a ≤3-minute video, which is a
submission-venue decision rather than a build one.


## Source List

The chatbot uses publicly available mutual fund information for the following HDFC Mutual Fund schemes:

- HDFC Large Cap Fund
- HDFC Flexi Cap Fund
- HDFC ELSS Tax Saver
- HDFC Small Cap Fund
- HDFC Balanced Advantage Fund

Detailed source URLs are available in [SOURCES.md](SOURCES.md).

## Sample Q&A

**Q: What is the fund size of HDFC Large Cap Fund Direct Growth?**  
A: The fund size (AUM) is ₹39,933.36 Cr. The answer includes the source and the date on which the source information was last updated.

**Q: What is the expense ratio of HDFC Large Cap Fund Direct Growth?**  
A: FundFacts AI retrieves the relevant expense-ratio information from its indexed source corpus and provides the source with the answer.

**Q: Should I invest in HDFC Large Cap Fund?**  
A: I answer factual questions only, so I can't tell you whether to buy, sell, or hold a scheme, or which one is better for you. Facts such as expense ratio, exit load, minimum SIP, lock-in, riskometer, and benchmark are available from the sources used by the assistant.

Additional examples are available in [SAMPLE_QA.md](SAMPLE_QA.md).

## Disclaimer

FundFacts AI is a **facts-only mutual fund FAQ assistant** intended for informational and educational purposes. It does not provide investment advice, recommendations, buy/sell/hold suggestions, portfolio advice, or predictions about fund performance. Users should verify important information from the cited source and consult an appropriately qualified professional before making investment decisions.

See [DISCLAIMER.txt](DISCLAIMER.txt) for the complete disclaimer.
