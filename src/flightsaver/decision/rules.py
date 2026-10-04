"""Transparent rule-based engine; the default and the baseline for Jev."""

from __future__ import annotations

from flightsaver.decision.base import DecisionContext
from flightsaver.models import FlightOffer, Verdict

MIN_HISTORY = 5  # runs needed before history percentiles are trusted


def percentile_rank(value: float, sample: list[float]) -> float:
    """Share of the sample strictly cheaper than value (0 = cheapest ever seen)."""
    if not sample:
        return 0.5
    return sum(1 for s in sample if s < value) / len(sample)


class RuleEngine:
    name = "rules"

    def evaluate(self, offers: list[FlightOffer], ctx: DecisionContext) -> list[Verdict]:
        if not offers:
            return []
        cheapest = min(o.price for o in offers)
        fastest = min(o.flying_minutes for o in offers) or 1
        use_history = len(ctx.history) >= MIN_HISTORY
        verdicts = []
        for o in offers:
            reasons: list[str] = []
            price_ratio = o.price / cheapest
            # Weighted mix: price vs. today's cheapest, stops, and flying time.
            score = (
                0.6 / price_ratio + 0.25 / (1 + o.stops) + 0.15 * fastest / max(o.flying_minutes, 1)
            )
            if price_ratio == 1:
                reasons.append("cheapest in this search")
            else:
                reasons.append(f"{(price_ratio - 1) * 100:.0f}% above cheapest")
            reasons.append("direct" if o.stops == 0 else f"{o.stops} stop(s)")
            if o.airport_change:
                reasons.append("change of airport between flights")
                score -= 0.1

            action = "watch"
            rank = percentile_rank(o.price, ctx.history) if use_history else None
            if rank is not None:
                reasons.append(f"cheaper than {(1 - rank) * 100:.0f}% of past checks")
                score = 0.7 * score + 0.3 * (1 - rank)
            if ctx.budget is not None and o.price <= ctx.budget:
                reasons.append(f"within budget {ctx.budget:.0f} {o.currency}")
                action = "buy"
            elif rank is not None and rank <= 0.2 and price_ratio <= 1.05:
                action = "buy"
            if price_ratio > 1.5 or o.stops > 2:
                action = "skip"
            verdicts.append(Verdict(action, round(min(score, 1.0), 3), reasons, self.name))
        return verdicts
