# SAMPLE_QA.md — deliverable D-4 (FR-19)

10 real question-and-answer pairs, produced by running the
pipeline against the persisted corpus. Every block below is the actual output of
`src.query.pipeline.answer` rendered by `src.ui.answer_view.render_answer` — nothing
is written by hand. Regenerate with:

```bash
python ingest.py --stage all
python scripts/gen_sample_qa.py
```

Corpus: 5 schemes · captured 2026-09-30 11:31 IST

Model output is not deterministic, so a regenerated file will not be byte-identical.
What must not change is the *shape* of each response: one link on an answer, a fixed
refusal on an opinion question, a redirect on a performance question, and a screen on
anything containing a personal identifier. `python eval.py` is what checks that.

---

## 1. What is the expense ratio of HDFC Large Cap Fund Direct Growth?

**Path taken:** `answer` · intent `FACT` · top match 0.77

```text
Facts-only. No investment advice.

The expense ratio of HDFC Large Cap Fund Direct Growth is 1.03%.
Source: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_large_cap_direct_growth::1::0` — HDFC Large Cap Fund Direct Growth (similarity 0.77)
- `hdfc_large_cap_direct_growth::0::0` — Scheme facts (as published on the scheme page) (similarity 0.59)
- `hdfc_large_cap_direct_growth::3::0` — Expense ratio (similarity 0.58)

---

## 2. What is the exit load on HDFC Balanced Advantage Fund Direct Growth?

**Path taken:** `answer` · intent `FACT` · top match 0.47

```text
Facts-only. No investment advice.

The exit load is 1% if redeemed within 1 year (applies to units in excess of 15% of the investment).
Source: https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_balanced_advantage::0::3` — Scheme facts (as published on the scheme page) (similarity 0.47)
- `hdfc_balanced_advantage::7::0` — Exit Load (similarity 0.47)
- `hdfc_balanced_advantage::5::0` — Exit load (similarity 0.46)

---

## 3. What is the minimum SIP amount for HDFC ELSS Tax Saver Fund?

**Path taken:** `answer` · intent `FACT` · top match 0.67

```text
Facts-only. No investment advice.

The minimum SIP amount for HDFC ELSS Tax Saver Fund is ₹500.
Source: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_elss_tax_saver::0::1` — Scheme facts (as published on the scheme page) (similarity 0.67)
- `hdfc_elss_tax_saver::2::0` — Minimum investments (similarity 0.57)
- `hdfc_elss_tax_saver::0::2` — Scheme facts (as published on the scheme page) (similarity 0.48)

---

## 4. What is the lock-in period for HDFC ELSS Tax Saver Fund?

**Path taken:** `answer` · intent `FACT` · top match 0.54

```text
Facts-only. No investment advice.

The lock‑in period for HDFC ELSS Tax Saver Fund is 3 years.
Source: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_elss_tax_saver::0::7` — Scheme facts (as published on the scheme page) (similarity 0.54)

---

## 5. What is the riskometer category of HDFC Small Cap Fund?

**Path taken:** `answer` · intent `FACT` · top match 0.52

```text
Facts-only. No investment advice.

The riskometer category of HDFC Small Cap Fund is Moderately High.
Source: https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_small_cap::0::4` — Scheme facts (as published on the scheme page) (similarity 0.52)

---

## 6. What benchmark does HDFC Equity Fund Direct Growth track?

**Path taken:** `answer` · intent `FACT` · top match 0.34

```text
Facts-only. No investment advice.

It tracks the NIFTY 500 Total Return Index (NIFTY 500 TRI).
Source: https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_equity_flexi_cap::0::6` — Scheme facts (as published on the scheme page) (similarity 0.34)
- `hdfc_equity_flexi_cap::0::5` — Scheme facts (as published on the scheme page) (similarity 0.24)

---

## 7. Which HDFC fund is the best one for me to invest in?

**Path taken:** `decline` · intent `OUT_OF_SCOPE` · top match 0.68

```text
Facts-only. No investment advice.

I couldn't answer that from the sources I'm limited to. I only answer questions about the five HDFC scheme pages in my sources.
Learn more: https://investor.sebi.gov.in/
Last updated from sources: 28 Sep 2026
```

Sources consulted:

- `hdfc_equity_flexi_cap::13::0` — About HDFC Flexi Cap Direct Plan Growth (similarity 0.68)
- `hdfc_large_cap_direct_growth::13::0` — About HDFC Large Cap Fund Direct Growth (similarity 0.68)
- `hdfc_small_cap::13::0` — About HDFC Small Cap Fund Direct Growth (similarity 0.68)

---

## 8. What is the 5-year return of the HDFC Large Cap Fund?

**Path taken:** `performance` · intent `PERFORMANCE`

```text
Facts-only. No investment advice.

I don't state, compare, or calculate returns or performance. The official monthly factsheet publishes those figures.
Official factsheet: https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf
Last updated from sources: 28 Sep 2026
```

---

## 9. Send the statement to my email id

**Path taken:** `pii` · intent `PII`

```text
Facts-only. No investment advice.

I can't help with that. This assistant does not accept, store, or share personal identifiers such as PAN, Aadhaar, account numbers, OTPs, email addresses, or phone numbers. Please don't share them here.
Last updated from sources: 28 Sep 2026
```

---

## 10. What is the OTP on my phone?

**Path taken:** `pii` · intent `PII`

```text
Facts-only. No investment advice.

I can't help with that. This assistant does not accept, store, or share personal identifiers such as PAN, Aadhaar, account numbers, OTPs, email addresses, or phone numbers. Please don't share them here.
Last updated from sources: 28 Sep 2026
```

---

## What each block demonstrates

| # | Question | Expected branch | What it shows |
|---|----------|-----------------|----------------|
| 1–6 | covered facts | `answer` | one source link, ≤3 sentences, freshness line (E-1, E-2, E-7) |
| 7 | "best for me" | refusal | no advice, no ranking, educational link (FR-10, E-3) |
| 8 | 5-year return | performance | no figure stated or computed; official factsheet link (C-3, E-4) |
| 9–10 | personal identifiers | `pii` | refused before retrieval; nothing stored (C-2, E-5) |

The 5-year return question is absent from the corpus by design and takes the
performance branch (C-3). Questions 9 and 10 name a *class* of identifier without
carrying one, which is what lets this file demonstrate the PII screen while still
containing no personal data (NFR-5). The corpus's one genuine gap is a cross-scheme
aggregate — see "Known limits" in `README.md`.

---

Facts-only. No investment advice.

Full wording and the refusal messages: `DISCLAIMER.txt`. Source list: `SOURCES.md`.
