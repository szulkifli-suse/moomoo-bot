"""Settings: config.yaml for thresholds, environment variables for secrets and paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    cfg: dict
    live_trading: bool
    data_dir: Path
    logs_dir: Path
    opend_host: str
    opend_port: int
    trade_pin: str | None = field(repr=False, default=None)
    telegram_token: str | None = field(repr=False, default=None)
    telegram_chat_id: str | None = None
    internal_api_port: int = 9000

    def __getitem__(self, key: str) -> dict:
        return self.cfg[key]

    @property
    def stop_file(self) -> Path:
        return self.data_dir / "STOP"


def load_settings() -> Settings:
    config_path = Path(os.environ.get("CONFIG_PATH", "/app/config/config.yaml"))
    with config_path.open() as fh:
        cfg = yaml.safe_load(fh)
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    logs_dir = Path(os.environ.get("LOGS_DIR", "/app/logs"))
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        cfg=cfg,
        live_trading=_env_bool("LIVE_TRADING", False),
        data_dir=data_dir,
        logs_dir=logs_dir,
        opend_host=os.environ.get("OPEND_HOST", "127.0.0.1"),
        opend_port=int(os.environ.get("OPEND_PORT", "11111")),
        trade_pin=os.environ.get("MOOMOO_TRADE_PIN") or None,
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
        internal_api_port=int(os.environ.get("INTERNAL_API_PORT", "9000")),
    )
