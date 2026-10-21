"""Intake & Damage Triage: the first conversation with a customer or a tow-in."""
from __future__ import annotations


from ..tools import adas_tools
from .base import Agent, Ctx, beta_tool, j

PHOTO_CHECKLIST = [
    "All four corners of the vehicle, full side, 10-15 ft back",
    "VIN plate (dash) and door-jamb label (paint code, build date)",
    "Odometer and dash with ignition on (warning lights)",
    "Each damaged panel straight-on and at 45 degrees",
    "Close-ups of gaps, misaligned panels, cracked lamps, deployed airbags",
    "Under-hood: radiator support, fans, any fluid leaks",
    "Wheels and tires on the impact side, including sidewall",
    "Interior: airbags, seat belts pulled out, child seats present",
    "Any prior damage not related to this loss (document it now)",
]

INSTRUCTIONS = """
You handle first contact: walk-ins, phone calls, tow-ins and online estimate requests. Goals, in order:
1. Safety and drivability. Ask about airbag deployment, warning lights, fluid leaks, steering pull, wheel damage. If any are present, say the vehicle should not be driven and offer a tow.
2. Claim path. Insurance or customer pay? If insurance: carrier, claim number, whether a claim is filed, and the deductible if they know it. If they ask about shop choice: the customer chooses who repairs the car; the insurer can recommend shops but not require one, and it pays based on the estimate it approves. Never cite a statute. If the carrier is one of our DRPs, say the process is streamlined.
3. Rental. Does the policy carry rental? Does the customer need one? Note it on the RO.
4. Photos. Use `photo_checklist` and tell the customer exactly what to send, or what the writer will shoot at check-in.
5. Expectations. Explain the sequence in plain words: check-in, teardown, supplement to the insurer if hidden damage, parts, body, paint, reassembly, scan and calibration, quality check. Never promise a completion date before teardown; give a typical range only if asked and label it typical.
6. Calibrations. Use `likely_calibrations` from the damage described so the writer knows to scope ADAS work from day one.
7. Create the RO draft with `draft_repair_order` once you have name, phone, vehicle and the loss description.

Tone: calm, friendly, no jargon with customers. Confirm appointment details back to them in one short block.
""".strip()


def build(ctx: Ctx) -> Agent:
    @beta_tool
    def photo_checklist(damage_area: str = "general") -> str:
        """Photo checklist to send a customer or use at check-in.

        Args:
            damage_area: Short description of where the damage is, e.g. "front end", "left side", "rear".
        """
        extra = []
        d = damage_area.lower()
        if "front" in d:
            extra = ["Headlamps lit (to show whether they work)", "Hood gaps at the fenders and cowl", "Behind the grille: condenser and radiator fins"]
        elif "rear" in d:
            extra = ["Trunk/liftgate opened and closed (does it latch?)", "Tail lamps lit", "Spare tire well from inside the trunk (floor buckling)"]
        elif "side" in d or "door" in d or "quarter" in d:
            extra = ["Door opened fully (hinge and latch alignment)", "Rocker panel from below", "Side airbags / curtain airbag areas"]
        return j({"damage_area": damage_area, "photos": PHOTO_CHECKLIST + extra})

    @beta_tool
    def likely_calibrations(damage_description: str, airbags_deployed: bool = False) -> str:
        """ADAS systems that commonly need calibration given the damage described.

        Args:
            damage_description: Free text describing the damage or operations, e.g. "front bumper, hood, windshield cracked".
            airbags_deployed: True if any airbag deployed.
        """
        ops = [p.strip() for p in damage_description.replace(";", ",").split(",")]
        items = adas_tools.calibration_checklist(ops, airbags_deployed=airbags_deployed)
        return j(adas_tools.checklist_report(items))

    @beta_tool
    def draft_repair_order(
        customer_name: str,
        phone: str,
        year: int,
        make: str,
        model: str,
        loss_description: str,
        insurer: str = "",
        claim_number: str = "",
        drivable: bool = True,
        rental: bool = False,
        airbags_deployed: bool = False,
        vin: str = "",
        email: str = "",
        deductible: float = 0.0,
        scheduled_for: str = "",
    ) -> str:
        """Create a new repair order in the shop system in 'scheduled' stage.

        Args:
            customer_name: Customer full name.
            phone: Mobile number for text updates.
            year: Vehicle model year.
            make: Vehicle make.
            model: Vehicle model.
            loss_description: What happened and where the damage is.
            insurer: Carrier name, or empty string for customer pay.
            claim_number: Claim number if known.
            drivable: Whether the vehicle is safe to drive.
            rental: Whether the customer needs a rental.
            airbags_deployed: Whether any airbag deployed.
            vin: VIN if known.
            email: Email if known.
            deductible: Policy deductible in dollars, 0 if unknown or customer pay.
            scheduled_for: Drop-off date (YYYY-MM-DD) if scheduled.
        """
        from ..models import Customer, Note, RepairOrder, Vehicle

        if scheduled_for:
            from datetime import date as _date

            _date.fromisoformat(scheduled_for)
        ro = RepairOrder(
            ro_number=ctx.store.next_ro_number(ctx.today.strftime("%y")),
            customer=Customer(name=customer_name, phone=phone, email=email),
            vehicle=Vehicle(year=year, make=make, model=model, vin=vin),
            insurer=insurer,
            claim_number=claim_number,
            drivable=drivable,
            rental=rental,
            airbags_deployed=airbags_deployed,
            deductible=float(deductible or 0),
            opened=ctx.today.isoformat(),
            dropped_off=scheduled_for or None,
            stage="scheduled",
            stage_changed=ctx.today.isoformat() + "T08:00:00",
            notes=[Note(at=ctx.today.isoformat() + "T08:00:00", author="intake-agent", text=f"Loss: {loss_description}" + (f" | drop-off {scheduled_for}" if scheduled_for else ""))],
        )
        ctx.store.insert(ro)
        return j({"created": ro.ro_number, "stage": ro.stage, "deductible": ro.deductible, "scheduled_for": scheduled_for or None,
                  "next": "Schedule drop-off; writer to photograph at check-in." + ("" if deductible or not insurer else " Deductible unknown: confirm with the customer or adjuster.")})

    return Agent(
        key="intake",
        name="the Intake & Damage Triage agent",
        role="the first voice a customer hears at the shop, who turns a messy phone call into a clean repair order",
        instructions=INSTRUCTIONS,
        tools=[photo_checklist, likely_calibrations, draft_repair_order],
        keywords=["new customer", "walk-in", "walk in", "tow", "tow-in", "drivable", "appointment", "drop off", "drop-off", "estimate request", "photos", "intake", "schedule a", "got hit", "hit a", "hitting a", "rear-ended", "rear ended", "accident", "claim filed", "claim already filed", "what do i ask", "set up the ro", "new ro", "open an ro", "write the ro", "customer called"],
        examples=[
            "A customer called: 2021 RAV4, rear-ended at a light, airbags didn't deploy, Example Mutual claim already filed. What do I ask and what photos do I need?",
            "Walk-in with a F-150, hood won't latch after hitting a deer, customer pay. Set up the RO.",
        ],
        shop=ctx.shop,
    )
