"""Daily market data prep: universe, daily stats, volume profiles, earnings dates, regime."""
from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date, timedelta

import httpx
import pandas as pd
from moomoo import KLType

from .broker import BrokerError, Quotes
from .config import Settings
from .indicators import daily_stats, sma
from .journal import Journal
from .util import iso, now_et, read_json, write_json

log = logging.getLogger("market")
HISTORY_PAUSE = 0.55  # history-kline limit is 60 requests per 30 seconds


class MarketData:
    def __init__(self, settings: Settings, quotes: Quotes, journal: Journal):
        self.s = settings
        self.q = quotes
        self.j = journal
        self.cache_path = settings.data_dir / "market_cache.json"
        self.cache = read_json(self.cache_path, {}) or {}

    # ---------- trading calendar
    def trading_days_around(self, back: int = 45, fwd: int = 15) -> list[str]:
        today = now_et().date()
        return self.q.trading_days(today - timedelta(days=back), today + timedelta(days=fwd))

    def is_trading_day(self, day: date) -> bool:
        return day.isoformat() in set(self.cache.get("trading_days", []))

    def next_trading_days(self, n: int, after: date) -> list[str]:
        days = [d for d in self.cache.get("trading_days", []) if d > after.isoformat()]
        return days[:n]

    # ---------- universe
    def build_universe(self) -> list[str]:
        ucfg = self.s["universe"]
        # moomoo allows 10 sector-list requests per 30 seconds: fetch the index first, then pace the plates.
        index_members: set[str] = set()
        index_error = None
        try:
            index_members = set(self.q.plate_stock(ucfg["index"]))
        except BrokerError as exc:
            index_error = exc
        plate_members: set[str] = set()
        for plate in ucfg["plates"]:
            time.sleep(3.2)
            try:
                plate_members.update(self.q.plate_stock(plate))
            except BrokerError as exc:
                self.j.event("universe", "plate_error", plate=plate, error=str(exc))
        source = "index"
        try:
            if index_error:
                raise index_error
            symbols = sorted(plate_members & index_members)
            if not symbols:
                raise BrokerError("index intersection is empty")
        except BrokerError as exc:
            # Fallback: large caps in the tech plates stand in for the S&P 500 filter.
            source = "market_cap_fallback"
            snaps = self.q.snapshot(sorted(plate_members))
            floor = ucfg["fallback_min_market_cap_usd"]
            symbols = sorted(
                c for c, r in snaps.items() if float(r.get("total_market_val") or 0) >= floor
            )
            self.j.event("universe", "index_unavailable", error=str(exc), fallback_count=len(symbols))
        write_json(self.s.data_dir / "universe.json", {"built_at": iso(), "source": source, "symbols": symbols})
        self.j.event("universe", "built", count=len(symbols), source=source)
        return symbols

    def universe(self) -> list[str]:
        return (read_json(self.s.data_dir / "universe.json", {}) or {}).get("symbols", [])

    # ---------- daily prep
    def daily_bars(self, code: str, days: int = 160) -> pd.DataFrame:
        end = now_et().date()
        start = end - timedelta(days=days)
        df = self.q.history(code, start.isoformat(), end.isoformat(), KLType.K_DAY)
        time.sleep(HISTORY_PAUSE)
        # Drop today's partial bar if the market is open.
        if not df.empty and df.index[-1].date() >= end:
            df = df.iloc[:-1]
        return df

    def volume_profile(self, code: str, checkpoints: list[str]) -> dict[str, float]:
        """Average cumulative volume by time of day over ~20 sessions, at each HH:MM checkpoint."""
        end = now_et().date() - timedelta(days=1)
        start = end - timedelta(days=30)
        df = self.q.history(code, start.isoformat(), end.isoformat(), KLType.K_5M)
        time.sleep(HISTORY_PAUSE)
        if df.empty:
            return {}
        df = df.copy()
        df["day"] = df.index.date
        df["hhmm"] = df.index.strftime("%H:%M")
        days = sorted(df["day"].unique())[-20:]
        out = {}
        for cp in checkpoints:
            totals = [df[(df["day"] == d) & (df["hhmm"] <= cp)]["volume"].sum() for d in days]
            totals = [t for t in totals if t > 0]
            if totals:
                out[cp] = float(sum(totals) / len(totals))
        return out

    def earnings_next(self, trading_days: list[str]) -> tuple[set[str] | None, str]:
        """Symbols reporting on any of the given days. None means the source was unavailable."""
        found: set[str] = set()
        try:
            for d in trading_days:
                r = httpx.get(
                    "https://api.nasdaq.com/api/calendar/earnings",
                    params={"date": d},
                    headers={"User-Agent": "Mozilla/5.0 (compatible; claude-trading-desk)", "Accept": "application/json"},
                    timeout=20,
                )
                r.raise_for_status()
                rows = ((r.json().get("data") or {}).get("rows")) or []
                found.update(f"US.{row['symbol'].strip().upper()}" for row in rows if row.get("symbol"))
            return found, "nasdaq"
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return None, f"unavailable: {type(exc).__name__}"

    def premarket_prep(self, scan_times: list[str]) -> None:
        """Runs once a day before the open. Builds everything Scout and Risk need."""
        self.cache["trading_days"] = self.trading_days_around()
        symbols = self.build_universe()
        bench = self.s["universe"]["benchmark"]
        scout_cfg, risk_cfg = self.s["scout"], self.s["risk"]

        stats, profiles, errors = {}, {}, 0
        for code in symbols + [bench]:
            try:
                df = self.daily_bars(code)
                st = daily_stats(df, scout_cfg, risk_cfg)
                if st:
                    stats[code] = asdict(st)
                if code != bench:
                    profiles[code] = self.volume_profile(code, scan_times)
            except BrokerError as exc:
                errors += 1
                self.j.event("market", "history_error", symbol=code, error=str(exc))

        regime = "unknown"
        try:
            qqq = self.daily_bars(bench, days=120)
            qqq_sma = sma(qqq["close"], risk_cfg["regime_sma_period"]).iloc[-1]
            regime = "normal" if qqq["close"].iloc[-1] >= qqq_sma else "weak"
        except (BrokerError, IndexError, KeyError) as exc:
            self.j.event("market", "regime_error", error=str(exc))

        today = now_et().date()
        blackout_days = self.next_trading_days(self.s["filter"]["earnings_blackout_trading_days"], today - timedelta(days=1))
        earnings, source = self.earnings_next(blackout_days)

        self.cache.update(
            prepared_for=today.isoformat(),
            prepared_at=iso(),
            stats=stats,
            profiles=profiles,
            regime=regime,
            earnings=sorted(earnings) if earnings is not None else None,
            earnings_source=source,
            earnings_days=blackout_days,
        )
        write_json(self.cache_path, self.cache)
        self.j.event(
            "market", "premarket_prep", symbols=len(symbols), stats=len(stats),
            history_errors=errors, regime=regime, earnings_source=source,
        )

    @property
    def regime(self) -> str:
        return self.cache.get("regime", "unknown")

    def stats(self, code: str) -> dict | None:
        return (self.cache.get("stats") or {}).get(code)
