"""Minimal offline fixtures for ingestion tests."""

from __future__ import annotations

from src.ingest.registry import SourceSpec

SPECS = (
    SourceSpec(
        source_id="hdfc_large_cap_direct_growth",
        url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
        scheme="HDFC Large Cap Fund - Direct Growth",
        category="large_cap",
        plan_variant="direct_growth",
    ),
    SourceSpec(
        source_id="hdfc_elss_tax_saver",
        url="https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
        scheme="HDFC ELSS Tax Saver Fund - Direct Plan - Growth",
        category="elss",
        plan_variant="direct_growth",
    ),
)

MINIMAL_HTML = """
<html><body>
<nav>Home Products Pricing</nav>
<header>site chrome</header>
<h1>HDFC Large Cap Fund Direct Growth</h1>
<div class="ticker">
  <span>Moderately High Risk</span>
  <span>+8.71 % 3Y annualised</span>
  <span>1D 1M 6M 1Y 3Y 5Y All &#8377;1,189.08</span>
  <span>Min. for SIP &#8377;100</span>
  <span>Fund size (AUM) &#8377;39,933.37 Cr</span>
  <span>Expense ratio 1.03%</span>
</div>
<h2>Minimum investments</h2>
<table>
  <tr><th>Minimum SIP</th><td>&#8377;100</td></tr>
  <tr><th>Minimum lumpsum</th><td>&#8377;100</td></tr>
</table>
<h2>Annualised returns</h2>
<p>1 year 12.4% 3 years 15.2% 5 years 18.9%</p>
<h2>Holdings ( 50 )</h2>
<p>Some holding names</p>
<h2>Exit load</h2>
<p>Exit load of 1% if redeemed within 1 year</p>
<script id="__NEXT_DATA__" type="application/json">
{"props": {"pageProps": {"mfServerSideData": {
  "scheme_name": "HDFC Large Cap Fund Direct Growth",
  "expense_ratio": 1.03,
  "min_sip_investment": 100,
  "exit_load": "Exit load of 1% if redeemed within 1 year",
  "nfo_risk": "Moderately High Riskometer",
  "benchmark_name": "NIFTY 100 Total Return Index",
  "stamp_duty": "0.005% (from July 1st, 2020)",
  "nav": 1189.079,
  "nav_date": "25-Sep-2026",
  "return1y": 12.4,
  "simple_return": {"return1d": 0.14, "return1y": 12.4},
  "return_stats": [{"return1y": 12.4}],
  "holdings": [{"name": "Some Company"}]
}}}}
</script>
</body></html>
"""

ELSS_HTML = MINIMAL_HTML.replace(
    '"benchmark_name": "NIFTY 100 Total Return Index",',
    '"benchmark_name": "NIFTY 500 Total Return Index", "lock_in": {"years": 3, "months": 0, "days": 0},',
)
