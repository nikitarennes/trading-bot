# Report layout and validation

`generate_report()` writes matching Markdown and self-contained HTML reports,
and preserves its `(markdown_path, markdown_content)` return value. Styling is
shared with email; external HTML is escaped and unsafe links are rejected.

## Reading order

The main report contains the rating counts, General market outlook and cash
plan, today's stock actions, the unified Options Desk, a merged theme watch
book, and suggested portfolio allocation. The paper-account performance and
weekly discovery sections appear when available.

The watch book combines AI core/watch, chips, optics, SaaS, diversified dips,
and power into one row per ticker. Multiple memberships remain visible. Fixed
star ratings, investment views and risks, chip priorities and first-entry
zones, reference PE/targets, and power opening/adding prices are preserved.
Missing scores are explicitly unavailable and cannot create a buy instruction.
The theme summary distinguishes a price-zone trigger from an actionable buy.

Four evidence appendices hold option-chain data and watch-list ladders, macro
evidence/news, the full score ranking and disabled research scores, and method
notes. HTML folds these appendices and the watch table by default; stock details also
expand on demand. Markdown retains the same evidence in full.
The cash ladder is rendered once, and unified option contracts are rendered
once with their expiry, legs, premium, maximum loss/gain, capital requirement,
and execution checks. Separate watch-list put plans remain reference screens
and do not override the unified score. Top/bottom ranking tables and repeated
component breakdowns are replaced by one complete scoring table.

Method notes read the active momentum mode and risk settings from the strategy
module. Relative rank is never a blanket sell label. Stock budget percentages,
the separate cash pool, and portfolio allocation have different denominators.

## Email and console

HTML email and the plain-text console/email digest use the same decision desks,
without rebuilding all watch lists and rankings. Evidence appendices, individual
stock details and the disclaimer remain in the full HTML/Markdown attachments.
The theme book contributes only its summary table to the email body.

`decision_markdown()` selects and compacts the decision desks in report order.
`email_digest()` measures the rendered output including the actual closing
notice against `EMAIL_BUDGET` (92KB). Oversized sections retain their lead and
first table; the notice identifies abbreviated and omitted content. Full
`render_html(..., email=True)` remains available for an expanded conversion.
Short table cells stay on one line, while long explanatory cells can wrap.

## Validation

```sh
.venv_trading/bin/python -m pytest -q
.venv_trading/bin/python scripts/validate_report.py --replay reports/report_validation/TIMESTAMP/inputs.json
```

The replay uses saved equity, macro and option inputs and does not send email,
place orders, fetch broker holdings, call AI, or fetch missing regime data.
Older cash-plan samples are evaluated under the current percentage schema;
old quotes stay stale. A regime snapshot is derived only from fields present
in the saved sample: missing FRED data, S&P 500 breadth and historical analogs
remain unavailable. Event and freshness checks use the current date. This is
layout/rule validation, not a historical backtest or today's trading report.

The validator checks table widths, section order, deduplicated theme rows,
stock coverage, collapsed appendices, safe HTML/navigation, disabled research
scores, option-label consistency, and email size/decision coverage. It saves
Markdown, full HTML, email HTML, raw inputs and validation metrics under
`reports/report_validation`. Tests also cover fixed-reference preservation,
missing-score handling, compact action budgets, and shared email/text content.

Without `--replay`, the validator fetches public macro and option snapshots for
16 cached equity histories. Regime rendering still uses only those snapshots.
Browser inspection complements structural checks; no validation claims cover
all email clients.

## Privacy

The cash pool is expressed only as percentages; its size is never written into
configuration or reports. Covered-call watch ladders omit position quantities.
Analysis/watch desks do not read broker positions. The separately labelled
Alpaca Paper performance section displays the simulated account when enabled.
