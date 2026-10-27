"""System portfolio: only the cash and positions this bot opened, never the whole account."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import iso, read_json, write_json


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_at: str
    initial_stop: float
    stop: float
    atr: float
    highest_close: float
    days_held: int = 0
    partial_taken: bool = False
    entry_fees: float = 0.0
    realized_partial_usd: float = 0.0
    broker_stop_order_id: str | None = None
    exit_order_id: str | None = None
    exit_reason: str | None = None
    exit_qty: float = 0.0
    reason_chain: dict = field(default_factory=dict)

    @property
    def r(self) -> float:
        return self.entry_price - self.initial_stop


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    qty: float
    limit: float
    placed_at: str
    stop: float
    atr: float
    reason_chain: dict


class Portfolio:
    def __init__(self, path: Path, starting_capital: float):
        self.path = path
        raw = read_json(path, None)
        if raw is None:
            raw = {
                "starting_capital": starting_capital,
                "cash": starting_capital,
                "peak_equity": starting_capital,
                "day_start_equity": starting_capital,
                "day_start_date": None,
                "positions": {},
                "pending": {},
                "cooldowns": {},
                "equity_history": [],
                "realized_pnl": 0.0,
                "fees_paid": 0.0,
                "drawdown_halt": False,
                "drawdown_notified": False,
            }
        self.d = raw
        self.positions: dict[str, Position] = {k: Position(**v) for k, v in raw["positions"].items()}
        self.pending: dict[str, PendingOrder] = {k: PendingOrder(**v) for k, v in raw["pending"].items()}

    # ---- persistence
    def save(self) -> None:
        self.d["positions"] = {k: asdict(v) for k, v in self.positions.items()}
        self.d["pending"] = {k: asdict(v) for k, v in self.pending.items()}
        write_json(self.path, self.d)

    # ---- numbers
    @property
    def cash(self) -> float:
        return self.d["cash"]

    def invested(self, prices: dict[str, float]) -> float:
        return sum(p.qty * prices.get(s, p.entry_price) for s, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.invested(prices)

    def record_equity(self, prices: dict[str, float]) -> float:
        eq = self.equity(prices)
        self.d["peak_equity"] = max(self.d["peak_equity"], eq)
        hist = self.d["equity_history"]
        hist.append([iso(), round(eq, 2)])
        if len(hist) > 20000:
            del hist[: len(hist) - 20000]
        return eq

    def drawdown_pct(self, equity: float) -> float:
        peak = self.d["peak_equity"]
        return (peak - equity) / peak * 100 if peak else 0.0

    def day_loss_pct(self, equity: float) -> float:
        start = self.d["day_start_equity"]
        return (start - equity) / start * 100 if start else 0.0

    # ---- cash movements
    def apply_buy(self, symbol: str, qty: float, price: float, fees: float) -> None:
        self.d["cash"] -= qty * price + fees
        self.d["fees_paid"] += fees

    def apply_sell(self, symbol: str, qty: float, price: float, fees: float, cost_basis: float) -> float:
        proceeds = qty * price - fees
        self.d["cash"] += proceeds
        self.d["fees_paid"] += fees
        pnl = proceeds - cost_basis
        self.d["realized_pnl"] += pnl
        return pnl

    # ---- cooldowns after a stop-out
    def note_stopout(self, symbol: str, trading_day: str) -> None:
        cd = self.d["cooldowns"].get(symbol, {"reentries": 0})
        cd["stopped_on"] = trading_day
        cd["reentries_since_stop"] = 0
        self.d["cooldowns"][symbol] = cd

    def note_entry(self, symbol: str) -> None:
        cd = self.d["cooldowns"].get(symbol)
        if cd and "stopped_on" in cd:
            cd["reentries_since_stop"] = cd.get("reentries_since_stop", 0) + 1
