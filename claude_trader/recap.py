"""Daily recap and Friday tuning report."""
from __future__ import annotations

from collections import Counter

from .util import now_et


def _trade_stats(trades: list[dict]) -> dict:
    pnls = [float(t["pnl_usd"]) for t in trades if t.get("pnl_usd") not in (None, "")]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    return {
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "fees": round(sum(float(t.get("fees_usd") or 0) for t in trades), 2),
        "total_pnl": round(sum(pnls), 2),
    }


def funnel_totals(events: list[dict]) -> tuple[dict, Counter, list[dict]]:
    stages: dict[str, dict] = {}
    rules: Counter = Counter()
    near: list[dict] = []
    for e in events:
        if e.get("kind") == "funnel":
            for stage, counts in (e.get("stages") or {}).items():
                agg = stages.setdefault(stage, {"in": 0, "out": 0})
                agg["in"] += counts.get("in", 0)
                agg["out"] += counts.get("out", 0)
            near.extend(e.get("near_misses") or [])
        if e.get("kind") in ("reject", "veto", "pass") and e.get("rule") and e.get("agent") in ("scout", "filter", "analyst", "risk"):
            rules[f"{e['agent']}: {str(e['rule']).split(' ')[0]}"] += 1
    return stages, rules, near


def build_recap(portfolio, prices: dict, journal, mode: str, day: str, weekly: bool) -> str:
    events = journal.read_day(day)
    equity = portfolio.equity(prices)
    start = portfolio.d["day_start_equity"]
    total = equity - portfolio.d["starting_capital"]
    lines = [
        f"Claude Trading Desk ({mode}) recap {day}",
        f"Equity {start:,.2f} -> {equity:,.2f} | day {equity-start:+,.2f} | total {total:+,.2f}",
        f"Cash {portfolio.cash:,.2f} | regime {portfolio.d.get('regime','?')}",
    ]

    fills = [e for e in events if e.get("kind") in ("filled_buy", "filled_sell", "filled_partial")]
    lines.append(f"\nTrades today: {len(fills)}")
    for e in fills:
        chain = e.get("chain") or {}
        why = ""
        if chain:
            sc = (chain.get("scout") or {}).get("points") or []
            an = (chain.get("analyst") or {}).get("reason") or ""
            why = f" | scout: {'; '.join(sc)} | analyst: {an[:200]}"
        lines.append(f"- {e['kind']} {e.get('symbol')} {e.get('qty')} @ {e.get('price')} {e.get('reason','')}{why}")

    stages, rules, near = funnel_totals(events)
    if stages:
        lines.append("\nFunnel: " + " -> ".join(f"{k} {v['in']}>{v['out']}" for k, v in stages.items()))
    if rules:
        lines.append("Top rejections: " + ", ".join(f"{r} x{n}" for r, n in rules.most_common(6)))
    if near:
        lines.append("Near-misses: " + ", ".join(f"{n['symbol'].removeprefix('US.')} ({n['stage']}: {n['rule']})" for n in near[:12]))

    lines.append(f"\nOpen positions: {len(portfolio.positions)}")
    for s, p in portfolio.positions.items():
        px = prices.get(s, p.entry_price)
        lines.append(f"- {s} {p.qty} @ {p.entry_price:.2f} now {px:.2f} stop {p.stop:.2f} day {p.days_held}{' partial taken' if p.partial_taken else ''}")

    problems = [e for e in events if e.get("kind") in (
        "order_error", "sell_failed", "halted", "drawdown_halt", "daily_loss_limit", "reconcile_mismatch",
        "error", "history_error", "broker_stop_failed")]
    if problems:
        lines.append(f"\nErrors / halts: {len(problems)}")
        for e in problems[:10]:
            lines.append(f"- {e.get('agent')} {e.get('kind')} {e.get('symbol','')} {str(e.get('error') or e.get('reason') or '')[:150]}")

    if weekly:
        week_trades = journal.read_trades()
        st = _trade_stats(week_trades)
        lines.append("\nWeekly tuning (all trades so far):")
        lines.append(
            f"trades {st['trades']}, win rate {st['win_rate']}%, avg win {st['avg_win']}, avg loss {st['avg_loss']}, "
            f"profit factor {st['profit_factor']}, fees {st['fees']}, drawdown {portfolio.drawdown_pct(equity):.1f}%"
        )
        week_rules: Counter = Counter()
        today = now_et().date()
        from datetime import timedelta
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            _, r, _ = funnel_totals(journal.read_day(d))
            week_rules.update(r)
        if week_rules:
            top, count = week_rules.most_common(1)[0]
            lines.append(f"Rule that rejected the most this week: {top} ({count}). Suggestions only; change config.yaml by hand.")
    return "\n".join(lines)
