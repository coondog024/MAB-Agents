"""Production scheduling math: WIP board, touch time, stalled ROs, promise dates."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..models import PRODUCTION_STAGES, STAGE_LABELS, STAGES, RepairOrder

# Rough calendar days a job spends in each production stage per 10 labor hours.
# Tune in shop.toml later; these are only used for promise-date estimates.
STAGE_DAYS_PER_10H: dict[str, float] = {
    "blueprint": 0.5,
    "approval": 1.0,
    "parts_ordered": 2.0,
    "parts_received": 0.0,
    "body": 1.2,
    "paint_prep": 0.6,
    "paint": 0.8,
    "reassembly": 0.8,
    "qc": 0.4,
}


def _end_of(day: date) -> datetime:
    """Planning math counts a whole calendar day, so measure to the end of it."""
    return datetime.combine(day, datetime.max.time())


def business_days_after(start: date, days: float) -> date:
    d = start
    remaining = int(round(days))
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            remaining -= 1
    return d


def wip_board(ros: list[RepairOrder], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    board: dict[str, list[dict]] = {s: [] for s in STAGES}
    for ro in ros:
        board[ro.stage].append({
            "ro": ro.ro_number,
            "vehicle": ro.vehicle.label(),
            "customer": ro.customer.name,
            "insurer": ro.insurer or "Customer pay",
            "labor_hours": ro.labor_hours() + ro.paint_hours(),
            "days_in_shop": ro.days_in_shop(today),
            "days_in_stage": ro.days_in_stage(_end_of(today)) if ro.stage_changed else None,
            "promised": ro.promised,
            "rental": ro.rental,
            "crisma_status": ro.source_status,
            "flags": ro.flags,
        })
    hours_in_production = sum(ro.labor_hours() + ro.paint_hours() for ro in ros if ro.stage in PRODUCTION_STAGES)
    return {
        "date": today.isoformat(),
        "open_ros": len(ros),
        "in_production": sum(1 for ro in ros if ro.stage in PRODUCTION_STAGES),
        "waiting_parts_or_approval": sum(1 for ro in ros if ro.stage in {"approval", "parts_ordered"}),
        "hours_in_production": round(hours_in_production, 1),
        "stages": {s: {"label": STAGE_LABELS[s], "ros": board[s]} for s in STAGES if board[s]},
    }


def stalled(ros: list[RepairOrder], max_days: float, today: date | None = None) -> list[dict]:
    now = _end_of(today or date.today())
    out = []
    for ro in ros:
        if ro.stage in {"scheduled", "delivered", "ready"}:
            continue
        d = ro.days_in_stage(now)
        if d >= max_days:
            out.append({
                "ro": ro.ro_number,
                "vehicle": ro.vehicle.label(),
                "stage": STAGE_LABELS[ro.stage],
                "days_in_stage": d,
                "rental": ro.rental,
                "insurer": ro.insurer or "Customer pay",
                "why_it_matters": "Rental days burning" if ro.rental else "Cycle time and customer trust",
            })
    out.sort(key=lambda x: -x["days_in_stage"])
    return out


def touch_time(ro: RepairOrder, today: date | None = None) -> dict[str, Any]:
    days = ro.days_in_shop(today)
    hours = ro.labor_hours() + ro.paint_hours()
    return {
        "ro": ro.ro_number,
        "labor_hours": round(hours, 1),
        "days_in_shop": days,
        "touch_time_hours_per_day": round(hours / days, 2) if days else None,
    }


def promise_date(ro: RepairOrder, *, parts_eta: str | None = None, start: date | None = None) -> dict[str, Any]:
    """Estimate a delivery date from remaining stages, labor hours and parts ETA."""
    start = start or date.today()
    hours = ro.labor_hours() + ro.paint_hours()
    idx = STAGES.index(ro.stage)
    remaining = [s for s in STAGES[idx:] if s in STAGE_DAYS_PER_10H]
    days = sum(STAGE_DAYS_PER_10H[s] * max(hours / 10.0, 0.5) for s in remaining)
    if parts_eta:
        eta = date.fromisoformat(parts_eta)
        if eta > start:
            # Body work can't finish before parts land: push by the gap, minus overlap already counted.
            days = max(days, (eta - start).days + STAGE_DAYS_PER_10H["body"] * max(hours / 10.0, 0.5) + 2)
    target = business_days_after(start, days)
    return {
        "ro": ro.ro_number,
        "from_stage": ro.stage,
        "labor_hours": round(hours, 1),
        "estimated_business_days": round(days, 1),
        "estimated_delivery": target.isoformat(),
        "parts_eta_considered": parts_eta,
        "note": "Estimate for planning; give the customer a date with a day of buffer and update them if it moves.",
    }


def technician_load(ros: list[RepairOrder], techs: dict[str, float], today: date | None = None) -> dict[str, Any]:
    """techs = {name: available hours per day}. Returns days of backlog per tech given hours in production."""
    hours = sum(ro.labor_hours() for ro in ros if ro.stage in {"body", "paint_prep", "reassembly", "blueprint"})
    paint = sum(ro.paint_hours() for ro in ros if ro.stage in {"paint_prep", "paint"})
    body_capacity = sum(h for n, h in techs.items() if "paint" not in n.lower())
    paint_capacity = sum(h for n, h in techs.items() if "paint" in n.lower())
    return {
        "body_hours_open": round(hours, 1),
        "paint_hours_open": round(paint, 1),
        "body_capacity_per_day": body_capacity,
        "paint_capacity_per_day": paint_capacity,
        "body_backlog_days": round(hours / body_capacity, 1) if body_capacity else None,
        "paint_backlog_days": round(paint / paint_capacity, 1) if paint_capacity else None,
    }
