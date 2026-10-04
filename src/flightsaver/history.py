"""SQLite price history, used to judge whether today's price is a good one."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flightsaver.models import FlightOffer, SearchQuery

DEFAULT_PATH = Path.home() / ".flightsaver" / "history.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    route TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    airlines TEXT NOT NULL,
    stops INTEGER NOT NULL,
    departure TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_route ON observations(route, observed_at);
"""


def route_key(q: SearchQuery) -> str:
    """Same trip, regardless of when we looked."""
    ret = q.return_date.isoformat() if q.return_date else "-"
    return "|".join(
        [
            q.origin.upper(),
            q.destination.upper(),
            q.depart.isoformat(),
            ret,
            q.cabin,
            str(q.adults),
            q.currency,
        ]
    )


class PriceHistory:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def record(
        self, q: SearchQuery, offers: list[FlightOffer], when: datetime | None = None
    ) -> None:
        stamp = (when or datetime.now(timezone.utc)).isoformat()
        self.db.executemany(
            "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    route_key(q),
                    stamp,
                    o.source,
                    o.price,
                    o.currency,
                    ",".join(o.airlines),
                    o.stops,
                    o.departure.isoformat(),
                )
                for o in offers
            ],
        )
        self.db.commit()

    def cheapest_per_run(self, q: SearchQuery) -> list[float]:
        """Cheapest price seen in each past search run for this trip, oldest first."""
        rows = self.db.execute(
            "SELECT MIN(price) FROM observations WHERE route = ? "
            "GROUP BY observed_at ORDER BY observed_at",
            (route_key(q),),
        )
        return [r[0] for r in rows]

    def close(self) -> None:
        self.db.close()
