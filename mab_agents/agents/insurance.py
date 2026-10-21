"""Insurance & Claims Liaison: supplements, total-loss math, rentals, storage."""
from __future__ import annotations

from anthropic import beta_tool

from ..tools import insurer_tools
from .base import Agent, Ctx, j

INSTRUCTIONS = """
You handle the carrier side: supplements, rental extensions, total-loss conversations and storage notices.
- `supplement_request` drafts the email from itemized findings at posted rates; attach the audit findings' justifications and name the photos. Follow the carrier rule on file (portal vs email, photos required, call-before-pull).
- `total_loss_check` runs the state threshold or total loss formula. Say clearly it is a reference and the carrier's valuation governs; coach the writer on what the customer can do (dispute the ACV with comparable listings, ask for the valuation report, owner-retain options).
- `rental_extension` drafts the ask with the real reason and the new date; send it before the rental expires, not after.
- `storage_notice` computes teardown and storage charges for a vehicle a carrier is moving or totaling.
- Tone with adjusters: factual, brief, procedure-cited, never adversarial. Every ask ties to pre-loss condition, OEM procedure or safety.
- Never promise coverage to a customer; only the carrier can.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def supplement_request(ro_number: str, items_json: str, photos_attached: bool = True) -> str:
        """Draft a supplement email to the carrier.

        Args:
            ro_number: The RO number.
            items_json: JSON list of items: [{"description": str, "labor_hours": float, "labor_type": str, "paint_hours": float, "part_price": float, "justification": str}].
            photos_attached: Whether photos are attached.
        """
        import json as _json

        ro = ctx.store.require(ro_number)
        items = _json.loads(items_json)
        return j(insurer_tools.supplement_request(ro, ctx.shop, items, photos_attached=photos_attached))

    @beta_tool
    def total_loss_check(repair_cost: float, acv: float, state: str = "", salvage_value: float = 0.0) -> str:
        """Compare repair cost to actual cash value under the state's total-loss rule.

        Args:
            repair_cost: Full repair cost including supplements.
            acv: Actual cash value the carrier assigned.
            state: Two-letter state; empty uses the shop's state.
            salvage_value: Salvage bid, needed in total-loss-formula states.
        """
        st = state or ctx.shop["shop"]["state"]
        return j(insurer_tools.total_loss_check(repair_cost, acv, st, salvage_value or None))

    @beta_tool
    def rental_extension(ro_number: str, new_date: str, reason: str) -> str:
        """Draft a rental extension request to the carrier.

        Args:
            ro_number: The RO number.
            new_date: ISO date to extend through.
            reason: Reason, e.g. "supplement approval took 4 business days".
        """
        return j(insurer_tools.rental_extension(ctx.store.require(ro_number), ctx.shop, new_date, reason))

    @beta_tool
    def storage_notice(ro_number: str, days: int) -> str:
        """Teardown plus storage charges for a vehicle being moved or totaled.

        Args:
            ro_number: The RO number.
            days: Days of storage to bill.
        """
        return j(insurer_tools.storage_notice(ctx.store.require(ro_number), ctx.shop, days, today=ctx.today))

    @beta_tool
    def carrier_rule(carrier: str) -> str:
        """The shop's rule card for a carrier (parts preference, photo and rental rules).

        Args:
            carrier: Carrier name.
        """
        from .. import config

        return j(config.carrier_rule(ctx.shop, carrier) or {"error": f"No rule on file for {carrier}; treat as non-DRP: OEM parts quoted, customer authorizes alternatives."})

    return Agent(
        key="insurance",
        name="the Insurance & Claims Liaison",
        role="the person who gets supplements approved, rentals extended and total-loss conversations handled without a fight",
        instructions=INSTRUCTIONS,
        tools=[supplement_request, total_loss_check, rental_extension, storage_notice, carrier_rule],
        keywords=["insurance", "insurer", "carrier", "adjuster", "claim", "total loss", "totaled", "acv", "rental extension", "storage", "drp", "desk review", "appraiser", "deductible", "approval"],
        examples=["Repair is $14,200 on a RAV4 the carrier valued at $17,500. Is it a total?", "Draft the supplement for 26-1042 from the audit findings."],
        shop=ctx.shop,
    )
