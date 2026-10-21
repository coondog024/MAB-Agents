"""Accounts Receivable & Short-Pay agent: the money the shop already earned and hasn't been paid."""
from __future__ import annotations

import json as _json

from ..tools import ar_tools
from .base import Agent, Ctx, beta_tool, j

INSTRUCTIONS = """
You watch the money after the car leaves. Carriers short-pay routinely (safety inspections, in-process scans, steering-column checks, weld tests, not-included operations), and a balance nobody chases is a write-off.
- Start of week: `ar_aging` for every open balance by age bucket, oldest and largest first; name who owes it (customer vs carrier) and the next action.
- When a payment arrives: `record_payment`, then compare to the expected split; any carrier short is a `short_pay_letter` the same day, with each line's basis (OEM procedure, not-included operation per the estimating guide, or found on teardown) and the documents attached.
- Short-pay SOP for Maryland: document each line with the OEM or DEG cite, invoice the balance, carry it as receivable (never write it off quietly), send the letter, follow up at 10 business days, then give the customer the `mia_complaint_outline`. Insurance Article 27-303/27-304 (unfair claim settlement, failure to act promptly), COMAR 31.15.07 (15 working days to acknowledge, written status every 45 days) and 10-502 (usual and customary) are the frames; search_knowledge has the Maryland rules.
- Customer balances: deductible plus customer-pay items; never call a customer about a balance the front desk didn't disclose before pickup; fix the disclosure first.
- Be specific with numbers and dates. Drafts are for a human to send.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def ar_aging() -> str:
        """Open balances on ready/delivered ROs by age bucket, with customer and carrier split."""
        return j(ar_tools.aging(ctx.store.list(include_delivered=True), ctx.rates(), today=ctx.today))

    @beta_tool
    def ro_balance(ro_number: str) -> str:
        """Invoice total, payments and balance for one RO.

        Args:
            ro_number: The RO number.
        """
        return j(ar_tools.balance(ctx.store.require(ro_number), ctx.rates()))

    @beta_tool
    def record_payment(ro_number: str, payer: str, amount: float, method: str = "check", reference: str = "", paid_on: str = "") -> str:
        """Record a payment received on an RO.

        Args:
            ro_number: The RO number.
            payer: "customer" or the carrier name.
            amount: Dollars received.
            method: check, eft, card or cash.
            reference: Check or EFT number.
            paid_on: ISO date; today if blank.
        """
        from datetime import date as _date

        day = paid_on or ctx.today.isoformat()
        _date.fromisoformat(day)
        if amount <= 0:
            raise ValueError("amount must be positive")

        def apply(ro) -> None:
            ro.payments.append({"date": day, "payer": payer, "amount": round(float(amount), 2), "method": method, "reference": reference})

        ro = ctx.store.mutate(ro_number, apply)
        ctx.store.add_note(ro_number, f"Payment ${amount:,.2f} from {payer} ({method} {reference}) on {day}", author="ar-agent")
        return j({"recorded": True, **ar_tools.balance(ro, ctx.rates())})

    @beta_tool
    def short_pay_letter(ro_number: str, items_json: str, paid_amount: float) -> str:
        """Draft the letter to the carrier for unpaid invoice lines.

        Args:
            ro_number: The RO number.
            items_json: JSON list: [{"description": str, "amount": float, "justification": str}].
            paid_amount: What the carrier paid.
        """
        items = _json.loads(items_json)
        return j(ar_tools.short_pay_letter(ctx.store.require(ro_number), ctx.shop, items, paid_amount, today=ctx.today))

    @beta_tool
    def mia_complaint_outline(ro_number: str, items_json: str, timeline_json: str = "[]") -> str:
        """Outline of a Maryland Insurance Administration complaint for a short-paid claim.

        Args:
            ro_number: The RO number.
            items_json: JSON list of unpaid items: [{"description": str, "amount": float, "justification": str}].
            timeline_json: JSON list of dated contact strings, e.g. ["2026-10-02 supplement sent", "2026-10-09 called adjuster"].
        """
        return j(ar_tools.mia_complaint_outline(ctx.store.require(ro_number), _json.loads(items_json), _json.loads(timeline_json)))

    return Agent(
        key="ar",
        name="the Accounts Receivable & Short-Pay agent",
        role="the person who makes sure every dollar the shop earned actually arrives, and documents the ones a carrier refuses",
        instructions=INSTRUCTIONS,
        tools=[ar_aging, ro_balance, record_payment, short_pay_letter, mia_complaint_outline],
        keywords=["short pay", "short-pay", "short paid", "short-paid", "receivable", "receivables", "aging", "ar", "outstanding", "over 30 days", "over 60 days", "over 90 days", "past due", "who owes", "unpaid", "owes us", "balance due", "paid us", "payment received", "eft", "check came", "write-off", "write off", "collect", "collections", "didn't pay", "did not pay", "mia complaint"],
        examples=["What's outstanding over 30 days and who do I call?", "Example Mutual paid $3,470 on 26-1042 but the invoice was $4,120. Draft the short-pay letter."],
        shop=ctx.shop,
    )
