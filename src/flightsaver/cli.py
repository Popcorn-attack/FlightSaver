"""Command line interface.

flightsaver search LON SHANGHAI 2026-12-20 --return 2027-01-05
flightsaver links MAN PEK 2026-12-20
flightsaver airports
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date

from flightsaver.airports import CHINA_AIRPORTS, METROS, UK_AIRPORTS, check_corridor
from flightsaver.decision import DecisionContext, evaluate
from flightsaver.history import DEFAULT_PATH, PriceHistory
from flightsaver.models import BookingLink, SearchQuery
from flightsaver.providers import default_providers
from flightsaver.providers.links import airline_links, platform_links
from flightsaver.search import search


def _query(args: argparse.Namespace) -> SearchQuery:
    check_corridor(args.origin, args.destination)
    return SearchQuery(
        origin=args.origin.upper(),
        destination=args.destination.upper(),
        depart=date.fromisoformat(args.depart),
        return_date=date.fromisoformat(args.return_date) if args.return_date else None,
        adults=args.adults,
        cabin=args.cabin,
        currency=args.currency.upper(),
        max_stops=args.max_stops,
    )


def _print_links(title: str, links: list[BookingLink]) -> None:
    print(f"\n{title}")
    for link in links:
        mark = "" if link.prefilled else "  (open and enter the trip)"
        print(f"  {link.name:<18} {link.url}{mark}")


def cmd_search(args: argparse.Namespace) -> int:
    q = _query(args)
    result = search(q, default_providers())

    history = None if args.no_history else PriceHistory(args.history)
    past = history.cheapest_per_run(q) if history else []
    verdicts, note = evaluate(args.engine, result.offers, DecisionContext(past, args.budget))
    if history and result.offers:
        history.record(q, result.offers)

    shown = list(zip(result.offers, verdicts, strict=True))[: args.limit]
    if args.json:
        print(
            json.dumps(
                {
                    "query": asdict(q),
                    "offers": [
                        {**asdict(o), "stops": o.stops, "verdict": asdict(v)} for o, v in shown
                    ],
                    "platform_links": [asdict(x) for x in result.platform_links],
                    "airline_links": [asdict(x) for x in result.airline_links],
                    "errors": result.errors,
                    "note": note,
                },
                default=str,
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    trip = f"{q.origin} -> {q.destination}  {q.depart}"
    if q.return_date:
        trip += f" / back {q.return_date}  (prices are round-trip totals)"
    print(trip)
    for source, err in result.errors.items():
        print(f"  ! {source} failed: {err.splitlines()[0]}", file=sys.stderr)
    if note:
        print(f"  ! {note}", file=sys.stderr)
    if not shown:
        print("\nNo priced offers found. Search directly on the platforms below.")
    for o, v in shown:
        route = " > ".join([o.legs[0].from_airport, *(leg.to_airport for leg in o.legs)])
        hours = f"{o.flying_minutes // 60}h{o.flying_minutes % 60:02d}"
        print(f"\n  {o.price:>8.0f} {o.currency}  [{v.action.upper():5}] {', '.join(o.airlines)}")
        print(
            f"           {route}  dep {o.departure:%d %b %H:%M}  "
            f"arr {o.arrival:%d %b %H:%M}  fly {hours}"
        )
        print(f"           {'; '.join(v.reasons)}")
        print(f"           book: {o.booking_url}")
    _print_links("Compare on platforms:", result.platform_links)
    _print_links("Airline websites:", result.airline_links)
    return 0


def cmd_links(args: argparse.Namespace) -> int:
    q = _query(args)
    _print_links("Platforms:", platform_links(q))
    _print_links("Airline websites:", airline_links())
    return 0


def cmd_airports(_: argparse.Namespace) -> int:
    for title, table in (("UK", UK_AIRPORTS), ("China", CHINA_AIRPORTS)):
        print(title)
        for code, name in table.items():
            print(f"  {code}  {name}")
    print("Metro codes")
    for code, airports in METROS.items():
        print(f"  {code:<9} {', '.join(airports)}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("error: web extra not installed (uv sync --extra web)", file=sys.stderr)
        return 2
    uvicorn.run("flightsaver.web.app:app", host=args.host, port=args.port)
    return 0


def _add_trip_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("origin", help="IATA airport or metro code, e.g. LHR, LON, SHANGHAI")
    p.add_argument("destination")
    p.add_argument("depart", help="YYYY-MM-DD")
    p.add_argument("--return", dest="return_date", help="YYYY-MM-DD for a round trip")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument(
        "--cabin", default="economy", choices=["economy", "premium-economy", "business", "first"]
    )
    p.add_argument("--currency", default="GBP")
    p.add_argument("--max-stops", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flightsaver",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="search priced offers and list booking links")
    _add_trip_args(s)
    s.add_argument("--budget", type=float, help="mark offers at or below this price as BUY")
    s.add_argument(
        "--engine",
        default="rules",
        choices=["rules", "jev"],
        help="decision engine (jev needs TYPESAFE_API_KEY)",
    )
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.add_argument("--history", default=str(DEFAULT_PATH), help="price history SQLite path")
    s.add_argument("--no-history", action="store_true")
    s.set_defaults(func=cmd_search)

    lk = sub.add_parser("links", help="only print booking links, no scraping")
    _add_trip_args(lk)
    lk.set_defaults(func=cmd_links)

    ap = sub.add_parser("airports", help="list supported airports")
    ap.set_defaults(func=cmd_airports)

    web = sub.add_parser("web", help="start the chat website (needs ANTHROPIC_API_KEY)")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    web.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
