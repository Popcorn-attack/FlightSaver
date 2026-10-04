"""Journey time with layovers, and how it shapes ranking."""

from datetime import date, datetime

from flightsaver.decision import DecisionContext
from flightsaver.decision.rules import RuleEngine
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.web.tools import run_search


def offer(price, legs, **kw):
    """legs: (from, to, dep 'MM-DD HH:MM', arr 'MM-DD HH:MM', minutes)"""

    def t(s):
        return datetime.strptime("2026-" + s, "%Y-%m-%d %H:%M")

    return FlightOffer(
        "test",
        price,
        "GBP",
        ("X",),
        tuple(Leg(a, b, t(d), t(r), m) for a, b, d, r, m in legs),
        "https://x",
        **kw,
    )


DIRECT = offer(600, [("LHR", "PVG", "12-01 12:00", "12-02 07:00", 660)])
# 13h flying, but 9h wait in Beijing.
LONG_WAIT = offer(
    450,
    [
        ("LHR", "PEK", "12-01 12:00", "12-02 06:00", 600),
        ("PEK", "PVG", "12-02 15:00", "12-02 17:00", 120),
    ],
)
# Same fare and flights, quick 2h connection.
SHORT_WAIT = offer(
    450,
    [
        ("LHR", "PEK", "12-01 12:00", "12-02 06:00", 600),
        ("PEK", "PVG", "12-02 08:00", "12-02 10:00", 120),
    ],
)


def test_layovers_use_local_times_at_the_connection():
    assert LONG_WAIT.layovers == [("PEK", 540)]
    assert LONG_WAIT.total_minutes == 600 + 120 + 540
    assert DIRECT.layovers == [] and DIRECT.total_minutes == 660


def test_long_layover_loses_to_short_one_at_same_price():
    v_long, v_short = RuleEngine().evaluate([LONG_WAIT, SHORT_WAIT], DecisionContext())
    assert v_short.score > v_long.score
    assert any("long layover 9h00 at PEK" in r for r in v_long.reasons)
    assert any("incl. 2h00 connecting" in r for r in v_short.reasons)


def test_time_is_traded_against_price():
    # Direct is GBP 150 dearer but saves 2h; long-wait is GBP 150 cheaper but 10h slower.
    v = RuleEngine().evaluate([DIRECT, SHORT_WAIT, LONG_WAIT], DecisionContext())
    scores = {name: x.score for name, x in zip(["direct", "short", "long"], v, strict=True)}
    assert scores["long"] < scores["direct"] and scores["long"] < scores["short"]


def test_tight_connection_and_self_transfer_are_penalised():
    tight = offer(
        450,
        [
            ("LHR", "PEK", "12-01 12:00", "12-02 06:00", 600),
            ("PEK", "PVG", "12-02 06:40", "12-02 08:40", 120),
        ],
    )
    self_t = offer(
        450,
        [
            ("LHR", "PEK", "12-01 12:00", "12-02 06:00", 600),
            ("PEK", "PVG", "12-02 08:00", "12-02 10:00", 120),
        ],
        self_transfer=True,
    )
    v = RuleEngine().evaluate([SHORT_WAIT, tight, self_t], DecisionContext())
    assert v[0].score > v[1].score and v[0].score > v[2].score
    assert any("tight connection" in r for r in v[1].reasons)
    assert any("self-transfer" in r for r in v[2].reasons)


def test_run_search_sorts_three_ways():
    class P:
        name = "p"

        def search(self, q):
            return [LONG_WAIT, DIRECT, SHORT_WAIT]

    q = SearchQuery("LHR", "PVG", date(2026, 12, 1))

    def order(sort):
        data = run_search(q, None, providers=[P()], sort=sort)
        return [(o["price"], o["total_minutes"]) for o in data["offers"]]

    assert order("cheapest")[0][0] == 450 and order("cheapest")[0][1] == 840
    assert order("fastest")[0] == (600, 660)
    best = run_search(q, None, providers=[P()])["offers"]
    assert best[0]["total_minutes"] != 1260  # the 9h-wait option is never "best"
    assert best[0]["layovers"] in ([], [{"airport": "PEK", "minutes": 120}])
