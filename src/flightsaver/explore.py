"""Flexible search: a window of departure dates and/or any city in a country.

"Edinburgh to anywhere in China, any day from 25 Oct to 15 Nov, direct" would
be hundreds of ordinary searches. Instead, per route we load Trip.com once:
that page also fetches a low-fare calendar (cheapest fare for every day for
months ahead) and says whether the route has any nonstop service. Then only
the most promising route is searched in full on its cheapest day.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date

from flightsaver import fx
from flightsaver.airports import region
from flightsaver.models import FlightOffer, SearchQuery
from flightsaver.nlp import ANY_CN, ANY_UK, ParsedRequest
from flightsaver.providers import browser, default_providers
from flightsaver.providers.base import ProviderError
from flightsaver.providers.google_flights import GoogleFlightsProvider
from flightsaver.providers.links import airline_links, platform_links, trip_com
from flightsaver.providers.trip_com import Calendar, TripComProvider
from flightsaver.search import SearchResult, dedupe, search, to_currency

# Cities with long-haul service on the UK <-> China corridor, most served first.
GATEWAYS = {
    ANY_CN: ("BEIJING", "SHANGHAI", "CAN", "SZX", "CHENGDU", "HKG", "CKG", "HGH"),
    ANY_UK: ("LON", "MAN", "EDI", "BHX"),
}
MAX_ROUTES = int(os.environ.get("FLIGHTSAVER_EXPLORE_MAX_ROUTES", "6"))


def is_flexible(p: ParsedRequest) -> bool:
    return p.depart_until is not None or p.origin in GATEWAYS or p.destination in GATEWAYS


@dataclass
class RouteSummary:
    origin: str
    destination: str
    has_direct: bool | None
    cheapest_date: str | None
    cheapest_price: float | None
    offers_found: int
    link: str
    error: str | None = None


def _routes(p: ParsedRequest) -> list[tuple[str, str]]:
    origins = GATEWAYS.get(p.origin, (p.origin,))
    dests = GATEWAYS.get(p.destination, (p.destination,))
    pairs = [(o, d) for o in origins for d in dests if o != d and region(o) != region(d)]
    return pairs[:MAX_ROUTES]


def _query(p: ParsedRequest, origin: str, dest: str, day: date) -> SearchQuery:
    return SearchQuery(origin, dest, day, None, p.adults, p.cabin, p.currency, p.max_stops)


def _price(value: float, currency: str | None, target: str) -> float:
    if not currency or currency == target:
        return value
    try:
        return fx.convert(value, currency, target)
    except ValueError:
        return value


def explore(
    p: ParsedRequest,
    trip: TripComProvider | None = None,
    providers_factory=default_providers,
    google: GoogleFlightsProvider | None = None,
) -> tuple[SearchResult, SearchQuery, dict]:
    """Returns (merged result, query for the best route/day, explore summary)."""
    start = p.depart
    end = p.depart_until or p.depart
    if trip is None and browser.available():
        trip = TripComProvider()
    if google is None:
        google = GoogleFlightsProvider()

    summaries: list[RouteSummary] = []
    offers: list[FlightOffer] = []
    errors: dict[str, str] = {}
    for origin, dest in _routes(p):
        q = _query(p, origin, dest, start)
        found: list[FlightOffer] = []
        cal = Calendar()
        error = None
        try:
            if trip is not None:
                found, cal = trip.explore(q)
            else:  # no browser on this host: Google over HTTP, start date only
                found = google.search(q)
        except (ProviderError, ValueError) as exc:
            error = str(exc).splitlines()[0][:160]
        best_day = cal.cheapest(start, end)
        summaries.append(
            RouteSummary(
                origin=origin,
                destination=dest,
                has_direct=cal.has_direct,
                cheapest_date=best_day[0].isoformat() if best_day else None,
                cheapest_price=(
                    round(_price(best_day[1], cal.currency, p.currency)) if best_day else None
                ),
                offers_found=len(found),
                link=trip_com(q).url,
                error=error,
            )
        )
        offers.extend(found)

    # Search the most promising route in full on its cheapest day.
    candidates = [s for s in summaries if s.cheapest_date]
    if p.max_stops == 0 and any(s.has_direct for s in candidates):
        candidates = [s for s in candidates if s.has_direct]
    best = min(candidates, key=lambda s: s.cheapest_price, default=None)
    if best is not None:
        focus = _query(p, best.origin, best.destination, date.fromisoformat(best.cheapest_date))
    else:
        first = summaries[0] if summaries else None
        focus = _query(
            p,
            first.origin if first else p.origin,
            first.destination if first else p.destination,
            start,
        )
    source_counts = (
        {"trip_com": len(offers)} if trip is not None else {"google_flights": len(offers)}
    )
    if best is not None and focus.depart != start:
        detail = search(focus, providers_factory())
        offers.extend(detail.offers)
        errors.update(detail.errors)
        for k, v in detail.source_counts.items():
            source_counts[k] = source_counts.get(k, 0) + v

    merged = dedupe(to_currency(offers, p.currency))
    carriers = sorted({n for o in merged for n in o.airlines})
    result = SearchResult(
        query=focus,
        offers=merged,
        platform_links=platform_links(focus),
        airline_links=airline_links(carriers if merged else None),
        errors=errors,
        source_counts=source_counts,
    )
    summary = {
        "origin": p.origin,
        "destination": p.destination,
        "window": [start.isoformat(), end.isoformat()],
        "direct_only": p.max_stops == 0,
        "routes": [asdict(s) for s in summaries],
        "focus": {
            "origin": focus.origin,
            "destination": focus.destination,
            "date": focus.depart.isoformat(),
        },
    }
    return result, focus, summary
