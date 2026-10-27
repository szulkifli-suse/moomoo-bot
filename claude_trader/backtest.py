"""Backtester for the technical rules on daily bars.

Run inside the pod:  python -m claude_trader.backtest
Writes data/backtest.json (the dashboard's Backtest page reads it).

Limits, stated in the report: the catalyst point is a gap-on-volume proxy, the Analyst and
earnings blackout are not simulated, intraday order of high/low is unknown (stops are checked
before profit targets), and the universe is today's members (survivorship bias).
"""
from __future__ import annotations

import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd
from moomoo import KLType

from .fees import order_fee, round_trip_cost
from .agents.risk import initial_stop
from .broker import BrokerError, Quotes
from .config import load_settings
from .indicators import atr, sma
from .util import iso, now_et, write_json

log = logging.getLogger("backtest")
ENTRY_SLIPPAGE = 0.0005


@dataclass
class BtPos:
    sym: str
    qty: float
    entry: float
    stop: float
    r: float
    atr: float
    hi: float
    days: int = 0
    partial: bool = False
    realized: float = 0.0
    fees: float = 0.0


def load_bars(q: Quotes | None, codes: list[str], years: int, cache_dir: Path) -> dict[str, pd.DataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    end = now_et().date()
    start = end - timedelta(days=int(365 * years) + 120)
    out = {}
    for code in codes:
        path = cache_dir / f"{code}.csv"
        if path.exists() and path.stat().st_mtime > time.time() - 86400:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        elif q is not None:
            try:
                df = q.history(code, start.isoformat(), end.isoformat(), KLType.K_DAY)
            except BrokerError as exc:
                log.warning("history %s failed: %s", code, exc)
                continue
            time.sleep(0.55)
            df.to_csv(path)
        else:
            continue
        if len(df) > 80:
            out[code] = df
    return out


def prepare(df: pd.DataFrame, sc: dict, rk: dict) -> pd.DataFrame:
    d = df.copy()
    d["sma"] = sma(d["close"], sc["sma_period"])
    d["sma_rising"] = d["sma"] > d["sma"].shift(sc["sma_rising_lookback_days"])
    d["atr"] = atr(d, rk["atr_period"])
    d["vol20"] = d["volume"].shift(1).rolling(20).mean()
    d["rvol"] = d["volume"] / d["vol20"]
    d["dollar20"] = (d["close"] * d["volume"]).rolling(20).mean()
    d["high_n"] = d["high"].rolling(sc["near_high_days"]).max()
    d["ret_n"] = d["close"] / d["close"].shift(sc["rs_days"]) - 1
    gap = (d["open"] / d["close"].shift(1) - 1) * 100
    hit = (gap >= sc["catalyst_gap_pct"]) & (d["rvol"] >= sc["catalyst_gap_volume_mult"])
    d["catalyst"] = hit.rolling(sc["catalyst_lookback_days"]).max().fillna(0).astype(bool)
    return d


def simulate(data: dict[str, pd.DataFrame], bench: pd.DataFrame, cfg: dict, regime_on=True, partial_on=True) -> dict:
    sc, fl, rk, fees_cfg = cfg["scout"], cfg["filter"], cfg["risk"], cfg["fees"]
    def fee(value: float, qty: float) -> float:
        return order_fee(fees_cfg, value, qty)

    cash = float(cfg["account"]["starting_capital_usd"])
    bench = bench.copy()
    bench["sma"] = sma(bench["close"], rk["regime_sma_period"])
    bench["ret_n"] = bench["close"] / bench["close"].shift(sc["rs_days"]) - 1
    dates = bench.index[sc["sma_period"] + sc["sma_rising_lookback_days"] + 25:]
    cutoff = bench.index[-1] - pd.Timedelta(days=int(365 * cfg["backtest"]["years"]))
    dates = [d for d in dates if d >= cutoff]

    positions: dict[str, BtPos] = {}
    trades, curve, cooldown = [], [], {}
    pending: list[tuple[str, float, float]] = []  # (sym, qty-budget, atr) to open next day
    peak, max_dd = cash, 0.0

    def equity(day) -> float:
        return cash + sum(p.qty * data[s].loc[day, "close"] for s, p in positions.items() if day in data[s].index)

    for i, day in enumerate(dates):
        # 1. Open yesterday's signals at today's open.
        for sym, budget, a in pending:
            df = data[sym]
            if day not in df.index or sym in positions:
                continue
            px = df.loc[day, "open"] * (1 + ENTRY_SLIPPAGE)
            qty = math.floor(min(budget, cash - fee(budget, budget / px)) / px)
            if qty < 1:
                continue
            stop = initial_stop(px, a, rk)
            f_in = fee(qty * px, qty)
            cash -= qty * px + f_in
            positions[sym] = BtPos(sym, qty, px, stop, px - stop, a, px, fees=f_in)
        pending = []

        # 2. Manage open positions on today's bar.
        for sym in list(positions):
            p, df = positions[sym], data[sym]
            if day not in df.index:
                continue
            bar = df.loc[day]
            exit_px, reason = None, None
            if bar["low"] <= p.stop:
                exit_px, reason = min(bar["open"], p.stop), ("trail" if p.partial else "stop")
            elif partial_on and not p.partial and bar["high"] >= p.entry + rk["partial_at_r"] * p.r:
                half = math.floor(p.qty / 2)
                tgt = max(bar["open"], p.entry + rk["partial_at_r"] * p.r)
                if half >= 1:
                    f_half = fee(half * tgt, half)
                    cash += half * tgt - f_half
                    p.realized += half * (tgt - p.entry)
                    p.fees += f_half
                    p.qty -= half
                p.partial, p.stop = True, max(p.stop, p.entry)
            p.days += 1
            if exit_px is None and p.days >= rk["time_stop_trading_days"] and not p.partial:
                exit_px, reason = bar["close"], "time"
            if exit_px is not None:
                f_out = fee(p.qty * exit_px, p.qty)
                cash += p.qty * exit_px - f_out
                pnl = p.realized + p.qty * (exit_px - p.entry) - p.fees - f_out
                trades.append({"symbol": sym, "exit_date": str(day.date()), "pnl": pnl, "reason": reason,
                               "days": p.days, "r": (exit_px - p.entry) / p.r if p.r else 0, "fees": p.fees + f_out})
                if reason == "stop":
                    cooldown[sym] = i
                del positions[sym]
                continue
            p.hi = max(p.hi, bar["close"])
            if not math.isnan(df.loc[day, "atr"]):
                p.atr = df.loc[day, "atr"]
            if p.partial or not partial_on:
                # Without the partial rule, trailing starts once the trade is +2R.
                if p.partial or p.hi >= p.entry + rk["partial_at_r"] * p.r:
                    p.stop = max(p.stop, p.hi - rk["trail_atr_mult"] * p.atr)

        # 3. Signals at today's close for tomorrow.
        eq = equity(day)
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)
        curve.append([str(day.date()), round(eq, 2)])
        weak = regime_on and bench.loc[day, "close"] < bench.loc[day, "sma"]
        pct = rk["weak_position_pct"] if weak else rk["position_pct"]
        max_pos = rk["weak_max_positions"] if weak else rk["max_positions"]
        slots = max_pos - len(positions)
        if slots <= 0:
            continue
        bench_ret = bench.loc[day, "ret_n"]
        picks = []
        for sym, df in data.items():
            if sym in positions or day not in df.index:
                continue
            if sym in cooldown and i - cooldown[sym] < fl["reentry_cooldown_trading_days"]:
                continue
            row = df.loc[day]
            if any(pd.isna(row[k]) for k in ("sma", "atr", "rvol", "dollar20", "ret_n")):
                continue
            if not (row["close"] > row["sma"] and row["sma_rising"]):
                continue
            pts = int(row["rvol"] >= sc["rvol_min"]) + int(row["catalyst"]) \
                + int(row["close"] >= row["high_n"] * (1 - sc["near_high_pct"] / 100)) \
                + int(row["ret_n"] > bench_ret)
            if pts < sc["min_points"] or row["dollar20"] < fl["min_avg_dollar_volume_usd"]:
                continue
            picks.append((pts, row["rvol"], sym, row["atr"]))
        picks.sort(reverse=True)
        invested = eq - cash
        room = eq * rk["max_invested_pct"] / 100 - invested
        for _, _, sym, a in picks[:slots]:
            budget = min(eq * pct / 100, room)
            px = data[sym].loc[day, "close"]
            value = math.floor(budget / px) * px
            if value <= 0 or round_trip_cost(fees_cfg, value, px) / value * 100 > fl["max_round_trip_cost_pct"]:
                continue
            pending.append((sym, budget, a))
            room -= budget

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    final = curve[-1][1] if curve else cash
    start_cap = cfg["account"]["starting_capital_usd"]
    yrs = max(len(curve) / 252, 0.01)
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "avg_r": round(sum(t["r"] for t in trades) / len(trades), 2) if trades else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "max_drawdown_pct": round(float(max_dd), 1),
        "avg_holding_days": round(sum(t["days"] for t in trades) / len(trades), 1) if trades else None,
        "total_fees": round(sum(t["fees"] for t in trades), 2),
        "final_equity": round(float(final), 2),
        "cagr_pct": round(((final / start_cap) ** (1 / yrs) - 1) * 100, 1) if final > 0 else None,
        "exit_reasons": {k: int(v) for k, v in pd.Series([t["reason"] for t in trades]).value_counts().items()} if trades else {},
        "equity_curve": curve,
        "recent_trades": trades[-50:],
    }


