"""Provider interface: anything that turns a SearchQuery into priced offers."""

from __future__ import annotations

from typing import Protocol

from flightsaver.models import FlightOffer, SearchQuery


class ProviderError(Exception):
    """A provider failed (blocked, changed markup, network). Other providers still run."""


class Provider(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        """Return priced offers. Raise ProviderError on failure."""
        ...
