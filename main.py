#!/usr/bin/env python3
"""
Stock Recommendation Engine - Macro Buy-and-Hold Strategy

Composite scores use only the public technical, trend, and news-sentiment
analyzers. Their baseline weights are proportionally normalized when a component
is unavailable; private alpha/scoring modules are not used.

Usage:
    python main.py                                    # Analyze all stocks
    python main.py --tickers AAPL,MSFT,NVDA          # Specific tickers
    python main.py --top 10                           # Show top 10 only
    python main.py --quick                            # Skip news (faster)
"""
import os
import market_bars
import sys
import argparse
import math
from datetime import datetime

# Ensure imports resolve from this script's directory when launched elsewhere.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

import data_fetcher
import technical_analyzer
import sentiment_analyzer
import trend_analyzer
import report_generator
import macro_analyzer
from configs import ANALYZER_WEIGHTS, UNIVERSE


def _validated_analyzer_output(name, result):
    """Validate an analyzer's normalized score and confidence before composing."""
    if result is None:
        return None
    try:
        score = float(result["score"])
        confidence = float(result["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} analyzer must provide numeric score and confidence") from exc
    if not math.isfinite(score) or not (0 <= score <= 1):
        raise ValueError(f"{name} analyzer score must be between 0 and 1")
    if not math.isfinite(confidence) or not (0 <= confidence <= 1):
        raise ValueError(f"{name} analyzer confidence must be between 0 and 1")
    return score, confidence


def _analyzer_output_available(name, result):
    if result is None:
        return False
    if not isinstance(result, dict):
        return True
    if name == "sentiment" and result.get("num_articles") == 0:
        return False
    if (name in ("technical", "trend")
            and result.get("confidence") == 0
            and not result.get("components")):
        return False
    return True


def compose_score(technical_result, trend_result, sentiment_result=None):
    """Compose the analyzer-only score, reweighting only available analyzers."""
    outputs = {
        "technical": technical_result,
        "trend": trend_result,
        "sentiment": sentiment_result,
    }
    outputs = {
        name: output for name, output in outputs.items()
        if _analyzer_output_available(name, output)
    }
    validated = {
        name: _validated_analyzer_output(name, output)
        for name, output in outputs.items()
        if output is not None
    }
    if not validated:
        raise ValueError("At least one analyzer result is required to compose a score")

    weight_total = sum(ANALYZER_WEIGHTS[name] for name in validated)
    components = {}
    score = 0.0
    confidence = 0.0
    for name, (raw_score, raw_confidence) in validated.items():
        weight = ANALYZER_WEIGHTS[name] / weight_total
        score += raw_score * weight
        confidence += raw_confidence * weight
        components[name] = {
            "score": round(raw_score * 100, 1),
            "raw_score": raw_score,
            "confidence": raw_confidence,
            "weight": round(weight, 4),
            "details": outputs[name].get("components", {}),
        }

    score = round(score * 100, 1)
    recommendation = (
        "Strong Buy" if score >= 75 else
        "Buy" if score >= 60 else
        "Hold" if score >= 45 else
        "Reduce" if score >= 30 else
        "Avoid"
    )
    grade = (
        "A" if score >= 80 else
        "B+" if score >= 70 else
        "B" if score >= 60 else
        "C+" if score >= 50 else
        "C" if score >= 40 else
        "D" if score >= 30 else
        "F"
    )
    reasoning = [
        f"{name.title()}: {components[name]['score']:.1f}/100 "
        f"({components[name]['weight']:.1%} of available analyzer weight)"
        for name in components
    ]
    regime = (
        (trend_result or {}).get("components", {})
        .get("regime", {}).get("signals", {}).get("regime")
    )
    return {
        "score": score,
        "raw_score": round(score / 100, 4),
        "confidence": round(confidence, 4),
        "grade": grade,
        "recommendation": recommendation,
        "reasoning": reasoning,
        "components": components,
        "regime": regime or "-",
        "score_version": "analyzer-composite-v1",
    }


def analyze_stock(ticker, price_data, benchmark_data, hourly_data=None,
                  news_data=None, alpha_score=None, sector=None,
                  macro_score=None):
    """
    Run the public analyzers on a single stock and compose their results.

    Args:
        price_data: 2y daily OHLCV DataFrame.
        benchmark_data: SPY 2y daily OHLCV DataFrame.
        hourly_data: 1y hourly OHLCV DataFrame (optional, enhances technical signals).
        news_data: list of news items (optional).
        alpha_score: Retained for call compatibility; not used in scoring.
        sector: stock sector string (optional, enhances sentiment analysis).
        macro_score: float 0-100 from macro_analyzer (optional, blended into trend).

    Returns the analyzer-only score_result and technical analyzer result.
    """
    # Technical analysis (daily + hourly)
    tech_result = technical_analyzer.analyze(price_data, hourly_data=hourly_data)

    # Build price context for sentiment analysis
    indicators = tech_result.get("indicators", {})
    price_context = {k: indicators[k] for k in ("price", "change_1d", "change_5d", "change_1m")
                     if k in indicators}

    # Sentiment analysis (Claude LLM with keyword fallback)
    if news_data:
        sent_result = sentiment_analyzer.analyze_sentiment(
            news_data, ticker=ticker, sector=sector, price_context=price_context)
    else:
        sent_result = None

    # Macro trend analysis (daily) — macro_score from macro_analyzer replaces per-stock Claude calls
    trend_result = trend_analyzer.analyze(price_data, benchmark_data, macro_score=macro_score)

    score_result = compose_score(tech_result, trend_result, sent_result)

    # Attach sentiment and trend details for report generation
    score_result["sentiment_detail"] = {
        "reasoning": (sent_result or {}).get("reasoning", ""),
        "key_themes": (sent_result or {}).get("key_themes", []),
        "risk_flags": (sent_result or {}).get("risk_flags", []),
        "method": (sent_result or {}).get("method", "unavailable"),
        "num_articles": (sent_result or {}).get("num_articles", 0),
    }
    score_result["trend_detail"] = {
        "claude_synthesis": (trend_result or {}).get("claude_synthesis", {}),
    }

    return score_result, tech_result


def _fetch_stock_data(ticker, skip_news=False):
    """Fetch all data for a single stock (for parallel execution)."""
    try:
        price_data = data_fetcher.fetch_price_data(ticker, period="2y")
        if price_data is None:
            return ticker, None

        hourly_data = data_fetcher.fetch_hourly_data(ticker, period="1y")

        news_data = None
        if not skip_news:
            raw_news = data_fetcher.fetch_news(ticker)
            if raw_news:
                news_data = raw_news

        info = data_fetcher.fetch_stock_info(ticker)

        return ticker, {
            "price_data": price_data,
            "hourly_data": hourly_data,
            "news_data": news_data,
            "info": info,
        }
    except Exception:
        return ticker, None


def run_analysis(tickers, skip_news=False, skip_alpha=False):
    """
    Run full analysis on a list of tickers.

    Args:
        tickers: list of ticker symbols
        skip_news: if True, skip news sentiment (faster)
        skip_alpha: Retained for call compatibility; alpha scoring is disabled.

    Returns:
        tuple of (results_list, macro_result)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Run macro analysis first
    print(f"\nRunning macro market analysis...")
    try:
        macro_result = macro_analyzer.analyze_macro()
        print(f"  Macro Score: {macro_result['score']:.0f}/100 — {macro_result['recommendation']}")
    except Exception as e:
        print(f"  Macro analysis failed: {e}")
        macro_result = None

    print(f"\nFetching SPY benchmark data...")
    benchmark_data = data_fetcher.fetch_benchmark(period="2y")
    if benchmark_data is None:
        print("  Warning: Could not fetch benchmark data. Relative strength will be unavailable.")

    total = len(tickers)

    # Phase 1: Fetch all data in parallel (I/O bound — threads help a lot)
    print(f"\nFetching data for {total} stocks (parallel)...")
    fetched = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_stock_data, t, skip_news): t for t in tickers}
        for future in as_completed(futures):
            ticker, data = future.result()
            if data is not None:
                fetched[ticker] = data
                bars = len(data["price_data"])
                hourly = len(data["hourly_data"]) if data["hourly_data"] is not None else 0
                news_count = len(data["news_data"]) if data["news_data"] else 0
                print(f"  {ticker}: {bars} daily + {hourly} hourly bars, {news_count} news")
            else:
                print(f"  {ticker}: skipped (no data)")

    print(f"\n{len(fetched)}/{total} stocks fetched. Running analysis...")

    # Phase 2: Analyze sequentially (CPU bound + Claude API calls)
    results = []
    for i, ticker in enumerate(tickers, 1):
        if ticker not in fetched:
            continue

        stock_data = fetched[ticker]
        price_data = stock_data["price_data"]
        hourly_data = stock_data["hourly_data"]
        news_data = stock_data["news_data"]
        info = stock_data["info"]

        print(f"\n[{i}/{total}] Analyzing {ticker}...")

        # Run analysis — pass macro_score so trend_analyzer can blend macro context
        # without making a separate Claude API call per stock
        _macro_score = macro_result.get("score") if macro_result else None
        try:
            score_result, tech_result = analyze_stock(
                ticker, price_data, benchmark_data,
                hourly_data=hourly_data,
                news_data=news_data,
                sector=info.get("sector", "Unknown"),
                macro_score=_macro_score,
            )
        except Exception as e:
            print(f"  Error: {e}")
            continue

        # Get indicators from technical result
        indicators = tech_result.get("indicators", {})

        result = {
            "ticker": ticker,
            "data_as_of": market_bars.last_session_date(price_data),
            "name": info.get("name", ticker),
            "sector": info.get("sector", "Unknown"),
            "score_result": score_result,
            "indicators": indicators,
            "info": info,
            "news_data": news_data or [],
        }

        results.append(result)

        # Print summary
        sr = score_result
        print(f"  Score: {sr['score']:.0f}/100 ({sr['grade']}) - {sr['recommendation']}")
        print(f"  Confidence: {sr['confidence']*100:.0f}% | Regime: {sr.get('regime', '-')}")
        comp = sr["components"]
        component_labels = {
            "technical": "Tech",
            "trend": "Trend",
            "sentiment": "Sentiment",
        }
        component_summary = []
        for name, label in component_labels.items():
            component = comp.get(name)
            if component:
                component_summary.append(
                    f"{label}: {component['score']:.0f} "
                    f"(weight {component['weight']:.1%})"
                )
            else:
                component_summary.append(f"{label}: unavailable")
        print("  " + " | ".join(component_summary))

    import stock_signals
    return stock_signals.add_peer_ranks(results), macro_result


def main():
    parser = argparse.ArgumentParser(
        description="Stock Recommendation Engine - Macro Buy-and-Hold Strategy"
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker symbols (default: all from config)"
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Only show top N recommendations in console output"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip news sentiment analysis (faster)"
    )
    parser.add_argument(
        "--no-alpha", action="store_true",
        help="Deprecated: alpha scoring is disabled; kept for CLI compatibility"
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Output directory for reports (default: reports/)"
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip report generation, only print to console"
    )
    args = parser.parse_args()

    # Determine tickers
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = sorted(set(UNIVERSE.keys()))
        if not tickers:
            parser.error("No default universe is configured; provide --tickers SYMBOL[,SYMBOL...]")

    print("=" * 70)
    print("STOCK RECOMMENDATION ENGINE")
    print("Scoring: Technical (43.75%) + Trend (37.5%) + Sentiment (18.75% when available)")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Stocks: {len(tickers)}")
    print(f"News: {'Enabled' if not args.quick else 'Disabled'}")
    print("Alpha: Disabled (analyzer-only composite)")
    print("=" * 70)

    # Run analysis
    results, macro_result = run_analysis(tickers, skip_news=args.quick, skip_alpha=args.no_alpha)

    if not results:
        print("\nNo results generated. Check your tickers and network connection.")
        sys.exit(1)

    # Sort by score
    results.sort(key=lambda x: x["score_result"]["score"], reverse=True)

    # Print summary
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS SUMMARY")
    print("=" * 70)

    show = results[:args.top] if args.top else results

    print(f"\n{'Ticker':<8} {'Score':>6} {'Grade':>6} {'Rec':<12} {'Conf':>6} {'Regime':<10} {'Price':>10}")
    print("-" * 70)

    for r in show:
        sr = r["score_result"]
        price = r.get("indicators", {}).get("price", 0)
        print(
            f"{r['ticker']:<8} "
            f"{sr['score']:>5.0f} "
            f"{sr['grade']:>6} "
            f"{sr['recommendation']:<12} "
            f"{sr['confidence']*100:>5.0f}% "
            f"{sr.get('regime', '-'):<10} "
            f"${price:>9.2f}"
        )

    # Category counts
    categories = {}
    for r in results:
        rec = r["score_result"]["recommendation"]
        categories[rec] = categories.get(rec, 0) + 1

    print(f"\n{'='*70}")
    for cat in ["Strong Buy", "Buy", "Hold", "Reduce", "Avoid"]:
        count = categories.get(cat, 0)
        if count > 0:
            print(f"  {cat}: {count}")

    # Generate report
    if not args.no_report:
        report_path, _ = report_generator.generate_report(results, output_dir=args.output_dir, macro_result=macro_result)
        print(f"\nReport saved: {report_path}")

    print(f"\nAnalysis complete. {len(results)} stocks analyzed.")


if __name__ == "__main__":
    main()
