from datetime import date, datetime

import pytest

from flightsaver.models import FlightOffer, Leg, SearchQuery


@pytest.fixture
def query():
    return SearchQuery("LHR", "PVG", date(2026, 12, 20), date(2027, 1, 5))


def make_offer(price, airlines=("Air China",), stops=0, source="google_flights", dep_hour=10):
    legs = []
    airports = ["LHR", *(["PEK"] * stops), "PVG"]
    for i in range(stops + 1):
        dep = datetime(2026, 12, 20, dep_hour + i * 2, 0)
        legs.append(Leg(airports[i], airports[i + 1], dep, dep, 600))
    return FlightOffer(source, price, "GBP", tuple(airlines), tuple(legs), "https://x")
