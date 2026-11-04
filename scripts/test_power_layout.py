"""Offline tests for the 电力加仓 section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import power_layout as pl
from test_ai_portfolio import _row, _plan

TODAY = date(2026, 9, 10)


def test_default_levels_and_ladder_status():
    assert pl.layout_list() == ["GEV", "BE", "PWR"]
    assert pl.thesis("GEV") == {"module": "电网 / 发电设备", "open_px": 920.0, "add_px": 865.0}
    assert pl.thesis("BE")["open_px"] == 232.0 and pl.thesis("PWR")["add_px"] == 560.0
    assert pl.ladder_status(1000, 920, 865).startswith("高于开仓")
    assert pl.ladder_status(900, 920, 865) == "已到开仓区间"
    assert pl.ladder_status(865, 920, 865) == "已到加仓价"
    assert pl.ladder_status(800, 920, 865).startswith("已跌破加仓")
    os.environ["POWER_LAYOUT"] = " gev, BE ,gev"
    try:
        assert pl.layout_list() == ["GEV", "BE"]
    finally:
        del os.environ["POWER_LAYOUT"]


def test_build_marks_open_and_add_rungs():
    os.environ["POWER_LAYOUT"] = "GEV,BE,PWR"
    try:
        data = pl.build([
            _row("GEV", from_high=-4.0, price=900.0),
            _row("BE", from_high=-22.0, price=198.0),
        ], sell_put_plan=_plan("GEV"), held=set(), today=TODAY)
    finally:
        del os.environ["POWER_LAYOUT"]
    by = {x["ticker"]: x for x in data["names"]}
    assert by["GEV"]["ladder"] == "已到开仓区间"
    assert by["BE"]["ladder"].startswith("已跌破加仓")
    assert not by["PWR"]["in_run"]
    assert data["summary"]["open_now"] == ["GEV"]
    assert data["summary"]["add_now"] == ["BE"]
    md = pl.build_section(data)
    assert md.startswith("## 电力加仓 — Power Portfolio")
    assert "| **GEV** | 电网 / 发电设备 | $900 |" in md
    assert "### 今日可动手" in md
    assert "持仓" not in md
    lines = pl.email_lines(data)
    assert lines[0] == "=== 电力加仓 ==="
    assert any("GEV" in l and "开仓" in l for l in lines)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
