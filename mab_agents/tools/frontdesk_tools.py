"""Front-desk helpers: phone scripts, checklists, customer lookup, today's call list, what the customer owes.

The scripts here are the shop's defaults. Anything the shop writes into the
knowledge folder (front_desk_playbook.md) takes precedence; the Front Desk
agent is told to search that first.
"""
from __future__ import annotations

import difflib
from datetime import date, datetime
from typing import Any

from ..models import STAGE_LABELS, RepairOrder

SCRIPTS: dict[str, dict[str, Any]] = {
    "new_claim": {
        "title": "New claim / first call",
        "ask": [
            "Is everyone OK? Is the car drivable, and where is it right now? (the person and the car first; insurance questions come after)",
            "Name and best mobile number for texts",
            "Year, make, model (and colour)",
            "What happened and where the damage is",
            "Any warning lights, leaks, wheel damage, airbags? (airbags or leaks = do not drive it, offer a tow)",
            "Are they thinking of a claim or paying out of pocket? If a claim: insurance company and claim number if they have one. A claim number is not required to schedule.",
            "Does the policy include rental? How many days and what daily limit? Do they need one?",
            "When can they bring it in",
        ],
        "say": [
            "You choose who repairs your car. If you tell the insurance company you're bringing it to Mullikin's, we handle the paperwork with them.",
            "The insurance company's first estimate is a starting point; most jobs find more once the car is apart. We document it with photos and send it to them the same day.",
            "Once we take it apart and the insurer approves the repair plan, we'll give you a target date and text you every 2 to 3 days.",
        ],
        "never": ["Open with 'Have you filed a claim yet?'", "Promise a completion date before teardown", "Quote a repair price over the phone"],
        "then": "Create the RO (Check-in tab) with the rental days, and send the photo checklist if they want a rough idea before drop-off. For a self-pay customer, offer a written cash estimate within 24 hours.",
    },
    "status_call": {
        "title": "Customer wants a status",
        "ask": ["Phone number or name to look up the RO"],
        "say": [
            "Read the current stage in plain words; the status text draft already is.",
            "If it's waiting on the insurer or a part, say so and say what we're doing about it today.",
            "Would you like me to text you updates as it moves? We do that at every stage.",
        ],
        "never": ["Guess a date that isn't on the RO", "Say 'it should be done soon'"],
        "then": "Log the call as a note on the RO. If the promised date moved, send the delay text.",
    },
    "delay_complaint": {
        "title": "Angry about a delay",
        "ask": ["Let them finish. Then confirm the RO."],
        "say": [
            "You're right, that's longer than we want too.",
            "Here is exactly where it is today and the one thing it's waiting on.",
            "I'm going to <action> and text you by <time> today.",
        ],
        "never": ["Blame a technician, insurer or vendor by name", "Say the manager is busy when they ask for the manager"],
        "then": "Do the action. Text by the time you promised even if the news is 'no change yet'.",
    },
    "deductible": {
        "title": "Why do I pay the deductible to you?",
        "ask": [],
        "say": [
            "The insurance company subtracts your deductible from what they pay us, so the shop collects it at pickup. We take card, cash or check.",
            "If the other driver was at fault, your insurer may recover it for you later; that's a question for your adjuster.",
        ],
        "never": ["Offer to waive or 'absorb' a deductible"],
        "then": "Put the amount in the ready-for-pickup text so there are no surprises.",
    },
    "insurer_calling": {
        "title": "Insurance company / adjuster calling",
        "ask": ["Claim number first, then the RO"],
        "say": ["Current stage, whether the supplement is submitted, the promised date, the rental return date."],
        "never": ["Argue labor rates or operations; hand that to the estimator or manager"],
        "then": "Note the call on the RO with the adjuster's name and what they asked for.",
    },
    "tow_arrival": {
        "title": "Tow truck arriving",
        "ask": ["Tow company and driver name, tow bill, whose account it goes on", "Is the key with the car?"],
        "say": ["Photograph the car on the truck and after unloading, all four corners."],
        "never": ["Sign for a car without photos"],
        "then": "Write the RO as a tow-in (not drivable), check for personal items, text the customer it arrived.",
    },
    "rental": {
        "title": "Rental car questions",
        "ask": ["Does the policy have rental coverage? Which rental company and reservation?"],
        "say": [
            "The insurer sets up the reservation; we give the rental company the promised date and update it when it moves.",
            "If the repair will run past the covered days, we ask the adjuster for an extension before it runs out.",
        ],
        "never": ["Tell a customer the insurer will definitely extend"],
        "then": "Put the rental company and return date on the RO notes.",
    },
    "after_hours_pickup": {
        "title": "After-hours pickup",
        "ask": ["Is the balance paid? Is the release signed?"],
        "say": ["Keys go in the lockbox; the code goes only to the mobile number on the RO."],
        "never": ["Release a car with a balance due"],
        "then": "Send the delivered text and the review request the next morning.",
    },
    "steering": {
        "title": "Insurer says they must use the insurer's shop",
        "ask": [],
        "say": [
            "You choose who repairs your car. The insurance company can recommend shops, but it can't require one.",
            "They pay based on the estimate they approve. If what they approve is less than our invoice, we tell you before work starts, not at pickup.",
            "We work with <carrier> regularly and handle the paperwork with them.",
        ],
        "never": ["Argue with the customer about their insurer", "Promise the insurer will pay our full invoice"],
        "then": "Move to scheduling. If they push back, offer the manager. Write the adjuster's name, the date and the exact words on the RO; Maryland Insurance Article 10-503 requires insurers to tell customers they may choose their shop, and the MIA consumer line is 800-492-6116 (details in the knowledge base).",
    },
    "diminished_value": {
        "title": "Customer asks whether the car is worth less now (diminished value)",
        "ask": ["Was the other driver at fault, and which carrier is paying?", "Is the repair finished and the final invoice in hand?"],
        "say": [
            "A repaired car can be worth less than it was before the accident. That loss is called diminished value, and in Maryland it's a real claim.",
            "If the other driver was at fault, you claim it from their insurance company after the repair is done. If it was a hit-and-run or an uninsured driver, your own uninsured-motorist property damage coverage has to consider it too.",
            "Insurers don't bring it up; you ask in writing. You'll need a before-and-after valuation from a dealer or an independent appraiser, and the proof is on you. We'll give you the final invoice, the photos and the scan and calibration reports for the file.",
        ],
        "never": ["Quote an amount or promise the insurer will pay", "Say the shop appraises diminished value or files the claim", "Promise it applies under the customer's own collision coverage (the policy may exclude it)"],
        "then": "Note on the RO that DV was discussed. Point them to the MIA consumer line 800-492-6116 if the carrier refuses to consider it; MIA Bulletin 24-8 and the Maryland rules doc in the knowledge base have the details.",
    },
    "total_loss": {
        "title": "The insurer says it may be a total loss",
        "ask": ["Which carrier and adjuster; has the customer received a valuation or an offer yet?", "Is there a loan on the car? Are personal items still in it?"],
        "say": [
            "Only the insurance company decides a total loss. In Maryland the title threshold is repairs over 75% of the car's value, but they can total it below that.",
            "Once they decide, they generally have 10 business days to make you a cash offer, and you can ask in writing for the valuation details; they have 7 business days to send them, and 5 to answer a counteroffer.",
            "We'll send them the total-loss notice today and copy you. Please get your personal items out and send us the lender's payoff information if there's a loan.",
        ],
        "never": ["Say the car is or isn't a total loss", "Let storage accrue silently; the storage policy goes to the customer and the carrier in writing"],
        "then": "Move the RO to total-loss handling with the Insurance agent: storage notice, personal items, lien payoff letter, written valuation request. Rental days keep burning while they wait, so push the adjuster.",
    },
    "betterment": {
        "title": "Why is there a charge for tires or a battery?",
        "ask": ["Which line and what did the adjuster prorate (tread depth, age)?"],
        "say": [
            "The insurance company only pays for the wear that was left on the old part. The new tire gives you more life than you had, so they charge the difference; that's called betterment.",
            "We'll show you the exact dollar amount and the measurement behind it before we order the part, and it appears as its own line on your invoice, separate from the deductible.",
        ],
        "never": ["Order a part with betterment before the customer has agreed to the amount"],
        "then": "Add the betterment as a customer-pay item on the RO so the balance at pickup is right.",
    },
    "claim_or_cash": {
        "title": "Should I file a claim or pay for this myself?",
        "ask": ["What is their deductible? Roughly what size is the damage? Have they had a claim recently?"],
        "say": [
            "If the repair is close to your deductible, paying yourself can make sense; we can write you a cash estimate within 24 hours, no claim number needed.",
            "We can't predict what your insurer will do to your rate; that's a question for your agent. We can give you the number to compare against your deductible.",
        ],
        "never": ["Tell a customer whether their premium will go up"],
        "then": "Book an estimate slot and note 'cash estimate requested' on the RO.",
    },
    "customer_pay_estimate": {
        "title": "Customer-pay estimate request",
        "ask": ["Photos or an appointment for a walk-around", "Budget expectations: fix properly vs cosmetic only"],
        "say": ["Our estimate is free with the car here. Photo estimates are a range, not a price."],
        "never": ["Quote a number from a photo as final"],
        "then": "Book a 15-minute estimate slot; the estimator writes it.",
    },
}