def gate(base: dict) -> dict:
    # Plain Python bools: numpy bools would be saved as the strings "True"/"False".
    return {
        "profit_factor_ge_1_3": bool(base["profit_factor"] is not None and float(base["profit_factor"]) >= 1.3),
        "at_least_100_trades": bool(int(base["trades"]) >= 100),
        "max_drawdown_lt_20": bool(float(base["max_drawdown_pct"]) < 20),
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Backtest the technical rules")
    ap.add_argument("--years", type=float, default=None, help="test period in years, e.g. 0.25 for 3 months")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    s = load_settings()
    if args.years:
        s.cfg["backtest"]["years"] = args.years
    from .util import read_json
    universe = (read_json(s.data_dir / "universe.json", {}) or {}).get("symbols") or []
    q = Quotes(s.opend_host, s.opend_port)
    try:
        if not universe:
            from .journal import Journal
            from .market import MarketData
            universe = MarketData(s, q, Journal(s.logs_dir, "BACKTEST")).build_universe()
        bench_code = s["universe"]["benchmark"]
        log.info("loading bars for %d symbols", len(universe) + 1)
        # Always load 3+ years so short tests reuse the same cached bars and have warm-up history.
        bars = load_bars(q, universe + [bench_code], max(3, s["backtest"]["years"]), s.data_dir / "bt_bars")
    finally:
        q.close()
    bench = bars.pop(bench_code)
    data = {k: prepare(v, s["scout"], s["risk"]) for k, v in bars.items()}
    report = {
        "generated_at": iso(),
        "universe_size": len(data),
        "years": s["backtest"]["years"],
        "notes": [
            "Technical rules only. The Analyst and the earnings blackout are not simulated.",
            "Catalyst point uses a gap-up-on-volume proxy, same as live Scout.",
            "Entries at the next day's open plus 0.05% slippage; stops checked before profit targets.",
            "Universe is today's index members, so results carry survivorship bias (likely optimistic).",
            "Fees use the config fee model per order.",
        ],
        "base": simulate(data, bench, s.cfg),
        "regime_off": simulate(data, bench, s.cfg, regime_on=False),
        "partial_off": simulate(data, bench, s.cfg, partial_on=False),
    }
    report["gate"] = gate(report["base"])
    write_json(s.data_dir / "backtest.json", report)
    for k in ("base", "regime_off", "partial_off"):
        r = report[k]
        log.info("%-11s trades=%s win=%s%% PF=%s maxDD=%s%% final=%s fees=%s", k, r["trades"], r["win_rate"],
                 r["profit_factor"], r["max_drawdown_pct"], r["final_equity"], r["total_fees"])
    log.info("gate: %s", report["gate"])


if __name__ == "__main__":
    main()
