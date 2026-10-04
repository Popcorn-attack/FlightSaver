import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("anthropic")

from flightsaver.web.agent import ChatSession, FlightAgent, system_prompt  # noqa: E402
from flightsaver.web.tools import parse_args, run_search  # noqa: E402

FUTURE = (date.today() + timedelta(days=60)).isoformat()


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, **inp):
    return SimpleNamespace(type="tool_use", id=id_, name="search_flights", input=inp)


class FakeStream:
    def __init__(self, content, stop_reason):
        self.message = SimpleNamespace(content=content, stop_reason=stop_reason)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for block in self.message.content:
                if block.type == "text":
                    yield SimpleNamespace(type="text", text=block.text)

        return gen()

    async def get_final_message(self):
        return self.message


class FakeClient:
    """Replays scripted (content, stop_reason) turns and records requests."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return FakeStream(*self.turns.pop(0))


def collect(agent, session, msg):
    async def run():
        return [e async for e in agent.chat(session, msg)]

    return asyncio.run(run())


def test_parse_args_validates():
    q, budget = parse_args(
        {"origin": "lon", "destination": "shanghai", "depart_date": FUTURE, "budget": 600}
    )
    assert q.origin == "LON" and q.cabin == "economy" and budget == 600
    with pytest.raises(ValueError):
        parse_args({"origin": "PEK", "destination": "PVG", "depart_date": FUTURE})
    with pytest.raises(ValueError):
        parse_args({"origin": "LHR", "destination": "PVG", "depart_date": "2001-01-01"})
    with pytest.raises(ValueError):
        parse_args({"origin": "LHR", "destination": "PVG"})


def test_run_search_shape():
    from conftest import make_offer

    class P:
        name = "fake"

        def search(self, q):
            return [make_offer(500), make_offer(700, airlines=("China Eastern",), stops=1)]

    q, _ = parse_args({"origin": "LHR", "destination": "PVG", "depart_date": FUTURE})
    data = run_search(q, 550, providers=[P()])
    assert [o["verdict"] for o in data["offers"]] == ["buy", "watch"]
    assert data["offers"][1]["route"] == ["LHR", "PEK", "PVG"]
    assert any(link["name"] == "Trip.com" for link in data["platform_links"])
    json.dumps(data)


def test_agent_tool_loop():
    calls = []

    def search_fn(query, budget, engine):
        calls.append((query.origin, query.destination, budget, engine))
        return {"query": {}, "offers": [{"price": 512}], "platform_links": []}

    client = FakeClient(
        [
            (
                [
                    text("Let me search."),
                    tool_use("t1", origin="LON", destination="PVG", depart_date=FUTURE, budget=600),
                ],
                "tool_use",
            ),
            ([text("Cheapest is 512 GBP.")], "end_turn"),
        ]
    )
    session = ChatSession()
    events = collect(FlightAgent(client=client, search_fn=search_fn), session, "伦敦飞上海")

    assert calls == [("LON", "PVG", 600.0, "rules")]
    kinds = [e["type"] for e in events]
    assert kinds == ["text", "status", "results", "text", "done"]
    # History: user, assistant(tool_use), user(tool_result), assistant(final)
    assert [m["role"] for m in session.messages] == ["user", "assistant", "user", "assistant"]
    result = session.messages[2]["content"][0]
    assert result["tool_use_id"] == "t1" and "512" in result["content"]
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["tools"][0]["name"] == "search_flights"


def test_agent_reports_invalid_input_to_model():
    client = FakeClient(
        [
            ([tool_use("t1", origin="PEK", destination="PVG", depart_date=FUTURE)], "tool_use"),
            ([text("Domestic routes are not supported yet.")], "end_turn"),
        ]
    )
    session = ChatSession()
    events = collect(FlightAgent(client=client, search_fn=lambda *a: {}), session, "北京飞上海")
    result = session.messages[2]["content"][0]
    assert result["is_error"] and "UK <-> China" in result["content"]
    assert "results" not in [e["type"] for e in events]


def test_agent_survives_search_failure():
    def boom(*a):
        raise RuntimeError("blocked")

    client = FakeClient(
        [
            ([tool_use("t1", origin="LHR", destination="PVG", depart_date=FUTURE)], "tool_use"),
            ([text("Search failed, try the links.")], "end_turn"),
        ]
    )
    session = ChatSession()
    collect(FlightAgent(client=client, search_fn=boom), session, "hi")
    assert "blocked" in session.messages[2]["content"][0]["content"]


def test_system_prompt_has_date_and_airports():
    s = system_prompt(date(2026, 10, 4))
    assert "2026-10-04" in s and "LHR" in s and "SHANGHAI=PVG/SHA" in s


def test_app_streams_events(monkeypatch):
    from fastapi.testclient import TestClient

    from flightsaver.web.app import create_app

    client = FakeClient([([text("你好！")], "end_turn")])
    app = create_app(FlightAgent(client=client, search_fn=lambda *a: {}))
    tc = TestClient(app)
    assert "FlightSaver" in tc.get("/").text
    res = tc.post("/api/chat", json={"message": "hi"})
    events = [json.loads(line[6:]) for line in res.text.split("\n\n") if line.startswith("data: ")]
    assert events[0]["type"] == "session"
    assert {"type": "text", "text": "你好！"} in events and events[-1] == {"type": "done"}


def test_app_token(monkeypatch):
    from fastapi.testclient import TestClient

    from flightsaver.web.app import create_app

    monkeypatch.setenv("FLIGHTSAVER_ACCESS_TOKEN", "s3cret")
    app = create_app(
        FlightAgent(client=FakeClient([([text("ok")], "end_turn")]), search_fn=lambda *a: {})
    )
    tc = TestClient(app)
    assert tc.get("/api/config").json() == {"token_required": True}
    assert tc.post("/api/chat", json={"message": "hi"}).status_code == 401
    ok = tc.post("/api/chat", json={"message": "hi"}, headers={"X-Access-Token": "s3cret"})
    assert ok.status_code == 200
