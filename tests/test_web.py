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
    # Default is the cheap model, with no effort/fallback extras (Haiku rejects them).
    assert req["model"] == "claude-haiku-4-5" and "fallbacks" not in req
    assert req["max_tokens"] <= 2000
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
    cfg = tc.get("/api/config").json()
    assert cfg["token_required"] is True and cfg["ai_enabled"] is True
    assert tc.post("/api/chat", json={"message": "hi"}).status_code == 401
    ok = tc.post("/api/chat", json={"message": "hi"}, headers={"X-Access-Token": "s3cret"})
    assert ok.status_code == 200


def test_request_options_per_model():
    from flightsaver.web.agent import request_options

    assert request_options("claude-haiku-4-5") == {}
    opts = request_options("claude-opus-5-5")
    assert opts["output_config"] == {"effort": "low"} and opts["fallbacks"] == "default"


def test_compact_for_model_drops_links():
    from conftest import make_offer

    class P:
        name = "fake"

        def search(self, q):
            return [make_offer(500 + i, dep_hour=6 + i) for i in range(9)]

    q, _ = parse_args({"origin": "LHR", "destination": "PVG", "depart_date": FUTURE})
    from flightsaver.web.tools import compact_for_model

    small = compact_for_model(run_search(q, None, providers=[P()]))
    assert len(small["offers"]) == 5
    text = json.dumps(small)
    assert "http" not in text and "platform_links" not in text


def test_history_is_reset_when_long():
    from flightsaver.web.agent import MAX_HISTORY

    session = ChatSession(messages=[{"role": "user", "content": "x"}] * MAX_HISTORY)
    client = FakeClient([([text("ok")], "end_turn")])
    events = collect(FlightAgent(client=client, search_fn=lambda *a: {}), session, "hi")
    assert events[0]["type"] == "status"
    assert len(client.requests[0]["messages"]) == 1


def test_quick_search_endpoint_needs_no_ai(monkeypatch):
    from conftest import make_offer
    from fastapi.testclient import TestClient

    import flightsaver.web.app as web_app

    calls = []

    def fake_search(query, budget, engine):
        calls.append((query.origin, query.destination, query.depart.isoformat(), budget))

        class P:
            name = "fake"

            def search(self, q):
                return [make_offer(480), make_offer(620, stops=1, dep_hour=7)]

        return run_search(query, budget, engine, providers=[P()])

    monkeypatch.setattr(web_app, "_search_with_history", fake_search)
    client = FakeClient([])  # any AI call would fail: no scripted turns
    tc = TestClient(web_app.create_app(FlightAgent(client=client, search_fn=fake_search)))
    res = tc.post("/api/search", json={"message": f"伦敦飞上海 {FUTURE} 预算500镑"}).json()
    assert calls == [("LON", "SHANGHAI", FUTURE, 500.0)]
    assert "最低 480 GBP" in res["message"] and res["data"]["offers"]
    assert client.requests == []

    res = tc.post("/api/search", json={"message": "我想去上海"}).json()
    assert res["missing"] == ["origin", "depart"] and "出发城市" in res["message"]


def test_ai_daily_limit(monkeypatch):
    from fastapi.testclient import TestClient

    from flightsaver.web.app import create_app

    monkeypatch.setenv("FLIGHTSAVER_AI_DAILY_LIMIT", "1")
    client = FakeClient([([text("ok")], "end_turn")])
    tc = TestClient(create_app(FlightAgent(client=client, search_fn=lambda *a: {})))
    first = tc.post("/api/chat", json={"message": "hi"}).text
    second = tc.post("/api/chat", json={"message": "hi"}).text
    assert '"done"' in first and "次数已用完" in second
    assert len(client.requests) == 1
    assert tc.get("/api/config").json()["ai_remaining"] == 0


def test_quick_follow_up_uses_previous_route():
    from flightsaver.web.quick import quick_search

    seen = []

    def fake(query, budget, engine):
        seen.append((query.origin, query.destination, query.depart.isoformat()))
        return {
            "offers": [],
            "query": {"currency": "GBP", "round_trip_prices": False},
            "total_offers_found": 0,
        }

    first = quick_search("伦敦飞上海", fake)
    assert first["missing"] == ["depart"] and not seen
    second = quick_search(f"{FUTURE}", fake, context=first["parsed"])
    assert seen == [("LON", "SHANGHAI", FUTURE)] and "没有抓到" in second["message"]
    # A new route replaces the old one.
    quick_search(f"曼彻斯特飞北京 {FUTURE}", fake, context=second["parsed"])
    assert seen[-1][:2] == ("MAN", "BEIJING")
