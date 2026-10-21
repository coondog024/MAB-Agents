"""Parts coordination: build the order from the estimate, apply carrier parts
rules, track backorders and draft vendor requests for quotes."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from ..models import EstimateLine, Part, RepairOrder

# Rough vendor routing by part family. Replace with your real vendor list.
VENDOR_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("bumper cover", "fascia", "grille", "headlamp", "tail lamp", "taillamp", "mirror", "fender", "hood", "door shell", "liftgate"), "OEM dealer or certified aftermarket (CAPA/NSF) per carrier rule"),
    (("quarter", "rocker", "rail", "apron", "radiator support", "pillar", "floor"), "OEM dealer only (structural/welded)"),
    (("windshield", "glass", "back glass", "door glass"), "Glass vendor (sublet install + calibration)"),
    (("airbag", "pretensioner", "seat belt", "srs"), "OEM dealer only (safety restraint)"),
    (("sensor", "radar", "camera", "module"), "OEM dealer (electronics; verify part supersession)"),
    (("wheel", "rim"), "OEM dealer or reconditioned wheel vendor if carrier allows"),
    (("tire",), "Tire vendor (match brand/model/size and tread depth)"),
    (("condenser", "radiator", "cooling fan"), "OEM or aftermarket per carrier rule"),
    (("clip", "fastener", "retainer", "emblem", "molding"), "OEM dealer (one-time-use)"),
]

SAFETY_OEM_ONLY = ("airbag", "pretensioner", "seat belt", "srs", "radar", "camera", "sensor", "rail", "rocker", "pillar", "apron", "radiator support")


def vendor_hint(description: str) -> str:
    d = description.lower()
    for needles, vendor in VENDOR_HINTS:
        if any(n in d for n in needles):
            return vendor
    return "Standard vendor"


def recommend_part_type(line: EstimateLine, *, model_year: int, carrier_rule: dict[str, Any] | None, today: date | None = None) -> tuple[str, str]:
    """Return (recommended_part_type, reason). Safety and structural parts stay OEM."""
    d = line.description.lower()
    if any(n in d for n in SAFETY_OEM_ONLY):
        return "oem", "Safety, structural or electronic component: OEM regardless of carrier preference."
    age = (today or date.today()).year - model_year
    pref = (carrier_rule or {}).get("parts_preference", "").lower()
    if not carrier_rule:
        return ("oem", "Customer pay / no carrier rule on file: quote OEM, offer alternatives with customer sign-off.")
    if age <= 1:
        return "oem", f"Vehicle is {age} model year(s) old; carrier rule: {carrier_rule.get('parts_preference')}"
    if "aftermarket" in pref and any(n in d for n in ("bumper cover", "fascia", "lamp", "grille", "mirror", "fender", "hood")):
        return "aftermarket", f"Cosmetic part on a {age}-year-old vehicle; carrier allows certified aftermarket. Inspect fit on arrival."
    if "recycled" in pref and any(n in d for n in ("door", "liftgate", "deck lid", "assembly", "engine", "transmission", "axle")):
        return "recycled", "Assembly on an older vehicle; carrier allows recycled. Verify VIN match and condition photos."
    return "oem", "Default to OEM; carrier preference does not clearly allow an alternative here."


def build_parts_order(ro: RepairOrder, carrier_rule: dict[str, Any] | None, today: date | None = None) -> list[Part]:
    parts: list[Part] = []
    for l in ro.lines:
        if l.operation not in {"replace", "r&r"} or l.part_price <= 0 and not l.part_number:
            continue
        ptype, reason = recommend_part_type(l, model_year=ro.vehicle.year, carrier_rule=carrier_rule, today=today)
        parts.append(Part(
            description=l.description,
            part_number=l.part_number,
            part_type=l.part_type if l.part_type not in {"none", ""} else ptype,
            qty=l.qty,
            vendor=vendor_hint(l.description),
            price=l.part_price,
            status="needed",
            line=l.line,
        ))
        if l.part_type not in {"none", ""} and l.part_type != ptype:
            parts[-1].description += f"  [estimate says {l.part_type}; rule suggests {ptype}: {reason}]"
    return parts


def parts_status(ro: RepairOrder, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    by_status: dict[str, list[dict]] = {}
    late: list[dict] = []
    for p in ro.parts:
        by_status.setdefault(p.status, []).append(asdict(p))
        if p.status in {"ordered", "backordered"} and p.eta and date.fromisoformat(p.eta) < today:
            late.append({**asdict(p), "days_late": (today - date.fromisoformat(p.eta)).days})
    outstanding = [p for p in ro.parts if p.status in {"needed", "ordered", "backordered"}]
    latest_eta = max((p.eta for p in outstanding if p.eta), default=None)
    return {
        "ro_number": ro.ro_number,
        "total_parts": len(ro.parts),
        "by_status": {k: len(v) for k, v in by_status.items()},
        "outstanding": [asdict(p) for p in outstanding],
        "late": late,
        "all_received": not outstanding and bool(ro.parts),
        "latest_eta": latest_eta,
        "can_start_body": not any(p.status in {"needed", "ordered", "backordered"} and any(n in p.description.lower() for n in ("rail", "apron", "quarter", "support", "rocker", "pillar")) for p in ro.parts),
    }


def rfq_email(ro: RepairOrder, parts: list[Part], shop: dict[str, Any], vendor: str = "Parts Department") -> dict[str, str]:
    v = ro.vehicle
    lines = "\n".join(
        f"  {i}. {p.description} - part# {p.part_number or 'TBD'} - qty {p.qty} - {p.part_type.upper()}"
        for i, p in enumerate(parts, start=1)
    )
    body = (
        f"Hello {vendor},\n\n"
        f"Please quote price and availability on the following for a {v.label()}, VIN {v.vin or 'on request'}"
        f"{', paint code ' + v.paint_code if v.paint_code else ''}:\n\n{lines}\n\n"
        f"Reference: RO {ro.ro_number}. Please include ETA to our shop and note any superseded part numbers.\n\n"
        f"Thanks,\n{shop['shop']['name']}\n{shop['shop']['phone']}\n{shop['shop']['address']}"
    )
    return {"subject": f"RFQ - RO {ro.ro_number} - {v.label()}", "body": body}