CHECKIN_CHECKLIST = [
    "Photo ID and insurance card; confirm mobile number and text/call preference",
    "Signed repair authorization with the Maryland 'Customer's Rights' block above the signature (Com. Law 14-1008) and direction-to-pay; the daily storage rate and its start date are written in it",
    "Tow-in or after-hours drop: give the rights notice by phone and write down who was told, the date and time, and sign the note (14-1008 allows oral notice only with that record)",
    "Walk-around with the customer: four corners, VIN, odometer, dash lights, interior, prior damage (point it out and note it)",
    "Personal items out; toll transponder, garage opener, child seats noted",
    "Fuel level and mileage on the RO",
    "Explain the sequence: teardown, insurer approval, parts, body, paint, reassembly, scan and calibration, quality check",
    "Set expectations: a text at every stage",
    "Rental handed off or confirmed",
]

DELIVERY_CHECKLIST = [
    "Washed, vacuumed, no dust in jambs, no tape, no overspray on glass or trim",
    "Invoice matches approved estimate plus supplements; deductible and customer-pay items itemized; used or reconditioned parts clearly marked (Com. Law 14-1003); customer signs and gets a copy",
    "Offer the replaced parts back (Com. Law 14-1004, except warranty-return cores); note on the RO whether they took or declined them",
    "Walk the repaired area and panel gaps with the customer; show calibration report and post-repair scan if applicable",
    "Warranty wording handed over, explained in one sentence",
    "Payment collected; insurer checks payable to the customer endorsed",
    "Keys, personal items, rental return reminder",
    "Review request text goes out the same day",
]

