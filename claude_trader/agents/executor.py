"""Executor: the only agent that places or cancels orders.

Shadow mode simulates fills at real quotes (buys at the ask, sells at the bid) and sends nothing.
Live mode sends orders to moomoo and always reads status and fills back; it never assumes a fill
and never retries a failed order in a loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..broker import DONE_STATUSES, BrokerError
from ..fees import order_fee
from ..portfolio import PendingOrder, Position
from ..util import iso, now_et, now_utc
from .risk import Order


class Executor:
    name = "executor"

    def __init__(self, settings, quotes, trader, portfolio, journal, notifier):
        self.s = settings
        self.live = settings.live_trading
        self.q = quotes
        self.t = trader  # None in shadow mode
        self.pf = portfolio
        self.j = journal
        self.n = notifier
        self.fee_cfg = settings["fees"]

    @property
    def mode(self) -> str:
        return "LIVE" if self.live else "SHADOW"

    def _fees(self, order_id: str | None, qty: float, price: float) -> float:
        if self.live and order_id:
            try:
                f = self.t.fees(order_id)
                if f:
                    return f
            except BrokerError:
                pass
        return order_fee(self.fee_cfg, qty * price, qty)

    def _notify_fill(self, text: str) -> None:
        if not self.live:
            return
        today = now_et().date().isoformat()
        first_day = self.pf.d.setdefault("first_live_day", today)
        if first_day == today:
            self.n.send(f"[LIVE] {text}")

    def _fail(self, symbol: str, action: str, exc: Exception) -> None:
        self.j.event(self.name, "order_error", symbol, action=action, error=str(exc)[:500])
        self.n.send(f"Order error ({self.mode}) {action} {symbol}: {str(exc)[:300]}. Not retrying.")

    # ---------------- entries
    def enter(self, o: Order) -> None:
        if self.live:
            try:
                oid = self.t.buy_limit(o.symbol, o.qty, o.limit)
            except BrokerError as exc:
                self._fail(o.symbol, "buy", exc)
                return
        else:
            oid = f"shadow-{o.symbol}-{int(now_utc().timestamp())}"
        self.pf.pending[oid] = PendingOrder(oid, o.symbol, o.qty, o.limit, iso(), o.stop, o.atr, o.chain)
        self.j.event(self.name, "buy_placed", o.symbol, order_id=oid, qty=o.qty, limit=o.limit, stop=o.stop)
        self.pf.save()
        self.poll_pending()

    def poll_pending(self) -> None:
        timeout = timedelta(minutes=self.s["executor"]["entry_timeout_minutes"])
        snaps = self.q.snapshot([p.symbol for p in self.pf.pending.values()]) if self.pf.pending else {}
        for oid, p in list(self.pf.pending.items()):
            age = now_utc() - datetime.fromisoformat(p.placed_at)
            if self.live:
                try:
                    info = self.t.order(oid)
                except BrokerError as exc:
                    self.j.event(self.name, "status_error", p.symbol, order_id=oid, error=str(exc))
                    continue
                status = str(info["order_status"])
                filled = float(info.get("dealt_qty") or 0)
                avg = float(info.get("dealt_avg_price") or 0)
                if status == "FILLED_ALL":
                    self._opened(p, filled, avg, oid)
                elif status in DONE_STATUSES:
                    del self.pf.pending[oid]
                    if filled > 0:
                        self._opened(p, filled, avg, oid, keep_pending=True)
                    self.j.event(self.name, "buy_ended", p.symbol, order_id=oid, status=status, filled=filled)
                elif age > timeout:
                    try:
                        self.t.cancel(oid)
                        self.j.event(self.name, "buy_cancel_requested", p.symbol, order_id=oid, reason="timeout")
                    except BrokerError as exc:
                        self._fail(p.symbol, "cancel", exc)
            else:
                snap = snaps.get(p.symbol) or {}
                ask = float(snap.get("ask_price") or 0)
                if 0 < ask <= p.limit:
                    self._opened(p, p.qty, ask, oid)
                elif age > timeout:
                    del self.pf.pending[oid]
                    self.j.event(self.name, "buy_cancelled", p.symbol, order_id=oid, reason="timeout", ask=ask, limit=p.limit)
        self.pf.save()

    def _opened(self, p: PendingOrder, qty: float, price: float, oid: str, keep_pending: bool = False) -> None:
        if not keep_pending:
            self.pf.pending.pop(oid, None)
        fees = self._fees(oid, qty, price)
        self.pf.apply_buy(p.symbol, qty, price, fees)
        # Keep the same stop distance Risk chose, measured from the real fill.
        stop = round(price - (p.limit - p.stop), 2)
        chain = dict(p.reason_chain)
        chain["executor"] = {"order_id": oid, "fill_price": price, "qty": qty, "fees": round(fees, 2), "mode": self.mode}
        pos = Position(
            symbol=p.symbol, qty=qty, entry_price=price, entry_at=iso(), initial_stop=stop, stop=stop,
            atr=p.atr, highest_close=price, entry_fees=fees, reason_chain=chain,
        )
        self.pf.positions[p.symbol] = pos
        self.pf.note_entry(p.symbol)
        self.j.event(self.name, "filled_buy", p.symbol, qty=qty, price=round(price, 4), stop=stop, fees=fees, chain=chain)
        self._notify_fill(f"Bought {qty} {p.symbol} @ {price:.2f}, stop {stop:.2f}")
        if self.live:
            self._place_broker_stop(pos)

    def _place_broker_stop(self, pos: Position) -> None:
        if pos.qty != int(pos.qty):
            self.j.event(self.name, "broker_stop_skipped", pos.symbol, reason="fractional quantity; Risk monitors instead")
            return
        try:
            pos.broker_stop_order_id = self.t.sell_stop(pos.symbol, pos.qty, pos.stop)
            self.j.event(self.name, "broker_stop_placed", pos.symbol, order_id=pos.broker_stop_order_id, stop=pos.stop)
        except BrokerError as exc:
            self.j.event(self.name, "broker_stop_failed", pos.symbol, error=str(exc)[:300], fallback="risk monitor every 5 minutes")

    # ---------------- stops
    def move_stop(self, symbol: str, new_stop: float) -> None:
        pos = self.pf.positions[symbol]
        pos.stop = max(pos.stop, new_stop)
        if self.live and pos.broker_stop_order_id:
            try:
                self.t.modify_stop(pos.broker_stop_order_id, pos.qty, pos.stop)
            except BrokerError as exc:
                self._fail(symbol, "modify_stop", exc)
        self.j.event(self.name, "stop_moved", symbol, stop=pos.stop)
        self.pf.save()

    # ---------------- exits
    def exit(self, symbol: str, qty: float, reason: str, partial: bool = False) -> None:
        pos = self.pf.positions[symbol]
        if partial and qty <= 0:
            # One whole share can't be split: just lock the stop at entry.
            pos.partial_taken = True
            self.j.event(self.name, "partial_skipped", symbol, reason="position too small to split")
            self.move_stop(symbol, pos.entry_price)
            return
        if not self.live:
            snap = self.q.snapshot([symbol]).get(symbol) or {}
            price = float(snap.get("bid_price") or snap.get("last_price") or 0)
            if price <= 0:
                self.j.event(self.name, "exit_deferred", symbol, reason="no bid")
                return
            self._closed(pos, qty, price, reason, None, partial)
            return
        try:
            if pos.broker_stop_order_id:
                if partial:
                    self.t.modify_stop(pos.broker_stop_order_id, pos.qty - qty, max(pos.stop, pos.entry_price))
                else:
                    self.t.cancel(pos.broker_stop_order_id)
                    pos.broker_stop_order_id = None
            pos.exit_order_id = self.t.sell_market(symbol, qty)
            pos.exit_reason = reason
            pos.exit_qty = qty
            self.j.event(self.name, "sell_placed", symbol, order_id=pos.exit_order_id, qty=qty, reason=reason)
        except BrokerError as exc:
            self._fail(symbol, f"sell ({reason})", exc)
        self.pf.save()
        self.poll_exits()

    def poll_exits(self) -> None:
        if not self.live:
            return
        for sym, pos in list(self.pf.positions.items()):
            # A broker-side stop may have filled on its own.
            if pos.broker_stop_order_id and not pos.exit_order_id:
                try:
                    info = self.t.order(pos.broker_stop_order_id)
                except BrokerError:
                    continue
                if str(info["order_status"]) == "FILLED_ALL":
                    self._closed(pos, float(info["dealt_qty"]), float(info["dealt_avg_price"]),
                                 "trailing_stop" if pos.partial_taken else "stop", pos.broker_stop_order_id, False)
                continue
            if not pos.exit_order_id:
                continue
            try:
                info = self.t.order(pos.exit_order_id)
            except BrokerError as exc:
                self.j.event(self.name, "status_error", sym, order_id=pos.exit_order_id, error=str(exc))
                continue
            status = str(info["order_status"])
            if status == "FILLED_ALL":
                partial = pos.exit_qty < pos.qty
                self._closed(pos, float(info["dealt_qty"]), float(info["dealt_avg_price"]),
                             pos.exit_reason or "exit", pos.exit_order_id, partial)
            elif status in DONE_STATUSES:
                self.j.event(self.name, "sell_failed", sym, order_id=pos.exit_order_id, status=status)
                self.n.send(f"Sell for {sym} ended as {status}. Check the position; not retrying.")
                pos.exit_order_id = None
        self.pf.save()

    def _closed(self, pos: Position, qty: float, price: float, reason: str, oid: str | None, partial: bool) -> None:
        fees = self._fees(oid, qty, price)
        share = qty / pos.qty
        basis = qty * pos.entry_price + pos.entry_fees * share
        pnl = self.pf.apply_sell(pos.symbol, qty, price, fees, basis)
        if partial:
            pos.qty = round(pos.qty - qty, 6)
            pos.entry_fees -= pos.entry_fees * share
            pos.realized_partial_usd += pnl
            pos.partial_taken = True
            pos.exit_order_id = None
            pos.exit_qty = 0.0
            self.j.event(self.name, "filled_partial", pos.symbol, qty=qty, price=price, pnl=round(pnl, 2))
            self._notify_fill(f"Sold half: {qty} {pos.symbol} @ {price:.2f} ({reason})")
            self.move_stop(pos.symbol, pos.entry_price)
            return
        total_pnl = pnl + pos.realized_partial_usd
        r_mult = (price - pos.entry_price) / pos.r if pos.r > 0 else None
        self.j.trade({
            "closed_at": iso(), "symbol": pos.symbol, "mode": self.mode, "qty": qty,
            "entry_price": pos.entry_price, "exit_price": price, "entry_at": pos.entry_at,
            "days_held": pos.days_held, "pnl_usd": round(total_pnl, 2),
            "pnl_pct": round((price / pos.entry_price - 1) * 100, 2),
            "r_multiple": round(r_mult, 2) if r_mult is not None else "",
            "fees_usd": round(fees + pos.entry_fees, 2), "exit_reason": reason,
            "reason_chain": str(pos.reason_chain)[:2000],
        })
        if reason in ("stop", "trailing_stop") and not pos.partial_taken:
            self.pf.note_stopout(pos.symbol, now_et().date().isoformat())
        del self.pf.positions[pos.symbol]
        self.j.event(self.name, "filled_sell", pos.symbol, qty=qty, price=round(price, 4), reason=reason, pnl=round(total_pnl, 2))
        self._notify_fill(f"Sold {qty} {pos.symbol} @ {price:.2f} ({reason}), P&L {total_pnl:+.2f}")
        self.pf.save()

    def cancel_open_buys(self, why: str) -> None:
        for oid, p in list(self.pf.pending.items()):
            if self.live:
                try:
                    self.t.cancel(oid)
                except BrokerError as exc:
                    self._fail(p.symbol, "cancel", exc)
                    continue
            del self.pf.pending[oid]
            self.j.event(self.name, "buy_cancelled", p.symbol, order_id=oid, reason=why)
        self.pf.save()
