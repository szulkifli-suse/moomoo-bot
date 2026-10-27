"""Risk: decides size and exits, and can veto any trade."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..fees import order_fee
from .scout import Candidate


@dataclass
class Order:
    symbol: str
    qty: float
    limit: float
    stop: float
    atr: float
    chain: dict


def initial_stop(entry: float, atr_value: float, cfg: dict) -> float:
    dist = cfg["stop_atr_mult"] * atr_value
    dist = min(max(dist, entry * cfg["stop_min_pct"] / 100), entry * cfg["stop_max_pct"] / 100)
    return round(entry - dist, 2)


class Risk:
    name = "risk"

    def __init__(self, cfg: dict, exec_cfg: dict, fees_cfg: dict, allow_fractional: bool, journal):
        self.cfg = cfg
        self.exec_cfg = exec_cfg
        self.fees = fees_cfg
        self.fractional = allow_fractional
        self.j = journal

    # ---------- limits
    def limits(self, regime: str) -> tuple[float, int]:
        if regime == "weak":
            return self.cfg["weak_position_pct"], self.cfg["weak_max_positions"]
        return self.cfg["position_pct"], self.cfg["max_positions"]

    def position_value(self, equity: float, regime: str) -> float:
        pct, _ = self.limits(regime)
        return equity * pct / 100

    def buy_block(self, portfolio, equity: float, halted: bool) -> str | None:
        """A reason no new buys are allowed right now, or None."""
        if halted:
            return "kill_switch"
        if portfolio.d.get("drawdown_halt"):
            return "drawdown_limit"
        if portfolio.day_loss_pct(equity) >= self.cfg["daily_loss_limit_pct"]:
            return "daily_loss_limit"
        return None

    # ---------- entries
    def size(self, cands: list[Candidate], portfolio, prices: dict, regime: str):
        equity = portfolio.equity(prices)
        pct, max_pos = self.limits(regime)
        open_slots = max_pos - len(portfolio.positions) - len(portfolio.pending)
        invested = portfolio.invested(prices) + sum(p.qty * p.limit for p in portfolio.pending.values())
        room = equity * self.cfg["max_invested_pct"] / 100 - invested
        cash = portfolio.cash

        orders: list[Order] = []
        for c in cands:
            if open_slots <= 0:
                self.j.event(self.name, "veto", c.symbol, rule="no_open_slots", max_positions=max_pos, regime=regime)
                continue
            ask = float(c.snapshot.get("ask_price") or 0) or c.price
            limit = round(min(ask, c.price * (1 + self.exec_cfg["max_entry_slippage_pct"] / 100)), 2)
            target = equity * pct / 100
            fee = order_fee(self.fees, target, target / limit)
            budget = min(target, room, cash - fee)
            qty = budget / limit if self.fractional else math.floor(budget / limit)
            if self.fractional:
                qty = math.floor(qty * 10000) / 10000
            if qty <= 0 or (not self.fractional and qty < 1):
                rule = "share_price_over_position_size" if budget > 0 else "no_room_or_cash"
                self.j.event(self.name, "veto", c.symbol, rule=rule, budget=round(budget, 2), price=limit)
                continue
            if not c.stats.get("atr"):
                self.j.event(self.name, "veto", c.symbol, rule="no_atr")
                continue
            stop = initial_stop(limit, c.stats["atr"], self.cfg)
            value = qty * limit
            chain = c.chain()
            chain["filter"] = {"spread_pct": c.notes.get("spread_pct"), "unverified": c.unverified}
            chain["analyst"] = c.notes.get("analyst")
            chain["risk"] = {"qty": qty, "limit": limit, "stop": stop, "atr": round(c.stats["atr"], 3),
                             "value": round(value, 2), "regime": regime, "position_pct": pct}
            self.j.event(self.name, "approve", c.symbol, qty=qty, limit=limit, stop=stop, value=round(value, 2), regime=regime)
            orders.append(Order(c.symbol, qty, limit, stop, c.stats["atr"], chain))
            open_slots -= 1
            room -= value
            cash -= value + fee
        return orders

    # ---------- exits, every few minutes during market hours
    def monitor(self, portfolio, prices: dict) -> list[dict]:
        actions = []
        cfg = self.cfg
        for sym, p in portfolio.positions.items():
            if p.exit_order_id:
                continue  # an exit is already working
            price = prices.get(sym)
            if not price:
                continue
            if price <= p.stop:
                reason = "trailing_stop" if p.partial_taken else "stop"
                actions.append({"type": "exit", "symbol": sym, "qty": p.qty, "reason": reason, "price": price})
                continue
            if not p.partial_taken and p.r > 0 and price >= p.entry_price + cfg["partial_at_r"] * p.r:
                half = p.qty / 2 if self.fractional else math.floor(p.qty / 2)
                actions.append({"type": "partial", "symbol": sym, "qty": half, "price": price,
                                "reason": f"+{cfg['partial_at_r']}R"})
                continue
            if p.days_held >= cfg["time_stop_trading_days"] and not p.partial_taken:
                actions.append({"type": "exit", "symbol": sym, "qty": p.qty, "reason": "time_stop", "price": price})
        return actions

    # ---------- after the close
    def end_of_day(self, portfolio, closes: dict[str, float], atrs: dict[str, float]) -> list[dict]:
        moves = []
        for sym, p in portfolio.positions.items():
            close = closes.get(sym)
            if close:
                p.highest_close = max(p.highest_close, close)
            if atrs.get(sym):
                p.atr = atrs[sym]
            p.days_held += 1
            if p.partial_taken:
                trail = round(p.highest_close - self.cfg["trail_atr_mult"] * p.atr, 2)
                if trail > p.stop:
                    moves.append({"symbol": sym, "old": p.stop, "new": trail})
                    p.stop = trail
        return moves