DAILY_ROUTINE = {
    "opening": [
        "Open the Today screen (or `mab brief`)",
        "Work the 'Needs an update' list: everyone silent for 2+ days gets a text today, and every text says when they'll hear from us next",
        "Ready for pickup: confirm pickup times and what they owe (deductible, betterment, anything the insurer declined)",
        "Awaiting approval: supplements older than 48 hours get a call to the adjuster; older than 72 hours go to the manager",
        "Rental cars: anyone past day 15, or within 5 days of their covered days, gets the adjuster asked for an extension today",
        "Confirm today's drop-offs and rentals",
    ],
    "during": [
        "Phone by the third ring, with your name; open with the person and the car, not 'have you filed a claim?'",
        "Missed call: text back within a few minutes",
        "Every stage change from the floor becomes a customer text the same day; bad news (delay, hidden damage) goes by phone, then text",
        "Log every call and text on the RO",
    ],
    "closing": [
        "Cars promised tomorrow: confirm with production, text the customer either way",
        "Keys locked; ready cars marked",
        "Batch out the card terminal",
    ],
}


FILLER = set("a an the is are on about customer customers caller call calls calling phone asking asks says said wants want to me my i what do when they them someone".split())


def _words(text: str) -> set[str]:
    return {w for w in text.lower().replace("_", " ").replace("-", " ").replace("/", " ").split() if w not in FILLER and len(w) > 2}


