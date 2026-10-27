"""Filter: kills any candidate that fails a rule. Missing data passes, tagged unverified."""
from __future__ import annotations

import statistics
import time
from datetime import date

from ..fees import round_trip_cost
from .scout import Candidate


def spread_pct(snap: dict) -> float | None:
    bid, ask = float(snap.get("bid_price") or 0), float(snap.get("ask_price") or 0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2) * 100


class Filter:
    name = "filter"

    def __init__(self, cfg: dict, fees_cfg: dict, journal):
        self.cfg = cfg
        self.fees = fees_cfg
        self.j = journal

    def sample_spreads(self, quotes, codes: list[str], snaps: dict) -> dict[str, float | None]:
        samples: dict[str, list[float]] = {c: [] for c in codes}
        for i in range(self.cfg["spread_samples"]):
            if i > 0:
                time.sleep(self.cfg["spread_sample_gap_seconds"])
                snaps = quotes.snapshot(codes)
            for c in codes:
                sp = spread_pct(snaps.get(c) or {})
                if sp is not None:
                    samples[c].append(sp)
        return {c: statistics.median(v) if v else None for c, v in samples.items()}

    def run(self, cands: list[Candidate], quotes, market, portfolio, position_value: float, today: date):
        cfg = self.cfg
        codes = [c.symbol for c in cands]
        spreads = self.sample_spreads(quotes, codes, {c.symbol: c.snapshot for c in cands}) if codes else {}
        earnings = market.cache.get("earnings")
        earnings_days = market.cache.get("earnings_days") or []
        trading_days = market.cache.get("trading_days") or []

        passed, near_misses = [], []
        for c in cands:
            fails, unverified = [], list(c.unverified)
            # Whole shares: the position is the largest whole number of shares that fits.
            value = position_value
            if c.price > 0 and position_value >= c.price:
                value = (position_value // c.price) * c.price
            cost_pct = round_trip_cost(self.fees, value, c.price) / value * 100 if value > 0 else 999

            if c.stats["avg_dollar_volume20"] < cfg["min_avg_dollar_volume_usd"]:
                fails.append("dollar_volume")

            sp = spreads.get(c.symbol)
            if sp is None:
                unverified.append("spread")
            elif sp > cfg["max_spread_pct"]:
                fails.append(f"spread {sp:.3f}%")

            if earnings is None:
                unverified.append("earnings")
            elif c.symbol in earnings:
                fails.append(f"earnings within {cfg['earnings_blackout_trading_days']}d ({','.join(earnings_days)})")

            if c.snapshot.get("suspension") in (True, "True", 1):
                fails.append("halted")

            if c.symbol in portfolio.positions:
                fails.append("already_held")
            if any(p.symbol == c.symbol for p in portfolio.pending.values()):
                fails.append("open_buy_order")

            cd = portfolio.d["cooldowns"].get(c.symbol)
            if cd and cd.get("stopped_on"):
                since = [d for d in trading_days if cd["stopped_on"] < d <= today.isoformat()]
                if len(since) < cfg["reentry_cooldown_trading_days"]:
                    fails.append("stopped_out_recently")
                elif cd.get("reentries_since_stop", 0) >= cfg["max_reentries_after_stop"]:
                    fails.append("reentry_used")

            if cost_pct > cfg["max_round_trip_cost_pct"]:
                fails.append(f"fees {cost_pct:.2f}% of position")

            c.unverified = unverified
            c.notes["spread_pct"] = sp
            if fails:
                self.j.event(self.name, "reject", c.symbol, rule=fails[0], fails=fails)
                if len(fails) == 1:
                    near_misses.append({"symbol": c.symbol, "stage": "filter", "rule": fails[0]})
            else:
                self.j.event(self.name, "pass", c.symbol, spread_pct=sp, unverified=unverified)
                passed.append(c)
        return passed, {"in": len(cands), "out": len(passed)}, near_misses
