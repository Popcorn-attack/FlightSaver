"""Transparent rule-based engine; the default and the baseline for Jev.

Offers are compared on a generalised cost: the fare plus the traveller's time
(door-to-door, so waiting at connections counts) plus penalties for risky or
tiring connections. The cheapest generalised cost scores 1.0.
"""

from __future__ import annotations

import os

from flightsaver import fx
from flightsaver.decision.base import DecisionContext
from flightsaver.models import FlightOffer, Verdict

MIN_HISTORY = 5  # runs needed before history percentiles are trusted
# What an hour of travel time is worth, in GBP. Higher favours faster trips.
VALUE_OF_TIME_GBP = float(os.environ.get("FLIGHTSAVER_VALUE_OF_TIME", "15"))
LONG_LAYOVER = 6 * 60  # minutes
SHORT_LAYOVER = 60  # minutes; tight for an international connection


def percentile_rank(value: float, sample: list[float]) -> float:
    """Share of the sample strictly cheaper than value (0 = cheapest ever seen)."""
    if not sample:
        return 0.5
    return sum(1 for s in sample if s < value) / len(sample)


def hm(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def time_value_per_hour(currency: str) -> float:
    try:
        return fx.convert(VALUE_OF_TIME_GBP, "GBP", currency)
    except ValueError:
        return VALUE_OF_TIME_GBP


def generalised_cost(o: FlightOffer, per_hour: float) -> tuple[float, list[str]]:
    """Fare + time + connection penalties, with the reasons for each penalty."""
    cost = o.price + per_hour * o.total_minutes / 60
    notes = []
    for airport, wait in o.layovers:
        if wait > LONG_LAYOVER:
            # Long waits are worse than their raw hours: tiring, maybe a hotel.
            cost += per_hour * 0.5 * (wait - LONG_LAYOVER) / 60
            notes.append(f"long layover {hm(wait)} at {airport}")
        elif wait < SHORT_LAYOVER:
            # Risk of missing the connection: priced like three extra hours.
            cost += per_hour * 3.0
            notes.append(f"tight connection {hm(wait)} at {airport}")
    if o.airport_change:
        cost += per_hour * 2
        notes.append("change of airport between flights")
    if o.self_transfer:
        cost += per_hour * 2
        notes.append("self-transfer: separate tickets")
    return cost, notes


class RuleEngine:
    name = "rules"

    def evaluate(self, offers: list[FlightOffer], ctx: DecisionContext) -> list[Verdict]:
        if not offers:
            return []
        per_hour = time_value_per_hour(offers[0].currency)
        costs = [generalised_cost(o, per_hour) for o in offers]
        best_cost = min(c for c, _ in costs)
        cheapest = min(o.price for o in offers)
        use_history = len(ctx.history) >= MIN_HISTORY
        verdicts = []
        for o, (cost, notes) in zip(offers, costs, strict=True):
            score = best_cost / cost
            price_ratio = o.price / cheapest
            reasons = []
            if price_ratio == 1:
                reasons.append("cheapest in this search")
            else:
                reasons.append(f"{(price_ratio - 1) * 100:.0f}% above cheapest")
            journey = f"total {hm(o.total_minutes)}"
            if o.stops:
                journey += f" incl. {hm(o.layover_minutes)} connecting"
            reasons.append("direct, " + journey if o.stops == 0 else journey)
            reasons.extend(notes)

            action = "watch"
            rank = percentile_rank(o.price, ctx.history) if use_history else None
            if rank is not None:
                reasons.append(f"cheaper than {(1 - rank) * 100:.0f}% of past checks")
                score = 0.7 * score + 0.3 * (1 - rank)
            if ctx.budget is not None and o.price <= ctx.budget and score >= 0.75:
                reasons.append(f"within budget {ctx.budget:.0f} {o.currency}")
                action = "buy"
            elif score >= 0.97 or (rank is not None and rank <= 0.2 and score >= 0.85):
                action = "buy"
            if cost > 1.6 * best_cost or o.stops > 2:
                action = "skip"
            verdicts.append(Verdict(action, round(min(score, 1.0), 3), reasons, self.name))
        return verdicts
