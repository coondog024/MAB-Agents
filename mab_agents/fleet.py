"""Fleet registry and router."""
from __future__ import annotations

import re
from typing import Any

from .agents import adas, customer, estimator, insurance, intake, manager, parts, production
from .agents.base import Agent, Ctx

MODULES = [intake, estimator, adas, parts, production, customer, insurance, manager]


def build_fleet(ctx: Ctx) -> dict[str, Agent]:
    return {a.key: a for a in (m.build(ctx) for m in MODULES)}


def route(question: str, fleet: dict[str, Agent]) -> tuple[str, dict[str, int]]:
    """Pick an agent by keyword score. Ties and no-hits go to the manager, who has every tool."""
    q = question.lower()
    scores: dict[str, int] = {}
    for key, agent in fleet.items():
        if key == "manager":
            continue
        score = 0
        for kw in agent.keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", q):
                score += 2 if " " in kw else 1
        scores[key] = score
    best = max(scores, key=lambda k: scores[k]) if scores else "manager"
    top = scores.get(best, 0)
    if top == 0 or list(scores.values()).count(top) > 1:
        return "manager", scores
    return best, scores


def describe(fleet: dict[str, Agent]) -> list[dict[str, Any]]:
    return [
        {"key": a.key, "name": a.name, "role": a.role, "tools": [getattr(t, "name", getattr(t, "__name__", "?")) for t in a.tools], "examples": a.examples}
        for a in fleet.values()
    ]
