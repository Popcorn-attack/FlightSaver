"""Parsers against real payloads captured by .github/workflows/probe.yml."""

import gzip
import json
from datetime import date
from pathlib import Path

import pytest

from flightsaver import fx
from flightsaver.models import SearchQuery
from flightsaver.providers import ctrip, google_flights, kayak, trip_com
from flightsaver.providers.browser import Captured, parse_sse
from flightsaver.search import search

LIVE = Path(__file__).parent / "fixtures" / "live"


def load(name):
    path = LIVE / f"{name}.json.gz"
    if not path.exists():
        pytest.skip(f"{name} fixture not captured")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _body(c):
    # The browser decodes event streams to their last JSON event; fixtures keep raw text.
    if "event-stream" in c.get("ctype", "") and isinstance(c["body"], str):
        events = parse_sse(c["body"])
        return events[-1] if events else None
    return c["body"]


def bodies(name):
    return [_body(c) for c in load(name)["captured"]]


def first_date(offers):
    return min(o.departure.date() for o in offers)


OW = SearchQuery("LHR", "PVG", date(2026, 12, 3))
RT = SearchQuery("LHR", "PVG", date(2026, 12, 3), date(2026, 12, 17))


def check_offers(offers, src, currency):
    assert len(offers) >= 10
    for o in offers:
        assert o.source == src and o.currency == currency and o.price > 0
        assert o.legs and o.airlines
        for a, b in zip(o.legs, o.legs[1:], strict=False):
            assert a.departure < b.departure
        assert o.legs[0].from_airport in {"LHR", "LGW", "STN", "LTN", "LCY", "SEN"}
        assert o.legs[-1].to_airport in {"PVG", "SHA"}


def test_kayak_one_way():
    offers = kayak.parse(bodies("kayak_ow"), OW, "https://kayak")
    check_offers(offers, "kayak", "GBP")
    cheapest = min(offers, key=lambda o: o.price)
    assert 100 < cheapest.price < 2000
    assert all(o.booking_url.startswith("https://www.kayak.co.uk/flights/") for o in offers)
    assert any(o.stops == 0 for o in offers)
    assert any(leg.flight_no for o in offers for leg in o.legs)


def test_kayak_round_trip_prices_cover_both_ways():
    ow = min(o.price for o in kayak.parse(bodies("kayak_ow"), OW, ""))
    offers = kayak.parse(bodies("kayak_rt"), RT, "")
    check_offers(offers, "kayak", "GBP")
    assert all(o.round_trip_price for o in offers)
    assert min(o.price for o in offers) > ow


def test_ctrip_one_way_and_round_trip():
    ow = ctrip.parse(bodies("ctrip_ow"), OW, "https://ctrip")
    rt = ctrip.parse(bodies("ctrip_rt"), RT, "https://ctrip")
    check_offers(ow, "ctrip", "CNY")
    check_offers(rt, "ctrip", "CNY")
    assert min(o.price for o in rt) > min(o.price for o in ow)
    # Airline names are normalised to English where known.
    assert any(
        n in {"Air China", "China Eastern", "China Southern"} for o in ow for n in o.airlines
    )
    # Duplicate itineraries across streamed responses are merged.
    keys = [o.itinerary_key() for o in ow]
    assert len(keys) == len(set(keys))


def test_airport_change_detected():
    offers = kayak.parse(bodies("kayak_ow"), OW, "") + ctrip.parse(bodies("ctrip_ow"), OW, "")
    changes = [o for o in offers if o.airport_change]
    assert len(changes) < len(offers) / 2


def test_trip_com_one_way_and_round_trip():
    ow = trip_com.parse(bodies("trip_com_ow"), OW, "https://trip")
    rt = trip_com.parse(bodies("trip_com_rt"), RT, "https://trip")
    check_offers(ow, "trip_com", "GBP")
    check_offers(rt, "trip_com", "GBP")
    assert min(o.price for o in rt) > min(o.price for o in ow)
    assert any(o.stops == 0 and "Air China" in o.airlines for o in ow)


def test_google_shopping_results():
    texts = [c["body"] for c in load("google_ow")["captured"] if "GetShoppingResults" in c["url"]]
    offers = google_flights.parse_shopping(texts, OW, "https://google")
    assert len(offers) >= 5
    for o in offers:
        assert o.source == "google_flights" and o.price > 0 and o.legs
        assert o.legs[0].from_airport == "LHR" and o.legs[-1].to_airport in {
            "PVG",
            "SHA",
            "PKX",
            "PEK",
            "SZX",
            "CAN",
        }
        assert o.departure.year == 2026
    assert any(leg.flight_no for o in offers for leg in o.legs)


def test_google_falls_back_to_browser():
    texts = [c["body"] for c in load("google_ow")["captured"] if "GetShoppingResults" in c["url"]]

    def blocked(q):
        raise RuntimeError("IndexError from consent page")

    def capture(url, match, done, timeout, locale="en-GB", **kw):
        assert "google.com/travel/flights" in url
        return [Captured("x" + google_flights.SHOPPING, 200, None, t, 0.0) for t in texts]

    p = google_flights.GoogleFlightsProvider(fetch=blocked, capture=capture)
    assert len(p.search(OW)) >= 5


def test_ctrip_finished_flag():
    assert ctrip._finished(bodies("ctrip_ow"))
    assert not ctrip._finished([])


def test_providers_with_recorded_capture_merge_in_gbp():
    fx.set_rates_for_tests({"GBP": 0.85, "CNY": 8.2})

    def replay(name):
        def capture(url, match, done, timeout, locale="en-GB", **kw):
            caps = [
                Captured(c["url"], c["status"], c["post"], _body(c), 0.0)
                for c in load(name)["captured"]
                if match(c["url"])
            ]
            assert done(caps), "provider's done() should accept the full recording"
            return caps

        return capture

    result = search(
        OW,
        [
            kayak.KayakProvider(capture=replay("kayak_ow")),
            ctrip.CtripProvider(capture=replay("ctrip_ow")),
            trip_com.TripComProvider(capture=replay("trip_com_ow")),
        ],
    )
    assert result.errors == {}
    assert {o.source for o in result.offers} == {"kayak", "ctrip", "trip_com"}
    assert all(o.currency == "GBP" for o in result.offers)
    converted = [o for o in result.offers if o.original_currency == "CNY"]
    assert converted and all(o.original_price > o.price for o in converted)
    prices = [o.price for o in result.offers]
    assert prices == sorted(prices)


def test_kayak_airline_direct_fares_and_self_transfer():
    offers = kayak.parse(bodies("kayak_ow"), OW, "")
    direct = [o for o in offers if o.direct_price]
    assert direct, "KAYAK lists airline websites among booking options"
    for o in direct:
        assert o.direct_price >= o.price and o.direct_seller
    assert any(o.self_transfer for o in offers)
    assert all(o.total_minutes >= o.flying_minutes for o in offers)


def test_lean_host_allowlist_and_crash_retry(monkeypatch):
    from flightsaver.providers import browser

    assert browser._host_allowed("https://ak-d.tripcdn.com/x.js", ("tripcdn.com",))
    assert not browser._host_allowed("https://www.googletagmanager.com/gtm.js", ("trip.com",))
    assert browser._host_allowed("https://anything.example", ())

    calls = []

    def once(*a):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Page.wait_for_timeout: Page crashed")
        return ["ok"]

    monkeypatch.setattr(browser, "_capture_once", once)
    assert browser.capture_json("u", lambda u: True, lambda c: True) == ["ok"]
    assert len(calls) == 2
