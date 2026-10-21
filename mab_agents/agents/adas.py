"""ADAS & OEM Procedures Advisor: makes sure calibrations are scoped, documented and billed."""
from __future__ import annotations

from anthropic import beta_tool

from ..tools import adas_tools
from .base import Agent, Ctx, j

INSTRUCTIONS = """
You are the shop's conscience on driver-assistance systems. A missed calibration is a liability event, so you err toward checking.
- From operations on an estimate or RO, use `calibration_checklist` to list systems that commonly need calibration, why, and the process steps.
- You never assert an OEM requirement as fact. Phrase it as "the OEM procedure for this VIN will say whether X is required; look under <section>". Name the research sources: OEM1Stop, the OEM's own technical information site, ALLDATA/Mitchell/I-CAR RTS.
- Separate static calibrations (targets, level floor, measured distances) from dynamic (road drive at speed with lane markings) and tell the writer which the shop can do in-house vs sublet.
- Pre-conditions matter: full fuel or specified load, correct tire pressure, ride height, no cargo, windshield clean, OEM-equivalent glass with correct bracket. List them.
- Billing: each calibration is a separate line (diagnostic labor or sublet + markup), plus pre- and post-repair scans. Say so plainly.
- Documentation: the RO must contain the OEM procedure printout, pre/post scan reports and the calibration completion report. Remind the user to attach them.
Output: a checklist first, then notes, in under 250 words unless asked for more.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def calibration_checklist(operations: str, year: int = 0, make: str = "", model: str = "", airbags_deployed: bool = False) -> str:
        """List ADAS calibrations likely triggered by the given repair operations.

        Args:
            operations: Comma-separated operations or damaged areas, e.g. "windshield replace, front bumper cover R&I, alignment".
            year: Model year (0 if unknown).
            make: Make (optional).
            model: Model (optional).
            airbags_deployed: Whether an airbag deployed.
        """
        ops = [o.strip() for o in operations.split(",")]
        items = adas_tools.calibration_checklist(ops, airbags_deployed=airbags_deployed)
        vehicle = " ".join(str(x) for x in (year or "", make, model) if x).strip()
        return j(adas_tools.checklist_report(items, vehicle=vehicle))

    @beta_tool
    def calibration_checklist_for_ro(ro_number: str) -> str:
        """Build the ADAS checklist from the estimate lines on a repair order.

        Args:
            ro_number: The RO number.
        """
        ro = ctx.store.require(ro_number)
        ops = [f"{l.operation} {l.description}" for l in ro.lines]
        items = adas_tools.calibration_checklist(ops, airbags_deployed=ro.airbags_deployed)
        return j({"ro": ro.ro_number, **adas_tools.checklist_report(items, vehicle=ro.vehicle.label())})

    return Agent(
        key="adas",
        name="the ADAS & OEM Procedures Advisor",
        role="the technician who makes sure every camera, radar and sensor the shop touches is calibrated, documented and billed",
        instructions=INSTRUCTIONS,
        tools=[calibration_checklist, calibration_checklist_for_ro],
        keywords=["adas", "calibration", "calibrate", "radar", "camera", "windshield", "lane", "blind spot", "oem procedure", "position statement", "scan", "pre-scan", "post-scan", "sensor"],
        examples=[
            "2022 CR-V, replacing the windshield and R&I front bumper cover. What calibrations do I scope?",
            "Build the ADAS checklist for RO 26-1051.",
        ],
        shop=ctx.shop,
    )
