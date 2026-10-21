"""Parts Coordinator: right part, right vendor, on time, with the paperwork to prove it."""
from __future__ import annotations

from anthropic import beta_tool

from .. import config
from ..tools import parts_tools
from .base import Agent, Ctx, j

INSTRUCTIONS = """
You run parts for the shop. Parts delays are the number one cause of cycle-time misses, so you are relentless about ETAs.
- `build_order` turns an RO's replace lines into an order with a recommended part type per the carrier's rule and a vendor hint. Safety, structural and electronic parts stay OEM no matter what the carrier prefers; say so when you override.
- `parts_status` shows outstanding, late and backordered parts. For anything late, propose: call the vendor for a new ETA, source from an alternate (another dealer, certified aftermarket, recycled with photos), or notify the customer and the carrier for a rental extension.
- `rfq_email` drafts a quote request; `update_part` records order/ETA/received.
- Mirror-match rule: before ordering a replacement for a mis-shipped or damaged part, note it on the RO with photos so the return credit is not lost.
- Always close with the single most important next action and who does it.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def build_order(ro_number: str) -> str:
        """Build the parts order from an RO's replace lines, applying the carrier's parts rule.

        Args:
            ro_number: The RO number.
        """
        ro = ctx.store.require(ro_number)
        rule = config.carrier_rule(ctx.shop, ro.insurer)
        parts = parts_tools.build_parts_order(ro, rule, today=ctx.today)
        return j({"ro": ro.ro_number, "insurer": ro.insurer or "customer pay", "carrier_rule": rule, "parts": [p.__dict__ for p in parts]})

    @beta_tool
    def parts_status(ro_number: str) -> str:
        """Outstanding, late and backordered parts for an RO.

        Args:
            ro_number: The RO number.
        """
        return j(parts_tools.parts_status(ctx.store.require(ro_number), today=ctx.today))

    @beta_tool
    def rfq_email(ro_number: str, vendor: str = "Parts Department", only_outstanding: bool = True) -> str:
        """Draft a request-for-quote email to a vendor for an RO's parts.

        Args:
            ro_number: The RO number.
            vendor: Vendor or contact name for the greeting.
            only_outstanding: Only include parts not yet received.
        """
        ro = ctx.store.require(ro_number)
        parts = [p for p in ro.parts if (p.status in {"needed", "backordered"} or not only_outstanding)]
        if not parts:
            rule = config.carrier_rule(ctx.shop, ro.insurer)
            parts = parts_tools.build_parts_order(ro, rule, today=ctx.today)
        return j(parts_tools.rfq_email(ro, parts, ctx.shop, vendor=vendor))

    @beta_tool
    def update_part(ro_number: str, description_contains: str, status: str, eta: str = "", vendor: str = "", price: float = 0.0) -> str:
        """Update a part's status/ETA on the RO (matches the first part whose description contains the text).

        Args:
            ro_number: The RO number.
            description_contains: Text to find the part by, e.g. "headlamp".
            status: One of needed, ordered, backordered, received, returned.
            eta: ISO date (YYYY-MM-DD) if known.
            vendor: Vendor name if known.
            price: Price if known.
        """
        ro = ctx.store.require(ro_number)
        for p in ro.parts:
            if description_contains.lower() in p.description.lower():
                p.status = status
                if eta:
                    p.eta = eta
                if vendor:
                    p.vendor = vendor
                if price:
                    p.price = price
                ctx.store.upsert(ro)
                ctx.store.add_note(ro_number, f"Part '{p.description}' -> {status}{' ETA ' + eta if eta else ''}", author="parts-agent")
                return j({"updated": p.__dict__})
        return j({"error": f"No part on {ro_number} matching '{description_contains}'", "parts": [p.description for p in ro.parts]})

    return Agent(
        key="parts",
        name="the Parts Coordinator",
        role="the person who knows where every part is, when it lands, and what to do when it doesn't",
        instructions=INSTRUCTIONS,
        tools=[build_order, parts_status, rfq_email, update_part],
        keywords=["parts", "part", "backorder", "back order", "eta", "vendor", "dealer", "aftermarket", "capa", "recycled", "lkq", "order", "rfq", "quote", "mis-shipped", "return"],
        examples=["What's late on RO 26-1042 and what do I do about it?", "Build the parts order for 26-1051 and draft the RFQ."],
        shop=ctx.shop,
    )
