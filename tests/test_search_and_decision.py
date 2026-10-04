from types import SimpleNamespace

from conftest import make_offer

from flightsaver.decision import DecisionContext, evaluate
from flightsaver.decision.jev import JevEngine
from flightsaver.decision.rules import RuleEngine
from flightsaver.history import PriceHistory
from flightsaver.search import dedupe, search


class FakeProvider:
    def __init__(self, name, offers=None, error=None):
        self.name, self.offers, self.error = name, offers or [], error

    def search(self, query):
        if self.error:
            raise self.error
        return self.offers


def test_dedupe_keeps_cheapest():
    a = make_offer(700)
    b = make_offer(650, source="other")
    c = make_offer(500, airlines=("China Eastern",), stops=1)
    assert [o.price for o in dedupe([a, b, c])] == [500, 650]


def test_search_isolates_provider_errors(query):
    result = search(
        query,
        [FakeProvider("ok", [make_offer(600)]), FakeProvider("bad", error=RuntimeError("blocked"))],
    )
    assert [o.price for o in result.offers] == [600]
    assert result.errors == {"bad": "blocked"}
    assert [link.name for link in result.airline_links] == ["Air China"]
    assert len(result.platform_links) >= 6


def test_rules_budget_and_skip():
    offers = [make_offer(500), make_offer(800, stops=1, dep_hour=6)]
    v = RuleEngine().evaluate(offers, DecisionContext(budget=550))
    assert v[0].action == "buy" and v[1].action == "skip"
    assert v[0].score > v[1].score


def test_rules_history_triggers_buy():
    offers = [make_offer(500)]
    ctx = DecisionContext(history=[700, 650, 620, 680, 640, 600])
    v = RuleEngine().evaluate(offers, ctx)[0]
    assert v.action == "buy"
    assert any("past checks" in r for r in v.reasons)


def test_history_roundtrip(query):
    h = PriceHistory(":memory:")
    from datetime import datetime

    h.record(query, [make_offer(600), make_offer(550, airlines=("X",))], datetime(2026, 1, 1))
    h.record(query, [make_offer(580)], datetime(2026, 1, 2))
    assert h.cheapest_per_run(query) == [550, 580]


def test_jev_engine_maps_answers():
    class FakeClient:
        def system_one(self, state, questions, model=None):
            self.questions = questions
            assert set(state["offers"]) == {"offer_0", "offer_1"}
            return SimpleNamespace(
                choices={
                    "offer_0_action": SimpleNamespace(choice="buy", confidence=0.9),
                    "offer_1_action": SimpleNamespace(choice="skip", confidence=0.7),
                },
                scores={
                    "offer_0_deal": SimpleNamespace(score=3.6),
                    "offer_1_deal": SimpleNamespace(score=0.8),
                },
            )

    client = FakeClient()
    offers = [make_offer(500), make_offer(900, stops=2), make_offer(950, airlines=("Y",))]
    v = JevEngine(client=client, max_offers=2).evaluate(offers, DecisionContext())
    assert [x.action for x in v] == ["buy", "skip", "watch"]
    assert v[0].score == 0.9
    assert client.questions["offer_0_action"].criteria.keys() == {"buy", "watch", "skip"}


def test_jev_falls_back_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    verdicts, note = evaluate("jev", [make_offer(500)], DecisionContext())
    assert verdicts[0].engine == "rules"
    assert "TYPESAFE_API_KEY" in note
