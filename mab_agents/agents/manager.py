"""Shop Manager: the coordinator that sees the whole shop and borrows every other agent's tools."""
from __future__ import annotations


from ..tools import kpi_tools
from . import adas, customer, estimator, front_desk, insurance, parts, production
from .base import Agent, Ctx, beta_tool, j, tool_name

INSTRUCTIONS = """
You are the general manager's chief of staff. You have every specialist's tools and you use them in combination.
- Mornings: `daily_brief` first, then dig into anything red with the production, parts and insurance tools. Output a prioritized list of actions with an owner for each (writer, parts, production, front desk, manager).
- Questions about money: `shop_kpis` and estimate totals. Explain the number and the one lever that moves it.
- When a question spans departments (an RO is stalled because a supplement is pending because parts can't be ordered), trace the chain and name the single blocking step.
- Keep the whole answer under 300 words unless the user asks for detail. Numbers in a short table.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def daily_brief() -> str:
        """The morning brief: open ROs, due today, overdue, stalled, rentals, headline KPIs."""
        return j(kpi_tools.daily_brief(ctx.store.list(include_delivered=True), ctx.shop, today=ctx.today))

    @beta_tool
    def shop_kpis() -> str:
        """Cycle time, touch time, average RO, supplement ratio versus the shop's targets."""
        return j(kpi_tools.kpis(ctx.store.list(include_delivered=True), ctx.shop, today=ctx.today))

    @beta_tool
    def repair_order(ro_number: str) -> str:
        """Everything on file for one RO.

        Args:
            ro_number: The RO number.
        """
        return j(ctx.store.require(ro_number).to_dict())

    @beta_tool
    def add_note(ro_number: str, text: str) -> str:
        """Add a note to an RO's history.

        Args:
            ro_number: The RO number.
            text: Note text.
        """
        ctx.store.add_note(ro_number, text, author="manager-agent")
        return j({"ok": True})

    shared = []
    for mod in (production, parts, estimator, insurance, customer, adas, front_desk):
        shared.extend(mod.build(ctx).tools)
    # De-duplicate by tool name, keeping the first.
    seen: set[str] = set()
    tools = []
    for t in [daily_brief, shop_kpis, repair_order, add_note] + shared:
        name = tool_name(t)
        if name not in seen:
            seen.add(name)
            tools.append(t)

    return Agent(
        key="manager",
        name="the Shop Manager agent",
        role="the coordinator who sees the whole shop, knows which specialist to pull in, and turns the morning into a short list of actions",
        instructions=INSTRUCTIONS,
        tools=tools,
        keywords=["brief", "morning", "today", "kpi", "numbers", "profit", "overview", "everything", "status of", "what's going on", "priorities", "report"],
        examples=["Give me the morning brief.", "Why is 26-1047 taking so long and what do I do about it?"],
        shop=ctx.shop,
    )
