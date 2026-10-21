"""Parts coordination: build the order from the estimate, apply carrier parts
rules, track backorders and draft vendor requests for quotes."""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, datetime, timedelta
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
    """Return (recommended_part_type, reason).

    Safety, structural and electronic parts stay OEM. Otherwise a non-OEM type is
    recommended only when the carrier rule's structured lists name this part and
    the vehicle is at least the rule's minimum age; free text is never parsed.
    """
    d = line.description.lower()
    if any(n in d for n in SAFETY_OEM_ONLY):
        return "oem", "Safety, structural or electronic component: OEM regardless of carrier preference."
    if not carrier_rule:
        return "oem", "Customer pay / no carrier rule on file: quote OEM, offer alternatives with customer sign-off."
    age = (today or date.today()).year - model_year
    for ptype, parts_key, age_key in (("aftermarket", "aftermarket_allowed_parts", "aftermarket_min_age_years"), ("recycled", "recycled_allowed_parts", "recycled_min_age_years")):
        allowed = [a.lower() for a in carrier_rule.get(parts_key, [])]
        min_age = int(carrier_rule.get(age_key, 0))
        if allowed and any(a in d for a in allowed):
            if age >= min_age:
                return ptype, f"{carrier_rule['name']} allows {ptype} on '{allowed[[a in d for a in allowed].index(True)]}' at {min_age}+ model years (vehicle is {age}). Inspect fit on arrival; photos for recycled."
            return "oem", f"{carrier_rule['name']} allows {ptype} on this part only at {min_age}+ model years; vehicle is {age}."
    if not carrier_rule.get("aftermarket_allowed_parts") and not carrier_rule.get("recycled_allowed_parts"):
        return "oem", f"No structured parts lists on the {carrier_rule['name']} rule; defaulting to OEM. Add aftermarket_allowed_parts / recycled_allowed_parts to shop.toml to apply the agreement."
    return "oem", f"{carrier_rule['name']} rule does not list this part for aftermarket or recycled; OEM."


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


# ---------------------------------------------------------------------------
# Pricing and quote comparison
# ---------------------------------------------------------------------------
def price_part(
    list_price: float,
    part_type: str,
    shop: dict[str, Any],
    *,
    cost: float | None = None,
    max_markup: float | None = None,
) -> dict[str, Any]:
    """What a part costs us and what we bill, using the [parts] matrix in shop.toml.

    OEM: cost is list minus the dealer discount; we bill list. Everything else:
    cost is what the vendor quoted (or list if unknown) and we bill cost plus the
    markup. `max_markup` caps the markup when a carrier agreement limits it.
    """
    pcfg = shop.get("parts", {})
    pt = (part_type or "oem").lower().replace("opt_oem", "oem")
    notes: list[str] = []
    if pt == "oem":
        discount = float(pcfg.get("oem_dealer_discount", 0.25))
        cost_val = cost if cost is not None else round(list_price * (1 - discount), 2)
        sell = round(list_price, 2)
        markup = round(sell / cost_val - 1, 4) if cost_val else None
        notes.append(f"OEM billed at list; cost assumes {discount:.0%} dealer discount" if cost is None else "OEM billed at list; cost as quoted")
    else:
        key = {"aftermarket": "aftermarket_markup", "recycled": "recycled_markup", "reconditioned": "reconditioned_markup", "sublet": "sublet_markup"}.get(pt, "aftermarket_markup")
        markup = float(pcfg.get(key, 0.3))
        if max_markup is not None and markup > max_markup:
            notes.append(f"Markup capped at {max_markup:.0%} by carrier agreement (shop default {markup:.0%})")
            markup = max_markup
        cost_val = cost if cost is not None else list_price
        sell = round(cost_val * (1 + markup), 2)
        if list_price and sell > list_price:
            notes.append(f"Billed price exceeds OEM list ${list_price:,.2f}; carriers usually will not pay above list for non-OEM")
    gp = round(sell - cost_val, 2)
    return {
        "part_type": pt,
        "list_price": list_price,
        "cost": round(cost_val, 2),
        "sell": sell,
        "gross_profit": gp,
        "gross_profit_pct": round(gp / sell, 4) if sell else None,
        "markup_applied": markup,
        "notes": notes,
    }


