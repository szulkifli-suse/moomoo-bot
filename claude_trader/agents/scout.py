"""Scout: scans the universe and outputs scored candidates."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Candidate:
    symbol: str
    price: float
    score: int
    points: list[str]
    rvol: float | None
    stats: dict
    snapshot: dict
    unverified: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    def chain(self) -> dict:
        return {"scout": {"score": self.score, "points": self.points, "rvol": self.rvol,
                          "unverified": self.unverified}}


class Scout:
    name = "scout"

    def __init__(self, cfg: dict, journal):
        self.cfg = cfg
        self.j = journal

    def run(self, universe: list[str], snaps: dict, market, checkpoint: str | None):
        cfg = self.cfg
        bench = market.stats(market.s["universe"]["benchmark"]) or {}
        profiles = market.cache.get("profiles") or {}
        out: list[Candidate] = []
        near_misses: list[dict] = []
        rejected = 0

        for code in universe:
            st = market.stats(code)
            snap = snaps.get(code)
            if not st or not snap:
                rejected += 1
                self.j.event(self.name, "reject", code, rule="no_data")
                continue
            price = float(snap.get("last_price") or 0)
            if price <= 0:
                rejected += 1
                self.j.event(self.name, "reject", code, rule="no_price")
                continue

            fails = []
            if st["sma"] is None or price <= st["sma"]:
                fails.append("below_sma50")
            if not st["sma_rising"]:
                fails.append("sma50_not_rising")

            points, unverified = [], []
            # Relative volume, matched to time of day.
            rvol = None
            prof = profiles.get(code) or {}
            today_vol = float(snap.get("volume") or 0)
            if checkpoint and prof.get(checkpoint):
                rvol = today_vol / prof[checkpoint]
            elif st.get("avg_volume20"):
                rvol = st["last_volume"] / st["avg_volume20"]  # yesterday vs 20-day average
                unverified.append("rvol_fallback_yesterday")
            if rvol is not None and rvol >= cfg["rvol_min"]:
                points.append(f"rvol {rvol:.2f}x")
            if st["catalyst_gap"]:
                points.append(f"catalyst: {st['catalyst_detail']}")
            if st["high_n"] and price >= st["high_n"] * (1 - cfg["near_high_pct"] / 100):
                points.append(f"within {cfg['near_high_pct']}% of {cfg['near_high_days']}d high")
            if st.get("ret_n") is not None and bench.get("ret_n") is not None:
                if st["ret_n"] > bench["ret_n"]:
                    points.append(f"beats QQQ over {cfg['rs_days']}d ({st['ret_n']*100:.1f}% vs {bench['ret_n']*100:.1f}%)")
            else:
                unverified.append("relative_strength")

            if len(points) < cfg["min_points"]:
                fails.append(f"points {len(points)}<{cfg['min_points']}")

            if fails:
                rejected += 1
                self.j.event(self.name, "reject", code, rule=fails[0], fails=fails, points=points)
                if len(fails) == 1:
                    near_misses.append({"symbol": code, "stage": "scout", "rule": fails[0]})
                continue

            c = Candidate(code, price, len(points), points, rvol, st, snap, unverified)
            self.j.event(self.name, "candidate", code, score=c.score, points=points, rvol=rvol, unverified=unverified)
            out.append(c)

        out.sort(key=lambda c: (c.score, c.rvol or 0), reverse=True)
        return out, {"in": len(universe), "out": len(out), "rejected": rejected}, near_misses
