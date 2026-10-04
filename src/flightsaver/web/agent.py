"""Claude-driven chat loop: understand the request, call search_flights, answer.

Yields small event dicts that the web server streams to the browser:
  {"type": "text", "text": ...}          assistant text as it is generated
  {"type": "status", "text": ...}        progress ("searching LHR -> PVG ...")
  {"type": "results", "data": {...}}     a search_flights result to render as cards
  {"type": "error", "text": ...}
  {"type": "done"}
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date

import anthropic

from flightsaver.airports import CHINA_AIRPORTS, METROS, UK_AIRPORTS
from flightsaver.web.tools import SEARCH_TOOL, SORTS, compact_for_model, parse_args, run_search

# Haiku is the cheapest model and handles "parse the request, call one tool,
# summarise" well. Set FLIGHTSAVER_MODEL to use a larger one.
MODEL = os.environ.get("FLIGHTSAVER_MODEL", "claude-haiku-4-5")
MAX_STEPS = 4  # model calls per user message
MAX_TOKENS = 1500  # replies are short summaries
MAX_HISTORY = 12  # messages kept before the context is reset, to bound input tokens
MAX_JSON_RETRIES = 2
# Models that accept output_config.effort and server-side refusal fallbacks.
_EFFORT_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


def request_options(model: str) -> dict:
    """Per-model extras: cheap effort and fallbacks where supported, nothing on Haiku."""
    if model.startswith(_EFFORT_MODELS):
        return {
            "output_config": {"effort": "low"},
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
    return {}


_AIRPORTS = "\n".join(
    [
        f"UK: {', '.join(f'{c} {n}' for c, n in UK_AIRPORTS.items())}",
        f"China: {', '.join(f'{c} {n}' for c, n in CHINA_AIRPORTS.items())}",
        "Metro codes: " + ", ".join(c + "=" + "/".join(a) for c, a in METROS.items()),
    ]
)

SYSTEM_TEMPLATE = """You are FlightSaver, a flight search assistant for trips between the UK and \
China (mainland and Hong Kong). Today is {today}.

How to work:
- Turn the user's request into search_flights calls. Resolve relative dates ("next Friday", \
"国庆", "Christmas") against today's date and say which dates you used.
- If the route or the departure date is genuinely unclear, ask one short question instead of \
guessing. Default to 1 adult, economy, GBP unless the user says otherwise.
- Call search_flights at most twice per message (each search is slow); for flexible dates pick \
the most likely date and say the user can ask for others.
- Only quote prices, times and airlines that appear in tool results. Never invent fares.
- Summarise the best 2-3 options in a few short lines (price, airline, stops, verdict). The \
page already shows full result cards and booking links, so do not paste URLs.
- FlightSaver only searches and links to booking sites; it does not book or take payment.
- Domestic China routes and routes outside UK <-> China are not supported yet; say so.
- If a source failed or returned nothing, tell the user and point them to the platform links.
- Reply in the user's language (Chinese or English), concisely.

Supported airports:
{airports}"""


def system_prompt(today: date | None = None) -> str:
    return SYSTEM_TEMPLATE.format(today=(today or date.today()).isoformat(), airports=_AIRPORTS)


SearchFn = Callable[..., dict]


@dataclass
class ChatSession:
    """One browser conversation. History is append-only, as the API expects."""

    messages: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _describe(raw: dict) -> str:
    trip = f"{raw.get('origin', '?')} → {raw.get('destination', '?')} {raw.get('depart_date', '')}"
    if raw.get("return_date"):
        trip += f" / {raw['return_date']}"
    return trip


class FlightAgent:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        search_fn: SearchFn = run_search,
        model: str = MODEL,
        on_usage: Callable | None = None,
    ) -> None:
        self.client = client or anthropic.AsyncAnthropic()
        self.search_fn = search_fn
        self.model = model
        self.on_usage = on_usage

    async def _call_tool(self, block, engine: str) -> tuple[dict, dict | None]:
        """Run one tool_use block. Returns (tool_result, data for the page or None)."""
        if block.name != SEARCH_TOOL["name"]:
            return self._error(block.id, f"unknown tool {block.name}"), None
        try:
            query, budget = parse_args(block.input)
        except (ValueError, TypeError) as exc:
            return self._error(block.id, f"invalid input: {exc}"), None
        try:
            sort = block.input.get("sort") if block.input.get("sort") in SORTS else "best"
            data = await asyncio.to_thread(self.search_fn, query, budget, engine, sort)
        except Exception as exc:  # a failed search must not break the conversation
            return self._error(block.id, f"search failed: {exc}"), None
        # The model gets a trimmed summary; the page renders the full result.
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": json.dumps(compact_for_model(data), ensure_ascii=False),
        }, data

    @staticmethod
    def _error(tool_use_id: str, message: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "is_error": True,
            "content": message,
        }

    async def chat(
        self, session: ChatSession, user_text: str, engine: str = "rules"
    ) -> AsyncIterator[dict]:
        if len(session.messages) >= MAX_HISTORY:
            session.messages.clear()
            yield {"type": "status", "text": "Long conversation: earlier context cleared."}
        session.messages.append({"role": "user", "content": user_text})
        system = [{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}]
        json_retries = 0
        steps = 0
        while steps < MAX_STEPS:
            steps += 1
            try:
                async with self.client.beta.messages.stream(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    tools=[SEARCH_TOOL],
                    messages=session.messages,
                    **request_options(self.model),
                ) as stream:
                    async for event in stream:
                        if event.type == "text":
                            yield {"type": "text", "text": event.text}
                    response = await stream.get_final_message()
                json_retries = 0
                if self.on_usage is not None and getattr(response, "usage", None) is not None:
                    self.on_usage(response.usage)
            except ValueError:
                # Tool input JSON the SDK could not parse; nothing was appended, so retry.
                json_retries += 1
                if json_retries > MAX_JSON_RETRIES:
                    yield {"type": "error", "text": "The model produced an unreadable tool call."}
                    return
                steps -= 1
                continue

            session.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                yield {"type": "error", "text": "The request was declined."}
                return
            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                yield {"type": "done"}
                return

            if response.stop_reason == "max_tokens":
                # A truncated tool input parses as a partial object; answer it with errors so
                # the history stays valid, and let the model retry.
                results = [
                    self._error(b.id, "tool input was truncated; try again") for b in tool_uses
                ]
            else:
                for b in tool_uses:
                    if isinstance(b.input, dict):
                        yield {"type": "status", "text": f"Searching {_describe(b.input)} …"}
                pairs = await asyncio.gather(*(self._call_tool(b, engine) for b in tool_uses))
                results = []
                for result, data in pairs:
                    results.append(result)
                    if data is not None:
                        yield {"type": "results", "data": data}
            # All results go back in one user message.
            session.messages.append({"role": "user", "content": results})

        yield {"type": "error", "text": "Stopped after too many steps; please narrow the request."}
