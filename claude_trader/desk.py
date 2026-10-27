"""The desk: schedule, hand-offs between agents, state publishing for the dashboard."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .agents.analyst import Analyst
from .agents.executor import Executor
from .agents.filter import Filter
from .agents.risk import Risk
from .agents.scout import Scout
from .broker import BrokerError, Quotes, Trader
from .config import Settings
from .journal import Journal
from .market import MarketData
from .notifier import Notifier
from .portfolio import Portfolio
from .recap import build_recap, funnel_totals
from .util import ET, iso, now_et, now_utc, write_json

log = logging.getLogger("desk")
OPEN_ET = (9, 30)
CLOSE_ET = (16, 0)


def _hhmm(base: tuple[int, int], minutes: int) -> str:
    t = datetime(2000, 1, 1, *base) + timedelta(minutes=minutes)
    return t.strftime("%H:%M")


class Desk:
    def __init__(self, settings: Settings):
        self.s = settings
        self.mode = "LIVE" if settings.live_trading else "SHADOW"
        self.j = Journal(settings.logs_dir, self.mode)
        self.n = Notifier(settings.telegram_token, settings.telegram_chat_id, self.j)
        self.q = Quotes(settings.opend_host, settings.opend_port)
        self.pf = Portfolio(settings.data_dir / "portfolio.json", settings["account"]["starting_capital_usd"])
        self.market = MarketData(settings, self.q, self.j)
        sched = settings["schedule"]
        self.scan_times = sorted(
            [_hhmm(OPEN_ET, m) for m in sched["scan_minutes_after_open"]]
            + [_hhmm(CLOSE_ET, -m) for m in sched["scan_minutes_before_close"]]
        )
        first_allowed = _hhmm(OPEN_ET, sched["no_scan_first_minutes"])
        self.scan_times = [t for t in self.scan_times if t >= first_allowed]
        self.trader: Trader | None = None
        self.scout = Scout(settings["scout"], self.j)
        self.filter = Filter(settings["filter"], settings["fees"], self.j, settings["account"]["allow_fractional"])
        self.analyst = Analyst(settings["analyst"], self.j)
        self.risk = Risk(settings["risk"], settings["executor"], settings["fees"],
                         settings["account"]["allow_fractional"], self.j)
        self.ex: Executor | None = None
        self.last_risk_check = 0.0
        self.last_state = 0.0
        self.halted_logged = False
        self.prices: dict[str, float] = {}
        self.market_open = False
        self.opend_ok = False
        self.started = False
        self.last_progress = time.time()

    # ---------------- startup
    def wait_for_opend(self) -> None:
        while not self.q.logged_in():
            self.opend_ok = False
            self.publish_state(note="waiting for OpenD login")
            log.info("waiting for OpenD login")
            time.sleep(15)
            self.last_progress = time.time()
        self.opend_ok = True

    def start(self) -> None:
        self.wait_for_opend()
        if self.s.live_trading:
            acc = self.s["account"]
            self.trader = Trader(self.s.opend_host, self.s.opend_port, acc["security_firm"], acc["acc_id"], self.s.trade_pin)
        self.ex = Executor(self.s, self.q, self.trader, self.pf, self.j, self.n)
        self.j.event("desk", "started", mode=self.mode, scan_times_et=self.scan_times)
        self.reconcile()
        self.started = True

    def reconcile(self) -> None:
        """Never trust local state after a restart: compare with moomoo first."""
        if not self.trader:
            self.j.event("desk", "reconcile", note="shadow mode: local state is the record")
            return
        try:
            broker_pos = self.trader.positions()
            open_orders = {str(o["order_id"]) for o in self.trader.open_orders()}
        except BrokerError as exc:
            self.j.event("desk", "reconcile_error", error=str(exc))
            self.n.send(f"Reconcile failed at startup: {exc}")
            return
        for sym, p in list(self.pf.positions.items()):
            held = broker_pos.get(sym, 0.0)
            if held + 1e-9 < p.qty:
                self.j.event("desk", "reconcile_mismatch", sym, local_qty=p.qty, broker_qty=held)
                self.n.send(f"Reconcile: {sym} local {p.qty} vs broker {held}. Using broker quantity.")
                if held <= 0:
                    del self.pf.positions[sym]
                else:
                    p.qty = held
            if p.broker_stop_order_id and p.broker_stop_order_id not in open_orders:
                self.j.event("desk", "reconcile_stop_missing", sym, order_id=p.broker_stop_order_id)
        for oid in list(self.pf.pending):
            if oid not in open_orders:
                self.j.event("desk", "reconcile_pending_closed", self.pf.pending[oid].symbol, order_id=oid)
        self.pf.save()
        self.ex.poll_pending()
        self.ex.poll_exits()
        self.j.event("desk", "reconcile", positions=len(self.pf.positions), pending=len(self.pf.pending))

    # ---------------- helpers
    def halted(self) -> bool:
        return self.s.stop_file.exists()

    def refresh_prices(self, extra: list[str] | None = None) -> dict:
        codes = sorted(set(self.pf.positions) | set(extra or []))
        if codes:
            snaps = self.q.snapshot(codes)
            for c, r in snaps.items():
                if float(r.get("last_price") or 0) > 0:
                    self.prices[c] = float(r["last_price"])
        return self.prices

    def _day_flag(self, key: str) -> bool:
        return self.pf.d.get(key) == now_et().date().isoformat()

    def _set_day_flag(self, key: str) -> None:
        self.pf.d[key] = now_et().date().isoformat()
        self.pf.save()

    # ---------------- jobs
    def premarket(self) -> None:
        self.market.premarket_prep(self.scan_times)
        self.pf.d["regime"] = self.market.regime
        for sym, p in self.pf.positions.items():
            st = self.market.stats(sym)
            if st and st.get("atr"):
                p.atr = st["atr"]
        self.refresh_prices()
        eq = self.pf.equity(self.prices)
        self.pf.d["day_start_equity"] = eq
        self.pf.d["day_start_date"] = now_et().date().isoformat()
        self._set_day_flag("premarket_done")

    def scan(self, checkpoint: str) -> None:
        universe = self.market.universe()
        bench = self.s["universe"]["benchmark"]
        snaps = self.q.snapshot(universe + [bench])
        for c, r in snaps.items():
            if float(r.get("last_price") or 0) > 0:
                self.prices[c] = float(r["last_price"])
        stages, near = {}, []

        cands, stages["scout"], nm = self.scout.run(universe, snaps, self.market, checkpoint)
        near += nm
        equity = self.pf.equity(self.prices)
        regime = self.market.regime
        pos_value = self.risk.position_value(equity, regime)
        cands, stages["filter"], nm = self.filter.run(cands, self.q, self.market, self.pf, pos_value, now_et().date())
        near += nm

        block = self.risk.buy_block(self.pf, equity, self.halted())
        _, max_pos = self.risk.limits(regime)
        if block:
            self.j.event("risk", "no_new_buys", reason=block, candidates=[c.symbol for c in cands])
            stages["analyst"] = {"in": len(cands), "out": 0, "skipped": block}
        elif len(self.pf.positions) + len(self.pf.pending) >= max_pos:
            self.j.event("risk", "no_open_slots", candidates=[c.symbol for c in cands])
            stages["analyst"] = {"in": len(cands), "out": 0, "skipped": "no_open_slots"}
        else:
            approved, stages["analyst"], nm = self.analyst.run(cands)
            near += nm
            orders = self.risk.size(approved, self.pf, self.prices, regime)
            stages["risk"] = {"in": len(approved), "out": len(orders)}
            for o in orders:
                self.ex.enter(o)
            stages["executor"] = {"in": len(orders), "out": len(orders)}
        self.j.event("desk", "funnel", checkpoint=checkpoint, stages=stages, near_misses=near, regime=regime)

    def risk_check(self) -> None:
        self.refresh_prices()
        self.ex.poll_pending()
        self.ex.poll_exits()
        equity = self.pf.record_equity(self.prices)
        dd = self.pf.drawdown_pct(equity)
        if dd >= self.s["risk"]["drawdown_limit_pct"] and not self.pf.d.get("drawdown_halt"):
            self.pf.d["drawdown_halt"] = True
            self.j.event("risk", "drawdown_halt", drawdown_pct=round(dd, 2))
            self.ex.cancel_open_buys("drawdown_limit")
            self.n.send(f"Drawdown limit hit: equity {equity:.2f} is {dd:.1f}% below peak. No new buys until you reset it.")
        if self.pf.day_loss_pct(equity) >= self.s["risk"]["daily_loss_limit_pct"] and not self._day_flag("daily_loss_logged"):
            self.j.event("risk", "daily_loss_limit", day_loss_pct=round(self.pf.day_loss_pct(equity), 2))
            self.ex.cancel_open_buys("daily_loss_limit")
            self._set_day_flag("daily_loss_logged")
        for a in self.risk.monitor(self.pf, self.prices):
            self.j.event("risk", a["type"], a["symbol"], reason=a["reason"], price=a["price"], qty=a["qty"])
            self.ex.exit(a["symbol"], a["qty"], a["reason"], partial=a["type"] == "partial")
        self.pf.save()

    def after_close(self) -> None:
        self.refresh_prices()
        atrs = {s: (self.market.stats(s) or {}).get("atr") for s in self.pf.positions}
        for m in self.risk.end_of_day(self.pf, dict(self.prices), {k: v for k, v in atrs.items() if v}):
            self.j.event("risk", "trail_stop", m["symbol"], old=m["old"], new=m["new"])
            self.ex.move_stop(m["symbol"], m["new"])
        self.pf.record_equity(self.prices)
        self.pf.save()
        today = now_et()
        text = build_recap(self.pf, self.prices, self.j, self.mode, today.date().isoformat(), weekly=today.weekday() == 4)
        self.n.send(text)
        self._set_day_flag("after_close_done")

    # ---------------- state for the dashboard
    def publish_state(self, note: str = "") -> None:
        prices = self.prices
        equity = self.pf.equity(prices)
        positions = []
        for s, p in self.pf.positions.items():
            px = prices.get(s, p.entry_price)
            positions.append({
                "symbol": s, "qty": p.qty, "entry": p.entry_price, "price": px,
                "pnl_usd": round((px - p.entry_price) * p.qty + p.realized_partial_usd, 2),
                "pnl_pct": round((px / p.entry_price - 1) * 100, 2),
                "r_multiple": round((px - p.entry_price) / p.r, 2) if p.r > 0 else None,
                "stop": p.stop, "days_held": p.days_held, "partial_taken": p.partial_taken,
                "entry_at": p.entry_at, "reason_chain": p.reason_chain,
            })
        events = self.j.read_day(now_et().date().isoformat())
        stages, rules, near = funnel_totals(events)
        next_scan = next((t for t in self.scan_times if t > now_et().strftime("%H:%M")), None)
        state = {
            "updated_at": iso(),
            "mode": self.mode,
            "note": note,
            "opend_logged_in": self.opend_ok,
            "market_open": self.market_open,
            "next_scan_et": next_scan,
            "scan_times_et": self.scan_times,
            "halted": self.halted(),
            "drawdown_halt": bool(self.pf.d.get("drawdown_halt")),
            "daily_loss_halt": self.pf.day_loss_pct(equity) >= self.s["risk"]["daily_loss_limit_pct"],
            "regime": self.market.regime,
            "equity": round(equity, 2),
            "cash": round(self.pf.cash, 2),
            "invested_pct": round(self.pf.invested(prices) / equity * 100, 1) if equity else 0,
            "day_pnl": round(equity - self.pf.d["day_start_equity"], 2),
            "total_pnl": round(equity - self.pf.d["starting_capital"], 2),
            "peak_equity": round(self.pf.d["peak_equity"], 2),
            "drawdown_pct": round(self.pf.drawdown_pct(equity), 2),
            "fees_paid": round(self.pf.d["fees_paid"], 2),
            "positions": positions,
            "pending": [{"symbol": p.symbol, "qty": p.qty, "limit": p.limit, "placed_at": p.placed_at}
                        for p in self.pf.pending.values()],
            "funnel_today": stages,
            "top_rejections_today": rules.most_common(10),
            "near_misses_today": near[-30:],
            "equity_history": self.pf.d["equity_history"][-3000:],
            "universe_size": len(self.market.universe()),
            "earnings_source": self.market.cache.get("earnings_source"),
        }
        write_json(self.s.data_dir / "state.json", state)
        (self.s.data_dir / "heartbeat").write_text(iso())

    # ---------------- main loop
    def tick(self) -> None:
        now = now_et()
        today = now.date()
        hhmm = now.strftime("%H:%M")
        sched = self.s["schedule"]

        if self.halted() and not self.halted_logged:
            self.j.event("desk", "halted", reason="halted by kill switch")
            self.ex.cancel_open_buys("kill_switch")
            self.halted_logged = True
        elif not self.halted():
            self.halted_logged = False

        if not self.market.cache.get("trading_days") or today.isoformat() > self.market.cache["trading_days"][-1]:
            self.market.cache["trading_days"] = self.market.trading_days_around()
        trading_day = self.market.is_trading_day(today)

        if trading_day and hhmm >= sched["premarket_job_et"] and not self._day_flag("premarket_done"):
            self.premarket()

        try:
            self.market_open = trading_day and self.q.us_market_open()
        except BrokerError:
            self.market_open = False

        if self.market_open:
            interval = self.s["risk"]["check_interval_minutes"] * 60
            if time.monotonic() - self.last_risk_check >= interval:
                self.risk_check()
                self.last_risk_check = time.monotonic()
            done = self.pf.d.setdefault("scans_done", {})
            for t in self.scan_times:
                key = f"{today.isoformat()} {t}"
                # Run each scan once, and only within 30 minutes of its slot.
                if t <= hhmm < _hhmm(tuple(map(int, t.split(":"))), 30) and key not in done:
                    done[key] = iso()
                    self.pf.save()
                    if not self._day_flag("premarket_done"):
                        self.premarket()
                    self.scan(t)
            for k in [k for k in done if k < (today - timedelta(days=10)).isoformat()]:
                del done[k]
        elif self.pf.pending:
            self.ex.poll_pending()

        if trading_day and hhmm >= sched["after_close_job_et"] and not self._day_flag("after_close_done") \
                and self._day_flag("premarket_done"):
            self.after_close()

    def run(self) -> None:
        self.start()
        while True:
            try:
                if not self.q.logged_in():
                    self.opend_ok = False
                    self.publish_state(note="OpenD logged out; waiting")
                    self.last_progress = time.time()
                    time.sleep(15)
                    continue
                self.opend_ok = True
                self.tick()
                self.last_progress = time.time()
            except BrokerError as exc:
                self.j.event("desk", "error", error=str(exc)[:500])
            except Exception as exc:  # keep the desk alive; the error goes to the log and recap
                log.exception("tick failed")
                self.j.event("desk", "error", error=f"{type(exc).__name__}: {exc}"[:500])
            if time.monotonic() - self.last_state >= 30:
                try:
                    self.publish_state()
                except Exception:
                    log.exception("publish_state failed")
                self.last_state = time.monotonic()
            time.sleep(20)
