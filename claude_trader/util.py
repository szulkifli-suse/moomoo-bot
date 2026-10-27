"""Small shared helpers: clocks, atomic JSON files."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_et() -> datetime:
    return datetime.now(ET)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_utc()).isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open() as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Write via a temp file and rename, so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, default=str, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
