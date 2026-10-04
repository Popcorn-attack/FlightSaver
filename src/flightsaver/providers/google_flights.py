"""Google Flights via the fast-flights scraper.

Google Flights aggregates fares from airlines (BA, Virgin, Air China, China
Eastern, China Southern, Hainan, Cathay, ...) and many OTAs, so it is the
broadest free source and the first one wired in.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from flightsaver import airlines
from flightsaver.airports import expand
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers import browser
from flightsaver.providers.base import ProviderError
from flightsaver.providers.links import google_flights as google_link

SHOPPING = "/FlightsFrontendService/GetShoppingResults"

try:
    import fast_flights
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    fast_flights = None

_not_found = fast_flights.FlightsNotFound if fast_flights is not None else ()


def _to_datetime(sdt) -> datetime:
    y, m, d = sdt.date
    hh, mm = sdt.time
    return datetime(y, m, d, hh, mm)


def build_query(query: SearchQuery, origin: str, destination: str):
    """Build a fast-flights Query for one airport pair."""
    flights = [
        fast_flights.FlightQuery(
            date=query.depart.isoformat(), from_airport=origin, to_airport=destination
        )
    ]
    if query.return_date is not None:
        flights.append(
            fast_flights.FlightQuery(
                date=query.return_date.isoformat(), from_airport=destination, to_airport=origin
            )
        )
    return fast_flights.create_query(
        flights=flights,
        seat=query.cabin,
        trip="round-trip" if query.round_trip else "one-way",
        passengers=fast_flights.Passengers(adults=query.adults),
        language="en-GB",
        currency=query.currency,
        max_stops=query.max_stops,
    )


def convert(results, query: SearchQuery, url: str) -> list[FlightOffer]:
    """Convert fast-flights results into FlightOffers."""
    offers = []
    for item in results:
        if not item.flights or not item.price:
            continue
        legs = tuple(
            Leg(
                from_airport=f.from_airport.code,
                to_airport=f.to_airport.code,
                departure=_to_datetime(f.departure),
                arrival=_to_datetime(f.arrival),
                duration_minutes=int(f.duration or 0),
                aircraft=f.plane_type or "",
            )
            for f in item.flights
        )
        offers.append(
            FlightOffer(
                source="google_flights",
                price=float(item.price),
                currency=query.currency,
                airlines=tuple(item.airlines),
                legs=legs,
                booking_url=url,
                round_trip_price=query.round_trip,
            )
        )
    return offers


def _hm(value) -> tuple[int, int]:
    # Google omits zero components: [8] is 08:00, [None, 31] is 00:31.
    padded = [*(value or []), None, None]
    return (padded[0] or 0, padded[1] or 0)


def _dt(date_parts, time_parts) -> datetime:
    y, m, d = date_parts
    return datetime(y, m, d, *_hm(time_parts))


def parse_shopping(texts: list[str], query: SearchQuery, url: str) -> list[FlightOffer]:
    """Parse GetShoppingResults batch responses (same layout fast-flights reads from HTML)."""
    offers = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            if not line.startswith('[["wrb.fr"'):
                continue
            try:
                payload = json.loads(json.loads(line)[0][2])
            except (ValueError, IndexError, TypeError):
                continue
            for block in (payload[2], payload[3]):  # best flights, other flights
                for item in (block or [None])[0] or []:
                    try:
                        offers.append(_offer(item, query, url))
                    except (IndexError, TypeError, ValueError):
                        continue
    return offers


def _offer(item, query: SearchQuery, url: str) -> FlightOffer:
    flight, price = item[0], item[1][0][1]
    if not price:
        raise ValueError("no price")
    legs, names = [], []
    for seg in flight[2]:
        number = seg[22] if len(seg) > 22 and seg[22] else None
        legs.append(
            Leg(
                from_airport=seg[3],
                to_airport=seg[6],
                departure=_dt(seg[20], seg[8]),
                arrival=_dt(seg[21], seg[10]),
                duration_minutes=int(seg[11] or 0),
                aircraft=seg[17] or "",
                flight_no=f"{number[0]}{number[1]}" if number else "",
            )
        )
        if number:
            nm = airlines.name(number[0], number[3] or number[0])
            if nm not in names:
                names.append(nm)
    return FlightOffer(
        source="google_flights",
        price=float(price),
        currency=query.currency,
        airlines=tuple(names or flight[1]),
        legs=tuple(legs),
        booking_url=url,
        round_trip_price=query.round_trip,
    )


class GoogleFlightsProvider:
    name = "google_flights"

    def __init__(
        self,
        proxy: str | None = None,
        fetch: Callable | None = None,
        capture: Callable | None = None,
    ) -> None:
        self.proxy = proxy
        # Injected in tests; default to the live scraper / browser.
        self._fetch = fetch
        self._capture = capture or browser.capture_json
        self._use_browser = capture is not None or (fetch is None and browser.available())

    def _get(self, q):
        if self._fetch is not None:
            return self._fetch(q)
        if fast_flights is None:
            raise ProviderError("fast-flights is not installed")
        return fast_flights.get_flights(q, proxy=self.proxy)

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        """Direct HTTP first (fast); headless browser if Google rejects it (datacenter IPs)."""
        try:
            offers = self._search_http(query)
        except ProviderError:
            if not self._use_browser:
                raise
            offers = []
        if offers or not self._use_browser:
            return offers
        return self._search_browser(query)

    def _search_browser(self, query: SearchQuery) -> list[FlightOffer]:
        url = google_link(query).url
        captured = self._capture(
            url,
            match=lambda u: SHOPPING in u,
            done=lambda c: bool(c),
            timeout=25.0,
        )
        offers = parse_shopping([c.body for c in captured], query, url)
        if not offers:
            raise ProviderError("Google Flights returned no results")
        return offers

    def _search_http(self, query: SearchQuery) -> list[FlightOffer]:
        if fast_flights is None and self._fetch is None:
            raise ProviderError("fast-flights is not installed")
        offers: list[FlightOffer] = []
        errors: list[str] = []
        for origin in expand(query.origin):
            for destination in expand(query.destination):
                q = build_query(query, origin, destination)
                try:
                    results = self._get(q)
                except _not_found:
                    continue
                except Exception as exc:  # scraper raises many types; isolate per pair
                    errors.append(f"{origin}-{destination}: {exc}")
                    continue
                offers.extend(convert(results, query, q.url()))
        if not offers and errors:
            raise ProviderError("; ".join(errors))
        return offers
