"""Centralized market-data fetching with an FMP-first path for candidates."""
import os
import re
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta

import market_bars

_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_REQUEST_TIMEOUT = 20


def _fmp_api_key():
    key = os.environ.get("FMP_API_KEY", "").strip()
    if key:
        return key
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    name, value = line.split("=", 1)
                    if name.strip() == "FMP_API_KEY":
                        return value.strip()
    return ""


def _period_start(period):
    match = re.fullmatch(r"(\d+)(d|mo|y)", str(period).strip().lower())
    if not match:
        raise ValueError(f"Unsupported daily history period: {period}")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return date.today() - timedelta(days=amount)
    if unit == "mo":
        return (pd.Timestamp.today().normalize() - pd.DateOffset(months=amount)).date()
    return (pd.Timestamp.today().normalize() - pd.DateOffset(years=amount)).date()


def _fetch_fmp_price_data(ticker, period="2y", interval="1d"):
    """Fetch and normalize daily OHLCV bars from FMP, or return None."""
    api_key = _fmp_api_key()
    if not api_key or interval != "1d":
        return None

    params = {
        "symbol": ticker,
        "from": _period_start(period).isoformat(),
        "to": date.today().isoformat(),
        "apikey": api_key,
    }
    try:
        response = requests.get(
            f"{_FMP_BASE_URL}/historical-price-eod/full",
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  {ticker}: FMP price history unavailable ({type(exc).__name__}); trying yfinance")
        return None

    if not isinstance(payload, list) or not payload:
        print(f"  {ticker}: FMP returned no daily bars; trying yfinance")
        return None

    frame = pd.DataFrame(payload)
    if not {"date", "open", "high", "low", "close", "volume"}.issubset(frame.columns):
        print(f"  {ticker}: FMP daily bars have an unexpected schema; trying yfinance")
        return None
    frame["Date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.set_index("Date").rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame.dropna(subset=["Close"], inplace=True)
    frame = market_bars.drop_unfinished_bars(frame)
    if len(frame) < 50:
        print(f"  {ticker}: FMP returned fewer than 50 daily bars; trying yfinance")
        return None
    return frame


def _fetch_fmp_stock_info(ticker):
    """Fetch the company profile fields used by analysis from FMP."""
    api_key = _fmp_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{_FMP_BASE_URL}/profile",
            params={"symbol": ticker, "apikey": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  {ticker}: FMP company profile unavailable ({type(exc).__name__}); trying yfinance")
        return None
    if not isinstance(payload, list) or not payload:
        print(f"  {ticker}: FMP returned no company profile; trying yfinance")
        return None

    profile = payload[0]
    return {
        "sector": profile.get("sector") or "Unknown",
        "industry": profile.get("industry") or "Unknown",
        "market_cap": profile.get("marketCap") or 0,
        "name": profile.get("companyName") or ticker,
        "pe_ratio": None,
        "forward_pe": None,
        "dividend_yield": None,
        "beta": profile.get("beta"),
    }


def fetch_candidate_data(candidates, period="2y"):
    """Fetch selected Discovery candidates, preferring FMP then yfinance.

    Returns ``{ticker: {price_data, info, price_source, info_source}}``.
    """
    results = {}
    for candidate in candidates:
        ticker = (
            candidate.get("ticker", "")
            if isinstance(candidate, dict) else str(candidate)
        )
        ticker = ticker.strip().upper()
        if not ticker:
            continue

        price_data = _fetch_fmp_price_data(ticker, period=period)
        price_source = "fmp"
        if price_data is None:
            price_data = fetch_price_data(ticker, period=period, interval="1d")
            price_source = "yfinance" if price_data is not None else None

        info = _fetch_fmp_stock_info(ticker)
        info_source = "fmp"
        if info is None:
            info = fetch_stock_info(ticker)
            info_source = "yfinance" if info.get("sector") != "Unknown" else None

        results[ticker] = {
            "price_data": price_data,
            "info": info,
            "price_source": price_source,
            "info_source": info_source,
        }
    return results


def fetch_price_data(ticker, period="2y", interval="1d"):
    """
    Fetch OHLCV price data for a single ticker.

    Returns a clean DataFrame with columns: Open, High, Low, Close, Volume.
    Returns None if data is unavailable.
    """
    try:
        # Use Ticker.history() instead of yf.download() for thread safety.
        # yf.download() shares session state across threads, causing data
        # corruption (merged tickers, duplicate columns) in concurrent fetches.
        t = yf.Ticker(ticker)
        data = t.history(period=period, interval=interval)
    except Exception:
        return None

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    # Deduplicate columns (safety net for any remaining MultiIndex issues)
    if data.columns.duplicated().any():
        data = data.loc[:, ~data.columns.duplicated()]

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in data.columns for c in required):
        return None

    data = data[required].copy()
    data.dropna(subset=["Close"], inplace=True)
    # Never score the session in progress: drop today's bar until the close.
    data = market_bars.drop_unfinished_bars(data)

    if len(data) < 50:
        return None

    return data


def fetch_multiple(tickers, period="2y", interval="1d"):
    """
    Fetch price data for multiple tickers. Returns dict of {ticker: DataFrame}.
    Skips tickers that fail.
    """
    results = {}
    for ticker in tickers:
        df = fetch_price_data(ticker, period=period, interval=interval)
        if df is not None:
            results[ticker] = df
    return results


def fetch_news(ticker, max_items=30):
    """
    Fetch recent news headlines for a ticker via yfinance.

    Returns a list of dicts with keys: title, publisher, publish_time, link.
    Returns empty list on failure.
    """
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news
        if not raw_news:
            return []

        articles = []
        for item in raw_news[:max_items]:
            # Handle both old and new yfinance news format
            content = item.get("content", item)  # New format nests under 'content'

            title = content.get("title", "") or item.get("title", "")
            if not title:
                continue

            # Publisher
            provider = content.get("provider", {})
            if isinstance(provider, dict):
                publisher = provider.get("displayName", "")
            else:
                publisher = item.get("publisher", "")

            # Publish time - try multiple formats
            pub_dt = datetime.now()
            pub_date_str = content.get("pubDate", "")
            pub_time = item.get("providerPublishTime", 0)

            if pub_date_str:
                try:
                    # ISO format: "2026-03-05T23:00:00Z"
                    pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    pass
            elif pub_time:
                try:
                    pub_dt = datetime.fromtimestamp(pub_time)
                except (OSError, ValueError):
                    pass

            # Link
            canonical = content.get("canonicalUrl", {})
            if isinstance(canonical, dict):
                link = canonical.get("url", "")
            else:
                link = item.get("link", "")

            articles.append({
                "title": title,
                "publisher": publisher,
                "publish_time": pub_dt,
                "link": link,
            })

        return articles
    except Exception:
        return []


def fetch_hourly_data(ticker, period="1y"):
    """
    Fetch hourly OHLCV data for a single ticker (up to ~730 days).

    Returns a clean DataFrame or None.
    """
    return fetch_price_data(ticker, period=period, interval="1h")


def fetch_benchmark(period="2y"):
    """Fetch SPY as market benchmark."""
    return fetch_price_data("SPY", period=period)


def fetch_stock_info(ticker):
    """
    Fetch company info (sector, market cap, etc.).
    Returns dict or empty dict on failure.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return {
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap", 0),
            "name": info.get("shortName", ticker),
            "pe_ratio": info.get("trailingPE", None),
            "forward_pe": info.get("forwardPE", None),
            "dividend_yield": info.get("dividendYield", None),
            "beta": info.get("beta", None),
        }
    except Exception:
        return {"sector": "Unknown", "name": ticker}