def phone_script(situation: str) -> dict[str, Any]:
    key = situation.strip().lower().replace(" ", "_")
    if key in SCRIPTS:
        return {"situation": key, **SCRIPTS[key]}
    words = _words(situation)
    best, best_score = None, 0
    for k, v in SCRIPTS.items():
        score = len(words & (_words(k) | _words(v["title"])))
        if score > best_score:
            best, best_score = k, score
    if best is None:
        close = difflib.get_close_matches(key, list(SCRIPTS), n=1, cutoff=0.75)
        best = close[0] if close else None
    if not best:
        return {"situation": situation, "error": "No script for that; available: " + ", ".join(SCRIPTS)}
    return {"situation": best, **SCRIPTS[best]}


def ro_summary(ro: RepairOrder, today: date | None = None) -> dict[str, Any]:
    outstanding = [p for p in ro.parts if p.status in {"needed", "ordered", "backordered"}]
    return {
        "ro": ro.ro_number,
        "customer": ro.customer.name,
        "phone": ro.customer.phone,
        "vehicle": ro.vehicle.label(),
        "stage": ro.stage,
        "stage_label": STAGE_LABELS[ro.stage],
        "crisma_status": ro.source_status,
        "flags": ro.flags,
        "insurer": ro.insurer or "Customer pay",
        "claim_number": ro.claim_number,
        "adjuster": ro.adjuster,
        "promised": ro.promised,
        "days_in_shop": ro.days_in_shop(today),
        "rental": ro.rental,
        "deductible": ro.deductible,
        "parts_outstanding": len(outstanding),
        "latest_parts_eta": max((p.eta for p in outstanding if p.eta), default=None),
        "last_note": ro.notes[-1].text if ro.notes else "",
    }


def payment_summary(ro: RepairOrder, shop: dict[str, Any], storage_days: int = 0) -> dict[str, Any]:
    rates = shop["rates"]
    labor = sum(l.labor_hours * float(rates.get(l.labor_type, rates["body"])) for l in ro.lines)
    paint = ro.paint_hours() * (float(rates["refinish"]) + float(rates["paint_materials_per_hour"]))
    parts = ro.parts_total()
    total = round(labor + paint + parts, 2)
    storage = round(storage_days * float(rates.get("storage_per_day", 0)), 2)
    extras = ro.customer_pay_total()
    if ro.insurer:
        customer_owes = round(ro.deductible + storage + extras, 2)
        if not ro.deductible and not extras:
            who = f"{ro.insurer} job with no deductible on file yet. Confirm the deductible with the customer or adjuster before quoting a balance."
            say = "I'll confirm your deductible amount with your adjuster and text it to you before pickup."
        else:
            parts = [f"${ro.deductible:,.2f} deductible"] + [f"${float(i.get('amount', 0)):,.2f} {i.get('description', 'customer-pay item')}" for i in ro.customer_pay_items] + ([f"${storage:,.2f} storage"] if storage else [])
            who = f"{ro.insurer} pays the approved repair less the deductible; the customer pays: {', '.join(parts)}."
            say = (f"Your deductible is ${ro.deductible:,.2f}" + (f" plus ${extras + storage:,.2f} in items the insurance company doesn't cover" if extras or storage else "")
                   + ". If the insurance company's final payment changes anything, we'll tell you before pickup, not at the counter. We take card, cash or check.")
    else:
        customer_owes = round(total + storage + extras, 2)
        who = "Customer pay: the customer pays the full invoice at pickup."
        say = f"Your estimated balance at pickup is ${customer_owes:,.2f}, based on the written estimate; any changes get your OK first. We take card, cash or check."
        if shop.get("shop", {}).get("state", "").upper() == "MD":
            who += " Maryland Commercial Law 14-1002: no more than 10% over the written estimate without the customer's consent."
    return {
        "ro": ro.ro_number,
        "repair_total_estimate": total,
        "deductible": ro.deductible,
        "customer_pay_items": ro.customer_pay_items,
        "storage": storage,
        "customer_owes_at_pickup": customer_owes,
        "who_pays": who,
        "say": say,
        "accepted": ["card", "cash", "check"],
    }


