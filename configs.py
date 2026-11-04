"""Public, environment-backed configuration for the trading bot.

No inferred stock universe is enabled by default. Set TRADING_TICKERS to a
comma-separated list or pass --tickers explicitly before running analysis or
trading. This avoids silently substituting an incomplete universe for the
missing private configuration.
"""
import os


CORE_WATCH = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "AVGO",
    "TSM", "SMH",
)

# Relative source weights; main.compose_score renormalizes over available
# analyzers. These exclude the removed private Alpha component.
ANALYZER_WEIGHTS = {
    "technical": 0.4375,
    "trend": 0.375,
    "sentiment": 0.1875,
}


def _configured_universe():
    raw_tickers = os.environ.get("TRADING_TICKERS", "")
    if not raw_tickers:
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_file):
            with open(env_file, encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        if key.strip() == "TRADING_TICKERS":
                            raw_tickers = value.strip()
                            break
    tickers = {
        ticker.strip().upper()
        for ticker in raw_tickers.split(",")
        if ticker.strip()
    }
    return {
        ticker: {"name": ticker, "sector": "Unknown"}
        for ticker in sorted(tickers)
    }


UNIVERSE = _configured_universe()
