"""Insurance and claims helpers: supplement requests, total-loss math, rental and storage notices."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models import RepairOrder

# REFERENCE ONLY. Total-loss thresholds by state as a percentage of actual cash value; "TLF"
# means the state uses the total loss formula (repair cost + salvage value >= ACV). These are
# unverified reference values and laws change: set [shop] total_loss_rule in shop.toml for
# your own state after checking the current statute; that value overrides this table.
TOTAL_LOSS_THRESHOLDS: dict[str, float | str] = {
    "AL": 75, "AR": 70, "CO": 100, "DC": 75, "FL": 80, "GA": "TLF", "IA": 50, "IN": 70, "KS": 75, "KY": 75,
    "LA": 75, "MD": 75, "MI": 75, "MN": 80, "MO": 80, "MS": "TLF", "NC": 75, "ND": 75, "NE": 75, "NH": 75,
    "NV": 65, "NY": 75, "OH": "TLF", "OK": 60, "OR": 80, "PA": 100, "SC": 75, "TN": 75, "TX": 100,
    "VA": 75, "WI": 70, "WV": 75, "WY": 75,
    "AZ": "TLF", "CA": "TLF", "CT": "TLF", "DE": "TLF", "HI": "TLF", "ID": "TLF", "IL": "TLF", "MA": "TLF",
    "ME": "TLF", "MT": "TLF", "NJ": "TLF", "NM": "TLF", "RI": "TLF", "SD": "TLF", "UT": "TLF",
    "VT": "TLF", "WA": "TLF", "AK": "TLF",
}


def total_loss_check(repair_cost: float, acv: float, state: str, salvage_value: float | None = None, shop: dict[str, Any] | None = None) -> dict[str, Any]:
    st = state.upper()
    shop_cfg = (shop or {}).get("shop", {})
    verified = False
    if shop and shop_cfg.get("state", "").upper() == st and shop_cfg.get("total_loss_rule"):
        raw = str(shop_cfg["total_loss_rule"]).strip().upper()
        rule: float | str | None = "TLF" if raw == "TLF" else float(raw.rstrip("%"))
        verified = True
    else:
        rule = TOTAL_LOSS_THRESHOLDS.get(st)
    ratio = repair_cost / acv if acv else None
    result: dict[str, Any] = {
        "state": st,
        "repair_cost": repair_cost,
        "acv": acv,
        "repair_to_acv_pct": round(ratio * 100, 1) if ratio is not None else None,
        "rule": rule,
        "rule_source": f"shop.toml (verified {shop_cfg.get('total_loss_rule_verified', 'date not recorded')})" if verified else "unverified reference table",
        "likely_total_loss": None,
        "note": ("The carrier's own total-loss policy and the current statute govern; this is a planning number. "
                 + ("" if verified else "The rule came from an unverified reference table: check the statute and set [shop] total_loss_rule in shop.toml. ")
                 + "Do not tell the customer the car is or isn't a total loss; only the carrier decides."),
    }
    if rule is None:
        result["note"] = "State not in table; look up the statute and set [shop] total_loss_rule in shop.toml."
    elif rule == "TLF":
        if salvage_value is None:
            result["note"] += " TLF state: provide salvage_value to evaluate (repair + salvage >= ACV)."
        else:
            result["likely_total_loss"] = repair_cost + salvage_value >= acv
            result["tlf_sum"] = round(repair_cost + salvage_value, 2)
    else:
        result["threshold_pct"] = rule
        result["likely_total_loss"] = ratio is not None and ratio * 100 >= float(rule)
        result["headroom_dollars"] = round(acv * float(rule) / 100 - repair_cost, 2)
    return result


NOT_INCLUDED_HINTS = ("cover car", "mask", "tint", "spray-out", "spray out", "buff", "feather", "prime", "block", "hazardous", "flex", "clips", "fastener", "set up", "measure", "seam seal", "corrosion", "foam", "transfer", "r&i")


def _basis(item: dict[str, Any]) -> str:
    text = f"{item.get('description', '')} {item.get('justification', '')}".lower()
    if "oem" in text or "procedure" in text or "position statement" in text:
        return "per the OEM repair procedure cited"
    if any(h in text for h in NOT_INCLUDED_HINTS):
        return "a not-included operation per the estimating system's procedure pages"
    return "additional damage found on teardown (see photos)"


def supplement_request(ro: RepairOrder, shop: dict[str, Any], items: list[dict[str, Any]], photos_attached: bool = True, supplement_number: int = 1) -> dict[str, str]:
    """items: [{description, labor_hours, labor_type, paint_hours, part_price, justification}]"""
    v = ro.vehicle
    rates = shop["rates"]
    rows = []
    total = 0.0
    for i, it in enumerate(items, start=1):
        labor = float(it.get("labor_hours", 0)) * float(rates.get(it.get("labor_type", "body"), rates["body"]))
        paint = float(it.get("paint_hours", 0)) * float(rates["refinish"])
        mats = float(it.get("paint_hours", 0)) * float(rates["paint_materials_per_hour"])
        part = float(it.get("part_price", 0))
        line_total = labor + paint + mats + part
        total += line_total
        rows.append(
            f"{i}. {it['description']} - labor {it.get('labor_hours', 0)}h {it.get('labor_type', 'body')}, refinish {it.get('paint_hours', 0)}h, parts ${part:,.2f} = ${line_total:,.2f}\n"
            f"   Basis: {_basis(it)}. {it.get('justification', 'Found on teardown; see photos.')}"
        )
    subject = f"Supplement #{supplement_number} - Claim {ro.claim_number or 'TBD'} - RO {ro.ro_number} - {v.label()}"
    body = (
        f"{ro.adjuster or 'Claims'},\n\n"
        f"Please find supplement #{supplement_number} for the above claim. Vehicle: {v.label()}, VIN {v.vin}. "
        f"Each item below is needed to return the vehicle to pre-loss condition; the basis for each is stated on its line.\n\n"
        + "\n".join(rows)
        + f"\n\nSupplement total: ${total:,.2f} at our posted rates (body ${rates['body']}/hr, refinish ${rates['refinish']}/hr, mechanical ${rates['mechanical']}/hr).\n"
        + ("Photos and the OEM procedure excerpts are attached. " if photos_attached else "Photos available on request. ")
        + "Vehicle is disassembled and waiting; a same-day review keeps the rental and cycle time down.\n\n"
        f"Thank you,\n{shop['shop']['manager']}\n{shop['shop']['name']}\n{shop['shop']['phone']}"
    )
    return {"subject": subject, "body": body, "total": f"{total:.2f}"}


def rental_extension(ro: RepairOrder, shop: dict[str, Any], new_date: str, reason: str) -> dict[str, str]:
    return {
        "subject": f"Rental extension request - Claim {ro.claim_number} - RO {ro.ro_number}",
        "body": (
            f"{ro.adjuster or 'Claims'},\n\nRequesting rental coverage be extended through {new_date} for the {ro.vehicle.label()} on the above claim. "
            f"Reason: {reason}. Current status: {ro.stage.replace('_', ' ')}. "
            f"We will notify you the day the vehicle is completed so the rental can be returned promptly.\n\n"
            f"Thank you,\n{shop['shop']['name']}\n{shop['shop']['phone']}"
        ),
    }


def storage_notice(ro: RepairOrder, shop: dict[str, Any], days: int, today: date | None = None) -> dict[str, Any]:
    rate = float(shop["rates"]["storage_per_day"])
    teardown = float(shop["rates"].get("teardown_fee", 0))
    return {
        "ro": ro.ro_number,
        "days": days,
        "storage_per_day": rate,
        "storage_total": round(rate * days, 2),
        "teardown_fee": teardown,
        "total_due": round(rate * days + teardown, 2),
        "as_of": (today or date.today()).isoformat(),
        "text": (
            f"Notice of charges - RO {ro.ro_number}, {ro.vehicle.label()}: administrative/teardown fee ${teardown:,.2f} plus storage at ${rate:,.2f}/day "
            f"({days} days = ${rate * days:,.2f}). Total due before release: ${rate * days + teardown:,.2f}. Storage continues to accrue daily."
        ),
    }
