#!/usr/bin/env python3
"""Analyze a fixed ticker universe and rebalance an Alpaca paper account only."""
import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DEFAULT_TICKERS = "WFRD,WING,LPX,BROS,SAIA,APPF"
_TICKER_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,9}\Z")


def _load_local_env():
    """Load local .env values without overriding the process environment."""
    env_path = os.path.join(_ROOT, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def parse_tickers(value):
    """Validate a comma-separated universe with at least two unique symbols."""
    tickers = [ticker.strip().upper() for ticker in (value or "").split(",")
               if ticker.strip()]
    if len(tickers) < 2:
        raise argparse.ArgumentTypeError("provide at least two ticker symbols")
    if len(tickers) != len(set(tickers)):
        raise argparse.ArgumentTypeError("ticker symbols must be unique")
    invalid = [ticker for ticker in tickers if not _TICKER_PATTERN.fullmatch(ticker)]
    if invalid:
        raise argparse.ArgumentTypeError(
            "invalid ticker symbol(s): " + ", ".join(invalid)
        )
    return tickers


def run_paper_rebalance(tickers, dry_run=False):
    """Run analysis, then rebalance through the paper-only Alpaca client."""
    _load_local_env()
    if not os.environ.get("ALPACA_API_KEY", "").strip():
        raise RuntimeError("ALPACA_API_KEY is not configured")
    if not os.environ.get("ALPACA_SECRET_KEY", "").strip():
        raise RuntimeError("ALPACA_SECRET_KEY is not configured")

    import alpaca_trader
    import main as analysis_main

    results, macro_result = analysis_main.run_analysis(
        tickers, skip_news=True, skip_alpha=True
    )
    analyzed = {row["ticker"].strip().upper() for row in results}
    missing = [ticker for ticker in tickers if ticker not in analyzed]
    if missing:
        raise RuntimeError(
            "Analysis data unavailable for: " + ", ".join(missing)
            + "; no rebalance orders submitted"
        )

    outcome = alpaca_trader.run_trading(
        scored_results=results,
        macro_result=macro_result,
        paper=True,
        dry_run=dry_run,
    )
    failures = [
        row.get("ticker", "unknown")
        for row in outcome.get("results", [])
        if row.get("status") == "error"
    ]
    if failures:
        raise RuntimeError(
            "Paper order submission failed for: " + ", ".join(failures)
        )
    if outcome.get("status") == "error":
        raise RuntimeError(
            "Paper rebalance did not complete: " + outcome.get("reason", "unknown error")
        )
    return outcome


def main():
    _load_local_env()
    parser = argparse.ArgumentParser(
        description="Analyze tickers and rebalance an Alpaca paper account."
    )
    parser.add_argument(
        "--tickers",
        default=os.environ.get("TRADING_TICKERS") or _DEFAULT_TICKERS,
        help="Comma-separated symbols (defaults to TRADING_TICKERS or the discovery list)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and display trades without submitting orders",
    )
    args = parser.parse_args()
    try:
        tickers = parse_tickers(args.tickers)
        outcome = run_paper_rebalance(tickers, dry_run=args.dry_run)
    except (argparse.ArgumentTypeError, RuntimeError) as exc:
        parser.error(str(exc))
    print(f"Paper rebalance status: {outcome.get('status', 'unknown')}")


if __name__ == "__main__":
    main()
