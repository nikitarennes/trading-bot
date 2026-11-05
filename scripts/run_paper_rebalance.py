#!/usr/bin/env python3
"""Discover IJH constituents and rebalance the Alpaca paper account weekly."""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


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


def run_paper_rebalance(dry_run=False):
    """Discover eligible names, analyze them, then rebalance paper positions."""
    _load_local_env()
    required_keys = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY")
    missing_keys = [key for key in required_keys if not os.environ.get(key, "").strip()]
    if missing_keys:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing_keys)
        )

    import alpaca_trader
    import main as analysis_main
    import stock_discovery

    universe = stock_discovery.load_ijh_universe()
    print(f"Loaded {len(universe)} current IJH constituents for weekly discovery.")
    discovery = stock_discovery.discover_stocks(universe)
    candidates = discovery.get("additions") or []
    tickers = list(dict.fromkeys(
        str(candidate.get("ticker", "")).strip().upper()
        for candidate in candidates
        if str(candidate.get("ticker", "")).strip()
    ))
    print(f"Stock discovery passed {len(tickers)} candidate(s).")
    if len(tickers) < 2:
        return {
            "status": "skipped",
            "reason": "fewer_than_two_discovery_candidates",
            "candidate_count": len(tickers),
        }

    results, macro_result = analysis_main.run_analysis(
        tickers, skip_news=True, skip_alpha=True
    )
    analyzed = {row["ticker"].strip().upper() for row in results}
    missing = [ticker for ticker in tickers if ticker not in analyzed]
    if missing:
        raise RuntimeError(
            "Analysis data unavailable for discovered candidate(s): "
            + ", ".join(missing)
            + "; no rebalance orders submitted"
        )

    print(
        "Rebalancing against this week's discovery results; holdings no longer "
        "eligible are assigned a zero target and exited."
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
            "Paper rebalance did not complete: "
            + outcome.get("reason", "unknown error")
        )
    return outcome


def main():
    _load_local_env()
    parser = argparse.ArgumentParser(
        description="Discover current IJH constituents and rebalance Alpaca paper positions."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and display trades without submitting orders",
    )
    args = parser.parse_args()
    try:
        outcome = run_paper_rebalance(dry_run=args.dry_run)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(f"Paper rebalance status: {outcome.get('status', 'unknown')}")


if __name__ == "__main__":
    main()
