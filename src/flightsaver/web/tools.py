"""The search tool Claude calls, and its input validation."""

from __future__ import annotations

from datetime import date
from typing import Any

from flightsaver.airports import check_corridor
from flightsaver.decision import DecisionContext, evaluate
from flightsaver.history import PriceHistory
from flightsaver.models import SearchQuery
from flightsaver.providers import Provider, default_providers
from flightsaver.search import search

MAX_OFFERS = 30  # sent to the page; it shows 10 and expands
SORTS = ("best", "cheapest", "fastest")
CABINS = ["economy", "premium-economy", "business", "first"]

SEARCH_TOOL = {
    "name": "search_flights",
    "description": (
        "Search live flight prices (Google Flights, KAYAK, Ctrip) between the UK and "
        "mainland China / Hong Kong and get "
        "booking links for travel platforms (Google Flights, Skyscanner, KAYAK, Expedia, "
        "Trip.com, Ctrip, Qunar, Fliggy, LY.com) and airline websites. Prices for round "
        "trips are round-trip totals. Each offer carries a buy/watch/skip verdict. Call it "
        "once per distinct route/date combination the user wants compared."
    ),
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {
                "type": "string",
                "description": "IATA airport or metro code: LHR, "
                "LON, MAN, EDI, PEK, BEIJING, PVG, SHANGHAI, CAN, SZX, CHENGDU, HKG, ...",
            },
            "destination": {"type": "string", "description": "Same format as origin."},
            "depart_date": {"type": "string", "description": "YYYY-MM-DD"},
            "return_date": {"type": "string", "description": "YYYY-MM-DD; omit for one-way"},
            "adults": {"type": "integer", "minimum": 1, "maximum": 9},
            "cabin": {"type": "string", "enum": CABINS},
            "currency": {"type": "string", "description": "ISO code, default GBP"},
            "max_stops": {"type": "integer", "minimum": 0, "maximum": 3},
            "budget": {"type": "number", "description": "User's price ceiling, same currency"},
            "sort": {
                "type": "string",
                "enum": list(SORTS),
                "description": "best = price and door-to-door time incl. layovers (default)",
            },
        },
        "required": ["origin", "destination", "depart_date"],
    },
}


def parse_args(raw: Any) -> tuple[SearchQuery, float | None]:
    """Validate model-supplied tool input; raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("tool input must be an object")
    for key in ("origin", "destination", "depart_date"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"missing {key}")
    check_corridor(raw["origin"], raw["destination"])
    cabin = raw.get("cabin") or "economy"
    if cabin not in CABINS:
        raise ValueError(f"cabin must be one of {CABINS}")
    depart = date.fromisoformat(raw["depart_date"])
    if depart < date.today():
        raise ValueError("departure date is in the past")
    budget = raw.get("budget")
    query = SearchQuery(
        origin=raw["origin"].strip().upper(),
        destination=raw["destination"].strip().upper(),
        depart=depart,
        return_date=date.fromisoformat(raw["return_date"]) if raw.get("return_date") else None,
        adults=int(raw.get("adults") or 1),
        cabin=cabin,
        currency=(raw.get("currency") or "GBP").upper(),
        max_stops=raw.get("max_stops"),
    )
    return query, float(budget) if budget is not None else None


def run_search(
    query: SearchQuery,
    budget: float | None,
    engine: str = "rules",
    providers: list[Provider] | None = None,
    history: PriceHistory | None = None,
    sort: str = "best",
) -> dict:
    """Search, judge and return a JSON-able summary for both Claude and the web page.

    ``sort``: "best" (fare + journey time incl. layovers), "cheapest" or "fastest".
    """
    result = search(query, providers if providers is not None else default_providers())
    past = history.cheapest_per_run(query) if history else []
    verdicts, note = evaluate(engine, result.offers, DecisionContext(past, budget))
    if history and result.offers:
        history.record(query, result.offers)

    pairs = list(zip(result.offers, verdicts, strict=True))
    if sort == "cheapest":
        pairs.sort(key=lambda p: (p[0].price, p[0].total_minutes))
    elif sort == "fastest":
        pairs.sort(key=lambda p: (p[0].total_minutes, p[0].price))
    else:
        pairs.sort(key=lambda p: (-p[1].score, p[0].price))
    offers = []
    for o, v in pairs[:MAX_OFFERS]:
        offers.append(
            {
                "price": o.price,
                "currency": o.currency,
                "airlines": list(o.airlines),
                "route": [o.legs[0].from_airport, *(leg.to_airport for leg in o.legs)],
                "departure": o.departure.isoformat(timespec="minutes"),
                "arrival": o.arrival.isoformat(timespec="minutes"),
                "stops": o.stops,
                "flying_minutes": o.flying_minutes,
                "total_minutes": o.total_minutes,
                "layovers": [{"airport": a, "minutes": m} for a, m in o.layovers],
                "verdict": v.action,
                "score": v.score,
                "reasons": v.reasons,
                "booking_url": o.booking_url,
                "source": o.source,
                "original_price": o.original_price,
                "original_currency": o.original_currency,
                "airport_change": o.airport_change,
                "self_transfer": o.self_transfer,
                "direct_price": o.direct_price,
                "direct_seller": o.direct_seller,
            }
        )
    return {
        "query": {
            "origin": query.origin,
            "destination": query.destination,
            "depart_date": query.depart.isoformat(),
            "return_date": query.return_date.isoformat() if query.return_date else None,
            "adults": query.adults,
            "cabin": query.cabin,
            "currency": query.currency,
            "round_trip_prices": query.round_trip,
        },
        "sort": sort,
        "offers": offers,
        "total_offers_found": len(result.offers),
        "platform_links": [
            {"name": x.name, "url": x.url, "prefilled": x.prefilled} for x in result.platform_links
        ],
        "airline_links": [{"name": x.name, "url": x.url} for x in result.airline_links],
        "source_counts": result.source_counts,
        "source_errors": result.errors,
        "engine_note": note,
    }


def compact_for_model(data: dict, limit: int = 5) -> dict:
    """The fields the model needs to summarise a search; links and long lists stay out."""
    return {
        "query": data.get("query"),
        "total_offers_found": data.get("total_offers_found", len(data.get("offers", []))),
        "offers": [
            {
                "price": round(o["price"]),
                "airlines": o.get("airlines"),
                "route": "-".join(o.get("route", [])),
                "departure": o.get("departure"),
                "arrival": o.get("arrival"),
                "stops": o.get("stops"),
                "total_minutes": o.get("total_minutes"),
                "layovers": [f"{x['airport']} {x['minutes']}m" for x in o.get("layovers", [])],
                **({"airline_direct": o["direct_price"]} if o.get("direct_price") else {}),
                "verdict": o.get("verdict"),
                **({"airport_change": True} if o.get("airport_change") else {}),
            }
            for o in data.get("offers", [])[:limit]
        ],
        "source_errors": {
            k: v.splitlines()[0][:120] for k, v in data.get("source_errors", {}).items()
        },
    }
