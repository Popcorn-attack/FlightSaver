"""Jev (TypeSafe AI System One) as an optional decision engine.

Needs the vendored SDK (``uv sync --extra jev``) and ``TYPESAFE_API_KEY``. All
offers go into one request; each gets a buy/watch/skip choice and a 0-4 deal
score, answered with calibrated confidence.
"""

from __future__ import annotations

import os

from flightsaver.decision.base import DecisionContext
from flightsaver.models import FlightOffer, Verdict

ACTIONS = {
    "buy": "A good deal for this route and dates; booking now is sensible.",
    "watch": "Acceptable but not compelling; keep checking for a better price.",
    "skip": "Poor value: overpriced, too many stops or an unreasonable journey.",
}
DEAL_RUBRIC = [
    "Very poor value",
    "Below average value",
    "Average value",
    "Good value",
    "Excellent value",
]


class JevUnavailable(Exception):
    """SDK missing or no API key; callers fall back to the rule engine."""


def _offer_state(o: FlightOffer) -> dict:
    return {
        "price": o.price,
        "currency": o.currency,
        "round_trip_total": o.round_trip_price,
        "airlines": list(o.airlines),
        "stops": o.stops,
        "flying_minutes": o.flying_minutes,
        "route": " -> ".join([o.legs[0].from_airport, *(leg.to_airport for leg in o.legs)]),
        "departure": o.departure.isoformat(),
        "arrival": o.arrival.isoformat(),
    }


class JevEngine:
    name = "jev"

    def __init__(self, client=None, model: str | None = None, max_offers: int = 10) -> None:
        self.model = model
        self.max_offers = max_offers
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise JevUnavailable("TYPESAFE_API_KEY is not set")
        try:
            from typesafe_sdk import TypeSafeClient
        except ImportError as exc:
            raise JevUnavailable("typesafe-sdk not installed (uv sync --extra jev)") from exc
        self._client = TypeSafeClient()
        return self._client

    def evaluate(self, offers: list[FlightOffer], ctx: DecisionContext) -> list[Verdict]:
        if not offers:
            return []
        client = self._get_client()
        try:
            from typesafe_sdk import Choice, Score
        except ImportError as exc:
            raise JevUnavailable("typesafe-sdk not installed (uv sync --extra jev)") from exc

        considered = offers[: self.max_offers]
        state = {
            "task": "Judge flight offers for a traveller between the UK and China.",
            "budget": ctx.budget,
            "past_cheapest_prices": ctx.history[-30:],
            "offers": {f"offer_{i}": _offer_state(o) for i, o in enumerate(considered)},
        }
        questions = {}
        for i in range(len(considered)):
            questions[f"offer_{i}_action"] = Choice(
                instructions=f"What should the traveller do about offer_{i}?", criteria=ACTIONS
            )
            questions[f"offer_{i}_deal"] = Score(
                instructions=f"How good a deal is offer_{i} given the other offers, "
                "budget and past prices?",
                criteria=DEAL_RUBRIC,
            )
        response = client.system_one(state=state, questions=questions, model=self.model)

        verdicts = []
        for i, _ in enumerate(considered):
            choice = response.choices[f"offer_{i}_action"]
            deal = response.scores[f"offer_{i}_deal"]
            verdicts.append(
                Verdict(
                    action=choice.choice,
                    score=round(deal.score / (len(DEAL_RUBRIC) - 1), 3),
                    reasons=[
                        f"jev: {choice.choice} (confidence {choice.confidence:.2f})",
                        f"deal score {deal.score:.1f}/4",
                    ],
                    engine=self.name,
                )
            )
        # Offers beyond max_offers are not sent; leave them as 'watch' without a score.
        verdicts += [
            Verdict("watch", 0.0, ["not evaluated by jev"], self.name)
            for _ in offers[len(considered) :]
        ]
        return verdicts
