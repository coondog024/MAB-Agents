"""Production Manager: keeps cars moving and promise dates honest."""
from __future__ import annotations


from ..tools import schedule_tools
from .base import Agent, Ctx, beta_tool, j

INSTRUCTIONS = """
You run the production floor. Your two numbers are touch time (labor hours per calendar day a car is in the shop) and keys-to-keys cycle time; the shop targets are in the profile.
- Start with `wip_board` for the big picture and `stalled_ros` for what needs a push today. Rental cars and overdue promises outrank everything else.
- `promise_date` gives a planning estimate from remaining stages, hours and parts ETA. Add a day of buffer when you tell a customer, and never move a promise date without saying why and who tells the customer.
- `move_stage` records a stage change and notes it; it also returns the customer update draft so the front desk can send it.
- `tech_load` compares open hours to daily capacity so you can see a bottleneck forming (usually paint).
- Think in constraints: parts not here means do not disassemble further than needed; paint is the bottleneck so batch same-colour jobs; reassembly waits on nothing but clips.
- Your daily output is a prioritized list: RO, vehicle, stage, days in stage, what to do, who does it.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def wip_board() -> str:
        """Work-in-process board: every open RO grouped by production stage."""
        return j(schedule_tools.wip_board(ctx.store.list(), today=ctx.today))

    @beta_tool
    def stalled_ros(max_days: float = 0) -> str:
        """ROs that have sat in one stage too long.

        Args:
            max_days: Days in stage before an RO counts as stalled; 0 uses the shop target.
        """
        limit = max_days or float(ctx.shop.get("targets", {}).get("stalled_after_days", 2))
        return j(schedule_tools.stalled(ctx.store.list(), limit, today=ctx.today))

    @beta_tool
    def promise_date(ro_number: str, parts_eta: str = "") -> str:
        """Planning estimate of the delivery date for an RO from its current stage.

        Args:
            ro_number: The RO number.
            parts_eta: ISO date the last part lands, if known.
        """
        return j(schedule_tools.promise_date(ctx.store.require(ro_number), parts_eta=parts_eta or None, start=ctx.today))

    @beta_tool
    def move_stage(ro_number: str, stage: str, note: str = "") -> str:
        """Move an RO to a new stage and return the customer update draft.

        Args:
            ro_number: The RO number.
            stage: New stage: scheduled, checked_in, blueprint, approval, parts_ordered, parts_received, body, paint_prep, paint, reassembly, qc, ready, delivered.
            note: Optional note for the RO history.
        """
        from ..tools import customer_tools

        ro = ctx.store.set_stage(ro_number, stage, author="production-agent", note=note)
        draft = customer_tools.stage_update(ro, ctx.shop)
        ctx.store.log_message(ro_number, draft["channel"], draft["to"], draft["text"])
        return j({"ro": ro.ro_number, "stage": ro.stage, "customer_update_draft": draft})

    @beta_tool
    def tech_load(technicians: str = "Body tech A:8,Body tech B:8,Painter:8") -> str:
        """Open body and paint hours versus daily technician capacity.

        Args:
            technicians: Comma-separated "name:hours per day"; names containing 'paint' count as paint capacity.
        """
        techs: dict[str, float] = {}
        for part in technicians.split(","):
            if ":" in part:
                n, h = part.rsplit(":", 1)
                techs[n.strip()] = float(h)
        return j(schedule_tools.technician_load(ctx.store.list(), techs, today=ctx.today))

    return Agent(
        key="production",
        name="the Production Manager",
        role="the floor manager who keeps every car moving and every promise date honest",
        instructions=INSTRUCTIONS,
        tools=[wip_board, stalled_ros, promise_date, move_stage, tech_load],
        keywords=["production", "schedule", "wip", "stalled", "cycle time", "touch time", "promise", "delivery date", "when will", "bottleneck", "paint booth", "move", "stage", "board", "capacity", "tech", "floor", "focus on today", "to paint", "to body", "to reassembly"],
        examples=["What should the floor focus on today?", "Move 26-1047 to paint and give me the customer text."],
        shop=ctx.shop,
    )
