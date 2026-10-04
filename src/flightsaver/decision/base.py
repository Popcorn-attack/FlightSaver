"""Decision engine interface: rate offers as buy / watch / skip."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from flightsaver.models import FlightOffer, Verdict


@dataclass
class DecisionContext:
    """What an engine knows besides the offers themselves."""

    history: list[float] = field(default_factory=list)  # cheapest price per past run
    budget: float | None = None


class DecisionEngine(Protocol):
    name: str

    def evaluate(self, offers: list[FlightOffer], ctx: DecisionContext) -> list[Verdict]:
        """Return one verdict per offer, in the same order."""
        ...