def parse_eta_days(eta: Any, today: date) -> int | None:
    """Days until a part lands. Accepts an integer, 'N', 'N days', ISO dates, 10/28 or 10/28/2026. None if unreadable."""
    if eta is None or eta == "":
        return 0
    if isinstance(eta, bool):
        return None
    if isinstance(eta, (int, float)):
        return max(int(eta), 0)
    text = str(eta).strip().lower()
    m = re.fullmatch(r"(\d{1,3})\s*(d|day|days|business days)?", text)
    if m:
        return int(m.group(1))
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d", "%b %d", "%B %d"):
        try:
            d = datetime.strptime(text, fmt).date()
            if fmt in ("%m/%d", "%b %d", "%B %d"):
                d = d.replace(year=today.year)
                if d < today - timedelta(days=30):
                    d = d.replace(year=today.year + 1)
            return max((d - today).days, 0)
        except ValueError:
            continue
    return None


def compare_quotes(
    quotes: list[dict[str, Any]],
    shop: dict[str, Any],
    *,
    rental: bool = False,
    safety_part: bool = False,
    list_price: float | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Rank vendor quotes by total cost to the job, counting delay days.

    quotes: [{vendor, part_type, price, eta (ISO date or integer days), shipping?, notes?}]
    A cheaper part that lands three days later costs three rental days; with
    `rental` true that is priced at parts.rental_cost_per_day. Safety parts drop
    every non-OEM quote.
    """
    today = today or date.today()
    per_day = float(shop.get("parts", {}).get("rental_cost_per_day", 0)) if rental else 0.0
    ranked = []
    dropped = []
    for q in quotes:
        pt = str(q.get("part_type", "oem")).lower()
        if safety_part and pt != "oem":
            dropped.append({**q, "why": "safety/structural part: OEM only"})
            continue
        days = parse_eta_days(q.get("eta"), today)
        if days is None:
            dropped.append({**q, "why": f"could not read ETA '{q.get('eta')}': use days (e.g. 3) or a date like 2026-10-28 or 10/28"})
            continue
        price = float(q.get("price", 0)) + float(q.get("shipping", 0) or 0)
        q_list = q.get("list") if q.get("list") is not None else list_price
        notes = str(q.get("notes", ""))
        if pt == "oem" and q_list is None:
            disc = float(shop.get("parts", {}).get("oem_dealer_discount", 0.25))
            q_list = round(price / (1 - disc), 2) if disc < 1 else price
            notes = (notes + "; " if notes else "") + f"list assumed from dealer net at {disc:.0%} discount; confirm on the quote"
        pricing = price_part(q_list if q_list is not None else price, pt, shop, cost=price)
        delay_cost = round(days * per_day, 2)
        ranked.append({
            "vendor": q.get("vendor", "?"),
            "part_type": pt,
            "landed_cost": round(price, 2),
            "days_to_arrive": days,
            "delay_cost": delay_cost,
            "total_cost_to_job": round(price + delay_cost, 2),
            "we_bill": pricing["sell"],
            "gross_profit": pricing["gross_profit"],
            "notes": notes,
            "url": q.get("url", ""),
        })
    ranked.sort(key=lambda r: (r["total_cost_to_job"], r["days_to_arrive"], -r["gross_profit"]))
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return {
        "rental_cost_per_day_used": per_day,
        "recommended": ranked[0] if ranked else None,
        "ranked": ranked,
        "dropped": dropped,
        "note": "Total cost to the job = landed cost + days to arrive x rental cost per day. Confirm fit, warranty and return policy before ordering non-OEM.",
    }
