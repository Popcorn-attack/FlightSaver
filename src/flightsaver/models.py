"""Core data types shared by providers, decision engines and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

Cabin = Literal["economy", "premium-economy", "business", "first"]
LinkKind = Literal["metasearch", "ota-intl", "ota-cn", "airline"]


@dataclass(frozen=True)
class SearchQuery:
    """What the user wants to fly. Airport codes are IATA airport or metro codes."""

    origin: str
    destination: str
    depart: date
    return_date: date | None = None
    adults: int = 1
    cabin: Cabin = "economy"
    currency: str = "GBP"
    max_stops: int | None = None

    def __post_init__(self) -> None:
        if self.return_date is not None and self.return_date < self.depart:
            raise ValueError("return date is before departure date")
        if not 1 <= self.adults <= 9:
            raise ValueError("adults must be between 1 and 9")

    @property
    def round_trip(self) -> bool:
        return self.return_date is not None


@dataclass(frozen=True)
class Leg:
    """One flown segment."""

    from_airport: str
    to_airport: str
    departure: datetime
    arrival: datetime
    duration_minutes: int
    aircraft: str = ""


@dataclass(frozen=True)
class FlightOffer:
    """A priced itinerary from one source.

    For round trips, ``legs`` holds the outbound journey and ``price`` is the
    round-trip total, which is how Google Flights and most OTAs list fares.
    """

    source: str
    price: float
    currency: str
    airlines: tuple[str, ...]
    legs: tuple[Leg, ...]
    booking_url: str
    round_trip_price: bool = False

    @property
    def stops(self) -> int:
        return max(len(self.legs) - 1, 0)

    @property
    def flying_minutes(self) -> int:
        return sum(leg.duration_minutes for leg in self.legs)

    @property
    def departure(self) -> datetime:
        return self.legs[0].departure

    @property
    def arrival(self) -> datetime:
        return self.legs[-1].arrival

    def itinerary_key(self) -> tuple:
        """Identifies the same physical itinerary across sources."""
        return (
            tuple(sorted(self.airlines)),
            tuple((leg.from_airport, leg.to_airport, leg.departure) for leg in self.legs),
        )


@dataclass(frozen=True)
class BookingLink:
    """A link to search/book on a platform or airline site.

    ``prefilled`` is True when the URL carries the route and dates; otherwise it
    opens the platform's flight search page and the user fills in the form.
    """

    platform: str
    name: str
    url: str
    kind: LinkKind
    prefilled: bool


@dataclass
class Verdict:
    """A decision engine's opinion about one offer."""

    action: Literal["buy", "watch", "skip"]
    score: float  # 0..1, higher is a better deal
    reasons: list[str] = field(default_factory=list)
    engine: str = ""
