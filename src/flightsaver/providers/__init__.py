"""Flight data sources (priced offers) and booking-link builders."""

from flightsaver.providers.base import Provider, ProviderError
from flightsaver.providers.google_flights import GoogleFlightsProvider


def default_providers() -> list[Provider]:
    return [GoogleFlightsProvider()]


__all__ = ["Provider", "ProviderError", "GoogleFlightsProvider", "default_providers"]
