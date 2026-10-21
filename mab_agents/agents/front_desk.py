"""Front Desk agent: the trainer and safety net for whoever is answering the phone today."""
from __future__ import annotations


from ..tools import customer_tools, frontdesk_tools
from .base import Agent, Ctx, beta_tool, j

INSTRUCTIONS = """
You sit next to the person at the front desk. Assume they started this week and know nothing about collision repair; they are smart and busy. Your job is to make them sound like a ten-year veteran on the phone and never miss a customer.
- "What do I say" questions: search the shop's playbook with `search_knowledge` first (it is shop policy), then `phone_script` for the default script. Give the words to say, in quotes, then two or three bullets of why. Short. They are on the phone.
- A customer calls: use `lookup_customer` with the phone number or name, read the status in plain words, and hand them the ready-to-send text from `status_text`.
- Start of day: `todays_calls` is the list of customers who need a touch today and why. Work it top down. `daily_routine` is the checklist.
- Money: `what_customer_owes` before anyone quotes a balance. Never waive a deductible.
- Check-in and delivery: `checklist` gives the steps; walk them through it one item at a time if asked.
- After they send a message, remind them to `log_sent` so the next shift sees it.
- Escalate to the manager, and say so plainly, when: the customer asks for the manager, an adjuster denies a safety operation, a total-loss conversation starts, there is any threat of a complaint, or anything about injury or damage in our care.
Never promise a date, a price or coverage. Those come from production, the estimator and the carrier.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def lookup_customer(query: str) -> str:
        """Find repair orders by phone number, customer name, RO number or vehicle.

        Args:
            query: Phone digits, a name, an RO number like 26-1042, or "RAV4".
        """
        ros = ctx.store.find(query)
        return j({"query": query, "matches": [frontdesk_tools.ro_summary(r, ctx.today) for r in ros] or "none; check spelling or ask for the RO number"})

    @beta_tool
    def status_text(ro_number: str) -> str:
        """The ready-to-send plain-language status text for an RO's current stage.

        Args:
            ro_number: The RO number.
        """
        ro = ctx.store.require(ro_number)
        return j(customer_tools.stage_update(ro, ctx.shop))

    @beta_tool
    def phone_script(situation: str) -> str:
        """Default phone script for a situation: new_claim, status_call, delay_complaint, deductible, insurer_calling, tow_arrival, rental, after_hours_pickup, steering, customer_pay_estimate.

        Args:
            situation: One of the keys above or a few words describing the call.
        """
        return j(frontdesk_tools.phone_script(situation))

    @beta_tool
    def todays_calls() -> str:
        """Customers who need a call or text today, most urgent first, with the reason."""
        return j(frontdesk_tools.update_queue(ctx.store.list(), ctx.store.last_messages(), today=ctx.today, max_silence_days=2))

    @beta_tool
    def checklist(which: str = "checkin") -> str:
        """Step-by-step checklist.

        Args:
            which: "checkin" or "delivery".
        """
        return j({"which": which, "steps": frontdesk_tools.DELIVERY_CHECKLIST if which.lower().startswith("deliv") else frontdesk_tools.CHECKIN_CHECKLIST})

    @beta_tool
    def daily_routine() -> str:
        """Opening, during-the-day and closing routine for the front desk."""
        return j(frontdesk_tools.DAILY_ROUTINE)

    @beta_tool
    def what_customer_owes(ro_number: str, storage_days: int = 0) -> str:
        """Balance due at pickup: deductible or full customer-pay invoice, plus storage if any.

        Args:
            ro_number: The RO number.
            storage_days: Days of storage to bill, if any.
        """
        return j(frontdesk_tools.payment_summary(ctx.store.require(ro_number), ctx.shop, storage_days))

    @beta_tool
    def log_sent(ro_number: str, text: str, channel: str = "sms") -> str:
        """Record that a message was sent to the customer (after a human actually sent it).

        Args:
            ro_number: The RO number.
            text: The message as sent.
            channel: sms, email or call.
        """
        ro = ctx.store.require(ro_number)
        mid = ctx.store.log_message(ro_number, channel, ro.customer.phone or ro.customer.email, text, status="sent")
        ctx.store.add_note(ro_number, f"{channel} sent: {text[:80]}", author="front-desk")
        return j({"logged": mid})

    return Agent(
        key="front_desk",
        name="the Front Desk agent",
        role="the trainer and safety net sitting next to whoever is answering the phone today",
        instructions=INSTRUCTIONS,
        tools=[lookup_customer, status_text, phone_script, todays_calls, checklist, daily_routine, what_customer_owes, log_sent],
        keywords=["what do i say", "how do i", "script", "front desk", "phone", "caller", "calling", "on the phone", "check in", "check-in", "checklist", "who do i call", "call list", "need to text", "need to call", "text today", "call today", "who needs", "owes", "balance", "pay at pickup", "paying the deductible", "pays the deductible", "new hire", "training", "routine", "opening", "closing", "look up", "lookup", "customer named", "walk me through"],
        examples=["A customer is on the phone asking why she pays the deductible to us. What do I say?", "Who do I need to text today?"],
        shop=ctx.shop,
    )
