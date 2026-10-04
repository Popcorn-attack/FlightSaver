"""Run providers concurrently, merge and rank their offers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from flightsaver.models import BookingLink, FlightOffer, SearchQuery
from flightsaver.providers import Provider
from flightsaver.providers.links import airline_links, platform_links


@dataclass
class SearchResult:
    query: SearchQuery
    offers: list[FlightOffer]
    platform_links: list[BookingLink]
    airline_links: list[BookingLink]
    errors: dict[str, str] = field(default_factory=dict)


def dedupe(offers: list[FlightOffer]) -> list[FlightOffer]:
    """Keep the cheapest offer for each physical itinerary."""
    best: dict[tuple, FlightOffer] = {}
    for offer in offers:
        key = offer.itinerary_key()
        if key not in best or offer.price < best[key].price:
            best[key] = offer
    return sorted(best.values(), key=lambda o: (o.price, o.stops, o.flying_minutes))


def search(query: SearchQuery, providers: list[Provider]) -> SearchResult:
    offers: list[FlightOffer] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(len(providers), 1)) as pool:
        futures = {pool.submit(p.search, query): p for p in providers}
        for future, provider in futures.items():
            try:
                offers.extend(future.result())
            except Exception as exc:
                errors[provider.name] = str(exc)

    offers = dedupe(offers)
    carriers = sorted({name for o in offers for name in o.airlines})
    return SearchResult(
        query=query,
        offers=offers,
        platform_links=platform_links(query),
        # Carriers seen in results first; without results, list every known carrier.
        airline_links=airline_links(carriers if offers else None),
        errors=errors,
    )
