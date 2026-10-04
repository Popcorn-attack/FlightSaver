from datetime import date

import pytest
from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight

from flightsaver.models import SearchQuery
from flightsaver.providers.base import ProviderError
from flightsaver.providers.google_flights import GoogleFlightsProvider, build_query


def _flight(price, airline, legs):
    return Flights(
        type="CA",
        price=price,
        airlines=[airline],
        flights=[
            SingleFlight(
                Airport(a, a),
                Airport(b, b),
                SimpleDatetime((2026, 12, 20), (h, 0)),
                SimpleDatetime((2026, 12, 21), (h + 1, 30)),
                660,
                "A350",
            )
            for a, b, h in legs
        ],
        carbon=CarbonEmission(0, 0),
    )


def test_converts_results(query):
    def fetch(q):
        return [
            _flight(612, "Air China", [("LHR", "PEK", 9), ("PEK", "PVG", 12)]),
            _flight(0, "Broken", [("LHR", "PVG", 9)]),
        ]

    offers = GoogleFlightsProvider(fetch=fetch).search(query)
    assert len(offers) == 1
    o = offers[0]
    assert o.price == 612 and o.stops == 1 and o.round_trip_price
    assert o.legs[0].departure.hour == 9 and o.flying_minutes == 1320
    assert o.booking_url.startswith("https://www.google.com/travel/flights/search?tfs=")


def test_metro_expands_to_each_pair():
    seen = []

    def fetch(q):
        seen.append(q)
        return []

    q = SearchQuery("LON", "SHANGHAI", date(2026, 12, 20))
    GoogleFlightsProvider(fetch=fetch).search(q)
    assert len(seen) == 6


def test_all_pairs_failing_raises(query):
    def fetch(q):
        raise RuntimeError("blocked")

    with pytest.raises(ProviderError, match="blocked"):
        GoogleFlightsProvider(fetch=fetch).search(query)


def test_build_query_round_trip(query):
    q = build_query(query, "LHR", "PVG")
    assert q.get_trip_type() == "round-trip"
    assert len(q.flight_data) == 2
