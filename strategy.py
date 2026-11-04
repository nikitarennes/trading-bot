"""Public portfolio construction shared by reporting, paper trading, and backtests."""
import math
import os

import pandas as pd


TOP_N = 10
BUFFER_N = 20
MAX_POS = 25.0
MOM_PCT = 0.95
SCORE_PCT = 0.05
MOM_FORMATION_DAYS = 126
MOM_SKIP_DAYS = 21
COST_PER_SIDE = 0.0005
TRADING_DAYS = 252


def _environment_setting(name, default):
    value = os.environ.get(name)
    if value is not None:
        return value
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, env_value = line.split("=", 1)
                    if key.strip() == name:
                        return env_value.strip()
    return default


VOL_LOOKBACK = int(_environment_setting("VOL_LOOKBACK", "20"))
VOL_TARGET = float(_environment_setting("VOL_TARGET", "0.40"))
MOM_MODE = _environment_setting("MOM_MODE", "6_1").strip().lower()

if MOM_MODE not in {"6_1", "legacy"}:
    raise ValueError("MOM_MODE must be '6_1' or 'legacy'")
if VOL_LOOKBACK < 2:
    raise ValueError("VOL_LOOKBACK must be at least 2")
if not math.isfinite(VOL_TARGET) or VOL_TARGET < 0:
    raise ValueError("VOL_TARGET must be a finite non-negative number")


def _finite_number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def macro_cash_pct(macro_score):
    """Reserve 0-20% cash, declining linearly from macro score 30 to 70."""
    score = _finite_number(macro_score, 50.0)
    return min(0.20, max(0.0, (70.0 - score) / 200.0))


def macro_cash_label(cash_pct):
    """Human-readable label for a cash reserve expressed as a fraction."""
    pct = _finite_number(cash_pct, 0.0)
    return f"{min(1.0, max(0.0, pct)):.0%}"


def momentum_signal(indicators, mode=None):
    """Return the 6-1 momentum signal, falling back to the legacy 3m/1m blend."""
    indicators = indicators or {}
    mode = (mode or MOM_MODE).strip().lower()
    if mode not in {"6_1", "legacy"}:
        raise ValueError("momentum mode must be '6_1' or 'legacy'")

    six_one = _finite_number(indicators.get("change_6m_skip1"))
    if mode == "6_1" and six_one is not None:
        return six_one / 100.0

    one_month = _finite_number(indicators.get("change_1m"), 0.0)
    three_month = _finite_number(indicators.get("change_3m"), 0.0)
    return (0.3 * one_month + 0.7 * three_month) / 100.0


def momentum_6_1_from_close(close):
    """Compute close[t-21] / close[t-126] - 1 without using the latest month."""
    values = pd.to_numeric(pd.Series(close), errors="coerce").dropna()
    if len(values) <= MOM_FORMATION_DAYS:
        return None
    start = _finite_number(values.iloc[-MOM_FORMATION_DAYS - 1])
    end = _finite_number(values.iloc[-MOM_SKIP_DAYS - 1])
    if start is None or end is None or start <= 0:
        return None
    return end / start - 1.0


