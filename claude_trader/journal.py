"""Logger agent: every scan, rejection, decision, order and fill goes to logs/YYYY-MM-DD.jsonl.
Closed trades also go to logs/trades.csv."""
from __future__ import annotations

import csv
import json
import logging
import threading
from pathlib import Path

from .util import iso, now_et

log = logging.getLogger("journal")

TRADE_FIELDS = [
    "closed_at", "symbol", "mode", "qty", "entry_price", "exit_price", "entry_at",
    "days_held", "pnl_usd", "pnl_pct", "r_multiple", "fees_usd", "exit_reason", "reason_chain",
]


class Journal:
    def __init__(self, logs_dir: Path, mode: str):
        self.dir = logs_dir
        self.mode = mode
        self._lock = threading.Lock()

    def event(self, agent: str, kind: str, symbol: str | None = None, **data) -> dict:
        rec = {"ts": iso(), "mode": self.mode, "agent": agent, "kind": kind}
        if symbol:
            rec["symbol"] = symbol
        rec.update(data)
        path = self.dir / f"{now_et():%Y-%m-%d}.jsonl"
        line = json.dumps(rec, default=str)
        with self._lock, path.open("a") as fh:
            fh.write(line + "\n")
        log.info("%s %s %s %s", agent, kind, symbol or "", {k: v for k, v in data.items() if k != "chain"})
        return rec

    def trade(self, row: dict) -> None:
        path = self.dir / "trades.csv"
        new = not path.exists()
        with self._lock, path.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def read_day(self, day: str) -> list[dict]:
        path = self.dir / f"{day}.jsonl"
        if not path.exists():
            return []
        out = []
        with path.open() as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def read_trades(self) -> list[dict]:
        path = self.dir / "trades.csv"
        if not path.exists():
            return []
        with path.open() as fh:
            return list(csv.DictReader(fh))
