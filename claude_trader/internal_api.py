"""Internal API on 127.0.0.1 only. The dashboard uses it to read state and to press Halt."""
from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from .util import iso, read_json


class HaltRequest(BaseModel):
    by: str = "dashboard"


def start(settings, journal) -> threading.Thread:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/state")
    def state():
        return read_json(settings.data_dir / "state.json", {})

    @app.post("/halt")
    def halt(req: HaltRequest):
        if not settings.stop_file.exists():
            settings.stop_file.write_text(f"halted by {req.by[:80]} at {iso()}\n")
        journal.event("dashboard", "halt_pressed", by=req.by[:80])
        return {"halted": True}

    config = uvicorn.Config(app, host="127.0.0.1", port=settings.internal_api_port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="internal-api", daemon=True)
    t.start()
    return t
