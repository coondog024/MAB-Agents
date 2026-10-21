"""Accounts receivable and short-pay follow-up.

Carriers pay less than the invoice constantly (safety inspections, scans,
not-included operations). The money is only lost when nobody chases it. These
tools keep a balance per RO, age it, and draft the letter and the Maryland
Insurance Administration escalation.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..models import RepairOrder
from .insurer_tools import _basis

MIA_CONTACT = {
    "name": "Maryland Insurance Administration, Property & Casualty Complaints",
    "address": "200 St. Paul Place, Suite 2700, Baltimore, MD 21202",
    "consumer_line": "800-492-6116",
    "phone": "410-468-2000",
    "rapid_response_auto_claims": "410-468-2340",
    "email": "pccomplaints.mia@maryland.gov",
    "online": "https://insurance.maryland.gov/Consumer/Pages/FileAComplaint.aspx",
    "portal": "https://enterprise.insurance.maryland.gov/consumer",
    "expect": "Allow up to 90 days for a decision; most resolve sooner. Attach every document (estimates, supplements, photos, correspondence).",
    "claim_clocks": "COMAR 31.15.07: acknowledge a claim within 15 working days; affirm or deny coverage within 15 working days after proof of loss; a written reason every 45 days while a first-party investigation stays open. Ins. 27-305: up to $2,500 per violation.",
    "note": "The MIA judges whether the insurer acted reasonably (Ins. 27-303/27-304 unfair claim settlement practices); it does not set repair values. Ins. 10-502 lets an insurer limit payment to usual and customary area charges, so document market rates and OEM requirements.",
}


def invoice_total(ro: RepairOrder, rates: dict[str, float]) -> float:
    """What the job bills: the estimate's gross when we have it, else lines at posted rates."""
    gross = float(ro.estimate_totals.get("gross", 0) or 0)
    if gross:
        return round(gross, 2)
    labor = sum(l.labor_hours * float(rates.get(l.labor_type, rates.get("body", 0))) for l in ro.lines)
    paint = ro.paint_hours() * (float(rates.get("refinish", 0)) + float(rates.get("paint_materials_per_hour", 0)))
    return round(labor + paint + ro.parts_total(), 2)


def balance(ro: RepairOrder, rates: dict[str, float]) -> dict[str, Any]:
    total = invoice_total(ro, rates)
    paid = ro.paid_total()
    by_payer: dict[str, float] = {}
    for p in ro.payments:
        by_payer[p.get("payer", "?")] = round(by_payer.get(p.get("payer", "?"), 0) + float(p.get("amount", 0)), 2)
    expected_customer = round(ro.deductible + ro.customer_pay_total(), 2) if ro.insurer else total
    expected_carrier = round(total - expected_customer, 2) if ro.insurer else 0.0
    carrier_paid = round(sum(v for k, v in by_payer.items() if k.lower() not in {"customer", "owner", "cash", "card", "check"}), 2) if ro.insurer else 0.0
    customer_paid = round(paid - carrier_paid, 2)
    return {
        "ro": ro.ro_number,
        "invoice_total": total,
        "paid": paid,
        "balance": round(total - paid, 2),
        "expected_from_customer": expected_customer,
        "customer_paid": customer_paid,
        "customer_balance": round(expected_customer - customer_paid, 2),
        "expected_from_carrier": expected_carrier,
        "carrier_paid": carrier_paid,
        "carrier_short": round(expected_carrier - carrier_paid, 2) if ro.insurer else 0.0,
        "by_payer": by_payer,
    }