def _ranked_stocks(stocks):
    """Normalize and deduplicate candidates before assigning deterministic ranks."""
    by_ticker = {}
    for stock in stocks or []:
        if not isinstance(stock, dict):
            continue
        ticker = str(stock.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        mom = _finite_number(stock.get("mom"), 0.0)
        score = _finite_number(stock.get("score"), 0.0)
        candidate = {"ticker": ticker, "mom": mom, "score": min(100.0, max(0.0, score))}
        existing = by_ticker.get(ticker)
        if existing is None or (candidate["mom"], candidate["score"]) > (
                existing["mom"], existing["score"]):
            by_ticker[ticker] = candidate
    return sorted(by_ticker.values(), key=lambda row: (-row["mom"], -row["score"], row["ticker"]))


def _allocate_with_cap(raw_weights, budget, max_pos):
    """Allocate a percentage budget proportionally while enforcing a hard cap."""
    if not raw_weights or budget <= 0 or max_pos <= 0:
        return {ticker: 0.0 for ticker in raw_weights}
    result = {ticker: 0.0 for ticker in raw_weights}
    remaining = min(float(budget), max_pos * len(raw_weights))
    available = dict(raw_weights)
    while available and remaining > 1e-12:
        raw_total = sum(max(0.0, value) for value in available.values())
        if raw_total <= 0:
            break
        capped = []
        for ticker, raw in available.items():
            proposed = remaining * max(0.0, raw) / raw_total
            if proposed >= max_pos - result[ticker]:
                capped.append(ticker)
        if not capped:
            for ticker, raw in available.items():
                result[ticker] += remaining * max(0.0, raw) / raw_total
            remaining = 0.0
            break
        for ticker in capped:
            capacity = max(0.0, max_pos - result[ticker])
            result[ticker] += capacity
            remaining -= capacity
            available.pop(ticker)
    return result


def compute_target_weights(stocks, macro_score=50, held=None,
                           top_n=TOP_N, max_pos=MAX_POS):
    """Build the capped momentum-rank ladder; return weights (%) and cash fraction."""
    if not isinstance(top_n, int) or not 1 <= top_n <= TOP_N:
        raise ValueError(f"top_n must be an integer between 1 and {TOP_N}")
    max_pos = _finite_number(max_pos)
    if max_pos is None or max_pos <= 0:
        raise ValueError("max_pos must be a positive finite percentage")
    ranked = _ranked_stocks(stocks)
    if len(ranked) < 2:
        return {}, 1.0

    selected_count = min(int(top_n), len(ranked))
    held = {str(t).strip().upper() for t in (held or set())}
    eligible_holds = [row for index, row in enumerate(ranked, 1)
                      if row["ticker"] in held and index <= BUFFER_N]
    selected = eligible_holds[:selected_count]
    selected_symbols = {row["ticker"] for row in selected}
    for row in ranked:
        if len(selected) >= selected_count:
            break
        if row["ticker"] not in selected_symbols:
            selected.append(row)
            selected_symbols.add(row["ticker"])
    selected.sort(key=lambda row: (-row["mom"], -row["score"], row["ticker"]))

    cash = macro_cash_pct(macro_score)
    budget = 100.0 * (1.0 - cash)
    denominator = max(1, len(selected) - 1)
    raw_weights = {}
    for rank, row in enumerate(selected):
        rank_pct = rank / denominator
        momentum_rank_weight = 3.0 - 2.8 * rank_pct
        score_tilt = (row["score"] / 100.0) ** 2
        raw_weights[row["ticker"]] = MOM_PCT * momentum_rank_weight + SCORE_PCT * score_tilt

    weights = _allocate_with_cap(raw_weights, budget, float(max_pos))
    invested = sum(weights.values())
    return ({ticker: round(weight, 10) for ticker, weight in weights.items() if weight > 0},
            max(0.0, 1.0 - invested / 100.0))


def basket_realized_vol(weights, returns_by_ticker, lookback=VOL_LOOKBACK):
    """Annualized realized volatility of the weighted target basket."""
    if not isinstance(lookback, int) or lookback < 2:
        raise ValueError("lookback must be an integer of at least 2")
    usable = {}
    for ticker, weight in (weights or {}).items():
        weight = _finite_number(weight, 0.0)
        values = [_finite_number(value) for value in (returns_by_ticker or {}).get(ticker, [])]
        values = [value for value in values if value is not None]
        if weight > 0 and len(values) >= lookback:
            usable[ticker] = (weight, values[-lookback:])
    if not usable:
        return None

    total_weight = sum(weight for weight, _ in usable.values())
    if total_weight <= 0:
        return None
    basket = [
        sum(weight * values[index] for weight, values in usable.values()) / total_weight
        for index in range(lookback)
    ]
    return float(pd.Series(basket, dtype=float).std(ddof=1) * math.sqrt(TRADING_DAYS))


def vol_target_exposure(realized_vol, target=VOL_TARGET):
    """Scale exposure down to a volatility target, never above 100%."""
    target = _finite_number(target, 0.0)
    realized_vol = _finite_number(realized_vol)
    if target <= 0 or realized_vol is None or realized_vol <= 0:
        return 1.0
    return min(1.0, target / realized_vol)


def compute_target_weights_vol_managed(
        stocks, macro_score=50, held=None, returns_by_ticker=None,
        vol_target=VOL_TARGET, lookback=VOL_LOOKBACK):
    """Apply portfolio volatility targeting to the capped macro-cash ladder."""
    weights, cash = compute_target_weights(stocks, macro_score=macro_score, held=held)
    realized_vol = basket_realized_vol(weights, returns_by_ticker or {}, lookback=lookback)
    target = _finite_number(vol_target, VOL_TARGET)
    exposure = vol_target_exposure(realized_vol, target)
    if exposure == 1.0:
        return weights, cash, {
            "realized_vol": realized_vol,
            "vol_target": target,
            "exposure": exposure,
            "base_cash": cash,
        }
    scaled = {ticker: round(weight * exposure, 10) for ticker, weight in weights.items()}
    scaled = {ticker: weight for ticker, weight in scaled.items() if weight > 0}
    scaled_cash = max(0.0, 1.0 - sum(scaled.values()) / 100.0)
    return scaled, scaled_cash, {
        "realized_vol": realized_vol,
        "vol_target": target,
        "exposure": exposure,
        "base_cash": cash,
    }


def rebalance_cost(old_weights, new_weights, portfolio_value, cost_per_side=COST_PER_SIDE):
    """Estimate transaction costs from gross traded notional and per-side rate."""
    value = _finite_number(portfolio_value, 0.0)
    rate = _finite_number(cost_per_side, 0.0)
    if value < 0 or rate < 0:
        raise ValueError("portfolio_value and cost_per_side must be non-negative")
    tickers = set(old_weights or {}) | set(new_weights or {})
    turnover = sum(
        abs(_finite_number((new_weights or {}).get(ticker), 0.0)
            - _finite_number((old_weights or {}).get(ticker), 0.0))
        for ticker in tickers
    )
    return turnover * value * rate
