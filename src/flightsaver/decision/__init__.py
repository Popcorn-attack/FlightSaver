"""Decision engines. Rules is the default; Jev is the optional alternative."""

from __future__ import annotations

from flightsaver.decision.base import DecisionContext, DecisionEngine
from flightsaver.decision.jev import JevEngine, JevUnavailable
from flightsaver.decision.rules import RuleEngine
from flightsaver.models import FlightOffer, Verdict


def evaluate(
    engine_name: str, offers: list[FlightOffer], ctx: DecisionContext
) -> tuple[list[Verdict], str | None]:
    """Run the named engine. If Jev can't run, fall back to rules and return a note."""
    if engine_name == "jev":
        try:
            return JevEngine().evaluate(offers, ctx), None
        except JevUnavailable as exc:
            note = f"jev unavailable ({exc}); used rules instead"
        except Exception as exc:  # API errors shouldn't lose the search results
            note = f"jev failed ({exc}); used rules instead"
        return RuleEngine().evaluate(offers, ctx), note
    return RuleEngine().evaluate(offers, ctx), None


__all__ = ["DecisionContext", "DecisionEngine", "JevEngine", "RuleEngine", "evaluate"]
