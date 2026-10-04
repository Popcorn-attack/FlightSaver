"""Flight data sources (priced offers) and booking-link builders."""

from __future__ import annotations

import os

from flightsaver.providers import browser
from flightsaver.providers.base import Provider, ProviderError
from flightsaver.providers.ctrip import CtripProvider
from flightsaver.providers.google_flights import GoogleFlightsProvider
from flightsaver.providers.kayak import KayakProvider
from flightsaver.providers.trip_com import TripComProvider

# Sources that drive a headless browser (need the ``browser`` extra).
BROWSER_PROVIDERS = {"kayak": KayakProvider, "trip_com": TripComProvider, "ctrip": CtripProvider}
ALL = {"google_flights": GoogleFlightsProvider, **BROWSER_PROVIDERS}


def default_providers() -> list[Provider]:
    """Providers named in FLIGHTSAVER_PROVIDERS (comma-separated), default: all available."""
    names = [
        n.strip()
        for n in os.environ.get("FLIGHTSAVER_PROVIDERS", ",".join(ALL)).split(",")
        if n.strip()
    ]
    have_browser = browser.available()
    out = []
    for name in names:
        if name not in ALL:
            raise ValueError(f"unknown provider {name!r}; choose from {', '.join(ALL)}")
        if name in BROWSER_PROVIDERS and not have_browser:
            continue
        out.append(ALL[name]())
    return out


__all__ = [
    "Provider",
    "ProviderError",
    "GoogleFlightsProvider",
    "KayakProvider",
    "CtripProvider",
    "TripComProvider",
    "default_providers",
]
