"""Printable Maryland paperwork generated from a repair order.

Four documents, each a self-contained HTML page the browser prints to paper or PDF:

  authorization        Repair authorization (and direction to pay on insurance jobs) with the
                       statutory "Customer's Rights" block immediately above the signature
                       line (Com. Law 14-1008), the written-estimate items and notice
                       (14-1002), teardown and storage terms, the replaced-parts choice, and
                       the oral-notice record for tow-ins and after-hours drops.
  aftermarket          Replacement crash parts list with genuine / aftermarket specified for
                       every part (14-2302(a)) and, when an aftermarket crash part is used,
                       the statutory statement; both in 10-point capitals (14-2302(b)-(c)).
  supplement_consent   Authorization for additional repairs (14-1006) and the consent that
                       lets the bill exceed the written estimate by more than 10% (14-1002(b)).
  invoice_addendum     Final-invoice addendum: used / rebuilt / reconditioned parts disclosed
                       (14-1003(b)), replaced parts tendered (14-1004), the repeated notices,
                       the money split by payer and the warranty wording from shop.toml.

The statutory wording lives in STATUTES below so it can be re-verified against
mgaleg.maryland.gov in one place. Everything that comes from the RO is HTML-escaped.
"""
from __future__ import annotations

import html
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..models import STAGES, EstimateLine, RepairOrder
from . import ar_tools

FORM_KINDS = ("authorization", "aftermarket", "supplement_consent", "invoice_addendum")

FORM_TITLES = {
    "authorization": "Repair authorization and direction to pay",
    "authorization_cash": "Repair authorization",
    "aftermarket": "Replacement crash parts list and aftermarket parts disclosure",
    "supplement_consent": "Authorization for additional repairs",
    "invoice_addendum": "Final invoice addendum: parts, replaced parts, notices",
}

# ---------------------------------------------------------------------------------------
# Statutory text. Source: Maryland Code, Commercial Law, as displayed on mgaleg.maryland.gov
# and checked word for word on 2026-10-21. Re-check after each legislative session.
# ---------------------------------------------------------------------------------------
STATUTES: dict[str, Any] = {
    # 14-1008(a)-(b): the rights the authorization form must state, "displayed immediately
    # before the space for the signature of the customer conspicuously in easily readable
    # type", "physically separated from the other terms", "listed under the printed heading
    # 'Customer's Rights'". The statute prescribes the substance; the wording below tracks it.
    "customer_rights_heading": "Customer’s Rights",
    "customer_rights": [
        "You may request a written estimate for repairs which cost in excess of $50.",
        "You may not be charged any amount ten percent in excess of the written estimate without your consent.",
        "You are entitled to the return of any replaced parts except when parts are required to be returned to the manufacturer under a warranty agreement.",
        "Repairs not originally authorized by you may not be charged to you without your consent.",
    ],
    # 14-1008(c): oral notice is allowed when the vehicle is towed in or left while the shop
    # is closed, if the shop records who was told, the date and time, and the notifier signs.
    "oral_notice_rule": "If the vehicle was towed to the shop or left when the shop was not open, these rights may be given orally; the shop then records in writing the name of the person notified, the date and time of the notification, and the signature of the person who made the notification (Md. Commercial Law 14-1008(c)).",
    # 14-1002(a)(1)(iii), 14-1003(b)(2), 14-1008(e): the premises-damage statement (substance prescribed).
    "premises_notice": (
        "While your motor vehicle is on the premises of this automotive repair facility, the facility may not be responsible for damage to your "
        "motor vehicle under certain circumstances. You should ask a representative of the facility about the extent of its responsibility, "
        "including the extent of the facility's insurance coverage."
    ),
    # 14-1003(c) and 14-1008(d): prescribed word for word, heading line included.
    "nhtsa_heading": "Manufacturer Special Policy Adjustment Programs",
    "nhtsa_notice": (
        "Federal law requires manufacturers to furnish the National Highway Traffic Safety Administration (N.H.T.S.A.) with bulletins describing "
        "any defects in their vehicles. You may obtain copies of these bulletins from either the manufacturer or N.H.T.S.A. In addition, certain "
        "consumer publications or organizations publish this information, which may be available for a fee or for free."
    ),
    # 14-1002(c): the four excuses for missing the estimated completion date.
    "completion_excuses": "The facility is not liable for missing the estimated completion date if the delay is caused by an act of God, a strike, unexpected illness, or an unexpected shortage of labor or parts (Md. Commercial Law 14-1002(c)).",
    # 14-2302(b): prescribed word for word; 14-2302(c): in writing, clear and conspicuous,
    # 10 point capital type. The same typography applies to the parts list under 14-2302(a).
    "aftermarket_statement": (
        "THIS ESTIMATE HAS BEEN PREPARED BASED ON THE USE OF AFTERMARKET CRASH PARTS THAT ARE NOT MANUFACTURED BY THE ORIGINAL MANUFACTURER "
        "OF THE VEHICLE OR BY A MANUFACTURER AUTHORIZED BY THE ORIGINAL MANUFACTURER TO USE ITS NAME OR TRADEMARK. THE USE OF CERTAIN "
        "AFTERMARKET CRASH PARTS MAY MODIFY THE ORIGINAL MANUFACTURER’S WARRANTY ON THE CRASH PARTS BEING REPLACED. UPON REQUEST OF THE "
        "CUSTOMER, THE BODY SHOP SHALL PROVIDE, IF AVAILABLE, A COPY OF ANY WARRANTY FOR AN AFTERMARKET CRASH PART USED."
    ),
    "aftermarket_list_rule": (
        "Before beginning repair work on crash parts, the body shop provides the vehicle owner with this list of the replacement crash parts it "
        "intends to use and specifies whether each is a genuine crash part (manufactured by or for the vehicle's original manufacturer and "
        "authorized to carry its name or trademark). The list and any aftermarket statement are printed in 10-point capital type "
        "(Md. Commercial Law 14-2302). Crash parts are the sheet metal or fiberglass panels and parts that form the body of the vehicle: fenders, "
        "bumpers, quarter panels, door panels, hoods, grills, firewalls, permanent roofs, wheelwells and lamp display panels."
    ),
    "used_parts_rule": "Maryland Commercial Law 14-1003(b): the invoice states clearly if any used, rebuilt or reconditioned parts have been supplied, or if a part of a component system supplied is composed of used, rebuilt or reconditioned parts.",
    "replaced_parts_rule": "Maryland Commercial Law 14-1004: the facility tenders return of all replaced parts to the customer, except replaced parts which are required to be returned to the manufacturer or distributor under a warranty agreement.",
    "additional_work_rule": "Maryland Commercial Law 14-1006: the facility may not charge the customer for repairs not originally authorized or requested by the customer; additional repairs may be charged if the facility receives written or oral permission from the customer. 14-1002(b): the facility may not charge, without the customer's consent, any amount which exceeds the written estimate by 10 percent.",
    "estimate_notice_rule": "Maryland Commercial Law 14-1002(a): before beginning repair work for which the customer is charged more than $50, the facility gives the customer, on request, a written statement of the estimated completion date, the estimated price for labor and parts, the statement about responsibility for damage to the vehicle, and the estimated surcharge, if any.",
    "estimate_fee_rule": "Maryland Commercial Law 14-1002(a)(2): a fee for making the estimate may be charged only if it was disclosed to the customer before the estimate was made.",
    "verified": "Text checked against mgaleg.maryland.gov (Commercial Law 14-1001 to 14-1008, 14-2301 to 14-2303) on 2026-10-21.",
}