def aging(ros: list[RepairOrder], rates: dict[str, float], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = []
    buckets = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for ro in ros:
        if ro.stage not in {"ready", "delivered"}:
            continue
        b = balance(ro, rates)
        if b["balance"] <= 0.5:
            continue
        anchor = ro.delivered or ro.promised or ro.dropped_off or today.isoformat()
        age = max((today - date.fromisoformat(anchor[:10])).days, 0)
        bucket = "0-30" if age <= 30 else "31-60" if age <= 60 else "61-90" if age <= 90 else "90+"
        buckets[bucket] = round(buckets[bucket] + b["balance"], 2)
        rows.append({"ro": ro.ro_number, "customer": ro.customer.name, "insurer": ro.insurer or "Customer pay", "claim_number": ro.claim_number, "age_days": age, "bucket": bucket, **{k: b[k] for k in ("invoice_total", "paid", "balance", "customer_balance", "carrier_short")}})
    rows.sort(key=lambda r: (-r["age_days"], -r["balance"]))
    return {"as_of": today.isoformat(), "open_items": len(rows), "total_outstanding": round(sum(r["balance"] for r in rows), 2), "buckets": buckets, "rows": rows}


def short_pay_letter(ro: RepairOrder, shop: dict[str, Any], items: list[dict[str, Any]], paid_amount: float, today: date | None = None) -> dict[str, str]:
    """items: [{description, amount, justification}] the carrier did not pay."""
    today = today or date.today()
    s = shop["shop"]
    total = round(sum(float(i.get("amount", 0)) for i in items), 2)
    lines = "\n".join(f"  {n}. {i['description']} - ${float(i.get('amount', 0)):,.2f}\n     Basis: {_basis(i)}. {i.get('justification', '')}".rstrip() for n, i in enumerate(items, start=1))
    subject = f"Short payment on claim {ro.claim_number or 'TBD'} - RO {ro.ro_number} - {ro.vehicle.label()} - balance ${total:,.2f}"
    body = (
        f"{ro.adjuster or 'Claims'},\n\n"
        f"Thank you for the payment of ${paid_amount:,.2f} on the above claim. The following items on the final invoice were not paid:\n\n{lines}\n\n"
        f"Each was required to return the vehicle to pre-loss condition; the procedure or estimating-guide basis is noted on each line and the supporting documents (OEM procedure, photos, scan or calibration reports) are attached. "
        f"Please issue the balance of ${total:,.2f} or reply in writing with the specific reason each line is declined.\n\n"
        f"If we do not hear back within 10 business days we will advise the vehicle owner of the unpaid balance and of their right to raise it with the Maryland Insurance Administration.\n\n"
        f"Thank you,\n{s.get('manager', 'Shop Manager')}\n{s['name']}\n{s['phone']}\n{today.isoformat()}"
    )
    return {"subject": subject, "body": body, "balance": f"{total:.2f}"}


def mia_complaint_outline(ro: RepairOrder, items: list[dict[str, Any]], timeline: list[str]) -> dict[str, Any]:
    """What a customer (or the shop on their behalf) puts in an MIA complaint. The complaint is the policyholder's; the shop supplies the file."""
    return {
        "where": MIA_CONTACT,
        "who_files": "The policyholder files (a shop may assist and supply documents). Third-party claimants may also file.",
        "include": [
            f"Claim number {ro.claim_number or 'TBD'}, insurer {ro.insurer or 'TBD'}, adjuster {ro.adjuster or 'TBD'}, vehicle {ro.vehicle.label()} VIN {ro.vehicle.vin or 'TBD'}",
            "Final invoice and the insurer's payment breakdown showing the unpaid lines",
            *[f"Unpaid: {i['description']} ${float(i.get('amount', 0)):,.2f} - {_basis(i)}" for i in items],
            "OEM position statements and repair procedures for each unpaid operation (file names from the knowledge base)",
            "Photos, pre/post scan reports, calibration reports",
            "Dated log of every contact with the adjuster",
            *timeline,
            "What you are asking for: payment of the balance and a written explanation under Ins. 27-304 for any item still declined",
        ],
        "do_not": ["Argue labor rates on the complaint; cite usual and customary evidence instead (Ins. 10-502)", "Include other customers' information"],
    }
