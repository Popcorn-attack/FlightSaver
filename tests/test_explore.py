"""Flexible search: date windows, any-city destinations, nonstop requirements."""

from datetime import date, datetime

from flightsaver import nlp
from flightsaver.explore import explore
from flightsaver.models import FlightOffer, Leg
from flightsaver.providers.trip_com import Calendar
from flightsaver.search import SearchResult
from flightsaver.web.quick import quick_search, run_explore
from flightsaver.web.tools import package

TODAY = date(2026, 10, 4)
TEXT = "查一下爱丁堡从十月底开始到11月中的飞国内任何城市的直飞航班信息与价格"


def one_stop(origin, dest, day, price, hub="AMS"):
    t = datetime(day.year, day.month, day.day, 7)
    return FlightOffer(
        "trip_com",
        price,
        "GBP",
        ("KLM",),
        (
            Leg(origin, hub, t, t.replace(hour=9), 80),
            Leg(hub, dest, t.replace(hour=11), t.replace(day=day.day + 1, hour=5), 660),
        ),
        "https://trip",
    )


class FakeTrip:
    """Calendar: one cheap day per route; no route from EDI has nonstop service."""

    def __init__(self):
        self.calls = []

    def explore(self, q):
        self.calls.append((q.origin, q.destination, q.depart))
        cheap_day = {"BEIJING": date(2026, 11, 3), "SHANGHAI": date(2026, 11, 9)}.get(
            q.destination, date(2026, 10, 28)
        )
        prices = {date(2026, 10, d): 400 + d for d in range(20, 32)}
        prices.update({date(2026, 11, d): 380 + d for d in range(1, 20)})
        prices[cheap_day] = 300 if q.destination == "BEIJING" else 320
        cal = Calendar(prices=prices, currency="GBP", has_direct=False)
        return [one_stop(q.origin, "PEK", q.depart, 450)], cal


class FakeFull:
    name = "full"

    def __init__(self, log):
        self.log = log

    def search(self, q):
        self.log.append((q.destination, q.depart))
        return [one_stop(q.origin, "PEK", q.depart, 310)]


def test_user_request_is_understood():
    p = nlp.parse(TEXT, TODAY)
    assert (p.origin, p.destination) == ("EDI", nlp.ANY_CN)
    assert (p.depart, p.depart_until) == (date(2026, 10, 25), date(2026, 11, 15))
    assert p.max_stops == 0 and p.return_date is None


def test_explore_uses_calendar_then_searches_cheapest_day():
    trip, log = FakeTrip(), []
    p = nlp.parse(TEXT, TODAY)
    result, focus, summary = explore(p, trip=trip, providers_factory=lambda: [FakeFull(log)])
    # One Trip.com load per gateway (capped), all on the window's first day.
    assert len(trip.calls) == 6 and {c[2] for c in trip.calls} == {date(2026, 10, 25)}
    # Beijing is cheapest in the window (3 Nov), so only that is searched in full.
    assert (focus.destination, focus.depart) == ("BEIJING", date(2026, 11, 3))
    assert log == [("BEIJING", date(2026, 11, 3))]
    routes = {r["destination"]: r for r in summary["routes"]}
    assert routes["BEIJING"]["cheapest_price"] == 300 and routes["SZX"]["nonstop_seen"] is False


def test_no_nonstop_is_said_plainly_and_fewest_stops_shown():
    trip = FakeTrip()
    out = quick_search(TEXT, search_fn=None, today=TODAY, explore_fn=lambda p, e: _run(p, e, trip))
    msg = out["message"]
    assert "没有直飞" in msg and "EDI" in msg
    assert out["data"]["stops_relaxed"] is True
    assert all(o["stops"] == 1 for o in out["data"]["offers"])
    assert "中国任意城市" in out["understood"] and "之间出发" in out["understood"]


def _run(p, engine, trip):
    import flightsaver.web.quick as quick

    orig = quick.explore.explore
    quick.explore.explore = lambda parsed: orig(
        parsed, trip=trip, providers_factory=lambda: [FakeFull([])]
    )
    try:
        return run_explore(p, engine)
    finally:
        quick.explore.explore = orig


def test_stop_limit_applies_to_every_source():
    direct = FlightOffer(
        "kayak",
        600,
        "GBP",
        ("BA",),
        (Leg("LHR", "PVG", datetime(2026, 12, 1, 12), datetime(2026, 12, 2, 7), 660),),
        "x",
    )
    stop = one_stop("LHR", "PVG", date(2026, 12, 1), 400)
    q = nlp.parse("伦敦飞上海 12月1日 直飞", TODAY)
    from flightsaver.models import SearchQuery

    query = SearchQuery("LHR", "PVG", date(2026, 12, 1), max_stops=0)
    data = package(query, SearchResult(query, [stop, direct], [], []), None)
    assert [o["stops"] for o in data["offers"]] == [0] and not data["stops_relaxed"]
    data = package(query, SearchResult(query, [stop], [], []), None)
    assert data["stops_relaxed"] and data["offers"][0]["stops"] == 1
    assert q.max_stops == 0


def test_new_request_does_not_inherit_old_destination():
    calls = []

    def fake(query, budget, engine, sort="best"):
        calls.append(query.destination)
        return {
            "offers": [],
            "query": {"currency": "GBP", "round_trip_prices": False},
            "total_offers_found": 0,
        }

    old = {"origin": "EDI", "destination": "WUH", "depart_date": "2026-11-01", "lang": "zh"}
    out = quick_search("爱丁堡 11月20日 出发", fake, today=TODAY, context=old)
    assert out["missing"] == ["destination"] and calls == []
    quick_search("11月20日", fake, today=TODAY, context=old)  # a fragment still continues
    assert calls == ["WUH"]


def test_calendar_ignores_no_fare_days():
    from flightsaver.providers.trip_com import parse_calendar

    body = {"lowPriceInCalenderDtoInfoList": [
        {"dDate": 1793404800 - 8 * 3600, "currencyPrice": -1},  # 31 Oct: no fare
        {"dDate": 1793491200 - 8 * 3600, "currencyPrice": 410},  # 1 Nov
    ], "currency": "GBP", "hasDirectFlight": True}
    cal = parse_calendar([body])
    assert cal.cheapest(date(2026, 10, 1), date(2026, 11, 30)) == (date(2026, 11, 1), 410)


def test_sources_are_asked_without_stop_limit():
    trip, log = FakeTrip(), []
    p = nlp.parse(TEXT, TODAY)
    seen = []
    orig = trip.explore

    def spy(q):
        seen.append(q.max_stops)
        return orig(q)

    trip.explore = spy
    result, focus, _ = explore(p, trip=trip, providers_factory=lambda: [FakeFull(log)])
    assert set(seen) == {None}  # so a route without nonstops still yields options
    assert focus.max_stops == 0  # but the user's limit is applied to the merged list
