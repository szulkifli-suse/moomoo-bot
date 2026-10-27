"""Entry point: python -m claude_trader"""
from __future__ import annotations

import logging
import sys
import threading
import time

from . import internal_api
from .config import load_settings
from .desk import Desk
from .journal import Journal
from .util import iso, read_json, write_json


def _waiting_state(settings, mode: str) -> None:
    """Placeholder state so the dashboard has something to show before OpenD is logged in."""
    path = settings.data_dir / "state.json"
    prev = read_json(path, {}) or {}
    start = settings["account"]["starting_capital_usd"]
    state = {
        "equity": start, "cash": start, "invested_pct": 0, "day_pnl": 0, "total_pnl": 0, "drawdown_pct": 0,
        "fees_paid": 0, "positions": [], "pending": [], "funnel_today": {}, "top_rejections_today": [],
        "near_misses_today": [], "equity_history": [], "regime": "unknown", "universe_size": 0,
        "scan_times_et": [], "next_scan_et": None, "earnings_source": None, "market_open": False,
        "halted": settings.stop_file.exists(), "drawdown_halt": False, "daily_loss_halt": False,
    }
    state.update({k: v for k, v in prev.items() if k in state})
    state.update(updated_at=iso(), mode=mode, opend_logged_in=False,
                 note="waiting for OpenD login (run scripts/opend-login.sh)")
    write_json(path, state)


def _heartbeat(settings, desk_ref: dict) -> None:
    """Write the liveness heartbeat. While starting up (waiting for OpenD) the bot is idle, not hung."""
    while True:
        desk = desk_ref.get("desk")
        progressing = desk is None or not desk.started or time.time() - desk.last_progress < 900
        if progressing:
            (settings.data_dir / "heartbeat").write_text(iso())
            if desk is None or not desk.started:
                try:
                    _waiting_state(settings, "LIVE" if settings.live_trading else "SHADOW")
                except OSError:
                    pass
        time.sleep(30)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    mode = "LIVE" if settings.live_trading else "SHADOW"
    desk_ref: dict = {}
    threading.Thread(target=_heartbeat, args=(settings, desk_ref), daemon=True, name="heartbeat").start()
    internal_api.start(settings, Journal(settings.logs_dir, mode))
    logging.getLogger("main").info("mode=%s; connecting to OpenD", mode)
    # Connecting blocks until OpenD accepts the connection (after the moomoo login).
    desk = Desk(settings)
    desk_ref["desk"] = desk
    desk.run()


if __name__ == "__main__":
    main()
