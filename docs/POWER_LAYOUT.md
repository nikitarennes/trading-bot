# 电力加仓 — Power Portfolio

`power_layout.py` watches three power / grid names with fixed open and add prices:

| 股票 | 开仓 | 加仓 |
|---|---:|---:|
| GEV | $920 | $865 |
| BE | $232 | $200 |
| PWR | $600 | $560 |

The levels do not move with the tape. Live price, gap to each rung, 52-week drawdown and the add verdict update every report. Override with `POWER_LAYOUT`.

The section sits after 抄底布局 and before 今日建议买入. Email block: `=== 电力加仓 ===`.
