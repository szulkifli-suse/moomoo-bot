"""moomoo Malaysia US-stock fee model (estimate). Live mode uses moomoo's real fees when it can read them."""
from __future__ import annotations

import math


def order_fee(cfg: dict, value: float, qty: float) -> float:
    commission = max(value * cfg["commission_pct"] / 100, cfg["min_commission_usd"])
    commission = math.ceil(commission * 100) / 100
    platform = cfg["platform_fee_per_order_usd"]
    settlement = min(qty * cfg["settlement_per_share_usd"], value * 0.01)
    sst = (commission + platform + settlement) * cfg["sst_pct"] / 100
    stamp = value * cfg["stamp_duty_pct"] / 100
    return round(commission + platform + settlement + sst + stamp, 4)


def round_trip_cost(cfg: dict, value: float, price: float) -> float:
    qty = value / price if price > 0 else 0
    return 2 * order_fee(cfg, value, qty)
