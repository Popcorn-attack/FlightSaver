"""KAYAK (UK) via its results poll API, captured in a headless browser.

KAYAK aggregates airlines and dozens of OTAs; each result carries the cheapest
booking option and a shareable link to that exact itinerary.
"""

from __future__ import annotations

from datetime import datetime

from flightsaver import airlines
from flightsaver.models import FlightOffer, Leg, SearchQuery
from flightsaver.providers import browser
from flightsaver.providers.base import ProviderError
from flightsaver.providers.links import kayak as kayak_link

BASE = "https://www.kayak.co.uk"
POLL = "/i/api/search/dynamic/flights/poll"


def _final_body(bodies: list[dict]) -> dict | None:
    """Latest poll response with results (each poll returns the full current list)."""
    usable = [b for b in bodies if isinstance(b, dict) and b.get("results")]
    if not usable:
        return None
    complete = [b for b in usable if b.get("status") == "complete"]
    return (complete or usable)[-1]


def parse(bodies: list[dict], query: SearchQuery, search_url: str) -> list[FlightOffer]:
    body = _final_body(bodies)
    if body is None:
        return []
    legs_by_id = body.get("legs") or {}
    segs_by_id = body.get("segments") or {}
    carriers = body.get("airlines") or {}
    offers = []
    for result in body["results"]:
        if result.get("type") != "core" or not result.get("legs"):
            continue
        options = [o for o in result.get("bookingOptions") or [] if o.get("displayPrice")]
        if not options:
            continue
        best = min(options, key=lambda o: o["displayPrice"]["price"])
        price = best["displayPrice"]
        # For round trips, show the outbound journey; the price covers both.
        leg_ref = result["legs"][0]
        legs, codes = [], []
        for seg_ref in legs_by_id.get(leg_ref["id"], {}).get("segments", []) or leg_ref["segments"]:
            seg = segs_by_id.get(seg_ref["id"])
            if not seg:
                break
            legs.append(
                Leg(
                    from_airport=seg["origin"],
                    to_airport=seg["destination"],
                    departure=datetime.fromisoformat(seg["departure"]),
                    arrival=datetime.fromisoformat(seg["arrival"]),
                    duration_minutes=int(seg.get("duration") or 0),
                    aircraft=seg.get("equipmentTypeName") or "",
                    flight_no=f"{seg['airline']}{seg.get('flightNumber', '')}",
                )
            )
            codes.append(seg["airline"])
        else:
            names = []
            for code in codes:
                nm = airlines.NAMES.get(code) or (carriers.get(code) or {}).get("name") or code
                if nm not in names:
                    names.append(nm)
            share = result.get("shareableUrl")
            offers.append(
                FlightOffer(
                    source="kayak",
                    price=float(price["price"]),
                    currency=price.get("currency") or query.currency,
                    airlines=tuple(names),
                    legs=tuple(legs),
                    booking_url=BASE + share if share else search_url,
                    round_trip_price=query.round_trip,
                )
            )
    return offers


class KayakProvider:
    name = "kayak"

    def __init__(self, timeout: float = 25.0, capture=browser.capture_json) -> None:
        self.timeout = timeout
        self._capture = capture

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        url = kayak_link(query).url
        captured = self._capture(
            url,
            match=lambda u: POLL in u,
            done=lambda c: any(
                isinstance(x.body, dict) and x.body.get("status") == "complete" for x in c
            ),
            timeout=self.timeout,
        )
        if not captured:
            raise ProviderError("KAYAK returned no results (blocked or page changed)")
        return parse([c.body for c in captured], query, url)
