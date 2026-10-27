"""Claude Trading Desk web dashboard.

Read-only view of what the bot publishes, plus one Halt button. It never talks to OpenD or moomoo.
"""
from __future__ import annotations

import csv
import hashlib
import hmac

import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA = Path(os.environ.get("DATA_DIR", "/app/data"))
LOGS = Path(os.environ.get("LOGS_DIR", "/app/logs"))
CONFIG = Path(os.environ.get("CONFIG_PATH", "/app/config/config.yaml"))
BOT_API = os.environ.get("BOT_API", "http://127.0.0.1:9000")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
SESSION_HOURS = 12
STATIC = Path(__file__).parent / "static"
ET = ZoneInfo("America/New_York")
COOKIE = "ctd_session"

_password = os.environ.get("DASHBOARD_PASSWORD", "")
# Keep only a salted hash in memory.
_salt = secrets.token_bytes(16)
_pw_hash = hashlib.pbkdf2_hmac("sha256", _password.encode(), _salt, 200_000) if _password else None
_sessions: dict[str, float] = {}
_failures: dict[str, deque] = defaultdict(deque)
MAX_FAILS, FAIL_WINDOW = 5, 900

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _authed(request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    exp = _sessions.get(token or "")
    if not exp or exp < time.time():
        _sessions.pop(token or "", None)
        return False
    return True


def require(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="login required")


class Login(BaseModel):
    password: str


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    ip = _client_ip(request)
    q = _failures[ip]
    while q and q[0] < time.time() - FAIL_WINDOW:
        q.popleft()
    if len(q) >= MAX_FAILS:
        raise HTTPException(status_code=429, detail="too many attempts; wait 15 minutes")
    if _pw_hash is None:
        raise HTTPException(status_code=503, detail="DASHBOARD_PASSWORD is not set")
    attempt = hashlib.pbkdf2_hmac("sha256", body.password.encode(), _salt, 200_000)
    if not hmac.compare_digest(attempt, _pw_hash):
        q.append(time.time())
        raise HTTPException(status_code=401, detail="wrong password")
    q.clear()
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_HOURS * 3600
    response.set_cookie(COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True,
                        secure=COOKIE_SECURE, samesite="strict", path="/")
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    _sessions.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"authenticated": _authed(request)}


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


@app.get("/api/state")
def state(request: Request):
    require(request)
    st = _read_json(DATA / "state.json", None)
    if st is None:
        return {"available": False}
    age = (datetime.now().astimezone() - datetime.fromisoformat(st["updated_at"])).total_seconds()
    st["available"] = True
    st["stale_seconds"] = round(age)
    return st


def _trades() -> list[dict]:
    path = LOGS / "trades.csv"
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


@app.get("/api/trades")
def trades(request: Request):
    require(request)
    rows = _trades()
    pnls = [float(r["pnl_usd"]) for r in rows if r.get("pnl_usd")]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    stats = {
        "trades": len(pnls),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "total_fees": round(sum(float(r.get("fees_usd") or 0) for r in rows), 2),
        "total_pnl": round(sum(pnls), 2),
    }
    return {"stats": stats, "trades": list(reversed(rows))}


@app.get("/api/trades.csv")
def trades_csv(request: Request):
    require(request)
    path = LOGS / "trades.csv"
    if not path.exists():
        return PlainTextResponse("", media_type="text/csv")
    return FileResponse(path, media_type="text/csv", filename="trades.csv")


@app.get("/api/activity")
def activity(request: Request, agent: str = "", symbol: str = "", days: int = 2, limit: int = 400):
    require(request)
    days = max(1, min(days, 14))
    limit = max(10, min(limit, 2000))
    today = datetime.now(ET).date()
    out: list[dict] = []
    for i in range(days):
        path = LOGS / f"{(today - timedelta(days=i)).isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open() as fh:
            lines = fh.readlines()
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if agent and e.get("agent") != agent:
                continue
            if symbol and symbol.upper() not in str(e.get("symbol", "")).upper():
                continue
            out.append(e)
            if len(out) >= limit:
                return {"events": out}
    return {"events": out}


@app.get("/api/funnel")
def funnel(request: Request, days: int = 7):
    require(request)
    days = max(1, min(days, 30))
    today = datetime.now(ET).date()
    scans = []
    for i in range(days):
        path = LOGS / f"{(today - timedelta(days=i)).isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open() as fh:
            for line in fh:
                if '"kind": "funnel"' not in line:
                    continue
                try:
                    scans.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    scans.sort(key=lambda e: e["ts"], reverse=True)
    return {"scans": scans}


@app.get("/api/backtest")
def backtest(request: Request):
    require(request)
    return _read_json(DATA / "backtest.json", {"available": False})


@app.get("/api/config")
def config(request: Request):
    require(request)
    text = CONFIG.read_text() if CONFIG.exists() else "(config not mounted)"
    return {"config_yaml": text, "live_trading": os.environ.get("LIVE_TRADING", "false")}


@app.post("/api/halt")
def halt(request: Request):
    require(request)
    if request.headers.get("x-requested-with") != "claude-dashboard":
        raise HTTPException(status_code=400, detail="missing header")
    try:
        r = httpx.post(f"{BOT_API}/halt", json={"by": f"dashboard {_client_ip(request)}"}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"bot did not answer: {type(exc).__name__}")
    return {"halted": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
