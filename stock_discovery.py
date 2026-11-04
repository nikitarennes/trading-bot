"""Fundamental and price-based discovery for the configured stock universe."""

import csv
import io
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests


_MIN_DOLLAR_VOLUME = 5_000_000
_MIN_MARKET_CAP = 3_000_000_000
_MAX_MARKET_CAP = 10_000_000_000
_MIN_QUARTERS = 4
_MAX_QUARTERS = 5
_QUARTERS_PER_LTM = 4
_REQUEST_PAUSE_SECONDS = 0.25
_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
_ALPACA_DATA_URL = "https://data.alpaca.markets"
_ALPACA_BATCH_SIZE = 20
_REQUEST_TIMEOUT = 30
_IJH_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239763/"
    "ishares-core-s-p-mid-cap-etf/latest-holdings.csv"
)


class MarketDataProviderError(RuntimeError):
    """Raised when a configured market-data provider cannot fulfill a request."""


def load_ijh_universe():
    """Load current equity constituents of IJH as the discovery candidate pool."""
    response = requests.get(
        _IJH_HOLDINGS_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    lines = response.text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines)
         if line.startswith("Ticker,Name,Type,")),
        None,
    )
    if header_index is None:
        raise MarketDataProviderError(
            "IJH holdings CSV is missing its constituent table"
        )

    universe = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        ticker = "-".join((row.get("Ticker") or "").strip().upper().split())
        if (
            row.get("Type", "").strip().upper() != "EQUITY"
            or not ticker
            or ticker == "-"
            or not re.fullmatch(r"[A-Z0-9.-]{1,10}", ticker)
        ):
            continue
        universe[ticker] = {
            "name": (row.get("Name") or ticker).strip(),
            "sector": (row.get("Sector") or "Unknown").strip(),
        }

    if len(universe) < 300:
        raise MarketDataProviderError(
            f"IJH holdings CSV returned only {len(universe)} equity constituents"
        )
    return universe


def _load_env_file():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def discover_stocks(current_universe, scored_results=None, macro_result=None):
    """Return data-validated addition and removal candidates.

    ``current_universe`` supplies the candidate/held symbols and their optional
    configured name and sector. The public arguments and result keys are kept
    compatible with ``daily_report.py`` and ``report_generator.py``.
    """
    del scored_results, macro_result
    if not isinstance(current_universe, dict):
        raise TypeError("current_universe must be a ticker-to-metadata dictionary")
    _load_env_file()
    fmp_api_key = os.environ.get("FMP_API_KEY", "").strip()
    alpaca_key = os.environ.get("ALPACA_API_KEY", "").strip()
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    if not fmp_api_key:
        raise MarketDataProviderError("FMP_API_KEY is not configured")
    if not alpaca_key or not alpaca_secret:
        raise MarketDataProviderError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY are required for stock discovery"
        )

    exclusions = {}
    symbols = sorted(str(symbol).strip().upper()
                     for symbol in current_universe if str(symbol).strip())
    print(f"  Running financial stock discovery for {len(symbols)} symbols...")

    configured_universe = {
        str(symbol).strip().upper(): metadata
        for symbol, metadata in current_universe.items()
        if str(symbol).strip()
    }
    session = requests.Session()
    profiles = {}
    eligible_symbols = []
    for symbol in symbols:
        try:
            profile_rows = _fmp_get(
                session, fmp_api_key, "profile", {"symbol": symbol}
            )
        except MarketDataProviderError:
            raise
        except Exception as exc:
            _record_exclusion(exclusions, "data unavailable")
            print(f"  {symbol}: unable to retrieve company profile: {exc}")
            continue
        profile = profile_rows[0] if profile_rows else {}
        market_cap = _number(profile.get("marketCap"))
        if market_cap is None or market_cap <= 0:
            _record_exclusion(exclusions, "market cap unavailable")
            continue
        if market_cap < _MIN_MARKET_CAP:
            _record_exclusion(exclusions, "market cap below $3B")
            continue
        if market_cap > _MAX_MARKET_CAP:
            _record_exclusion(exclusions, "market cap above $10B")
            continue
        profiles[symbol] = profile
        eligible_symbols.append(symbol)

    eligible_universe = {
        symbol: configured_universe.get(symbol, {})
        for symbol in eligible_symbols
    }
    alpaca_data = (
        _fetch_alpaca_market_data(eligible_symbols, alpaca_key, alpaca_secret)
        if eligible_symbols else {}
    )
    snapshots = {}
    for symbol in eligible_symbols:
        configured = configured_universe.get(symbol, {})
        try:
            snapshots[symbol] = _load_snapshot(
                symbol, configured, session, fmp_api_key,
                alpaca_data.get(symbol), profile=profiles[symbol],
            )
        except MarketDataProviderError:
            raise
        except Exception as exc:
            _record_exclusion(exclusions, "data unavailable")
            print(f"  {symbol}: unable to retrieve discovery data: {exc}")
        time.sleep(_REQUEST_PAUSE_SECONDS)

    sector_averages = _sector_averages(snapshots)
    _impute_sector_metrics(snapshots, sector_averages)
    removals = _identify_removals(
        None, eligible_universe, financial_data=snapshots,
        sector_averages=sector_averages,
    )
    removal_symbols = {item["ticker"] for item in removals}
    additions = []

    for symbol, snapshot in snapshots.items():
        if not snapshot["quarterly_complete"]:
            _record_exclusion(exclusions, "Value Trap: fewer than four complete quarters")
            continue
        price_data = snapshot.get("price_data")
        if price_data and price_data.get("listed_two_years") is False:
            _record_exclusion(exclusions, "Value Trap: listing history is under two years")
            continue
        if price_data is None or price_data.get("unavailable"):
            _record_exclusion(exclusions, "price/volume data unavailable")
            continue

        category = _classify(snapshot, sector_averages)
        if category is None:
            _record_exclusion(exclusions, "forward criteria not met")
            continue

        if not snapshot["recovery"]:
            _record_exclusion(exclusions, "Value Trap: no upward LTM QoQ trend")
            continue

        if (
            price_data["volume_data_available"]
            and not price_data["volume_expansion"]
        ):
            _record_exclusion(exclusions, "insufficient volume expansion")
            continue

        if category in ("Core Long", "Weak Long"):
            if (price_data["return_21d"] <= 0
                    or price_data["live_price"] <= price_data["sma20"]):
                _record_exclusion(exclusions, "price trend confirmation failed")
                continue
        elif price_data["return_10d"] <= 5:
            _record_exclusion(exclusions, "Turnaround return below 5%")
            continue

        if symbol in removal_symbols:
            _record_exclusion(exclusions, "also meets a removal rule")
            continue

        additions.append(_addition_record(snapshot, category))

    additions.sort(
        key=lambda item: (
            item["category"],
            item["eg_fy2"] if item["eg_fy2"] is not None else -math.inf,
        ),
        reverse=True,
    )
    report = _build_discovery_report(additions, removals, [], "", exclusions)
    return {
        "additions": additions,
        "removals": removals,
        "themes": [],
        "report": report,
    }


