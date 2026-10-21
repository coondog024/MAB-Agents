"""Shared agent scaffolding: context, prompt assembly and the Agent record."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from ..store import Store

GROUND_RULES = """
Ground rules for every agent in this fleet:
- You work for the shop named above. Facts about the shop (rates, hours, DRPs, certifications) come only from the shop profile; never invent them.
- Use your tools for numbers, dates, estimate math and repair-order facts. Do not do that arithmetic in your head.
- Never state an OEM repair procedure, calibration requirement or statute as settled fact unless a tool or the user supplied it. Say "verify in the OEM procedure" and name what to look for.
- You draft; humans send. Customer texts, insurer emails and parts orders are returned as drafts for a person to approve. Never claim something was sent.
- Collision repair language: RO (repair order), DRP (direct repair program), ACV (actual cash value), R&I (remove and install), R&R (remove and replace), blend, supplement, teardown/blueprint, cycle time, touch time, keys-to-keys.
- Be concise and practical. Shop staff read on a phone between cars. Lead with the answer or the draft, then the reasoning in a few lines.
- Protect customer privacy: no claim numbers, VINs or phone numbers in anything meant for public posting.
""".strip()


@dataclass
class Ctx:
    shop: dict[str, Any]
    store: Store
    today: date = field(default_factory=date.today)

    def rates(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.shop["rates"].items()}


@dataclass
class Agent:
    key: str
    name: str
    role: str
    instructions: str
    tools: list[Callable[..., Any]]
    keywords: list[str]
    examples: list[str]
    shop: dict[str, Any]

    @property
    def system_prompt(self) -> str:
        return "\n\n".join([shop_block(self.shop), GROUND_RULES, f"You are {self.name}, {self.role}.", self.instructions.strip()])


def shop_block(shop: dict[str, Any]) -> str:
    s = shop["shop"]
    rules = "\n".join(
        f"  - {r['name']}: parts: {r.get('parts_preference', 'n/a')}; rental max {r.get('rental_max_days', 'n/a')} days; "
        f"photos required for supplements: {r.get('supplement_photos_required', 'n/a')}; notes: {r.get('notes', '')}"
        for r in shop.get("carrier_rules", [])
    ) or "  (none on file)"
    return (
        f"Shop profile\n"
        f"- Name: {s['name']} ({s.get('short_name', s['name'])})\n"
        f"- Address: {s['address']} | Phone: {s['phone']} | Hours: {s['hours']}\n"
        f"- State: {s['state']} | Manager: {s.get('manager', 'Shop Manager')}\n"
        f"- Certifications: {', '.join(s.get('certifications', [])) or 'none listed'}\n"
        f"- DRP partners: {', '.join(s.get('drps', [])) or 'none'}\n"
        f"- Posted rates ($/hr): {json.dumps(shop['rates'])}\n"
        f"- Targets: {json.dumps(shop.get('targets', {}))}\n"
        f"- Carrier rules:\n{rules}"
    )


def j(obj: Any) -> str:
    """Tool results go back to the model as compact JSON strings."""
    return json.dumps(obj, default=str, ensure_ascii=False)
