# SOURCES.md — deliverable D-2 (FR-2)

The complete list of public sources this assistant answers from. Generated from
`config/sources.yaml` by `python scripts/gen_sources_md.py`, so it cannot drift from
the allowlist the code enforces (C-1). Every URL below is a public scheme page;
there are no third-party blogs and no paywalled material.

Generated: 2026-09-30 · 5 sources · 166 chunks

## Corpus sources

| # | Scheme | Category | Plan | Fetched (UTC) | Chunks | URL |
|---|--------|----------|------|---------------|--------|-----|
| 1 | HDFC Large Cap Fund - Direct Growth | `large_cap` | `direct_growth` | 2026-09-28 | 32 | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 2 | HDFC Equity Fund - Direct Growth | `flexi_cap` | `direct_growth` | 2026-09-28 | 32 | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 3 | HDFC ELSS Tax Saver Fund - Direct Plan - Growth | `elss` | `direct_growth` | 2026-09-28 | 31 | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| 4 | HDFC Small Cap Fund - Direct Growth | `small_cap` | `direct_growth` | 2026-09-28 | 32 | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| 5 | HDFC Balanced Advantage Fund - Direct Growth | `balanced_advantage` | `direct_growth` | 2026-09-28 | 39 | https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth |

All five schemes are from the same AMC (HDFC Mutual Fund), and all five are the
**Direct Growth** plan variant. See "Scope and known limits" in `README.md` for
why the corpus is deliberately that narrow.

## Source links the assistant cites

Not corpus sources — these are the official pages a refusal or redirect points to,
so the user can go read the figure the assistant declined to state (FR-11, FR-20).

| Purpose | URL |
|---------|-----|
| SEBI mutual fund education | https://investor.sebi.gov.in/ |

The official monthly factsheet is the destination for a performance question
(FR-11). Which factsheet a scheme redirects to is configuration, not code:

| Scheme | Official factsheet |
|--------|-------------------|
| HDFC Large Cap Fund - Direct Growth | https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf |
| HDFC Equity Fund - Direct Growth | https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf |
| HDFC ELSS Tax Saver Fund - Direct Plan - Growth | https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf |
| HDFC Small Cap Fund - Direct Growth | https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf |
| HDFC Balanced Advantage Fund - Direct Growth | https://files.hdfcfund.com/s3fs-public/2026-09/HDFC%20MF%20Factsheet%20-%20August%202026.pdf |

> **Known limit.** As configured, all five schemes resolve to the same AMC-level
> factsheet rather than a per-scheme one, so a user redirected here still has to
> find their own scheme's figures. See "Known limits" in `README.md`.

## Rules this file exists to make checkable

- **C-1 / NFR-6** — only the URLs above may be cited. The registry rejects anything
  else, and `src/eval/harness.py` E-6 re-asserts it against real answers.
- **C-3** — the corpus contains no return, risk or performance figures, so the
  assistant never states or compares one; it links the official factsheet instead.
- **FR-2** — this file is the source list required by the brief, and is regenerated
  rather than edited by hand.

---

Facts-only. No investment advice. (D-5 — see `DISCLAIMER.txt`.)