def _record_exclusion(exclusions, reason):
    exclusions[reason] = exclusions.get(reason, 0) + 1


def _load_snapshot(
    symbol, configured, session, fmp_api_key, alpaca_data, profile=None
):
    profile = profile or {}
    statement_rows = _fmp_get(
        session, fmp_api_key, "income-statement",
        {"symbol": symbol, "period": "quarter", "limit": _MAX_QUARTERS},
    )
    estimate_rows = _fmp_get(
        session, fmp_api_key, "analyst-estimates",
        {"symbol": symbol, "period": "annual", "limit": 5},
    )
    statement_rows = statement_rows if isinstance(statement_rows, list) else []
    estimate_rows = estimate_rows if isinstance(estimate_rows, list) else []

    sector = (
        profile.get("sector")
        or _configured_value(configured, "sector")
        or "Unknown"
    )
    name = (
        profile.get("companyName")
        or _configured_value(configured, "name")
        or symbol
    )
    statement_frame = _statement_frame(statement_rows)
    quarterly = _quarterly_ltm(statement_frame)
    live_price = (
        _number(alpaca_data.get("live_price"))
        if alpaca_data else None
    )
    estimates = _forward_estimates(
        estimate_rows, statement_rows, live_price, profile
    )
    price_data = _price_metrics(alpaca_data)
    return {
        "ticker": symbol,
        "name": name,
        "sector": sector,
        "live_price": live_price,
        "market_cap": _number(profile.get("marketCap")),
        "ipo_date": _as_date(profile.get("ipoDate")),
        **estimates,
        **quarterly,
        "price_data": price_data,
    }


