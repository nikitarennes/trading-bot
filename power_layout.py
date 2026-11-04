"""
电力加仓 — GEV / BE / PWR with the user's open and add prices.

Override with POWER_LAYOUT (comma-separated). Open/add levels are fixed;
live price, how far each name is from those rungs, 52-week drawdown and
the add verdict update every report.
"""
import os

from ai_portfolio import REC_CN, _item, _money, _pct, _table

try:
    from configs import UNIVERSE
except ImportError:
    UNIVERSE = {}

# ticker, module, 开仓, 加仓
LAYOUT = (
    ("GEV", "电网 / 发电设备", 920.0, 865.0),
    ("BE", "燃料电池 / 数据中心电力", 232.0, 200.0),
    ("PWR", "电网 / 输电基建", 600.0, 560.0),
)
THESIS = {ticker: {"module": module, "open_px": open_px, "add_px": add_px}
          for ticker, module, open_px, add_px in LAYOUT}
DEFAULT_LAYOUT = tuple(ticker for ticker, *_ in LAYOUT)


def layout_list():
    raw = os.environ.get("POWER_LAYOUT", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_LAYOUT)
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def thesis(ticker):
    item = THESIS.get((ticker or "").strip().upper(), {})
    return {
        "module": item.get("module") or "",
        "open_px": item.get("open_px"),
        "add_px": item.get("add_px"),
    }


def ladder_status(price, open_px, add_px):
    if price is None or open_px is None or add_px is None:
        return "—"
    if price <= add_px:
        if price < add_px:
            return f"已跌破加仓 {((1 - price / add_px) * 100):.0f}%"
        return "已到加仓价"
    if price <= open_px:
        return "已到开仓区间"
    return f"高于开仓 {((price / open_px - 1) * 100):.0f}%"


def _gap(price, level):
    if price is None or level is None or level <= 0:
        return None
    return (price / level - 1) * 100


def build(ranked, macro_result=None, sell_put_plan=None, held=None, today=None):
    import daily_watch

    names = layout_list()
    rows = {(r.get("ticker") or "").upper(): r for r in (ranked or [])}
    subset = [rows[n] for n in names if n in rows]
    cards, event = daily_watch.annotate(
        subset, held=held, today=today, tape=(macro_result or {}).get("macro_tape"))
    card_map = {c["ticker"]: c for c in cards}
    plan_map = {p["ticker"]: p for p in ((sell_put_plan or {}).get("names") or [])}

    out = []
    for n in names:
        item = _item(n, rows.get(n), card_map.get(n), plan_map.get(n))
        item.update(thesis(n))
        item["display"] = n
        if not item.get("name") or item["name"] == n:
            item["name"] = (UNIVERSE.get(n) or {}).get("name") or n
        if not item.get("drawdown_label"):
            item["drawdown_label"] = daily_watch.drawdown_label(item.get("from_high"))
        if not item.get("add_verdict"):
            item["add_verdict"] = "不在今日评分池" if not item["in_run"] else (item.get("buy_label") or "—")
        item["ladder"] = ladder_status(item.get("price"), item.get("open_px"), item.get("add_px"))
        item["gap_open"] = _gap(item.get("price"), item.get("open_px"))
        item["gap_add"] = _gap(item.get("price"), item.get("add_px"))
        out.append(item)

    scored = [x for x in out if x["score"] is not None]
    summary = {
        "total": len(out),
        "in_run": sum(1 for x in out if x["in_run"]),
        "avg_score": sum(x["score"] for x in scored) / len(scored) if scored else None,
        "open_now": [x["display"] for x in out if x.get("ladder") == "已到开仓区间"],
        "add_now": [x["display"] for x in out if str(x.get("ladder") or "").startswith("已到加仓")
                    or str(x.get("ladder") or "").startswith("已跌破加仓")],
        "above_open": [x["display"] for x in out if str(x.get("ladder") or "").startswith("高于开仓")],
        "buy_now": [x["display"] for x in out if x["buy_action"] == "buy"],
        "scale_in": [x["display"] for x in out if x["buy_action"] in ("scale_in", "add_held")],
    }
    return {"event": event, "names": out, "summary": summary}


def build_section(data):
    names = data.get("names") or []
    s = data.get("summary") or {}
    lines = [
        "## 电力加仓 — Power Portfolio\n",
        "固定名单（`POWER_LAYOUT`）是电网 / 电力的开仓加仓本：GEV、BE、PWR。"
        "开仓价和加仓价是固定参考，不随当天行情改；现价离这两档还有多远、距高点回撤和加仓判断是当天数据。"
        "PWR 也在抄底布局里，这里只盯你给的价格带。\n",
    ]
    if names:
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**今日档位：** 已到开仓 {lst('open_now')} · 已到加仓 {lst('add_now')} · "
            f"高于开仓 {lst('above_open')}。\n"
        )

    rows = []
    for x in names:
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        rows.append([
            f"**{x['display']}**",
            x.get("module") or "—",
            _money(x["price"]),
            _money(x.get("open_px")),
            _money(x.get("add_px")),
            _pct(x.get("gap_open")),
            _pct(x.get("gap_add")),
            x.get("ladder") or "—",
            _pct(x["from_high"]),
            x.get("drawdown_label") or "—",
            f"{score}（{x['grade']}）", rec,
            x.get("add_verdict") or "—",
        ])
    lines.append(_table(
        ["股票", "模块", "现价", "开仓", "加仓", "距开仓", "距加仓", "价格档",
         "距高点", "回撤", "评分", "评级", "加仓判断"], rows))

    hits = [x for x in names if x.get("ladder") in ("已到开仓区间", "已到加仓价")
            or str(x.get("ladder") or "").startswith("已跌破加仓")]
    if hits:
        lines.append("\n### 今日可动手\n")
        action_rows = []
        for x in hits:
            action = "加仓" if "加仓" in (x.get("ladder") or "") else "开仓"
            action_rows.append([
                action,
                f"**{x['display']}**",
                _money(x["price"]),
                _money(x.get("open_px")),
                _money(x.get("add_px")),
                x.get("ladder") or "—",
                _pct(x["from_high"]),
                x.get("add_verdict") or "—",
            ])
        lines.append(_table(
            ["动作", "股票", "现价", "开仓", "加仓", "价格档", "距高点", "加仓判断"],
            action_rows))
        lines.append("")

    for x in names:
        head = f"- **{x['display']}**（开仓 {_money(x.get('open_px'))} / 加仓 {_money(x.get('add_px'))}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池，只看固定价格带。")
            continue
        lines.append(
            head + f"现价 {_money(x['price'])}，{x.get('ladder') or '—'}；"
            f"距高点 {_pct(x['from_high'])}（{x.get('drawdown_label') or '—'}） "
            f"{x.get('add_verdict') or ''}"
        )
    lines.append("")
    lines.append(
        "开仓价到了只开第一笔，加仓价到了再加一笔，不要两档一起打满。"
        "价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== 电力加仓 ==="]
    s = data.get("summary") or {}
    def lst(key):
        return "、".join(s.get(key) or []) or "无"
    lines.append(f"开仓 {lst('open_now')} · 加仓 {lst('add_now')} · 高于开仓 {lst('above_open')}")
    for x in data.get("names") or []:
        lines.append(
            f"  {x['display']:<4} {_money(x['price']):>8}  "
            f"开仓 {_money(x.get('open_px'))} 加仓 {_money(x.get('add_px'))}  "
            f"{x.get('ladder') or '—'} | {x.get('add_verdict') or '—'}"
        )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
