"""Printable Maryland paperwork generated from a repair order.

Four documents, each a self-contained HTML page the browser prints to paper or PDF:

  authorization        Repair authorization and direction to pay, with the statutory
                       "Customer's Rights" block above the signature line (Com. Law 14-1008),
                       the written-estimate items and notice (14-1002), storage terms, and
                       the oral-notice record for tow-ins and after-hours drops.
  aftermarket          Replacement crash parts list with OEM / non-OEM stated and, when an
                       aftermarket crash part is used, the statutory disclosure in 10-point
                       capitals (14-2302).
  supplement_consent   Authorization for additional repairs (14-1006) and the consent that
                       lets the bill exceed the written estimate by more than 10% (14-1002).
  invoice_addendum     Final-invoice addendum: used / rebuilt / reconditioned parts disclosed
                       (14-1003), replaced parts tendered (14-1004), the repeated notices, the
                       money line and the warranty wording from shop.toml.

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

from ..models import EstimateLine, RepairOrder
from . import ar_tools

FORM_KINDS = ("authorization", "aftermarket", "supplement_consent", "invoice_addendum")

FORM_TITLES = {
    "authorization": "Repair authorization and direction to pay",
    "aftermarket": "Replacement crash parts list and aftermarket parts disclosure",
    "supplement_consent": "Authorization for additional repairs",
    "invoice_addendum": "Final invoice addendum: parts, replaced parts, notices",
}

# ---------------------------------------------------------------------------------------
# Statutory text. Source: Maryland Code, Commercial Law, as displayed on mgaleg.maryland.gov
# (2026-10-21). Re-check after each legislative session.
# ---------------------------------------------------------------------------------------
STATUTES: dict[str, Any] = {
    # 14-1008(a)-(b): the rights the authorization form must state, "displayed immediately
    # before the space for the signature of the customer conspicuously in easily readable
    # type", "physically separated from the other terms", "listed under the printed heading
    # 'Customer's Rights'". The statute prescribes the substance; the wording below tracks it.
    "customer_rights_heading": "Customer\u2019s Rights",
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
        "AFTERMARKET CRASH PARTS MAY MODIFY THE ORIGINAL MANUFACTURER\u2019S WARRANTY ON THE CRASH PARTS BEING REPLACED. UPON REQUEST OF THE "
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
    "verified": "Text checked against mgaleg.maryland.gov (Commercial Law 14-1001 to 14-1008, 14-2301 to 14-2303) on 2026-10-21.",
}

PART_TYPE_LABELS = {
    "oem": "Genuine (OEM)",
    "opt_oem": "Genuine (OEM, alternate source)",
    "aftermarket": "AFTERMARKET (non-OEM)",
    "recycled": "USED / recycled OEM",
    "reconditioned": "REBUILT / reconditioned",
}
NON_OEM = {"aftermarket", "recycled", "reconditioned"}
USED_OR_REBUILT = {"recycled", "reconditioned"}

# 14-2301 "crash parts": exterior or interior sheet metal or fiberglass panels and parts
# which form the superstructure or body, "including but not limited to fenders, bumpers,
# quarter panels, door panels, hoods, grills, firewalls, permanent roofs, wheelwells, and
# front and rear lamp display panels". Lamps, glass, wheels, sensors and mechanical parts
# are not crash parts; they are still listed, just not marked.
_CRASH_RX = re.compile(
    r"\b(fender|bumper|fascia|hood|door( shell| skin| panel)?|quarter( panel)?|deck ?lid|trunk lid|liftgate|tailgate|roof( panel)?|"
    r"grille?|firewall|wheel ?well|wheel ?house|lamp (display )?panel|header panel|"
    r"rocker( panel)?|radiator support|core support|apron|rail|floor( pan)?|pillar|cowl|dash panel|"
    r"uniside|side panel|bed ?side|cab corner|valance|reinforcement|absorber|bracket)\b",
    re.I,
)


# ---------------------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------------------
def is_crash_part(description: str) -> bool:
    return bool(_CRASH_RX.search(description or ""))


def replacement_parts(ro: RepairOrder) -> list[dict[str, Any]]:
    """Every replacement part on the estimate with its type stated in plain words."""
    out = []
    for l in ro.lines:
        if l.operation not in {"replace", "r&r"} or l.part_type in {"none", ""}:
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
            "genuine": l.part_type in {"oem", "opt_oem"},
        })
    return out


def aftermarket_data(ro: RepairOrder, shop: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    parts = replacement_parts(ro)
    aftermarket = [p for p in parts if p["part_type"] == "aftermarket"]
    return {
        "kind": "aftermarket",
        "title": FORM_TITLES["aftermarket"],
        "ro": ro.ro_number,
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin},
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "parts": parts,
        "aftermarket_parts": aftermarket,
        "aftermarket_crash_parts": [p for p in aftermarket if p["crash_part"]],
        # Printed whenever any aftermarket part is on the estimate: over-disclosing a non-crash
        # part costs nothing, missing a crash part is the violation.
        "statement_required": bool(aftermarket),
        "statement": STATUTES["aftermarket_statement"] if aftermarket else "",
        "non_oem_count": sum(1 for p in parts if not p["genuine"]),
        "date": (today or date.today()).isoformat(),
    }


def authorization_data(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    s = shop["shop"]
    f = shop.get("forms", {})
    storage_rate = float(rates.get("storage_per_day", 0) or 0)
    grace = int(f.get("storage_grace_days", 3) or 0)
    estimate_total = ar_tools.invoice_total(ro, rates) if ro.lines or ro.estimate_totals else 0.0
    est_ref = f"{ro.estimate_system or 'estimate'} {ro.estimate_id}{' supplement ' + ro.supp_no if ro.supp_no not in {'', '00', '0'} else ''}".strip() if ro.estimate_id else ""
    terms = list(f.get("authorization_terms") or DEFAULT_TERMS)
    if storage_rate:
        terms.append(
            f"Storage: ${storage_rate:,.2f} per day begins {grace} day{'s' if grace != 1 else ''} after the shop notifies me that the vehicle is ready for pickup, "
            "or on the day I decline the repair after teardown. I agree to these storage charges."
        )
    fee = float(f.get("estimate_fee", 0) or 0)
    if fee:
        terms.append(f"If the repair does not proceed, a fee of ${fee:,.2f} applies for preparing the written estimate (disclosed here in advance).")
    return {
        "kind": "authorization",
        "title": FORM_TITLES["authorization"],
        "ro": ro.ro_number,
        "date": (today or date.today()).isoformat(),
        "customer": {"name": ro.customer.name, "phone": ro.customer.phone, "email": ro.customer.email},
        "vehicle": {"label": ro.vehicle.label(), "vin": ro.vehicle.vin, "plate": ro.plate, "color": ro.vehicle.color, "mileage": ro.vehicle.mileage},
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "deductible": ro.deductible if ro.insurer else 0.0,
        "estimate": {
            "reference": est_ref,
            "total": round(estimate_total, 2),
            "completion_date": ro.promised,
            "labor_hours": ro.labor_hours(),
            "paint_hours": ro.paint_hours(),
            "parts_total": ro.parts_total(),
            "surcharges": ([f"Storage ${storage_rate:,.2f}/day after the grace period"] if storage_rate else []) + ([f"Estimate fee ${fee:,.2f} if the repair does not proceed"] if fee else []),
        },
        "terms": terms,
        "customer_rights_heading": STATUTES["customer_rights_heading"],
        "customer_rights": STATUTES["customer_rights"],
        "premises_notice": STATUTES["premises_notice"],
        "nhtsa_notice": STATUTES["nhtsa_notice"],
        "oral_notice_rule": STATUTES["oral_notice_rule"],
        "shop": {"name": s["name"], "address": s.get("address", ""), "phone": s.get("phone", "")},
    }


DEFAULT_TERMS = [
    "I authorize the repairs described in the written estimate, and any additional repairs I approve in writing or orally as recorded by the shop, and I authorize the shop to operate the vehicle for inspection, testing and delivery.",
    "I direct my insurance company to pay the shop directly for the approved repairs. I remain responsible for my deductible, for any betterment or items my insurer declines that I approve, and for charges not covered by insurance.",
    "I will remove personal property from the vehicle. The shop is not responsible for personal property left in it.",
]


def supplement_consent_data(ro: RepairOrder, shop: dict[str, Any], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    base = ar_tools.invoice_total(ro, rates)
    original = float(ro.original_estimate_total or 0) or float((ro.supplements[0].get("gross") if ro.supplements else 0) or 0)
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
        "current_total": round(base, 2),
        "original_estimate_total": round(original, 2),
        "over_ten_percent": bool(original) and base > original * 1.10,
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
            "expected_from_customer": bal["expected_from_customer"],
            "customer_balance": bal["customer_balance"],
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
    """Which documents this RO needs, when, and why. Every RO gets all four listed; `required`
    says whether the law calls for it on this job, `when` says the moment to hand it over."""
    parts = replacement_parts(ro)
    aftermarket = [p for p in parts if p["part_type"] == "aftermarket"]
    used = [p for p in parts if p["part_type"] in USED_OR_REBUILT]
    has_supps = bool(ro.supplements) or bool(ro.customer_pay_items)
    delivered = ro.stage in {"ready", "delivered"}
    return [
        {
            "kind": "authorization", "title": FORM_TITLES["authorization"], "required": True, "when": "at check-in, before any work",
            "why": "Com. Law 14-1008 Customer's Rights above the signature line; 14-1002 written-estimate notice; storage terms; direction to pay."
                   + (" Vehicle not yet dropped off." if not ro.dropped_off else ""),
            "url": form_url(ro.ro_number, "authorization"),
        },
        {
            "kind": "aftermarket", "title": FORM_TITLES["aftermarket"], "required": bool(aftermarket), "when": "before parts are ordered",
            "why": (f"{len(aftermarket)} aftermarket part(s) on the estimate: Com. Law 14-2302 parts list and 10-point disclosure." if aftermarket
                    else ("Parts list only; no aftermarket parts on the estimate." if parts else "No replacement parts on the estimate yet.")),
            "url": form_url(ro.ro_number, "aftermarket"),
        },
        {
            "kind": "supplement_consent", "title": FORM_TITLES["supplement_consent"], "required": has_supps, "when": "when a supplement or customer-pay item is added",
            "why": (f"{len(ro.supplements)} supplement(s), {len(ro.customer_pay_items)} customer-pay item(s): Com. Law 14-1006 permission record and the 14-1002 10% consent." if has_supps
                    else "No supplements or customer-pay items yet."),
            "url": form_url(ro.ro_number, "supplement_consent"),
        },
        {
            "kind": "invoice_addendum", "title": FORM_TITLES["invoice_addendum"], "required": True, "when": "at delivery, with the final invoice",
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
@page { size: letter; margin: 0.6in; }
body { font: 11pt/1.35 Georgia, 'Times New Roman', serif; color: #000; margin: 0 auto; max-width: 7.3in; padding: 0.4in 0.2in; }
h1 { font: bold 15pt/1.2 Arial, Helvetica, sans-serif; margin: 0 0 2pt; }
h2 { font: bold 11.5pt/1.2 Arial, Helvetica, sans-serif; margin: 14pt 0 4pt; border-bottom: 1px solid #000; }
.shop { font: 10pt/1.3 Arial, Helvetica, sans-serif; margin-bottom: 10pt; }
.meta { width: 100%; border-collapse: collapse; font-size: 10.5pt; margin-bottom: 6pt; }
.meta td { padding: 2pt 6pt 2pt 0; vertical-align: top; }
.meta td.k { font-weight: bold; white-space: nowrap; width: 1.4in; }
table.list { width: 100%; border-collapse: collapse; font-size: 10pt; margin: 4pt 0 8pt; }
table.list.caps { font: 10pt/1.3 Arial, Helvetica, sans-serif; }
table.list th, table.list td { border: 1px solid #000; padding: 3pt 5pt; text-align: left; vertical-align: top; }
table.list th { background: #eee; font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt; }
td.num, th.num { text-align: right; white-space: nowrap; }
.rights { border: 2px solid #000; padding: 8pt 10pt; margin: 14pt 0 10pt; page-break-inside: avoid; }
.rights h2 { border: 0; margin: 0 0 4pt; font-size: 13pt; text-transform: uppercase; letter-spacing: 0.5pt; }
.rights ol { margin: 0; padding-left: 20pt; font-size: 11.5pt; font-weight: bold; }
.rights li { margin: 2pt 0; }
.statement { font: 10pt/1.4 Arial, Helvetica, sans-serif; border: 1px solid #000; padding: 6pt 8pt; margin: 8pt 0; page-break-inside: avoid; }
.notice { font-size: 9.5pt; margin: 6pt 0; page-break-inside: avoid; }
.signblock { page-break-inside: avoid; }
.sig { display: flex; gap: 24pt; margin-top: 18pt; page-break-inside: avoid; }
.sig div { flex: 1; border-top: 1px solid #000; padding-top: 3pt; font: 9pt Arial, Helvetica, sans-serif; }
.sig.oral { margin-top: 8pt; }
.box { border: 1px solid #000; padding: 6pt 8pt; margin: 8pt 0; page-break-inside: avoid; }
.muted { color: #333; font-size: 9.5pt; }
ul.terms { padding-left: 16pt; margin: 4pt 0; }
ul.terms li { margin: 3pt 0; }
.check { font-family: Arial, Helvetica, sans-serif; }
.toolbar { position: fixed; top: 8px; right: 8px; font: 12px system-ui, sans-serif; }
.toolbar button { font: inherit; padding: 6px 10px; }
@media print { .toolbar { display: none; } body { padding: 0; } }
"""


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _money(v: Any) -> str:
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _long_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return _e(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _page(title: str, shop: dict[str, Any], body: str) -> str:
    s = shop["shop"]
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
        "<div class=\"toolbar\"><button onclick=\"window.print()\">Print</button></div>"
        f"<div class=\"shop\"><b>{_e(s['name'])}</b><br>{_e(s.get('address', ''))} &middot; {_e(s.get('phone', ''))}</div>"
        f"<h1>{_e(title)}</h1>"
        f"{body}</body></html>"
    )


