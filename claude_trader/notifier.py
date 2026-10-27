"""Telegram messages. If no token is set, messages are only logged."""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("notifier")


class Notifier:
    def __init__(self, token: str | None, chat_id: str | None, journal=None):
        self.token = token
        self.chat_id = chat_id
        self.journal = journal

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if self.journal:
            self.journal.event("notifier", "message", text=text[:4000], sent=self.enabled)
        if not self.enabled:
            log.info("telegram not configured; message logged only")
            return False
        # Telegram caps messages at 4096 characters.
        chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
        ok = True
        for chunk in chunks:
            try:
                r = httpx.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True},
                    timeout=20,
                )
                ok = ok and r.status_code == 200
                if r.status_code != 200:
                    log.warning("telegram send failed: HTTP %s", r.status_code)
            except httpx.HTTPError as exc:
                log.warning("telegram send failed: %s", type(exc).__name__)
                ok = False
        return ok