def _fmp_get(session, api_key, endpoint, params):
    request_params = {**params, "apikey": api_key}
    try:
        response = session.get(
            f"{_FMP_BASE_URL}/{endpoint}",
            params=request_params,
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MarketDataProviderError(
            f"FMP {endpoint} request failed ({type(exc).__name__})"
        ) from None
    finally:
        time.sleep(_REQUEST_PAUSE_SECONDS)

    if response.status_code in (401, 402, 403, 429):
        raise MarketDataProviderError(
            f"FMP {endpoint} returned HTTP {response.status_code}; "
            "check API credentials, subscription access, and request quota"
        )
    try:
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MarketDataProviderError(
            f"FMP {endpoint} returned an invalid response"
        ) from exc
    if isinstance(data, dict) and (
        data.get("Error Message") or data.get("error")
    ):
        raise MarketDataProviderError(f"FMP {endpoint} returned an API error")
    if not isinstance(data, list):
        raise MarketDataProviderError(
            f"FMP {endpoint} returned unexpected {type(data).__name__} data"
        )
    return data


def _alpaca_symbol(symbol):
    return symbol.replace("-", ".")


def _from_alpaca_symbol(symbol):
    return symbol.replace(".", "-")


def _alpaca_get(session, path, params, headers):
    try:
        response = session.get(
            f"{_ALPACA_DATA_URL}{path}",
            params=params,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MarketDataProviderError(
            f"Alpaca market-data request failed ({type(exc).__name__})"
        ) from exc
    if response.status_code in (401, 403, 429):
        raise MarketDataProviderError(
            f"Alpaca {path} returned HTTP {response.status_code}; "
            "check API credentials, data-feed entitlement, and request quota"
        )
    try:
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MarketDataProviderError(
            f"Alpaca {path} returned an invalid response"
        ) from exc
    time.sleep(_REQUEST_PAUSE_SECONDS)
    return payload


def _fetch_alpaca_market_data(symbols, api_key, api_secret):
    """Fetch two years of daily bars and latest trades in Alpaca symbol batches."""
    session = requests.Session()
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    bars_by_symbol = {symbol: [] for symbol in symbols}
    start = (datetime.now(timezone.utc) - timedelta(days=760)).isoformat()

    for offset in range(0, len(symbols), _ALPACA_BATCH_SIZE):
        batch = symbols[offset:offset + _ALPACA_BATCH_SIZE]
        alpaca_symbols = [_alpaca_symbol(symbol) for symbol in batch]
        token = None
        while True:
            params = {
                "symbols": ",".join(alpaca_symbols),
                "timeframe": "1Day",
                "start": start,
                "limit": 10000,
                "adjustment": "all",
                "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
                "sort": "asc",
            }
            if token:
                params["page_token"] = token
            payload = _alpaca_get(
                session, "/v2/stocks/bars", params, headers
            )
            for alpaca_symbol, rows in (payload.get("bars") or {}).items():
                symbol = _from_alpaca_symbol(alpaca_symbol)
                if symbol in bars_by_symbol:
                    bars_by_symbol[symbol].extend(rows)
            token = payload.get("next_page_token")
            if not token:
                break

    latest_by_symbol = {}
    for offset in range(0, len(symbols), 100):
        batch = symbols[offset:offset + 100]
        payload = _alpaca_get(
            session,
            "/v2/stocks/trades/latest",
            {
                "symbols": ",".join(_alpaca_symbol(symbol) for symbol in batch),
                "feed": os.environ.get("ALPACA_DATA_FEED", "iex"),
            },
            headers,
        )
        for alpaca_symbol, trade in (payload.get("trades") or {}).items():
            latest_by_symbol[_from_alpaca_symbol(alpaca_symbol)] = trade

    market_data = {}
    for symbol in symbols:
        trade = latest_by_symbol.get(symbol) or {}
        market_data[symbol] = {
            "bars": bars_by_symbol.get(symbol, []),
            "live_price": _number(trade.get("p")),
        }
    return market_data


def _configured_value(configured, key):
    return configured.get(key) if isinstance(configured, dict) else None


def _as_date(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, (int, float, np.integer, np.floating)):
        if not math.isfinite(float(value)) or float(value) <= 0:
            return None
        value = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
    else:
        value = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(value):
        return None
    return value.date()


def _normalize_label(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _find_row(frame, aliases):
    if not isinstance(frame, pd.DataFrame):
        return None
    by_label = {_normalize_label(index): index for index in frame.index}
    for alias in aliases:
        index = by_label.get(_normalize_label(alias))
        if index is not None:
            return index
    return None


def _number(value):
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _statement_frame(statement_rows):
    values = {}
    for row in statement_rows:
        period_date = row.get("date")
        if not period_date:
            continue
        values[period_date] = {
            "Total Revenue": _number(row.get("revenue")),
            "Gross Profit": _number(row.get("grossProfit")),
            "EBITDA": _number(row.get("ebitda")),
            "EBIT": _number(row.get("ebit") or row.get("operatingIncome")),
            "EPS": _number(row.get("eps")),
        }
    if not values:
        return pd.DataFrame()
    return pd.DataFrame(values).sort_index(axis=1, ascending=False)


def _reported_fy0_eps(statement_rows):
    """Sum the last four reported quarterly EPS values for the FY0 baseline."""
    valid_rows = []
    for row in statement_rows:
        period_date = pd.to_datetime(row.get("date"), errors="coerce")
        eps = _number(row.get("eps"))
        if pd.isna(period_date) or eps is None:
            continue
        valid_rows.append((period_date, eps))
    valid_rows.sort(key=lambda item: item[0], reverse=True)
    if len(valid_rows) < _QUARTERS_PER_LTM:
        return None
    latest = valid_rows[:_QUARTERS_PER_LTM]
    dates = [item[0] for item in latest]
    if any(not 70 <= (dates[i] - dates[i + 1]).days <= 110 for i in range(3)):
        return None
    return sum(value for _, value in latest)


def _forward_estimates(estimate_rows, statement_rows, live_price, profile=None):
    """Align FMP annual analyst EPS estimates to the active fiscal year."""
    profile = profile or {}
    quarterly = sorted(
        statement_rows,
        key=lambda row: pd.to_datetime(row.get("date"), errors="coerce")
        if not pd.isna(pd.to_datetime(row.get("date"), errors="coerce"))
        else pd.Timestamp.min,
        reverse=True,
    )
    latest_report = quarterly[0] if quarterly else {}
    latest_report_date = _as_date(latest_report.get("date"))
    latest_period = str(latest_report.get("period", "")).upper()
    quarter_number = _number(latest_period.removeprefix("Q"))
    fiscal_end = None
    if latest_report_date is not None and quarter_number in (1, 2, 3, 4):
        months_to_fiscal_end = (
            12 if quarter_number == 4 else int(4 - quarter_number) * 3
        )
        fiscal_end = (
            pd.Timestamp(latest_report_date)
            + pd.DateOffset(months=months_to_fiscal_end)
        ).date()

    estimates = []
    for row in estimate_rows:
        estimate_date = _as_date(row.get("date"))
        eps = _number(row.get("epsAvg"))
        if estimate_date is None or eps is None:
            continue
        estimates.append((estimate_date, eps))
    estimates.sort(key=lambda item: item[0])

    fy1_index = next(
        (i for i, (estimate_date, _) in enumerate(estimates)
         if fiscal_end is None or estimate_date >= fiscal_end),
        None,
    )
    eps_fy1 = estimates[fy1_index][1] if fy1_index is not None else None
    if fy1_index is not None:
        fiscal_end = estimates[fy1_index][0]
    eps_fy2 = (
        estimates[fy1_index + 1][1]
        if fy1_index is not None and fy1_index + 1 < len(estimates)
        else None
    )
    eps_fy0 = _reported_fy0_eps(quarterly)

    if eps_fy1 is None:
        forward_pe = _number(profile.get("forwardPE"))
        if live_price is not None and forward_pe not in (None, 0):
            eps_fy1 = live_price / forward_pe

    return {
        "fiscal_year_end": fiscal_end,
        "adjusted_eps": eps_fy1,
        "eps_fy0": eps_fy0,
        "eps_fy1": eps_fy1,
        "eps_fy2": eps_fy2,
        "eps_fy1_source": "analyst" if fy1_index is not None else (
            "forwardPE" if eps_fy1 is not None else None
        ),
        "eps_fy2_source": "analyst" if eps_fy2 is not None else None,
        "eg_fy1": _growth_rate(eps_fy1, eps_fy0),
        "eg_fy2": _growth_rate(eps_fy2, eps_fy1),
        "pe_fy1": None,
        "pe_fy2": None,
        "forward_data_available": any(
            value is not None for value in (eps_fy0, eps_fy1, eps_fy2)
        ),
        "analyst_estimate_available": (
            eps_fy1 is not None or eps_fy2 is not None
        ),
        "estimates_complete": False,
    }


def _growth_rate(new_value, old_value):
    if new_value is None or old_value is None or old_value == 0:
        return None
    return ((new_value - old_value) / old_value) * 100


def _quarterly_ltm(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {"quarterly_complete": False, "recovery": False}

    metrics = {
        "revenue": ("Total Revenue", "Operating Revenue", "Revenue"),
        "gross_profit": ("Gross Profit",),
        "ebitda": ("EBITDA",),
        "ebit": ("EBIT", "Operating Income"),
    }
    rows = {name: _find_row(frame, aliases) for name, aliases in metrics.items()}
    if any(row is None for row in rows.values()):
        return {"quarterly_complete": False, "recovery": False}

    dated_columns = []
    for column in frame.columns:
        period_end = pd.to_datetime(column, utc=True, errors="coerce")
        if not pd.isna(period_end):
            dated_columns.append((period_end, column))
    dated_columns.sort(key=lambda item: item[0], reverse=True)
    if len(dated_columns) < _MIN_QUARTERS:
        return {"quarterly_complete": False, "recovery": False}
    recent = dated_columns[:_MAX_QUARTERS]
    dates = [item[0] for item in recent]
    gaps = [
        (dates[index] - dates[index + 1]).days
        for index in range(len(dates) - 1)
    ]
    if any(gap < 70 or gap > 110 for gap in gaps):
        return {"quarterly_complete": False, "recovery": False}

    raw = {}
    for name, row in rows.items():
        values = [_number(frame.loc[row, column]) for _, column in recent]
        if any(value is None for value in values):
            return {"quarterly_complete": False, "recovery": False}
        raw[name] = values

    anchor_count = len(recent) - _QUARTERS_PER_LTM + 1
    ltm = {
        name: [
            sum(values[offset:offset + _QUARTERS_PER_LTM])
            for offset in range(anchor_count)
        ]
        for name, values in raw.items()
    }
    margins = [
        ebitda / revenue if revenue != 0 else None
        for ebitda, revenue in zip(ltm["ebitda"], ltm["revenue"])
    ]
    complete_margins = all(value is not None and math.isfinite(value) for value in margins)
    revenue_improving = (
        len(ltm["revenue"]) >= 2
        and ltm["revenue"][0] > ltm["revenue"][1]
    )
    margin_improving = (
        complete_margins
        and len(margins) >= 2
        and margins[0] > margins[1]
    )
    recovery = revenue_improving or margin_improving
    return {
        "quarterly_complete": True,
        "recovery": bool(recovery),
        "revenue_ltm": ltm["revenue"],
        "gross_profit_ltm": ltm["gross_profit"],
        "ebitda_ltm": ltm["ebitda"],
        "ebit_ltm": ltm["ebit"],
        "ebitda_margin_ltm": margins,
    }


def _price_metrics(market_data):
    if not market_data:
        return {"unavailable": "no Alpaca market data"}
    bars = market_data.get("bars") or []
    if not bars:
        return {"unavailable": "empty Alpaca price history"}
    frame = pd.DataFrame(bars)
    if not {"t", "c"}.issubset(frame.columns):
        return {"unavailable": "Alpaca bars missing timestamp or close"}
    frame["timestamp"] = pd.to_datetime(frame["t"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["c"], errors="coerce")
    if "v" in frame.columns:
        frame["volume"] = pd.to_numeric(frame["v"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp")
    frame = frame[frame["close"] > 0]
    if frame.empty:
        return {"unavailable": "empty Alpaca price history"}

    last_bar = frame["timestamp"].iloc[-1].date()
    if (date.today() - last_bar).days > 7:
        return {"unavailable": "stale Alpaca price history"}
    close = pd.Series(
        frame["close"].to_numpy(),
        index=frame["timestamp"],
        dtype="float64",
    )
    earliest_stamp = close.index[0] if len(close) else None
    two_year_cutoff = (
        pd.Timestamp(date.today()) - pd.DateOffset(years=2)
    ).normalize()
    listed_two_years = (
        earliest_stamp is not None
        and earliest_stamp.tz_localize(None).normalize() <= two_year_cutoff
    )
    if len(close) < 64:
        return {
            "unavailable": "fewer than 64 valid daily bars",
            "listed_two_years": bool(listed_two_years),
        }

    live_price = _number(market_data.get("live_price"))
    if live_price is None or live_price <= 0:
        return {"unavailable": "live price unavailable"}

    return_21d = (live_price / float(close.iloc[-22]) - 1) * 100
    return_10d = (live_price / float(close.iloc[-11]) - 1) * 100
    volume_available = False
    average_volume_10d = None
    average_volume_60d = None
    average_dollar_volume_20d = None
    if "volume" in frame.columns:
        volume = pd.Series(
            frame["volume"].to_numpy(),
            index=frame["timestamp"],
            dtype="float64",
        ).reindex(close.index).dropna()
        volume = volume[volume >= 0]
        if len(volume) >= 60 and volume.index[-1] == close.index[-1]:
            volume_available = True
            average_volume_10d = float(volume.tail(10).mean())
            average_volume_60d = float(volume.tail(60).mean())
            average_dollar_volume_20d = float(volume.tail(20).mean() * live_price)
    sma20 = float(close.tail(20).mean())

    return {
        "live_price": live_price,
        "close": close,
        "return_21d": return_21d,
        "return_10d": return_10d,
        "sma20": sma20,
        "average_volume_10d": average_volume_10d,
        "average_volume_60d": average_volume_60d,
        "volume_data_available": volume_available,
        "volume_expansion": (
            average_volume_10d > 1.15 * average_volume_60d
            if volume_available else True
        ),
        "listed_two_years": listed_two_years,
        "average_dollar_volume_20d": average_dollar_volume_20d,
    }


def _sector_averages(snapshots):
    records = []
    for snapshot in snapshots.values():
        if not snapshot.get("analyst_estimate_available"):
            continue
        price = _number(snapshot.get("live_price"))
        eps_fy0 = _number(snapshot.get("eps_fy0"))
        eps_fy1 = _number(snapshot.get("eps_fy1"))
        eps_fy2 = _number(snapshot.get("eps_fy2"))
        eg_fy1 = _growth_rate(eps_fy1, eps_fy0)
        eg_fy2 = _growth_rate(eps_fy2, eps_fy1)
        row = {
            "sector": snapshot["sector"],
            "eps_fy0": eps_fy0 if eps_fy0 is not None else np.nan,
            "eps_fy1": eps_fy1 if eps_fy1 is not None else np.nan,
            "eps_fy2": eps_fy2 if eps_fy2 is not None else np.nan,
            "pe_fy1": (
                price / eps_fy1
                if price is not None and eps_fy1 is not None and eps_fy1 > 0
                else np.nan
            ),
            "pe_fy2": (
                price / eps_fy2
                if price is not None and eps_fy2 is not None and eps_fy2 > 0
                else np.nan
            ),
            "eg_fy1": eg_fy1 if eg_fy1 is not None else np.nan,
            "eg_fy2": eg_fy2 if eg_fy2 is not None else np.nan,
        }
        records.append(row)
    if not records:
        return {}
    frame = pd.DataFrame(records)
    grouped = frame.groupby("sector").mean(numeric_only=True)
    medians = frame.groupby("sector")[["eps_fy0", "eps_fy1", "eps_fy2"]].median()
    averages = grouped.to_dict(orient="index")
    for sector, row in medians.iterrows():
        averages.setdefault(sector, {}).update({
            "eps_fy0_median": _number(row["eps_fy0"]),
            "eps_fy1_median": _number(row["eps_fy1"]),
            "eps_fy2_median": _number(row["eps_fy2"]),
        })
    return averages


def _impute_sector_metrics(snapshots, sector_averages):
    """Impute missing forward metrics from same-sector forward observations."""
    for snapshot in snapshots.values():
        sector = sector_averages.get(snapshot["sector"], {})
        imputed = []
        eps_fy0 = _number(snapshot.get("eps_fy0"))
        eps_fy1 = _number(snapshot.get("eps_fy1"))
        eps_fy2 = _number(snapshot.get("eps_fy2"))
        live_price = _number(snapshot.get("live_price"))

        if eps_fy0 is None:
            eps_fy0 = _number(sector.get("eps_fy0_median"))
            if eps_fy0 is not None:
                imputed.append("eps_fy0")
        if eps_fy1 is None:
            eps_fy1 = _number(sector.get("eps_fy1_median"))
            if eps_fy1 is not None:
                imputed.append("eps_fy1")
            elif live_price is not None:
                sector_pe = _number(sector.get("pe_fy1"))
                if sector_pe is not None and sector_pe > 0:
                    eps_fy1 = live_price / sector_pe
                    imputed.append("eps_fy1")

        if eps_fy2 is None:
            eps_fy2 = _number(sector.get("eps_fy2_median"))
            if eps_fy2 is not None:
                imputed.append("eps_fy2")

        eg_fy1 = _growth_rate(eps_fy1, eps_fy0)
        if eg_fy1 is None:
            eg_fy1 = _number(sector.get("eg_fy1"))
            if eg_fy1 is not None:
                imputed.append("eg_fy1")

        eg_fy2 = _growth_rate(eps_fy2, eps_fy1)
        if eg_fy2 is None:
            eg_fy2 = _number(sector.get("eg_fy2"))
            if eg_fy2 is not None:
                imputed.append("eg_fy2")

        snapshot["eps_fy0"] = eps_fy0
        snapshot["eps_fy1"] = eps_fy1
        snapshot["eps_fy2"] = eps_fy2
        snapshot["adjusted_eps"] = eps_fy1
        snapshot["eg_fy1"] = eg_fy1
        snapshot["eg_fy2"] = eg_fy2
        snapshot["pe_fy1"] = (
            live_price / eps_fy1
            if live_price is not None and eps_fy1 is not None and eps_fy1 > 0
            else _number(sector.get("pe_fy1"))
        )
        snapshot["pe_fy2"] = (
            live_price / eps_fy2
            if live_price is not None and eps_fy2 is not None and eps_fy2 > 0
            else _number(sector.get("pe_fy2"))
        )
        snapshot["estimates_complete"] = all(
            _number(value) is not None
            for value in (eps_fy1, eps_fy2, eg_fy1, eg_fy2)
        ) and snapshot.get("fiscal_year_end") is not None
        snapshot["forward_data_available"] = (
            snapshot["forward_data_available"] or snapshot["estimates_complete"]
        )
        snapshot["imputed_forward_metrics"] = sorted(set(imputed))


def _classify(snapshot, sector_averages):
    sector = sector_averages.get(snapshot["sector"])
    if (
        not sector
        or not snapshot.get("estimates_complete")
        or _number(snapshot.get("adjusted_eps")) is None
    ):
        return None
    eps = snapshot["adjusted_eps"]
    eg_fy1, eg_fy2 = snapshot["eg_fy1"], snapshot["eg_fy2"]
    average_eg_fy1 = _number(sector.get("eg_fy1"))
    average_eg_fy2 = _number(sector.get("eg_fy2"))
    if average_eg_fy1 is None or average_eg_fy2 is None:
        return None

    if eps < 0 and eg_fy1 < 0 < average_eg_fy1 and eg_fy2 >= average_eg_fy2:
        return "Turnaround"
    if eps <= 0:
        return None

    average_pe_fy1 = _number(sector.get("pe_fy1"))
    average_pe_fy2 = _number(sector.get("pe_fy2"))
    pe_fy1 = snapshot.get("pe_fy1")
    pe_fy2 = snapshot.get("pe_fy2")
    if None in (average_pe_fy1, average_pe_fy2, pe_fy1, pe_fy2):
        return None
    if not (
        pe_fy1 > average_pe_fy1
        and pe_fy2 > average_pe_fy2
        and eg_fy2 >= average_eg_fy2
    ):
        return None
    if eg_fy1 > 0:
        return "Core Long"
    if eg_fy1 < 0:
        return "Weak Long"
    return None


def _identify_removals(
    scored_results, current_universe, claude_removals=None,
    financial_data=None, sector_averages=None,
):
    """Remove holdings that fail forward-growth or liquidity requirements."""
    del scored_results, claude_removals
    financial_data = financial_data or {}
    sector_averages = sector_averages or {}
    removals = []

    for raw_symbol in current_universe:
        symbol = str(raw_symbol).strip().upper()
        snapshot = financial_data.get(symbol)
        if snapshot is None:
            continue
        reasons = []
        sector_average = sector_averages.get(snapshot["sector"], {})
        average_eg_fy2 = _number(sector_average.get("eg_fy2"))
        eg_fy2 = _number(snapshot.get("eg_fy2"))

        if eg_fy2 is not None and (
            eg_fy2 < 0
            or (average_eg_fy2 is not None and eg_fy2 < average_eg_fy2)
        ):
            reasons.append("FY2 earnings growth is negative or below sector average")
        if (
            _number(snapshot.get("adjusted_eps")) is not None
            and _number(snapshot.get("adjusted_eps")) < 0
            and _number(snapshot.get("eg_fy1")) is not None
            and _number(snapshot.get("eg_fy1")) < 0
            and eg_fy2 is not None
            and eg_fy2 <= 0
        ):
            reasons.append("Turnaround failed: FY2 earnings growth is non-positive")

        price_data = snapshot.get("price_data")
        if price_data and price_data.get("unavailable") in (
            "empty price history", "stale price history",
        ):
            reasons.append("No recent tradable price data (possible suspension or delisting)")
        elif (
            price_data
            and not price_data.get("unavailable")
            and price_data.get("average_dollar_volume_20d") is not None
            and price_data["average_dollar_volume_20d"] < _MIN_DOLLAR_VOLUME
        ):
            reasons.append("20-day average dollar volume below $5M")

        if reasons:
            removals.append({
                "ticker": symbol,
                "reason": "; ".join(dict.fromkeys(reasons)),
                "source": "forward fundamentals/liquidity",
            })
    return removals


def _addition_record(snapshot, category):
    price_data = snapshot["price_data"]
    close = price_data["close"]
    price = price_data["live_price"]
    momentum_1m = (price / float(close.iloc[-22]) - 1) * 100
    momentum_3m = (price / float(close.iloc[-64]) - 1) * 100
    history_note = "complete one-year LTM with upward QoQ trend"
    imputed = snapshot.get("imputed_forward_metrics", [])
    imputation_note = (
        f" Sector-mean imputed: {', '.join(imputed)}." if imputed else ""
    )
    return {
        "ticker": snapshot["ticker"],
        "name": snapshot["name"],
        "sector": snapshot["sector"],
        "market_cap": snapshot["market_cap"],
        "reason": (
            f"{category}: FY1/FY2 EPS growth {snapshot['eg_fy1']:.1f}%/"
            f"{snapshot['eg_fy2']:.1f}%; {history_note} and price/volume gates passed."
            f"{imputation_note}"
        ),
        "category": category,
        "price": round(price, 2),
        "momentum_1m": round(momentum_1m, 2),
        "momentum_3m": round(momentum_3m, 2),
        "avg_dollar_volume": (
            round(price_data["average_dollar_volume_20d"] / 1_000_000, 1)
            if price_data["average_dollar_volume_20d"] is not None else None
        ),
        "adjusted_eps": round(snapshot["adjusted_eps"], 4),
        "pe_fy1": round(snapshot["pe_fy1"], 2) if snapshot["pe_fy1"] is not None else None,
        "pe_fy2": round(snapshot["pe_fy2"], 2) if snapshot["pe_fy2"] is not None else None,
        "eg_fy1": round(snapshot["eg_fy1"], 2),
        "eg_fy2": round(snapshot["eg_fy2"], 2),
        "imputed_forward_metrics": snapshot.get("imputed_forward_metrics", []),
        "fiscal_year_end": (
            snapshot["fiscal_year_end"].isoformat()
            if snapshot["fiscal_year_end"] is not None else "Unavailable"
        ),
    }


def _build_discovery_report(additions, removals, themes, reasoning, exclusions=None):
    """Build the report string consumed by report_generator.py."""
    section = "\n---\n\n## Stock Universe Discovery\n\n"
    if reasoning:
        section += f"*{reasoning}*\n\n"
    section += (
        "Quarterly statements and annual analyst EPS estimates come from FMP; "
        "daily bars and latest trades come from Alpaca. "
        "Only stocks with a verified market cap from $3B through $10B are "
        "included in discovery and sector-average calculations. "
        "FY1 is the current, not-yet-completed fiscal year; its EPS-growth "
        "baseline is the prior reported FY0 EPS when available. FY2 growth "
        "compares FY2 and FY1 forward EPS. Missing values may use FMP forwardPE "
        "or same-sector analyst-estimate statistics. Reported quarterly LTM "
        "data is a separate backward-looking recovery validation and is not "
        "used to calculate forward EPS. "
        "Missing volume data disables only the volume-expansion gate. Missing "
        "quarterly statements remain excluded: P/E ratios cannot reconstruct "
        "reported revenue, EBITDA, or LTM recovery.\n\n"
    )

    if themes:
        section += "### Trending Themes\n\n"
        section += "".join(f"- {theme}\n" for theme in themes)
        section += "\n"

    if additions:
        section += f"### Suggested Additions ({len(additions)} stocks)\n\n"
        section += (
            "| Ticker | Category | Sector | Market Cap | Price | P/E FY1 | P/E FY2 | "
            "EG FY1 | EG FY2 | Avg Vol($M) | Reason |\n"
            "|--------|----------|--------|------------|-------|---------|---------|"
            "--------|--------|-------------|--------|\n"
        )
        for item in additions:
            pe1 = f"{item['pe_fy1']:.1f}" if item["pe_fy1"] is not None else "N/A"
            pe2 = f"{item['pe_fy2']:.1f}" if item["pe_fy2"] is not None else "N/A"
            market_cap = f"${item['market_cap'] / 1_000_000_000:.2f}B"
            average_dollar_volume = (
                f"{item['avg_dollar_volume']:.1f}"
                if item["avg_dollar_volume"] is not None else "N/A"
            )
            section += (
                f"| **{item['ticker']}** | {item['category']} | "
                f"{item['sector'][:15]} | {market_cap} | "
                f"${item['price']:.2f} | {pe1} | {pe2} | "
                f"{item['eg_fy1']:+.1f}% | {item['eg_fy2']:+.1f}% | "
                f"{average_dollar_volume} | {item['reason'][:100]} |\n"
            )
        section += "\n"

    if removals:
        section += f"### Removal Candidates ({len(removals)} stocks)\n\n"
        section += "| Ticker | Reason | Source |\n|--------|--------|--------|\n"
        for item in removals:
            section += (
                f"| **{item['ticker']}** | {item['reason']} | {item['source']} |\n"
            )
        section += "\n"

    if exclusions:
        section += "### Excluded Candidates\n\n"
        section += "".join(
            f"- {reason}: {count}\n" for reason, count in sorted(exclusions.items())
        )
        section += "\n"

    if not additions and not removals:
        section += "*No candidates passed the configured fundamental and liquidity gates.*\n\n"
    section += "> These are screening results, not investment advice. Review before changing the configured universe.\n"
    return section + "\n---\n"
