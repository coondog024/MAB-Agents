"""Customer Communications & CSI: every update, delay and review, in plain words."""
from __future__ import annotations

from anthropic import beta_tool

from ..tools import customer_tools
from .base import Agent, Ctx, j

INSTRUCTIONS = """
You write what the front desk sends to customers. Customers judge a collision shop by communication more than by paint.
- `status_update` drafts the stage message from the RO facts; `delay_notice` and `supplement_notice` handle bad news; `review_request` goes out after delivery; `review_reply` gives guidelines for answering a public review.
- Bad news early beats good news late. A delay text goes out the day you know, with the reason in one plain clause and the new date.
- Plain English only: "hidden damage we found during teardown" not "supplement"; "the insurance company's approval" not "the adjuster's supp"; "a systems scan" not "post-repair scan".
- One message, one purpose, under the SMS limit. Put the action for the customer (deductible, rental return, pickup hours) in the last sentence.
- Public review replies: thank, acknowledge the specific point, no claim or repair details, take complaints offline with the manager's name, under 80 words.
- Every draft you return is for a human to approve. Say "Draft:" before it.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def status_update(ro_number: str, stage: str = "") -> str:
        """Draft the customer update for an RO's current (or given) stage.

        Args:
            ro_number: The RO number.
            stage: Optional stage to draft for instead of the current one.
        """
        ro = ctx.store.require(ro_number)
        draft = customer_tools.stage_update(ro, ctx.shop, stage or None)
        ctx.store.log_message(ro_number, draft["channel"], draft["to"], draft["text"])
        return j(draft)

    @beta_tool
    def delay_notice(ro_number: str, reason: str, new_date: str) -> str:
        """Draft a delay text with the reason and new target date.

        Args:
            ro_number: The RO number.
            reason: Plain-language reason, e.g. "the bumper cover is on backorder until Friday".
            new_date: New target completion, ISO date.
        """
        ro = ctx.store.require(ro_number)
        draft = customer_tools.delay_notice(ro, ctx.shop, reason, new_date)
        ctx.store.log_message(ro_number, "sms", draft["to"], draft["text"])
        return j(draft)

    @beta_tool
    def supplement_notice(ro_number: str, what_was_found: str, extra_days: int) -> str:
        """Draft the text that explains hidden damage found at teardown.

        Args:
            ro_number: The RO number.
            what_was_found: Plain words, e.g. "a bent radiator support and a cracked headlamp bracket".
            extra_days: Expected added days.
        """
        ro = ctx.store.require(ro_number)
        return j(customer_tools.supplement_notice(ro, ctx.shop, what_was_found, extra_days))

    @beta_tool
    def review_request(ro_number: str) -> str:
        """Draft the post-delivery review request.

        Args:
            ro_number: The RO number.
        """
        return j(customer_tools.review_request(ctx.store.require(ro_number), ctx.shop))

    @beta_tool
    def review_reply(stars: int) -> str:
        """Guidelines for replying to a public review with the given star rating.

        Args:
            stars: Star rating 1-5.
        """
        return j({"stars": stars, "guidelines": customer_tools.review_reply_guidelines(stars), "manager": ctx.shop["shop"].get("manager")})

    return Agent(
        key="customer",
        name="the Customer Communications agent",
        role="the front-desk voice that keeps customers calm, informed and coming back",
        instructions=INSTRUCTIONS,
        tools=[status_update, delay_notice, supplement_notice, review_request, review_reply],
        keywords=["text", "sms", "customer update", "update the customer", "message", "email the customer", "review", "google review", "csi", "complaint", "angry", "delay", "pickup", "deductible", "rental return"],
        examples=["Draft the delay text for 26-1042: bumper cover backordered to the 28th.", "Reply to a 2-star review that says we took three weeks and never called."],
        shop=ctx.shop,
    )
