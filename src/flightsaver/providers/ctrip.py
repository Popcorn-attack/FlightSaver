"""Ctrip (携程) international flights, captured in a headless browser.

Ctrip streams results through several ``/international/search/api/search/``
calls (batchSearch, then pull) until ``context.finished`` is true. Prices are in
CNY per adult: fare (``adultPrice``) plus taxes (``adultTax``).
"""

from __future__ import annotations

from datetime import datetime

from flightsaver import airlines
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers import browser
from flightsaver.providers.base import ProviderError
from flightsaver.providers.links import ctrip as ctrip_link

API = "/international/search/api/search/"


def _items(bodies: list[dict]):
    for body in bodies:
        data = (body or {}).get("data") or {}
        yield from data.get("flightItineraryList") or []


def _finished(bodies: list[dict]) -> bool:
    return any((((b or {}).get("data") or {}).get("context") or {}).get("finished") for b in bodies)


def parse(bodies: list[dict], query: SearchQuery, search_url: str) -> list[FlightOffer]:
    best: dict[tuple, FlightOffer] = {}
    for item in _items(bodies):
        prices = [
            p["adultPrice"] + p.get("adultTax", 0)
            for p in item.get("priceList") or []
            if isinstance(p.get("adultPrice"), (int, float))
        ]
        segments = item.get("flightSegments") or []
        if not prices or not segments:
            continue
        # Round trips list the outbound segment with the round-trip total.
        flights = segments[0].get("flightList") or []
        legs, names = [], []
        for f in flights:
            dep = datetime.fromisoformat(f["departureDateTime"])
            arr = datetime.fromisoformat(f["arrivalDateTime"])
            legs.append(
                Leg(
                    from_airport=f["departureAirportCode"],
                    to_airport=f["arrivalAirportCode"],
                    departure=dep,
                    arrival=arr,
                    duration_minutes=int(f.get("duration") or 0),
                    aircraft=f.get("aircraftName") or "",
                    flight_no=f.get("flightNo") or "",
                )
            )
            code = f.get("marketAirlineCode") or ""
            nm = airlines.name(code, f.get("marketAirlineName") or code)
            if nm not in names:
                names.append(nm)
        if not legs:
            continue
        offer = FlightOffer(
            source="ctrip",
            price=float(min(prices)) * query.adults,
            currency="CNY",
            airlines=tuple(names),
            legs=tuple(legs),
            booking_url=search_url,
            round_trip_price=query.round_trip,
        )
        # Codeshares appear as separate itineraries over the same flights.
        key = offer.itinerary_key()
        if key not in best or offer.price < best[key].price:
            best[key] = offer
    return list(best.values())


class CtripProvider:
    name = "ctrip"

    def __init__(self, timeout: float = 30.0, capture=browser.capture_json) -> None:
        self.timeout = timeout
        self._capture = capture

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        url = ctrip_link(query).url
        captured = self._capture(
            url,
            match=lambda u: API in u,
            done=lambda c: _finished([x.body for x in c]),
            timeout=self.timeout,
            locale="zh-CN",
        )
        if not captured:
            raise ProviderError("Ctrip returned no results (blocked or page changed)")
        return parse([c.body for c in captured], query, url)
