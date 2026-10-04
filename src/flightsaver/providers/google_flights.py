"""Google Flights via the fast-flights scraper.

Google Flights aggregates fares from airlines (BA, Virgin, Air China, China
Eastern, China Southern, Hainan, Cathay, ...) and many OTAs, so it is the
broadest free source and the first one wired in.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from flightsaver.airports import expand
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers.base import ProviderError

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


class GoogleFlightsProvider:
    name = "google_flights"

    def __init__(self, proxy: str | None = None, fetch: Callable | None = None) -> None:
        self.proxy = proxy
        # Injected in tests; defaults to the live scraper.
        self._fetch = fetch

    def _get(self, q):
        if self._fetch is not None:
            return self._fetch(q)
        if fast_flights is None:
            raise ProviderError("fast-flights is not installed")
        return fast_flights.get_flights(q, proxy=self.proxy)

    def search(self, query: SearchQuery) -> list[FlightOffer]:
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
