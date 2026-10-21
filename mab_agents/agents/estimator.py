"""Estimate Auditor & Supplement Writer: finds what the first estimate missed."""
from __future__ import annotations


from ..tools import estimate_tools
from .base import Agent, Ctx, beta_tool, j

INSTRUCTIONS = """
You review damage estimates the way a senior estimator does before they leave the shop or go to the carrier.
- Always run `audit_estimate_text` (pasted CSV/JSON) or `audit_repair_order` (RO on file) first. The result has two parts: `findings` (structural checks: blends, scans, corrosion protection, A/C, alignment) and `catalogue` (the shop's own not-included catalogue, 51 rules: triggered = missing, verify_if_equipped = depends on an option you must confirm, satisfied = already on the estimate). Report triggered items grouped by severity, each with the line that would go on the estimate; list verify-if-equipped items as questions for the writer.
- If the estimate came from CCC via EMS, `written_at_rates` and `rate_warnings` show the carrier's labor rates versus our posted rates; call out any rate below posted as a short-pay to address before the car is delivered.
- Then run `estimate_totals` so the writer sees labor by type, refinish hours, materials and parts at our posted rates.
- For each high finding, give a one-sentence justification the carrier will accept: cite the operation as "not included" per the estimating system's procedure pages, or point to the OEM procedure that requires it, and say which photo proves it.
- Blend logic: a refinished panel with an adjacent same-colour panel needs a blend unless the colour is a solid non-metallic and the writer confirms a match from a spray-out.
- Flag anything priced below our posted rate or any labor type that looks mis-coded (structural time coded as body, mechanical as body).
- If the estimate was written by a carrier or a photo-app, say so and list the standard teardown items it will not contain.
- Output format: a short summary line, then "Add to estimate" as a numbered list (description, labor hours/type, refinish hours, part), then "Verify" items, then totals.
Do not invent labor times. Where a time is needed, say "per database" or "actual time, document it" rather than guessing a number.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def audit_estimate_text(estimate: str, airbags_deployed: bool = False, adas_equipped: bool = True, model_year: int = 0) -> str:
        """Audit an estimate pasted as CSV (header: line,operation,description,labor_hours,labor_type,paint_hours,part_type,part_number,qty,part_price,note) or a JSON list of lines.

        Args:
            estimate: The estimate text in CSV or JSON.
            airbags_deployed: Whether airbags deployed in the loss.
            adas_equipped: Whether the vehicle has driver-assistance systems.
            model_year: Vehicle model year, 0 if unknown.
        """
        lines = estimate_tools.parse_estimate(estimate)
        findings = estimate_tools.audit_estimate(lines, airbags_deployed=airbags_deployed, adas_equipped=adas_equipped, model_year=model_year or None, state=ctx.shop["shop"].get("state"))
        return j({"lines_parsed": len(lines), **estimate_tools.audit_report(findings, estimate_tools.evaluate_rules(lines))})

    @beta_tool
    def audit_repair_order(ro_number: str) -> str:
        """Audit the estimate lines stored on a repair order.

        Args:
            ro_number: The RO number, e.g. 26-1042.
        """
        ro = ctx.store.require(ro_number)
        findings = estimate_tools.audit_estimate(ro.lines, airbags_deployed=ro.airbags_deployed, adas_equipped=ro.adas_equipped, model_year=ro.vehicle.year, state=ctx.shop["shop"].get("state"))
        from ..ems import rate_notes

        return j({"ro": ro.ro_number, "vehicle": ro.vehicle.label(), "insurer": ro.insurer, "estimate": {"id": ro.estimate_id, "supp_no": ro.supp_no, "system": ro.estimate_system, "totals": ro.estimate_totals, "written_at_rates": ro.labor_rates, "rate_warnings": rate_notes(ro, ctx.rates())},
                  "lines": [l.__dict__ for l in ro.lines], **estimate_tools.audit_report(findings, estimate_tools.evaluate_rules(ro.lines))})

    @beta_tool
    def estimate_totals(estimate: str = "", ro_number: str = "") -> str:
        """Totals at the shop's posted rates for either pasted estimate text or an RO on file. Provide one of the two arguments.

        Args:
            estimate: Estimate text in CSV or JSON (optional).
            ro_number: RO number to total instead (optional).
        """
        lines = ctx.store.require(ro_number).lines if ro_number else estimate_tools.parse_estimate(estimate)
        return j(estimate_tools.totals(lines, ctx.rates()))

    return Agent(
        key="estimator",
        name="the Estimate Auditor",
        role="a senior collision estimator who catches not-included operations and writes supplements that get approved the first time",
        instructions=INSTRUCTIONS,
        tools=[audit_estimate_text, audit_repair_order, estimate_totals],
        keywords=["estimate", "blend", "not included", "p-page", "refinish", "labor time", "missed", "audit", "feather", "prime", "teardown found", "line item", "csv", "not on it", "what's missing", "quarter panel", "labor hours"],
        examples=[
            "Audit RO 26-1042 and tell me what the carrier's estimate missed.",
            "Here's a CSV of the adjuster's estimate for a quarter panel replacement. What's not on it?",
        ],
        shop=ctx.shop,
    )
