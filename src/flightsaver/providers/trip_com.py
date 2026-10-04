"""Trip.com (UK site) via its FlightListSearchSSE stream, captured in a headless browser.

The result list arrives as a server-sent event whose JSON carries
``itineraryList[].journeyList`` (flights) and ``policies`` (fares, already in
the site currency). Round trips list the outbound journey priced as the
round-trip total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from flightsaver import airlines
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers import browser
from flightsaver.providers.base import ProviderError
from flightsaver.providers.links import trip_com as trip_link

API = "/FlightListSearchSSE"
CALENDAR = "/GetLowPriceInCalender"
_BEIJING = timezone(timedelta(hours=8))  # calendar days are midnight Beijing time


def _is_list(body) -> bool:
    """A list response, including an empty one (route with no flights that day)."""
    return isinstance(body, dict) and ("itineraryList" in body or "basicInfo" in body)


def _is_calendar(body) -> bool:
    return isinstance(body, dict) and "lowPriceInCalenderDtoInfoList" in body


@dataclass
class Calendar:
    """Lowest one-way fare per departure day (any number of stops)."""

    prices: dict[date, float] = field(default_factory=dict)
    currency: str | None = None
    has_direct: bool | None = None  # does the route have any nonstop service?

    def cheapest(self, start: date, end: date) -> tuple[date, float] | None:
        days = [(p, d) for d, p in self.prices.items() if start <= d <= end]
        if not days:
            return None
        price, day = min(days)
        return day, price


def parse_calendar(bodies: list) -> Calendar:
    cal = Calendar()
    for body in bodies:
        if not _is_calendar(body):
            continue
        cal.currency = body.get("currency") or cal.currency
        if body.get("hasDirectFlight") is not None:
            cal.has_direct = bool(cal.has_direct) or bool(body["hasDirectFlight"])
        for day in body.get("lowPriceInCalenderDtoInfoList") or []:
            if day.get("aDate") or not isinstance(day.get("currencyPrice"), (int, float)):
                continue  # round-trip pairs; explore uses one-way calendars
            d = datetime.fromtimestamp(day["dDate"], _BEIJING).date()
            cal.prices[d] = min(cal.prices.get(d, float("inf")), float(day["currencyPrice"]))
    return cal


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
        offers, _ = self.explore(query, want_calendar=False)
        return offers

    def explore(
        self, query: SearchQuery, want_calendar: bool = True
    ) -> tuple[list[FlightOffer], Calendar]:
        """One page load: that day's flights, plus the low-fare calendar if wanted."""
        url = trip_link(query).url

        def done(c) -> bool:
            bodies = [x.body for x in c]
            have_list = any(_is_list(b) for b in bodies)
            return have_list and (not want_calendar or any(_is_calendar(b) for b in bodies))

        captured = self._capture(
            url,
            match=lambda u: API in u or (want_calendar and CALENDAR in u),
            done=done,
            timeout=self.timeout,
        )
        bodies = [c.body for c in captured]
        if not any(_is_list(b) for b in bodies):
            raise ProviderError("Trip.com did not return a result list (blocked or page changed)")
        return parse(bodies, query, url), parse_calendar(bodies)