def _meta(rows: list[tuple[str, Any]]) -> str:
    cells = "".join(f"<tr><td class=\"k\">{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in rows if v not in (None, ""))
    return f"<table class=\"meta\">{cells}</table>"


def _sig(labels: list[str], cls: str = "") -> str:
    return f"<div class=\"sig {cls}\">" + "".join(f"<div>{_e(x)}</div>" for x in labels) + "</div>"


def _parts_table(parts: list[dict[str, Any]], mark_crash: bool = True, caps: bool = False) -> str:
    """caps=True prints the list in capital letters (14-2302(c) wants the crash-parts list in 10-point capitals)."""
    if not parts:
        return "<p class=\"muted\">No replacement parts on the estimate.</p>"
    t = (lambda v: str(v).upper()) if caps else (lambda v: v)
    head = ("<tr><th>LINE</th><th>REPLACEMENT PART</th><th>PART NUMBER</th><th class=\"num\">QTY</th><th>GENUINE OR AFTERMARKET</th>" + ("<th>CRASH PART</th>" if mark_crash else "")
            if caps else "<tr><th>Line</th><th>Replacement part</th><th>Part number</th><th class=\"num\">Qty</th><th>Type</th>" + ("<th>Crash part</th>" if mark_crash else "")) + "</tr>"
    rows = "".join(
        f"<tr><td>{_e(p['line'])}</td><td>{_e(t(p['description']))}</td><td>{_e(t(p['part_number']))}</td><td class=\"num\">{_e(p['qty'])}</td>"
        f"<td>{'<b>' if not p['genuine'] else ''}{_e(t(p['label']))}{'</b>' if not p['genuine'] else ''}</td>"
        + (f"<td>{t('yes') if p['crash_part'] else ''}</td>" if mark_crash else "") + "</tr>"
        for p in parts
    )
    return f"<table class=\"list{' caps' if caps else ''}\">{head}{rows}</table>"


def render_authorization(d: dict[str, Any], shop: dict[str, Any]) -> str:
    v, c, est = d["vehicle"], d["customer"], d["estimate"]
    body = _meta([
        ("RO number", d["ro"]), ("Date", _long_date(d["date"])),
        ("Customer", c["name"]), ("Phone", c["phone"]), ("Email", c["email"]),
        ("Vehicle", v["label"]), ("VIN", v["vin"]), ("Plate", v["plate"]), ("Color", v["color"]), ("Mileage in", v["mileage"]),
        ("Insurance", d["insurer"]), ("Claim number", d["claim_number"]), ("Deductible", _money(d["deductible"]) if d["deductible"] else ""),
    ])
    body += "<h2>Written estimate</h2>"
    body += _meta([
        ("Estimate", est["reference"] or "Attached written estimate"),
        ("Estimated price", (_money(est["total"]) + " for labor and parts" + (f" ({est['labor_hours']} labor hrs, {est['paint_hours']} refinish hrs, parts {_money(est['parts_total'])})" if est["labor_hours"] or est["parts_total"] else "")) if est["total"] else "Per the attached written estimate"),
        ("Estimated completion", _long_date(est["completion_date"]) or "To be set after teardown and insurer approval; you will be told in writing"),
        ("Estimated surcharges", "; ".join(est["surcharges"]) or "None"),
    ])
    body += f"<p class=\"notice\">{_e(STATUTES['estimate_notice_rule'])} {_e(STATUTES['completion_excuses'])}</p>"
    body += "<h2>Authorization</h2><ul class=\"terms\">" + "".join(f"<li>{_e(t)}</li>" for t in d["terms"]) + "</ul>"
    body += f"<p class=\"notice\"><b>Notice:</b> {_e(d['premises_notice'])}</p>"
    body += f"<p class=\"notice\"><b>{_e(STATUTES['nhtsa_heading'])}</b><br>{_e(d['nhtsa_notice'])}</p>"
    # 14-1008(b): the rights sit immediately before the signature space; keep the two on one page.
    body += (f"<div class=\"signblock\"><div class=\"rights\"><h2>{_e(d['customer_rights_heading'])}</h2><ol>"
             + "".join(f"<li>{_e(r)}</li>" for r in d["customer_rights"]) + "</ol></div>"
             + _sig(["Customer signature", "Date", "Printed name", f"For {shop['shop']['name']}"]) + "</div>")
    body += (f"<div class=\"box\" style=\"margin-top:16pt\"><b class=\"check\">Vehicle received without the customer present (tow-in or after-hours drop)</b>"
             f"<p class=\"muted\">{_e(d['oral_notice_rule'])}</p>"
             "<p class=\"check\">Customer's Rights read by phone to ______________________ on ____/____/______ at ______ (am/pm)</p>"
             + _sig(["Employee signature", "Printed name"], "oral") + "</div>")
    return _page(d["title"], shop, body)


def render_aftermarket(d: dict[str, Any], shop: dict[str, Any]) -> str:
    body = _meta([("RO number", d["ro"]), ("Date", _long_date(d["date"])), ("Vehicle owner", d["customer"]["name"]), ("Phone", d["customer"]["phone"]),
                  ("Vehicle", d["vehicle"]["label"]), ("VIN", d["vehicle"]["vin"]), ("Insurance", d["insurer"]), ("Claim number", d["claim_number"])])
    body += f"<p class=\"notice\">{_e(STATUTES['aftermarket_list_rule'])}</p>"
    body += "<h2>Replacement crash parts and their type</h2>" + _parts_table(d["parts"], caps=True)
    if d["statement_required"]:
        body += f"<div class=\"statement\">{_e(d['statement'])}</div>"
        body += ("<p class=\"check\">Cost to upgrade the listed aftermarket crash parts to genuine OEM parts, quoted by the estimator: $______________ "
                 "&nbsp; [ ] Customer declines the upgrade &nbsp; [ ] Customer accepts and will pay the difference</p>")
    else:
        body += "<p><b>No aftermarket crash parts are used on this estimate.</b> Every replacement part above is genuine or is marked as used or rebuilt.</p>"
    body += "<p class=\"notice\">A copy of any available warranty for an aftermarket crash part will be provided on request.</p>"
    body += _sig(["Customer acknowledgment", "Date", f"For {shop['shop']['name']}"])
    return _page(d["title"], shop, body)


def render_supplement_consent(d: dict[str, Any], shop: dict[str, Any]) -> str:
    body = _meta([("RO number", d["ro"]), ("Date", _long_date(d["date"])), ("Customer", d["customer"]["name"]), ("Phone", d["customer"]["phone"]),
                  ("Vehicle", d["vehicle"]["label"]), ("VIN", d["vehicle"]["vin"]), ("Insurance", d["insurer"]), ("Claim number", d["claim_number"])])
    body += "<h2>Additional repairs</h2>"
    if d["supplements"]:
        rows = "".join(f"<tr><td>{_e(x['supp_no'])}</td><td>{_long_date(x['submitted'])}</td><td>{_long_date(x['approved']) or 'pending'}</td><td class=\"num\">{_money(x['amount'])}</td><td class=\"num\">{_money(x['gross'])}</td></tr>" for x in d["supplements"])
        body += f"<table class=\"list\"><tr><th>Supplement</th><th>Submitted</th><th>Insurer approved</th><th class=\"num\">Amount</th><th class=\"num\">Running total</th></tr>{rows}</table>"
    body += "<p class=\"check\">Description of the additional work (hidden damage found, items added, who requested them):</p>" + "<div class=\"box\" style=\"min-height:1.2in\"></div>"
    if d["customer_pay_items"]:
        rows = "".join(f"<tr><td>{_e(i['description'])}</td><td>{_e(i['reason'])}</td><td class=\"num\">{_money(i['amount'])}</td></tr>" for i in d["customer_pay_items"])
        body += f"<h2>Items the customer pays</h2><table class=\"list\"><tr><th>Item</th><th>Why</th><th class=\"num\">Amount</th></tr>{rows}</table>"
    body += _meta([("Original written estimate", _money(d["original_estimate_total"]) if d["original_estimate_total"] else "see attached"), ("Current total with additions", _money(d["current_total"]))])
    if d["over_ten_percent"]:
        body += "<p><b>The current total exceeds the original written estimate by more than 10 percent. Your consent below is required before these charges apply.</b></p>"
    body += f"<p class=\"notice\">{_e(d['rule'])}</p>"
    body += ("<div class=\"box\"><b class=\"check\">Permission record</b><p class=\"check\">Permission for the additional work was obtained: &nbsp;[ ] in person &nbsp;[ ] by phone &nbsp;[ ] by text or email (copy attached)<br>"
             "from ______________________ on ____/____/______ at ______ (am/pm), by employee ______________________</p></div>")
    body += _sig(["Customer signature (consent to the additional repairs and charges)", "Date", f"For {shop['shop']['name']}"])
    return _page(d["title"], shop, body)


def render_invoice_addendum(d: dict[str, Any], shop: dict[str, Any]) -> str:
    m = d["money"]
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
             "<p class=\"check\">[ ] Customer took the replaced parts &nbsp; [ ] Customer declined the replaced parts &nbsp; "
             "[ ] Parts returned to the manufacturer or distributor under warranty or exchange: ______________________</p>")
    body += "<h2>Balance</h2>"
    rows = [("Invoice total", _money(m["invoice_total"])), ("Paid to date", _money(m["paid"])), ("Balance due", _money(m["balance"]))]
    if d["insurer"] != "Customer pay":
        rows += [("Deductible", _money(m["deductible"])), ("Customer portion", _money(m["expected_from_customer"])), ("Customer balance", _money(m["customer_balance"]))]
    body += _meta(rows)
    if m["customer_pay_items"]:
        body += "<table class=\"list\"><tr><th>Customer-pay item</th><th class=\"num\">Amount</th></tr>" + "".join(f"<tr><td>{_e(i['description'])}</td><td class=\"num\">{_money(i['amount'])}</td></tr>" for i in m["customer_pay_items"]) + "</table>"
    if d["warranty"]:
        body += f"<h2>Warranty</h2><p>{_e(d['warranty'])}</p>"
    body += f"<h2>Notices</h2><p class=\"notice\">{_e(d['premises_notice'])}</p><p class=\"notice\"><b>{_e(STATUTES['nhtsa_heading'])}</b><br>{_e(d['nhtsa_notice'])}</p>"
    body += "<p class=\"muted\">The customer receives a signed copy of the invoice and this addendum; the shop retains a copy.</p>"
    body += _sig(["Customer signature", "Date", f"For {shop['shop']['name']}"])
    return _page(d["title"], shop, body)


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
