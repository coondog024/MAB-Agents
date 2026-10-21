"""Fleet registry and router."""
from __future__ import annotations

import re
from typing import Any

from .agents import adas, ar, customer, estimator, front_desk, insurance, intake, manager, parts, production
from .agents.base import Agent, Ctx, tool_name, with_knowledge

MODULES = [front_desk, intake, estimator, adas, parts, production, customer, insurance, ar, manager]


def build_fleet(ctx: Ctx) -> dict[str, Agent]:
    fleet: dict[str, Agent] = {}
    for m in MODULES:
        a = m.build(ctx)
        a.tools = with_knowledge(a.tools, ctx)
        fleet[a.key] = a
    return fleet


# Openers a new hire types regardless of topic. They nudge toward the Front Desk agent but
# must not outrank a real domain word from another agent.
GENERIC_OPENERS = {"how do i", "look up", "lookup", "walk me through", "phone", "caller", "calling", "customer called"}


def _weight(kw: str) -> float:
    if kw in GENERIC_OPENERS:
        return 0.5
    return 2.0 if " " in kw else 1.0


def score(question: str, agent: Agent) -> float:
    q = question.lower()
    total = 0.0
    for kw in agent.keywords:
        # prefix match on the last word so 'calibration' also hits 'calibrations'
        if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"[a-z]*", q):
            total += _weight(kw)
    return total


def route(question: str, fleet: dict[str, Agent]) -> tuple[str, dict[str, float]]:
    """Pick an agent by weighted keyword score.

    Needs a clear winner (at least one point ahead); otherwise the Shop Manager,
    which carries every tool, takes it.
    """
    scores = {key: score(question, a) for key, a in fleet.items() if key != "manager"}
    if not scores:
        return "manager", scores
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top <= 0 or top - second < 1.0:
        return "manager", scores
    return best, scores


def describe(fleet: dict[str, Agent]) -> list[dict[str, Any]]:
    return [
        {"key": a.key, "name": a.name, "role": a.role, "tools": [tool_name(t) for t in a.tools], "examples": a.examples}
        for a in fleet.values()
    ]
