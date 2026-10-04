"""FastAPI server for the chat website.

uv sync --extra web
ANTHROPIC_API_KEY=... uv run flightsaver web     # http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from flightsaver.history import DEFAULT_PATH, PriceHistory
from flightsaver.web.agent import MODEL, ChatSession, FlightAgent
from flightsaver.web.quick import quick_search
from flightsaver.web.tools import run_search

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_SESSIONS = 200


class AiBudget:
    """Daily cap on AI chat messages (FLIGHTSAVER_AI_DAILY_LIMIT, 0 disables AI)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.day = ""
        self.used = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _roll(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self.day:
            self.day, self.used = today, 0

    def remaining(self) -> int:
        self._roll()
        return max(self.limit - self.used, 0)

    def take(self) -> bool:
        if self.remaining() <= 0:
            return False
        self.used += 1
        return True

    def record(self, usage) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        log.info("AI usage so far: %s in / %s out tokens", self.input_tokens, self.output_tokens)


class SearchRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    context: dict[str, str | None] | None = None  # previous route/dates, for follow-ups
    engine: str = Field(default="rules", pattern="^(rules|jev)$")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    engine: str = Field(default="rules", pattern="^(rules|jev)$")


def _search_with_history(query, budget, engine, sort="best"):
    # SQLite connections are per-thread; searches run in a worker thread.
    history = PriceHistory(os.environ.get("FLIGHTSAVER_HISTORY", str(DEFAULT_PATH)))
    try:
        return run_search(query, budget, engine, history=history, sort=sort)
    finally:
        history.close()


def create_app(agent: FlightAgent | None = None) -> FastAPI:
    app = FastAPI(title="FlightSaver")
    sessions: OrderedDict[str, ChatSession] = OrderedDict()
    token = os.environ.get("FLIGHTSAVER_ACCESS_TOKEN", "")
    state = {"agent": agent}
    budget = AiBudget(int(os.environ.get("FLIGHTSAVER_AI_DAILY_LIMIT", "20")))

    def get_agent() -> FlightAgent:
        # Created lazily so the page loads even before an API key is configured.
        if state["agent"] is None:
            state["agent"] = FlightAgent(search_fn=_search_with_history)
        state["agent"].on_usage = budget.record
        return state["agent"]

    def check_token(given: str | None) -> None:
        if token and not hmac.compare_digest(given or "", token):
            raise HTTPException(status_code=401, detail="access token required")

    def get_session(session_id: str | None) -> tuple[str, ChatSession]:
        if session_id and session_id in sessions:
            sessions.move_to_end(session_id)
            return session_id, sessions[session_id]
        session_id = uuid.uuid4().hex
        sessions[session_id] = ChatSession()
        while len(sessions) > MAX_SESSIONS:
            sessions.popitem(last=False)
        return session_id, sessions[session_id]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    def config() -> dict:
        return {
            "token_required": bool(token),
            "ai_enabled": budget.limit > 0,
            "ai_remaining": budget.remaining(),
            "ai_model": MODEL,
        }

    @app.post("/api/search")
    async def search(req: SearchRequest, x_access_token: str | None = Header(default=None)):
        """Quick mode: local parsing and a template summary, no Claude call."""
        check_token(x_access_token)
        try:
            return await asyncio.to_thread(
                quick_search, req.message, _search_with_history, req.engine, None, req.context
            )
        except Exception:
            log.exception("quick search failed")
            raise HTTPException(status_code=500, detail="search failed") from None

    @app.post("/api/chat")
    async def chat(req: ChatRequest, x_access_token: str | None = Header(default=None)):
        check_token(x_access_token)
        session_id, session = get_session(req.session_id)

        async def events():
            def sse(event: dict) -> str:
                return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

            yield sse({"type": "session", "session_id": session_id})
            if not budget.take():
                msg = "AI 模式今日次数已用完（或未开启），请用快速模式。"
                yield sse({"type": "error", "text": msg})
                return
            yield sse({"type": "ai_remaining", "value": budget.remaining()})
            if session.lock.locked():
                yield sse({"type": "error", "text": "Still answering the previous message."})
                return
            async with session.lock:
                try:
                    async for event in get_agent().chat(session, req.message, req.engine):
                        yield sse(event)
                except anthropic.AuthenticationError:
                    yield sse(
                        {
                            "type": "error",
                            "text": "Claude API key is missing or invalid "
                            "(set ANTHROPIC_API_KEY on the server).",
                        }
                    )
                except anthropic.RateLimitError:
                    yield sse(
                        {
                            "type": "error",
                            "text": "Rate limited by the Claude API; try again shortly.",
                        }
                    )
                except anthropic.APIStatusError as exc:
                    yield sse({"type": "error", "text": f"Claude API error {exc.status_code}."})
                except anthropic.APIConnectionError:
                    yield sse({"type": "error", "text": "Could not reach the Claude API."})
                except Exception as exc:
                    if isinstance(exc, TypeError) and "authentication" in str(exc):
                        # Raised by the SDK when no credentials are configured at all.
                        yield sse(
                            {
                                "type": "error",
                                "text": "No Claude API key configured "
                                "(set ANTHROPIC_API_KEY on the server).",
                            }
                        )
                        return
                    log.exception("chat turn failed")
                    yield sse({"type": "error", "text": "Something went wrong; please try again."})

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/api/reset")
    def reset(body: dict, x_access_token: str | None = Header(default=None)) -> dict:
        check_token(x_access_token)
        sessions.pop(str(body.get("session_id", "")), None)
        return {"ok": True}

    return app


app = create_app()