# How each part type answers 14-2302(a)(2) "genuine crash part or not". Genuine means made by
# or for the vehicle's manufacturer and carrying its mark (14-2301(e)); a used OEM part is
# still genuine. A reconditioned part can be either, so the estimator marks it on the sheet.
PART_TYPE_LABELS = {
    "oem": "Genuine (OEM)",
    "opt_oem": "Genuine (OEM, alternate source)",
    "aftermarket": "AFTERMARKET (non-OEM)",
    "recycled": "Genuine OEM (used / recycled)",
    "reconditioned": "RECONDITIONED / REBUILT",
}
GENUINE: dict[str, bool | None] = {"oem": True, "opt_oem": True, "recycled": True, "aftermarket": False, "reconditioned": None}
USED_OR_REBUILT = {"recycled", "reconditioned"}

# 14-2301(d) "crash parts": exterior or interior sheet metal or fiberglass panels and parts
# which form the superstructure or body, "including but not limited to fenders, bumpers,
# quarter panels, door panels, hoods, grills, firewalls, permanent roofs, wheelwells, and
# front and rear lamp display panels". Lamps, glass, mirrors, latches, wheels, sensors and
# mechanical parts are not crash parts; they are still listed, just not marked.
_CRASH_RX = re.compile(
    r"\b(fender|bumper|fascia|hood|door( shell| skin| panel)?|quarter( panel)?|deck ?lid|trunk lid|liftgate|tailgate|roof( panel)?|"
    r"grille?|firewall|wheel ?well|wheel ?house|lamp (display )?panel|header panel|(rear )?body panel|back panel|"
    r"rocker( panel)?|radiator support|core support|apron|frame rail|(front|rear|side|upper|lower) rail|floor( pan)?|pillar|cowl|dash panel|"
    r"uniside|side panel|bed ?side|cab corner|valance|bumper reinforcement|impact bar|energy absorber)\b",
    re.I,
)
# Parts that mention a panel name but are not panels (door mirror, hood latch, headlamp, ...).
_NOT_CRASH_RX = re.compile(
    r"\b(mirror|glass|window|windshield|handle|latch|lock|actuator|hinge|strut|shock|liner|splash shield|molding|moulding|trim|mat|rack|"
    r"weatherstrip|seal|sensor|camera|radar|emblem|badge|nameplate|wiring|harness|head ?lamp|tail ?lamp|fog lamp|lamp assembly|marker lamp|"
    r"condenser|radiator(?! (core )?support)|cooling fan|fan|intercooler|wheel(?! ?(well|house))|tire|battery|module|switch|motor|pump|"
    r"fuel rail|seat|airbag|belt|speaker|antenna|insulator|pad|clip|bolt|nut|screw|rivet|label|decal|cable|hose|duct|deflector|spoiler)\b",
    re.I,
)


# ---------------------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------------------
def is_crash_part(description: str) -> bool:
    d = description or ""
    return bool(_CRASH_RX.search(d)) and not _NOT_CRASH_RX.search(d)


def known_non_crash(description: str) -> bool:
    return bool(_NOT_CRASH_RX.search(description or ""))


def replacement_parts(ro: RepairOrder) -> list[dict[str, Any]]:
    """Every part supplied on the estimate with its type stated in plain words."""
    out = []
    for l in ro.lines:
        if l.part_type in {"none", ""}:
            continue
        if l.operation not in {"replace", "r&r"} and not (l.part_price > 0 or l.part_number):
            continue
        out.append({
            "line": l.line,
            "description": l.description,
            "part_number": l.part_number,
            "qty": l.qty,
            "price": round(l.part_price * max(l.qty, 1), 2),
            "part_type": l.part_type,
            "label": PART_TYPE_LABELS.get(l.part_type, l.part_type),
            "crash_part": is_crash_part(l.description),
            "genuine": GENUINE.get(l.part_type, False),
        })
    return out


