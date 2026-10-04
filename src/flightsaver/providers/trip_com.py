"""Trip.com (UK site) via its FlightListSearchSSE stream, captured in a headless browser.

The result list arrives as a server-sent event whose JSON carries
``itineraryList[].journeyList`` (flights) and ``policies`` (fares, already in
the site currency). Round trips list the outbound journey priced as the
round-trip total.
"""

from __future__ import annotations

from datetime import datetime

from flightsaver import airlines
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers import browser
from flightsaver.providers.base import ProviderError
from flightsaver.providers.links import trip_com as trip_link

API = "/FlightListSearchSSE"


def _lists(bodies: list[dict]):
    for body in bodies:
        if isinstance(body, dict) and body.get("itineraryList"):
            yield body


def parse(bodies: list[dict], query: SearchQuery, search_url: str) -> list[FlightOffer]:
    best: dict[tuple, FlightOffer] = {}
    for body in _lists(bodies):
        currency = (body.get("basicInfo") or {}).get("currency") or query.currency
        names_by_code = {a.get("code"): a.get("name") for a in body.get("airlineList") or []}
        for item in body["itineraryList"]:
            prices = [
                p["price"]["totalPrice"]
                for p in item.get("policies") or []
                if isinstance((p.get("price") or {}).get("totalPrice"), (int, float))
            ]
            journeys = item.get("journeyList") or []
            if not prices or not journeys:
                continue
            legs, names = [], []
            for sec in journeys[0].get("transSectionList") or []:
                info = sec.get("flightInfo") or {}
                legs.append(
                    Leg(
                        from_airport=sec["departPoint"]["airportCode"],
                        to_airport=sec["arrivePoint"]["airportCode"],
                        departure=datetime.fromisoformat(sec["departDateTime"]),
                        arrival=datetime.fromisoformat(sec["arriveDateTime"]),
                        duration_minutes=int(sec.get("duration") or 0),
                        aircraft=(info.get("craftInfo") or {}).get("name") or "",
                        flight_no=info.get("flightNo") or "",
                    )
                )
                code = info.get("airlineCode") or ""
                nm = airlines.name(code, names_by_code.get(code) or code)
                if nm not in names:
                    names.append(nm)
            if not legs:
                continue
            offer = FlightOffer(
                source="trip_com",
                price=float(min(prices)),
                currency=currency,
                airlines=tuple(names),
                legs=tuple(legs),
                booking_url=search_url,
                round_trip_price=query.round_trip,
            )
            key = offer.itinerary_key()
            if key not in best or offer.price < best[key].price:
                best[key] = offer
    return list(best.values())


class TripComProvider:
    name = "trip_com"

    def __init__(self, timeout: float = 30.0, capture=browser.capture_json) -> None:
        self.timeout = timeout
        self._capture = capture

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        url = trip_link(query).url
        captured = self._capture(
            url,
            match=lambda u: API in u,
            done=lambda c: any(True for _ in _lists([x.body for x in c])),
            timeout=self.timeout,
        )
        if not captured:
            raise ProviderError("Trip.com returned no results (blocked or page changed)")
        return parse([c.body for c in captured], query, url)
