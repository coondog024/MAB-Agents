"""Customer communication drafts: stage updates, delay notices, delivery and review requests.

Plain language, no shop jargon, under the SMS limit from shop.toml. The model
can polish these, but the facts (stage, dates, phone) come from the RO.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models import STAGE_LABELS, RepairOrder

STAGE_TEMPLATES: dict[str, str] = {
    "scheduled": "Hi {first}, this is {shop}. You're scheduled to drop off your {vehicle} on {date}. Please bring your keys, insurance claim info and remove personal items. Questions? Call {phone}.",
    "checked_in": "Hi {first}, your {vehicle} is checked in at {shop}. Next step is teardown so we can find any hidden damage. We'll update you within 2 business days.",
    "blueprint": "Hi {first}, we've started disassembly on your {vehicle} to document all the damage. If we find anything the first estimate missed, we'll send it to {payer} right away and let you know.",
    "approval": "Hi {first}, the full repair plan for your {vehicle} is with {payer} for approval. Parts get ordered the moment it's approved. We'll text you as soon as we hear back.",
    "parts_ordered": "Hi {first}, repairs on your {vehicle} are approved and parts are ordered. Current parts ETA is {parts_eta}. We'll let you know if that changes.",
    "parts_received": "Hi {first}, parts for your {vehicle} have arrived and it's moving into the body shop. Target completion is still {promised}.",
    "body": "Hi {first}, your {vehicle} is in body repair now. Target completion is {promised}. We'll text when it heads to paint.",
    "paint_prep": "Hi {first}, your {vehicle} is being prepped for paint. We match the factory color code and blend so the repair is invisible. Target completion {promised}.",
    "paint": "Hi {first}, your {vehicle} is in the paint booth today. After it cures, we reassemble and do a full quality check. Target completion {promised}.",
    "reassembly": "Hi {first}, paint is done on your {vehicle} and it's being reassembled. Final step is a quality inspection and a systems scan. Target completion {promised}.",
    "qc": "Hi {first}, your {vehicle} is in final inspection and detail. We expect it ready {promised}. We'll confirm pickup time soon.",
    "ready": "Hi {first}, great news: your {vehicle} is ready! Pick up anytime {hours}. {deductible_note}Please bring your rental back the same day if you have one. Call {phone} with questions.",
    "delivered": "Hi {first}, thanks for trusting {shop} with your {vehicle}. Your repairs carry our written lifetime warranty. If anything doesn't look right, call {phone} and we'll make it right.",
}

DELAY_TEMPLATE = "Hi {first}, quick update on your {vehicle}: {reason}. Your new target completion is {new_date}. Sorry for the delay. We'll keep you posted. {rental_note}- {shop}"
REVIEW_TEMPLATE = "Hi {first}, it was a pleasure repairing your {vehicle}. If you were happy with {shop}, a quick Google review helps our small team a lot: {review_url}. Thank you!"
SUPPLEMENT_TEMPLATE = "Hi {first}, during teardown on your {vehicle} we found additional damage ({what}). We've sent a supplement to {payer}. This may add about {days} days. We'll update you once approved."


def _ctx(ro: RepairOrder, shop: dict[str, Any], **extra: Any) -> dict[str, Any]:
    s = shop["shop"]
    first = ro.customer.name.split()[0] if ro.customer.name else "there"
    payer = ro.insurer or "you"
    parts_eta = max((p.eta for p in ro.parts if p.eta), default=None) or "being confirmed"
    deductible_note = f"Your ${ro.deductible:,.0f} deductible is due at pickup. " if ro.deductible else ""
    ctx = {
        "first": first,
        "shop": s.get("short_name", s["name"]),
        "vehicle": ro.vehicle.label(),
        "phone": s["phone"],
        "hours": s["hours"],
        "payer": payer,
        "promised": _fmt(ro.promised) if ro.promised else "being confirmed",
        "parts_eta": _fmt(parts_eta) if parts_eta != "being confirmed" else parts_eta,
        "date": _fmt(ro.dropped_off) if ro.dropped_off else "your scheduled date",
        "deductible_note": deductible_note,
        "review_url": s.get("review_url", ""),
        "rental_note": "We've asked the insurer to extend your rental. " if ro.rental and ro.insurer else "",
    }
    ctx.update(extra)
    return ctx


def _fmt(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return date.fromisoformat(iso[:10]).strftime("%a %b %d").replace(" 0", " ")
    except ValueError:
        return iso


def fit_sms(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def stage_update(ro: RepairOrder, shop: dict[str, Any], stage: str | None = None) -> dict[str, Any]:
    stage = stage or ro.stage
    template = STAGE_TEMPLATES.get(stage)
    if not template:
        raise ValueError(f"No template for stage {stage}")
    text = template.format(**_ctx(ro, shop))
    limit = int(shop.get("sms", {}).get("max_chars", 300))
    return {
        "ro": ro.ro_number,
        "channel": ro.customer.preferred_contact,
        "to": ro.customer.phone if ro.customer.preferred_contact == "sms" else ro.customer.email,
        "stage": STAGE_LABELS[stage],
        "text": fit_sms(text, limit),
        "chars": min(len(text), limit),
    }


def delay_notice(ro: RepairOrder, shop: dict[str, Any], reason: str, new_date: str) -> dict[str, Any]:
    text = DELAY_TEMPLATE.format(**_ctx(ro, shop, reason=reason.rstrip("."), new_date=_fmt(new_date)))
    return {"ro": ro.ro_number, "to": ro.customer.phone, "text": fit_sms(text, int(shop["sms"]["max_chars"]))}


def supplement_notice(ro: RepairOrder, shop: dict[str, Any], what: str, extra_days: int) -> dict[str, Any]:
    text = SUPPLEMENT_TEMPLATE.format(**_ctx(ro, shop, what=what, days=extra_days))
    return {"ro": ro.ro_number, "to": ro.customer.phone, "text": fit_sms(text, int(shop["sms"]["max_chars"]))}


def review_request(ro: RepairOrder, shop: dict[str, Any]) -> dict[str, Any]:
    text = REVIEW_TEMPLATE.format(**_ctx(ro, shop))
    return {"ro": ro.ro_number, "to": ro.customer.phone, "text": text}


def review_reply_guidelines(stars: int) -> list[str]:
    base = ["Thank the reviewer by first name only.", "Never mention claim numbers, insurers or repair details publicly.", "Sign with the manager's first name and the shop name."]
    if stars >= 4:
        return base + ["Mention one specific thing they praised.", "Invite them back and mention the lifetime warranty."]
    return base + [
        "Apologize without arguing; acknowledge the specific frustration.",
        "Take it offline: give the manager's direct line and ask them to call.",
        "State one concrete thing you will do (re-inspect, re-detail, review the timeline).",
        "Keep it under 80 words.",
    ]
