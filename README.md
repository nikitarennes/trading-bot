<div align="center">

# 🤖 Multi-Agent Quantitative Trading System

**A macro buy-and-hold engine that combines technical, trend, macro, and available news-sentiment analysis into a 0–100 score, then constructs a momentum portfolio and can execute paper trades through Alpaca.**

![Python](https://img.shields.io/badge/python-3.12-blue)
![Broker](https://img.shields.io/badge/broker-Alpaca-yellow)
![Status](https://img.shields.io/badge/paper%20trading-live-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## 📈 Live Paper-Trading Performance

> Real money-weighted Alpaca paper account, auto-generated from the broker API.
> Account opened **2026-03-13**. _Past performance ≠ future results._

| Snapshot | 🤖 Strategy (paper) | S&P 500 (SPY) | Nasdaq-100 (QQQ) | Equity |
|---|---:|---:|---:|---:|
| **2026-08-31** (latest) | **+34.5%** | +16.1% | +20.2% | $134,457 |
| 2026-07-09 | +39.8% | +13.5% | +21.4% | $139,829 |
| 2026-06-04 | +53.6% | +13.5% | +24.8% | $159,560 |

Starting capital **$100,000** → peak equity **$165,578** (2026-06-23), current equity **$134,457**, max drawdown **−26.8%**. The book is now **10** names and about **24% cash** (top holdings: TEAM, WIX, PATH) after rotating out of the July semiconductor pile. Still ahead of SPY and QQQ over the same window; the gap vs the June peak is the cost of that high-beta ride.

📊 **Full breakdown + equity curve → [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md)** (refresh anytime with `python scripts/fetch_paper_performance.py`)

---

## 🧪 Backtest — Strategy vs S&P 500 & Nasdaq-100

Per-calendar-year, point-in-time backtest with **T+1 execution** and **5 bps/side transaction costs** (the *same* weighting code the live bot trades):

| Year | 🤖 Strategy | S&P 500 | Nasdaq-100 |
|------|------------:|--------:|-----------:|
| 2023 | +92.4% | +26.7% | +55.9% |
| 2024 | +60.1% | +25.6% | +27.7% |
| 2025 | +24.2% | +18.0% | +21.0% |
| 2026 (YTD) | +26.6% | +10.7% | +21.5% |

> ⚠️ **Honesty note:** the universe is today's survivors held back through time (survivorship bias), so treat the raw alpha as an **upper bound**. The strategy is a high-beta amplifier — it shines in up years and overshoots drawdowns in down years. The full, self-critical analysis is in **[docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)** — including the strategy's edge *over its own basket*, which is small and noisy. We publish the warts on purpose.

---

## 🧠 Multi-Agent Architecture

The score for every stock is composed from the available public analyzers:

```
                         ┌─────────────────────────────┐
   Market data ─────────▶│        DATA LAYER           │  yfinance: 2y daily + 1y hourly
   (prices, news, SPY)   │  data_fetcher / discovery   │
                         └──────────────┬──────────────┘
                                        ▼
        ┌───────────────────────────────────────────────────────────────┐
        │                     ANALYSIS AGENTS                            │
        ├───────────────┬───────────────┬──────────────┬────────────────┤
        │ 📐 Technical  │ 📊 Trend      │ 🌍 Macro context │ 📰 Sentiment │
        │ SMA/RSI/MACD  │ rel-strength  │ blended into    │ news headline│
        │ ADX/Boll/OBV  │ vs SPY        │ trend analysis  │ lexicon+decay│
        │   (43.75%)    │   (37.5%)     │                 │ (18.75%*)    │
        └───────────────────────────────┬───────────────────────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │ Public score composer → 0–100│  available analyzer outputs only
                         │ Public strategy → target wts │  top-10 by 6-1 momentum, 25% cap, 40% vol target
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │  Alpaca executor + report   │  T+1 rebalance, email digest
                         │  (trend / macro / options   │  desks + config & movers)
                         └─────────────────────────────┘
```

**Plus a layer of Claude research agents** (in [`.claude/skills/`](.claude/skills/)) that enrich the macro view — `market-news-analyst`, `scenario-analyzer`, `market-environment-analysis`, `earnings-calendar`, and `economic-calendar-fetcher`.

> The historical 30-factor Alpha engine is not included. Current scoring and portfolio construction are implemented in public `main.py` and `strategy.py`, without a private `core/` dependency. `*` Sentiment is omitted when news is skipped or unavailable, and the remaining component weights are renormalized.

---

## 🎯 Scoring Model

| Agent | Weight | Signals |
|-------|:------:|---------|
| 📐 Technical | 43.75% | SMA 50/200, RSI, MACD, ADX, Bollinger, OBV |
| 📊 Trend | 37.5% | Relative strength vs SPY, market regime and macro context |
| 📰 Sentiment | 18.75%* | News headline analysis with recency decay |

The weights proportionally redistribute the former technical/trend/sentiment baseline after removing private Alpha. If sentiment is absent, technical and trend weights normalize to 53.85% and 46.15%. Analyzer confidence is reported separately and does not change the score.

**Portfolio construction (Strategy V7, 2026-09-28):** the live book is the top-10 by **6-month momentum skipping the latest month** (held names survive to rank 20), ladder-weighted with a 25% cap, a 0–20% macro cash reserve, and a **40% portfolio vol target** (exposure = min(1, 0.40 / 20-day realized vol of the target basket), never leveraged). The previous 1m/3m signal is one env var away (`MOM_MODE=legacy`); `VOL_TARGET=0` disables targeting. Evidence, including a point-in-time S&P 500 check, is in [docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md).

| Score | Recommendation | Grade |
|:-----:|----------------|:-----:|
| 75+ | Strong Buy | A |
| 60–74 | Buy | B / B+ |
| 45–59 | Hold | C / C+ |
| 30–44 | Reduce | D |
| <30 | Avoid | F |

---

## 🚀 Quick Start

> No private `core/` package is required. Set `TRADING_TICKERS` in the environment or pass `--tickers` explicitly; the repository intentionally does not guess a default trading universe.

```bash
python -m venv .venv_trading && source .venv_trading/bin/activate
pip install -r requirements.txt
export TRADING_TICKERS=AAPL,NVDA,MSFT

# Analyze explicitly selected stocks (or set TRADING_TICKERS first)
python main.py --tickers AAPL,NVDA,MSFT

# Specific tickers, top 10, skip news for speed
python main.py --tickers AAPL,NVDA,MSFT --top 10 --quick

# Daily report + paper trade (paper is the default)
python daily_report.py --trade

# Backtest vs SPY (a single recent year, verbose)
python backtest_strategy.py --tickers AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,TSLA,AMD,CRM,ORCL,ADBE
# Multi-year sweep vs SPY & Nasdaq
python backtest_years.py
```

### CLI options (`main.py`)

```
--tickers AAPL,MSFT    Analyze specific stocks
--top N                Show top N results only
--quick                Skip news sentiment (faster)
--no-alpha             Deprecated compatibility flag; Alpha is disabled
--output-dir DIR       Report output directory (default: reports/)
--no-report            Console output only, skip report file
```

---

## 📁 Repository Layout

```
main.py  daily_report.py        Entry points (CLI + daily runner)
data_fetcher.py                 Prices, news, benchmarks (yfinance)
technical_analyzer.py           Trend / momentum / volume / volatility
trend_analyzer.py               Relative strength vs SPY, market regime
strategy.py                     Public momentum ranking, allocation, and vol targeting
configs.py                      Public weights, core watch, optional TRADING_TICKERS universe
macro_analyzer.py               Market regime, 1w/1m/6m stance, rates tape
macro_regime.py                 Regime Desk: 宏观回撤与加仓时机 — breadth, rates/credit/vol, analog stats, entry ladder, reduce triggers + Claude synthesis (web search)
macro_events.py                 Hand-maintained catalyst calendar for the Regime Desk (warns when it runs low)
sp500_members.py                S&P 500 member list for the daily breadth read
daily_watch.py                  Options desk, config watch, top movers
covered_call_advisor.py         Covered-call ladders for a fixed watch list (CC_WATCH)
sell_put_advisor.py             Cash-secured puts on panic dips
ai_sell_put_plan.py             Daily sell-put entry plan for a fixed AI watch list
sentiment_analyzer.py           News headline sentiment
stock_discovery.py              Weekly universe expansion
report_generator.py             Markdown + HTML report output
report_format.py                Shared web/email styling, Markdown rendering, Gmail-sized email digest
email_sender.py                 Email digest + full report attachments
option_data.py                  Option chains from Alpaca (yfinance fallback) for every options desk
ai_portfolio.py                 AI Portfolio roll-up section (NVDA/AVGO/GOOGL/META/TSM/MU/AMAT/ORCL/ANET/SNPS)
saas_watch.py                   SaaS Watch section with fixed star ratings (CRM/NOW/ADBE/DDOG/SNOW/CRWD/NET/WDAY/INTU)
dip_layout.py                   抄底布局 diversified book (BRK.B/UNH/ABBV/ISRG/JPM/V/MA/ETN/PWR/CAT/COST/WMT/BKNG/FCX/XOM/NEE/CEG/GE/RTX/CB)
chip_layout.py                  半导体加仓: first-batch zones + 12-name add priority
optics_layout.py                光学互联 (COHR/LITE/AAOI/CRDO/MRVL/GLW/LYTE)
power_layout.py                 电力加仓 (GEV 920/865, BE 232/200, PWR 600/560)
alpaca_trader.py                Paper/live execution via Alpaca
scripts/run_paper_rebalance.py  Paper-only scheduled rebalance entry point
backtest_strategy.py            Strategy vs buy-and-hold (single window)
backtest_years.py               Per-year sweep vs SPY & Nasdaq
docs/                           STRATEGY · BACKTEST_RESULTS · PAPER_TRADING
scripts/                        fetch_paper_performance.py
.claude/skills/                 Claude research agents
```

---

## ⚙️ Paper Trading Deployment

- **GitHub Actions** runs the paper-only `scripts/run_paper_rebalance.py` workflow every Monday at 13:15 UTC and supports manual dry runs. It builds the Docker image from the repository; no GCP or always-on Codespace is required. GitHub scheduled workflows use UTC and can start late, so this is not an exact-time execution service.
- Before enabling automation, disable any existing Cloud Scheduler trigger for the GCP `daily-report` job to prevent duplicate orders. The workflow refuses live trading by construction: it always calls the Alpaca paper client.
- In repository **Settings → Secrets and variables → Actions**, add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` using **paper-account credentials only**. Set the repository variable `PAPER_TRADING_ENABLED` to `true` only after confirming the old GCP schedule is disabled. The workflow defaults to `WFRD,WING,LPX,BROS,SAIA,APPF`; optionally override this with the `TRADING_TICKERS` repository variable.
- The default rebalance interval is five trading days. The strategy also applies its weight-drift band, gradual rebalance, cash reserve, and open-order guard. The six symbols are the candidate universe, not a promise that all six will be held.
- A GitHub Actions schedule only runs after the workflow is on the default branch and Actions are enabled. Verify the first scheduled run and paper-account orders in the Actions tab. Do not paste credentials into workflow files or logs.
- **Local/manual:** run `python scripts/run_paper_rebalance.py --dry-run` first, then omit `--dry-run` only when intentionally submitting paper orders. Set credentials in `.env`; this entry point has no live-account option.
- **Container:** `docker build -t trading-bot-paper .`; pass paper credentials and `TRADING_TICKERS` at runtime. `.env` is excluded from the build context.

---

## 🔐 Security & Privacy

- Secrets live only in `.env` (git-ignored). No API keys, passwords, or personal emails are tracked.
- The launchd plist is rendered locally and git-ignored; only the placeholder `*.plist.template` is tracked.
- Legacy private Alpha/scoring code is not included; current scoring and portfolio sizing use the public analyzer and strategy modules.

---

## ⚠️ Disclaimer

This project is for **research and educational purposes only**. It is not investment advice. Markets carry risk; past and backtested performance does not guarantee future results. Trade live money at your own risk.

## 📄 License

[MIT](LICENSE) for the published harness. Legacy private Alpha/scoring code is not included in this repository.