def _aftermarket_triggers(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aftermarket parts that make the 14-2302(b) statement print: crash parts, plus any
    aftermarket part the classifier cannot rule out (over-disclosure is harmless)."""
    return [p for p in parts if p["part_type"] == "aftermarket" and (p["crash_part"] or not known_non_crash(p["description"]))]


def aftermarket_data(ro: RepairOrder, shop: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    parts = replacement_parts(ro)
    aftermarket = [p for p in parts if p["part_type"] == "aftermarket"]
    triggers = _aftermarket_triggers(parts)
    return {
        "kind": "aftermarket",
        "title": FORM_TITLES["aftermarket"],
        "ro": ro.ro_number,
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin},
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "parts": parts,
        "crash_parts": [p for p in parts if p["crash_part"]],
        "aftermarket_parts": aftermarket,
        "aftermarket_crash_parts": [p for p in aftermarket if p["crash_part"]],
        "statement_required": bool(triggers),
        "statement": STATUTES["aftermarket_statement"] if triggers else "",
        "needs_origin": [p for p in parts if p["genuine"] is None],
        "date": (today or date.today()).isoformat(),
    }


DEFAULT_TERMS = [
    "I authorize the repairs described in the written estimate, and any additional repairs I approve in writing or orally as recorded by the shop, and I authorize the shop to operate the vehicle for inspection, testing and delivery.",
    "I will remove personal property from the vehicle. The shop is not responsible for personal property left in it.",
]
DEFAULT_DIRECTION_TO_PAY = [
    "I direct my insurance company to pay the shop directly for the approved repairs. I remain responsible for my deductible, for any betterment or items my insurer declines that I approve, and for charges for repairs I have authorized that are not covered by insurance.",
]
DEFAULT_CUSTOMER_PAY = [
    "This is a customer-pay repair. I am responsible for the full amount of the final invoice, which is due when the vehicle is picked up.",
]


def _written_estimate(ro: RepairOrder, rates: dict[str, float]) -> tuple[float, str]:
    """The figure the customer was actually given, and where it came from:
    'estimate' = imported estimate gross, 'written' = recorded written-estimate total,
    'lines' = lines priced at posted rates (no written estimate on file), 'none'."""
    gross = float((ro.estimate_totals or {}).get("gross", 0) or 0)
    if gross:
        return round(gross, 2), "estimate"
    written = float(ro.original_estimate_total or 0)
    if written:
        return round(written, 2), "written"
    if ro.lines:
        return round(ar_tools.invoice_total(ro, rates), 2), "lines"
    return 0.0, "none"


def authorization_data(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    s = shop["shop"]
    f = shop.get("forms", {})
    insured = bool(ro.insurer)
    storage_rate = float(rates.get("storage_per_day", 0) or 0)
    teardown = float(rates.get("teardown_fee", 0) or 0)
    grace = int(f.get("storage_grace_days", 3) or 0)
    fee = float(f.get("estimate_fee", 0) or 0)
    total, source = _written_estimate(ro, rates)
    est_ref = f"{ro.estimate_system or 'estimate'} {ro.estimate_id}{' supplement ' + ro.supp_no if ro.supp_no not in {'', '00', '0'} else ''}".strip() if ro.estimate_id else ""

    terms = list(f.get("authorization_terms") or DEFAULT_TERMS)
    terms += list(f.get("direction_to_pay_terms") or DEFAULT_DIRECTION_TO_PAY) if insured else list(f.get("customer_pay_terms") or DEFAULT_CUSTOMER_PAY)
    if teardown:
        terms.append(
            "I authorize the shop to disassemble (tear down) the damaged area as needed to find hidden damage and write the estimate or supplement. "
            f"If the repair does not proceed after teardown, I agree to pay a teardown fee of ${teardown:,.2f} in addition to any storage; "
            "no separate teardown fee applies if the repair proceeds."
        )
    if storage_rate:
        terms.append(
            f"Storage: ${storage_rate:,.2f} per day begins {grace} day{'s' if grace != 1 else ''} after the shop notifies me that the vehicle is ready for pickup, "
            "or on the day I decline the repair. I agree to these storage charges."
        )
    surcharges = ([f"Teardown fee ${teardown:,.2f} if the repair does not proceed"] if teardown else []) \
        + ([f"Storage ${storage_rate:,.2f}/day after the grace period"] if storage_rate else []) \
        + ([f"Estimate fee ${fee:,.2f} if the repair does not proceed (see attestation)"] if fee else [])
    return {
        "kind": "authorization",
        "title": FORM_TITLES["authorization" if insured else "authorization_cash"],
        "ro": ro.ro_number,
        "date": (today or date.today()).isoformat(),
        "insured": insured,
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone, "email": ro.customer.email},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin, "plate": ro.plate, "color": ro.vehicle.color, "mileage": ro.vehicle.mileage},
        "received": ro.dropped_off,
        "loss_date": ro.loss_date,
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "adjuster": ro.adjuster,
        "deductible": ro.deductible if insured else 0.0,
        "estimate": {
            "reference": est_ref,
            "total": total,
            "total_source": source,
            "completion_date": ro.promised,
            "labor_hours": ro.labor_hours(),
            "paint_hours": ro.paint_hours(),
            "parts_total": ro.parts_total(),
            "surcharges": surcharges,
        },
        "terms": terms,
        "estimate_fee": fee,
        "customer_rights_heading": STATUTES["customer_rights_heading"],
        "customer_rights": STATUTES["customer_rights"],
        "premises_notice": STATUTES["premises_notice"],
        "nhtsa_notice": STATUTES["nhtsa_notice"],
        "oral_notice_rule": STATUTES["oral_notice_rule"],
        "shop": {"name": s["name"], "address": s.get("address", ""), "phone": s.get("phone", "")},
    }


def supplement_consent_data(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    gross = float((ro.estimate_totals or {}).get("gross", 0) or 0)
    s0 = ro.supplements[0] if ro.supplements else {}
    s0_gross, s0_amt = float(s0.get("gross") or 0), float(s0.get("amount") or 0)
    # The written estimate the 10% test runs against: the recorded original, else the total
    # before the first supplement (a supplement's gross is the post-supplement total).
    original = float(ro.original_estimate_total or 0) or (round(s0_gross - s0_amt, 2) if s0_gross and s0_amt and s0_gross > s0_amt else 0.0)
    supp_total = round(sum(float(x.get("amount", 0) or 0) for x in ro.supplements), 2)
    if gross:
        current = gross
    elif original:
        current = round(original + supp_total + ro.customer_pay_total(), 2)
    else:
        current = ar_tools.invoice_total(ro, rates)
    return {
        "kind": "supplement_consent",
        "title": FORM_TITLES["supplement_consent"],
        "ro": ro.ro_number,
        "date": (today or date.today()).isoformat(),
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin},
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "supplements": [
            {"supp_no": str(x.get("supp_no", "")), "submitted": x.get("submitted"), "approved": x.get("approved"), "amount": float(x.get("amount", 0) or 0), "gross": float(x.get("gross", 0) or 0)}
            for x in ro.supplements
        ],
        "customer_pay_items": [{"description": i.get("description", ""), "amount": float(i.get("amount", 0) or 0), "reason": i.get("reason", "")} for i in ro.customer_pay_items],
        "current_total": round(current, 2),
        "original_estimate_total": round(original, 2),
        "over_ten_percent": bool(original) and current > original * 1.10,
        "rule": STATUTES["additional_work_rule"],
        "shop": {"name": shop["shop"]["name"], "phone": shop["shop"].get("phone", "")},
    }


def invoice_addendum_data(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    parts = replacement_parts(ro)
    used = [p for p in parts if p["part_type"] in USED_OR_REBUILT]
    bal = ar_tools.balance(ro, rates)
    return {
        "kind": "invoice_addendum",
        "title": FORM_TITLES["invoice_addendum"],
        "ro": ro.ro_number,
        "date": (today or date.today()).isoformat(),
        "insured": bool(ro.insurer),
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin, "plate": ro.plate, "mileage": ro.vehicle.mileage},
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "used_parts": used,
        "replaced_parts": parts,
        "used_parts_rule": STATUTES["used_parts_rule"],
        "replaced_parts_rule": STATUTES["replaced_parts_rule"],
        "premises_notice": STATUTES["premises_notice"],
        "nhtsa_notice": STATUTES["nhtsa_notice"],
        "money": {
            "invoice_total": bal["invoice_total"],
            "paid": bal["paid"],
            "balance": bal["balance"],
            "deductible": ro.deductible if ro.insurer else 0.0,
            "customer_pay_items": [{"description": i.get("description", ""), "amount": float(i.get("amount", 0) or 0)} for i in ro.customer_pay_items],
            "customer_pay_total": ro.customer_pay_total(),
            "expected_from_customer": bal["expected_from_customer"],
            "customer_paid": bal["customer_paid"],
            "customer_balance": bal["customer_balance"],
            "expected_from_carrier": bal["expected_from_carrier"],
            "carrier_paid": bal["carrier_paid"],
            "carrier_short": bal["carrier_short"],
        },
        "warranty": shop["shop"].get("warranty", ""),
        "shop": {"name": shop["shop"]["name"], "address": shop["shop"].get("address", ""), "phone": shop["shop"].get("phone", "")},
    }


def form_data(kind: str, ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    if kind == "authorization":
        return authorization_data(ro, shop, rates, today)
    if kind == "aftermarket":
        return aftermarket_data(ro, shop, today)
    if kind == "supplement_consent":
        return supplement_consent_data(ro, shop, rates, today)
    if kind == "invoice_addendum":
        return invoice_addendum_data(ro, shop, rates, today)
    raise KeyError(f"No such form: {kind} (choose from {', '.join(FORM_KINDS)})")


def form_url(ro_number: str, kind: str) -> str:
    return f"/forms/{quote(ro_number, safe='')}/{kind}"


def required_forms(ro: RepairOrder, shop: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Which documents this RO needs, when, and why.

    `required`: the law calls for it on this job. `due_now`: this is the moment to hand it
    over, judged from the stage. Every RO lists all four so the desk can print any of them.
    """
    parts = replacement_parts(ro)
    aftermarket = [p for p in parts if p["part_type"] == "aftermarket"]
    crash = [p for p in parts if p["crash_part"]]
    used = [p for p in parts if p["part_type"] in USED_OR_REBUILT]
    has_supps = bool(ro.supplements) or bool(ro.customer_pay_items)
    idx = STAGES.index(ro.stage) if ro.stage in STAGES else 0
    delivered = ro.stage in {"ready", "delivered"}
    if aftermarket:
        why_list = f"{len(aftermarket)} aftermarket part(s) on the estimate: Com. Law 14-2302 crash-parts list, genuine/aftermarket stated, and the 10-point statement."
    elif crash:
        why_list = f"{len(crash)} replacement crash part(s), all genuine: Com. Law 14-2302(a) still wants the list with genuine stated for each, before work on them."
    elif parts:
        why_list = "Parts list only; no crash parts or aftermarket parts on the estimate."
    else:
        why_list = "No replacement parts on the estimate yet."
    return [
        {
            "kind": "authorization", "title": FORM_TITLES["authorization" if ro.insurer else "authorization_cash"], "required": True,
            # due until the car goes into production: once it is in body or paint the form is either signed or overdue
            "due_now": not ro.dropped_off or idx < STAGES.index("body"),
            "when": "at check-in, before any work",
            "why": "Com. Law 14-1008 Customer's Rights above the signature line; 14-1002 written-estimate notice; teardown and storage terms"
                   + ("; direction to pay." if ro.insurer else ".") + ("" if ro.dropped_off else " Vehicle not yet dropped off."),
            "url": form_url(ro.ro_number, "authorization"),
        },
        {
            "kind": "aftermarket", "title": FORM_TITLES["aftermarket"], "required": bool(aftermarket) or bool(crash),
            "due_now": (bool(aftermarket) or bool(crash)) and idx < STAGES.index("parts_received"),
            "when": "before parts are ordered",
            "why": why_list,
            "url": form_url(ro.ro_number, "aftermarket"),
        },
        {
            "kind": "supplement_consent", "title": FORM_TITLES["supplement_consent"], "required": has_supps, "due_now": has_supps,
            "when": "when a supplement or customer-pay item is added",
            "why": (f"{len(ro.supplements)} supplement(s), {len(ro.customer_pay_items)} customer-pay item(s): Com. Law 14-1006 permission record and the 14-1002 10% consent." if has_supps
                    else "No supplements or customer-pay items yet."),
            "url": form_url(ro.ro_number, "supplement_consent"),
        },
        {
            "kind": "invoice_addendum", "title": FORM_TITLES["invoice_addendum"], "required": True, "due_now": delivered,
            "when": "at delivery, with the final invoice",
            "why": (f"{len(used)} used or rebuilt part(s) to disclose (14-1003); " if used else "No used or rebuilt parts; ")
                   + "replaced parts tendered (14-1004); notices repeated; customer signs and keeps a copy."
                   + ("" if delivered else " Not at delivery yet."),
            "url": form_url(ro.ro_number, "invoice_addendum"),
        },
    ]


# ---------------------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------------------
CSS = """
body { font: 11pt/1.35 Georgia, 'Times New Roman', serif; color: #000; margin: 0 auto; max-width: 7.3in; padding: 0.4in 0.2in; }
h1 { font: bold 15pt/1.2 Arial, Helvetica, sans-serif; margin: 0 0 2pt; }
h2 { font: bold 11.5pt/1.2 Arial, Helvetica, sans-serif; margin: 14pt 0 4pt; border-bottom: 1px solid #000; }
.shop { font: 10pt/1.3 Arial, Helvetica, sans-serif; margin-bottom: 10pt; }
.meta { width: 100%; border-collapse: collapse; font-size: 10.5pt; margin-bottom: 6pt; }
.meta td { padding: 2pt 6pt 2pt 0; vertical-align: top; }
.meta td.k { font-weight: bold; white-space: nowrap; width: 1.4in; }
table.list { width: 100%; border-collapse: collapse; font-size: 10pt; margin: 4pt 0 8pt; }
table.list th, table.list td { border: 1px solid #000; padding: 3pt 5pt; text-align: left; vertical-align: top; }
table.list th { background: #eee; font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt; }
table.list.caps { font: 10pt/1.3 Arial, Helvetica, sans-serif; }
table.list.caps th { font-size: 10pt; }
td.num, th.num { text-align: right; white-space: nowrap; }
.rights { border: 2px solid #000; padding: 8pt 10pt; margin: 14pt 0 10pt; page-break-inside: avoid; }
.rights h2 { border: 0; margin: 0 0 4pt; font-size: 13pt; }
.rights ol { margin: 0; padding-left: 20pt; font-size: 11.5pt; font-weight: bold; }
.rights li { margin: 2pt 0; }
.statement { font: 10pt/1.4 Arial, Helvetica, sans-serif; border: 1px solid #000; padding: 6pt 8pt; margin: 8pt 0; page-break-inside: avoid; }
.notice { font-size: 9.5pt; margin: 6pt 0; page-break-inside: avoid; }
.signblock { page-break-inside: avoid; }
.ident { font: 8.5pt Arial, Helvetica, sans-serif; color: #333; margin: 10pt 0 0; }
.sig { display: flex; gap: 24pt; margin-top: 18pt; page-break-inside: avoid; }
.sig div { flex: 1; border-top: 1px solid #000; padding-top: 3pt; font: 9pt Arial, Helvetica, sans-serif; }
.sig.oral { margin-top: 8pt; }
.box { border: 1px solid #000; padding: 6pt 8pt; margin: 8pt 0; page-break-inside: avoid; }
.muted { color: #333; font-size: 9.5pt; }
ul.terms { padding-left: 16pt; margin: 4pt 0; }
ul.terms li { margin: 3pt 0; }
.check { font-family: Arial, Helvetica, sans-serif; page-break-inside: avoid; }
.toolbar { position: fixed; top: 8px; right: 8px; font: 12px system-ui, sans-serif; }
.toolbar button { font: inherit; padding: 6px 10px; }
@media print { .toolbar { display: none; } body { padding: 0; } }
"""


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _css_string(s: str) -> str:
    """A CSS string literal body: every character outside a safe set is hex-escaped, so a
    customer name can never close the <style> block or the string."""
    out = []
    for ch in s:
        out.append(ch if ch.isalnum() or ch in " .,:;'#/()-" else f"\\{ord(ch):x} ")
    return "".join(out)


def _money(v: Any) -> str:
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _long_date(iso: str | None) -> str:
    """Plain text (callers escape it)."""
    if not iso:
        return ""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


BLANK_DATE = "____/____/______"


def _page(title: str, shop: dict[str, Any], body: str, ident: str) -> str:
    """ident (RO, customer, vehicle) repeats in the printed page footer so a separated signed
    page can be matched to its job; browsers without @page margin boxes still get the
    ident line printed above every signature block."""
    s = shop["shop"]
    page_css = (
        "@page { size: letter; margin: 0.6in 0.6in 0.75in; "
        f"@bottom-left {{ content: \"{_css_string(ident)}\"; font: 8pt Arial, Helvetica, sans-serif; }} "
        "@bottom-right { content: \"Page \" counter(page) \" of \" counter(pages); font: 8pt Arial, Helvetica, sans-serif; } }\n"
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{page_css}{CSS}</style></head><body>"
        "<div class=\"toolbar\"><button onclick=\"window.print()\">Print</button></div>"
        f"<div class=\"shop\"><b>{_e(s['name'])}</b><br>{_e(s.get('address', ''))} &middot; {_e(s.get('phone', ''))}</div>"
        f"<h1>{_e(title)}</h1>"
        f"{body}</body></html>"
    )


def _ident(d: dict[str, Any]) -> str:
    v = d.get("vehicle", {})
    return f"RO {d['ro']} · {d.get('customer', {}).get('name', '')} · {v.get('label', '')}" + (f" · VIN {v['vin']}" if v.get("vin") else "")


def _meta(rows: list[tuple[str, Any]]) -> str:
    cells = "".join(f"<tr><td class=\"k\">{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in rows if v not in (None, ""))
    return f"<table class=\"meta\">{cells}</table>"


def _sig(labels: list[str], cls: str = "", ident: str = "") -> str:
    lead = f"<p class=\"ident\">{_e(ident)}</p>" if ident else ""
    return lead + f"<div class=\"sig {cls}\">" + "".join(f"<div>{_e(x)}</div>" for x in labels) + "</div>"


def _genuine_cell(p: dict[str, Any], caps: bool) -> str:
    label = p["label"].upper() if caps else p["label"]
    if p["genuine"] is None:
        tail = " &nbsp; GENUINE [ ] &nbsp; AFTERMARKET [ ]" if caps else " (genuine [ ] / aftermarket [ ])"
        return f"<b>{_e(label)}</b>{tail}"
    return f"<b>{_e(label)}</b>" if p["genuine"] is False else _e(label)


def _parts_table(parts: list[dict[str, Any]], mark_crash: bool = True, caps: bool = False) -> str:
    """caps=True prints the list in capital letters (14-2302(c): 10-point capital type)."""
    if not parts:
        return "<p class=\"muted\">No replacement parts on the estimate.</p>"
    t = (lambda v: str(v).upper()) if caps else (lambda v: v)
    head = ("<tr><th>LINE</th><th>REPLACEMENT PART</th><th>PART NUMBER</th><th class=\"num\">QTY</th><th>GENUINE OR AFTERMARKET</th>" + ("<th>CRASH PART</th>" if mark_crash else "")
            if caps else "<tr><th>Line</th><th>Replacement part</th><th>Part number</th><th class=\"num\">Qty</th><th>Type</th>" + ("<th>Crash part</th>" if mark_crash else "")) + "</tr>"
    rows = "".join(
        f"<tr><td>{_e(p['line'])}</td><td>{_e(t(p['description']))}</td><td>{_e(t(p['part_number']))}</td><td class=\"num\">{_e(p['qty'])}</td>"
        f"<td>{_genuine_cell(p, caps)}</td>"
        + (f"<td>{t('yes') if p['crash_part'] else ''}</td>" if mark_crash else "") + "</tr>"
        for p in parts
    )
    return f"<table class=\"list{' caps' if caps else ''}\">{head}{rows}</table>"


def render_authorization(d: dict[str, Any], shop: dict[str, Any]) -> str:
    v, c, est = d["vehicle"], d["customer"], d["estimate"]
    ident = _ident(d)
    body = _meta([
        ("RO number", d["ro"]), ("Date", _long_date(d["date"])),
        ("Vehicle received", _long_date(d["received"]) or BLANK_DATE), ("Date of loss", _long_date(d["loss_date"]) or BLANK_DATE),
        ("Customer", c["name"]), ("Phone", c["phone"]), ("Email", c["email"]), ("Address", "_" * 46),
        ("Vehicle", v["label"]), ("VIN", v["vin"]), ("Plate", v["plate"]), ("Color", v["color"]), ("Mileage in", v["mileage"]),
        ("Insurance", d["insurer"]), ("Claim number", d["claim_number"]), ("Adjuster", d["adjuster"]),
        ("Deductible", (_money(d["deductible"]) if d["deductible"] else "$__________  (not on file: confirm with the adjuster or customer before signing)") if d["insured"] else ""),
    ])
    body += "<h2>Written estimate</h2>"
    if est["total_source"] == "none":
        price = "To be provided in the written estimate before repairs begin"
        reference = est["reference"] or "Not yet written; teardown authorized below, written estimate to follow"
    else:
        detail = f" ({est['labor_hours']} labor hrs, {est['paint_hours']} refinish hrs, parts {_money(est['parts_total'])} at posted rates)" if est["total_source"] == "lines" else ""
        price = f"{_money(est['total'])} for labor and parts{detail}"
        reference = est["reference"] or ("Written estimate on file" if est["total_source"] == "written" else "Attached written estimate")
    body += _meta([
        ("Estimate", reference),
        ("Estimated price", price),
        ("Estimated completion", _long_date(est["completion_date"]) or f"{BLANK_DATE}  (entered by the estimator before signature)"),
        ("Estimated surcharges", "; ".join(est["surcharges"]) or "None"),
    ])
    body += f"<p class=\"notice\">{_e(STATUTES['estimate_notice_rule'])} {_e(STATUTES['completion_excuses'])}</p>"
    body += "<h2>Authorization</h2><ul class=\"terms\">" + "".join(f"<li>{_e(t)}</li>" for t in d["terms"]) + "</ul>"
    if d["estimate_fee"]:
        body += (f"<p class=\"check\">Estimate fee of {_money(d['estimate_fee'])} if the repair does not proceed: the fee was disclosed to me before the estimate was prepared "
                 "&nbsp;[ ] yes, initials ______ &nbsp;[ ] no, so no estimate fee applies. "
                 f"<span class=\"muted\">{_e(STATUTES['estimate_fee_rule'])}</span></p>")
    body += ("<p class=\"check\">Replaced parts (Md. Commercial Law 14-1004): &nbsp;[ ] I want my replaced parts returned to me at pickup "
             "&nbsp;[ ] I do not want them. Parts that must go back to the manufacturer or distributor under a warranty agreement cannot be returned.</p>")
    body += f"<p class=\"notice\"><b>Notice:</b> {_e(d['premises_notice'])}</p>"
    body += f"<p class=\"notice\"><b>{_e(STATUTES['nhtsa_heading'])}</b><br>{_e(d['nhtsa_notice'])}</p>"
    # 14-1008(b): the rights sit immediately before the signature space; keep the two on one page.
    body += (f"<div class=\"signblock\"><div class=\"rights\"><h2>{_e(d['customer_rights_heading'])}</h2><ol>"
             + "".join(f"<li>{_e(r)}</li>" for r in d["customer_rights"]) + "</ol></div>"
             + _sig(["Customer signature", "Date", "Printed name", f"For {shop['shop']['name']}"], ident=ident) + "</div>")
    body += (f"<div class=\"box\" style=\"margin-top:16pt\"><b class=\"check\">Vehicle received without the customer present (tow-in or after-hours drop)</b>"
             f"<p class=\"muted\">{_e(d['oral_notice_rule'])}</p>"
             f"<p class=\"check\">Customer’s Rights read by phone to ______________________ on {BLANK_DATE} at ______ (am/pm)</p>"
             + _sig(["Employee signature", "Printed name"], "oral") + "</div>")
    return _page(d["title"], shop, body, ident)


def render_aftermarket(d: dict[str, Any], shop: dict[str, Any]) -> str:
    ident = _ident(d)
    body = _meta([("RO number", d["ro"]), ("Date", _long_date(d["date"])), ("Vehicle owner", d["customer"]["name"]), ("Phone", d["customer"]["phone"]),
                  ("Vehicle", d["vehicle"]["label"]), ("VIN", d["vehicle"]["vin"]), ("Insurance", d["insurer"]), ("Claim number", d["claim_number"])])
    body += f"<p class=\"notice\">{_e(STATUTES['aftermarket_list_rule'])}</p>"
    body += "<h2>Replacement crash parts and their type</h2>" + _parts_table(d["parts"], caps=True)
    if d["needs_origin"]:
        body += "<p class=\"check\">RECONDITIONED PARTS: THE ESTIMATOR MARKS GENUINE OR AFTERMARKET FOR EACH BEFORE THE CUSTOMER SIGNS.</p>"
    if d["statement_required"]:
        body += f"<div class=\"statement\">{_e(d['statement'])}</div>"
        body += ("<p class=\"check\">Cost to upgrade the listed aftermarket crash parts to genuine OEM parts, quoted by the estimator: $______________ "
                 "&nbsp; [ ] Customer declines the upgrade &nbsp; [ ] Customer accepts and will pay the difference</p>")
    elif d["aftermarket_parts"]:
        body += "<p><b>The aftermarket parts listed above are not crash parts</b> (lamps, mirrors, glass, mechanical or trim parts); no aftermarket crash parts are used on this estimate.</p>"
    else:
        body += "<p><b>No aftermarket crash parts are used on this estimate.</b></p>"
    body += "<p class=\"notice\">A copy of any available warranty for an aftermarket crash part will be provided on request.</p>"
    body += "<div class=\"signblock\">" + _sig(["Customer acknowledgment", "Date", f"For {shop['shop']['name']}"], ident=ident) + "</div>"
    return _page(d["title"], shop, body, ident)


def render_supplement_consent(d: dict[str, Any], shop: dict[str, Any]) -> str:
    ident = _ident(d)
    body = _meta([("RO number", d["ro"]), ("Date", _long_date(d["date"])), ("Customer", d["customer"]["name"]), ("Phone", d["customer"]["phone"]),
                  ("Vehicle", d["vehicle"]["label"]), ("VIN", d["vehicle"]["vin"]), ("Insurance", d["insurer"]), ("Claim number", d["claim_number"])])
    body += "<h2>Additional repairs</h2>"
    if d["supplements"]:
        rows = "".join(f"<tr><td>{_e(x['supp_no'])}</td><td>{_e(_long_date(x['submitted']))}</td><td>{_e(_long_date(x['approved']) or 'pending')}</td><td class=\"num\">{_money(x['amount'])}</td><td class=\"num\">{_money(x['gross'])}</td></tr>" for x in d["supplements"])
        body += f"<table class=\"list\"><tr><th>Supplement</th><th>Submitted</th><th>Insurer approved</th><th class=\"num\">Amount</th><th class=\"num\">Running total</th></tr>{rows}</table>"
    body += "<p class=\"check\">Description of the additional work (hidden damage found, items added, who requested them):</p>" + "<div class=\"box\" style=\"min-height:1.2in\"></div>"
    if d["customer_pay_items"]:
        rows = "".join(f"<tr><td>{_e(i['description'])}</td><td>{_e(i['reason'])}</td><td class=\"num\">{_money(i['amount'])}</td></tr>" for i in d["customer_pay_items"])
        body += f"<h2>Items the customer pays</h2><table class=\"list\"><tr><th>Item</th><th>Why</th><th class=\"num\">Amount</th></tr>{rows}</table>"
    body += _meta([("Original written estimate", _money(d["original_estimate_total"]) if d["original_estimate_total"] else "see attached written estimate"), ("Current total with additions", _money(d["current_total"]))])
    if d["over_ten_percent"]:
        body += "<p><b>The current total exceeds the original written estimate by more than 10 percent. Your consent below is required before these charges apply.</b></p>"
    body += f"<p class=\"notice\">{_e(d['rule'])}</p>"
    body += ("<div class=\"box\"><b class=\"check\">Permission record</b><p class=\"check\">Permission for the additional work was obtained: &nbsp;[ ] in person &nbsp;[ ] by phone &nbsp;[ ] by text or email (copy attached)<br>"
             f"from ______________________ on {BLANK_DATE} at ______ (am/pm), by employee ______________________</p></div>")
    body += "<div class=\"signblock\">" + _sig(["Customer signature (consent to the additional repairs and charges)", "Date", f"For {shop['shop']['name']}"], ident=ident) + "</div>"
    return _page(d["title"], shop, body, ident)


def render_invoice_addendum(d: dict[str, Any], shop: dict[str, Any]) -> str:
    m = d["money"]
    ident = _ident(d)
    body = _meta([("RO number", d["ro"]), ("Date", _long_date(d["date"])), ("Customer", d["customer"]["name"]), ("Phone", d["customer"]["phone"]),
                  ("Vehicle", d["vehicle"]["label"]), ("VIN", d["vehicle"]["vin"]), ("Plate", d["vehicle"]["plate"]), ("Insurance", d["insurer"]), ("Claim number", d["claim_number"])])
    body += "<h2>Used, rebuilt or reconditioned parts installed</h2>"
    if d["used_parts"]:
        body += _parts_table(d["used_parts"], mark_crash=False)
    else:
        body += "<p><b>None.</b> No used, rebuilt or reconditioned parts were installed on this repair.</p>"
    body += f"<p class=\"notice\">{_e(d['used_parts_rule'])}</p>"
    body += "<h2>Replaced parts</h2>"
    if d["replaced_parts"]:
        body += _parts_table(d["replaced_parts"], mark_crash=False)
    body += (f"<p class=\"notice\">{_e(d['replaced_parts_rule'])}</p>"
             "<p class=\"check\">[ ] Customer took the replaced parts &nbsp; [ ] Customer declined the replaced parts<br>"
             "[ ] Not tendered: part required to be returned to the manufacturer or distributor under a warranty agreement (Com. Law 14-1004(b)): ______________________<br>"
             "[ ] Core-exchange part tendered; customer &nbsp;[ ] kept it and paid the core charge of $________ &nbsp;[ ] declined it</p>")
    body += "<h2>Balance</h2>"
    if d["insured"]:
        rows = [("Invoice total", _money(m["invoice_total"])),
                (f"{d['insurer']} portion", _money(m["expected_from_carrier"])), (f"Paid by {d['insurer']}", _money(m["carrier_paid"]))]
        if m["carrier_short"] > 0.5:
            rows.append((f"Still due from {d['insurer']}", _money(m["carrier_short"]) + "  (the shop pursues this with the insurer)"))
        rows += [("Deductible", _money(m["deductible"]))]
        if m["customer_pay_total"]:
            rows.append(("Customer-pay items", _money(m["customer_pay_total"])))
        rows += [("Customer portion", _money(m["expected_from_customer"])), ("Paid by customer", _money(m["customer_paid"])), ("Customer balance due", _money(m["customer_balance"]))]
    else:
        rows = [("Invoice total", _money(m["invoice_total"])), ("Paid to date", _money(m["paid"])), ("Balance due", _money(m["balance"]))]
    body += _meta(rows)
    if m["customer_pay_items"]:
        body += "<table class=\"list\"><tr><th>Customer-pay item</th><th class=\"num\">Amount</th></tr>" + "".join(f"<tr><td>{_e(i['description'])}</td><td class=\"num\">{_money(i['amount'])}</td></tr>" for i in m["customer_pay_items"]) + "</table>"
    if d["warranty"]:
        body += f"<h2>Warranty</h2><p>{_e(d['warranty'])}</p>"
    body += f"<h2>Notices</h2><p class=\"notice\">{_e(d['premises_notice'])}</p><p class=\"notice\"><b>{_e(STATUTES['nhtsa_heading'])}</b><br>{_e(d['nhtsa_notice'])}</p>"
    body += "<p class=\"muted\">The customer receives a signed copy of the invoice and this addendum; the shop retains a copy.</p>"
    body += "<div class=\"signblock\">" + _sig(["Customer signature", "Date", f"For {shop['shop']['name']}"], ident=ident) + "</div>"
    return _page(d["title"], shop, body, ident)


RENDERERS = {
    "authorization": render_authorization,
    "aftermarket": render_aftermarket,
    "supplement_consent": render_supplement_consent,
    "invoice_addendum": render_invoice_addendum,
}


def render(kind: str, ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> str:
    if kind not in RENDERERS:
        raise KeyError(f"No such form: {kind} (choose from {', '.join(FORM_KINDS)})")
    return RENDERERS[kind](form_data(kind, ro, shop, rates, today), shop)


def write_forms(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], out_dir: str | Path, kinds: list[str] | None = None, today: date | None = None) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    safe_ro = re.sub(r"[^A-Za-z0-9._-]+", "_", ro.ro_number) or "ro"
    written = []
    for kind in kinds or FORM_KINDS:
        p = out / f"{safe_ro}-{kind}.html"
        p.write_text(render(kind, ro, shop, rates, today), encoding="utf-8")
        written.append(p)
    return written