def update_queue(ros: list[RepairOrder], last_messages: dict[str, dict], today: date | None = None, max_silence_days: int = 2) -> list[dict[str, Any]]:
    """Who the front desk should contact today, with the reason, most urgent first."""
    today = today or date.today()
    out = []
    for ro in ros:
        if ro.stage == "delivered":
            continue
        last = last_messages.get(ro.ro_number)
        last_day = date.fromisoformat(last["created_at"][:10]) if last else None
        silent = (today - last_day).days if last_day else None
        reasons = []
        priority = 3
        if ro.stage == "ready":
            reasons.append("Ready for pickup: confirm pickup time and balance")
            priority = 1
        if ro.promised and ro.promised < today.isoformat() and ro.stage not in {"ready"}:
            reasons.append(f"Promised {ro.promised} has passed: send a delay text with a new date")
            priority = min(priority, 1)
        elif ro.promised == today.isoformat():
            reasons.append("Promised today: confirm it's on track or send a delay text")
            priority = min(priority, 1)
        if ro.stage_changed and (not last or last["created_at"] < ro.stage_changed):
            reasons.append(f"Stage changed to '{STAGE_LABELS[ro.stage]}' with no update sent")
            priority = min(priority, 2)
        if last is None:
            reasons.append("No message ever logged on this RO")
            priority = min(priority, 2)
        elif silent is not None and silent >= max_silence_days:
            reasons.append(f"No contact in {silent} days")
            priority = min(priority, 2)
        if ro.stage == "approval" and ro.days_in_stage(datetime.combine(today, datetime.max.time())) >= 1:
            reasons.append(f"Awaiting {ro.insurer or 'customer'} approval: chase it")
            priority = min(priority, 2)
        if "total_loss" in ro.flags:
            reasons.append("CRISMA shows TOTAL LOSS: use the total-loss script; storage clock and personal items")
            priority = min(priority, 2)
        if "backorder" in ro.flags:
            reasons.append("CRISMA shows a part on backorder: customer needs the new ETA")
            priority = min(priority, 2)
        used = ro.rental_days_used(today)
        if used is not None:
            cap = int(ro.rental_covered_days or 0)
            if cap and used >= cap:
                reasons.append(f"Rental day {used} of {cap}: coverage has run out; the customer is paying unless an extension was granted")
                priority = 1
            elif cap and used >= cap - 5:
                reasons.append(f"Rental day {used} of {cap}: ask {ro.insurer or 'the carrier'} for an extension before it runs out")
                priority = min(priority, 1)
            elif not cap and used >= 15:
                reasons.append(f"Rental day {used} with no covered-days cap on file: confirm the cap with the adjuster (most policies stop at 30)")
                priority = min(priority, 2)
        if ro.stage == "approval" and ro.supplements:
            last = ro.supplements[-1]
            if not last.get("approved") and last.get("submitted"):
                age = (today - date.fromisoformat(last["submitted"][:10])).days
                if age >= 3:
                    reasons.append(f"Supplement {last['supp_no']} submitted {age} days ago with no approval: escalate to the manager")
                    priority = 1
                elif age >= 2:
                    reasons.append(f"Supplement {last['supp_no']} submitted {age} days ago: call the adjuster today")
                    priority = min(priority, 2)
        if reasons:
            out.append({
                "priority": priority,
                "ro": ro.ro_number,
                "customer": ro.customer.name,
                "phone": ro.customer.phone,
                "vehicle": ro.vehicle.label(),
                "stage": STAGE_LABELS[ro.stage],
                "crisma_status": ro.source_status,
                "rental": ro.rental,
                "reasons": reasons,
                "suggested_text_stage": ro.stage,
            })
    out.sort(key=lambda x: (x["priority"], not x["rental"], x["ro"]))
    return out
