"""Shop KPIs from the ROs on file. Honest, simple math; nothing is modelled."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..models import RepairOrder
from . import schedule_tools


def kpis(ros: list[RepairOrder], shop: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    targets = shop.get("targets", {})
    rates = shop["rates"]
    open_ros = [r for r in ros if r.stage != "delivered"]
    delivered = [r for r in ros if r.stage == "delivered" and r.dropped_off and r.delivered]

    cycle_days = [r.days_in_shop(today) for r in delivered]
    touch = [schedule_tools.touch_time(r, today)["touch_time_hours_per_day"] for r in delivered]
    touch = [t for t in touch if t]
    sales = []
    for r in ros:
        labor = sum(l.labor_hours * float(rates.get(l.labor_type, rates["body"])) for l in r.lines)
        paint = r.paint_hours() * float(rates["refinish"]) + r.paint_hours() * float(rates["paint_materials_per_hour"])
        sales.append(labor + paint + r.parts_total())
    supp = [
        (sum(l.part_price * l.qty for l in r.lines) + (r.labor_hours() + r.paint_hours()) * float(rates["body"])) / r.original_estimate_total - 1
        for r in ros if r.original_estimate_total
    ]

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    def has(ro: RepairOrder, *needles: str) -> bool:
        return any(any(n in f"{l.operation} {l.description}".lower() for n in needles) for l in ro.lines)

    with_lines = [r for r in ros if r.lines]
    rental_days = [d for d in (r.rental_days_used(today) for r in ros) if d is not None]

    return {
        # The four things carriers grade DRP shops on, plus what CCC reports industry-wide.
        "scan_rate": round(sum(1 for r in with_lines if has(r, "scan")) / len(with_lines), 2) if with_lines else None,
        "calibration_rate": round(sum(1 for r in with_lines if has(r, "calibrat", "aim")) / len(with_lines), 2) if with_lines else None,
        "supplement_rate": round(sum(1 for r in ros if r.supplements or "has_supplement" in r.flags or (r.supp_no and r.supp_no not in {"00", "0"})) / len(ros), 2) if ros else None,
        "average_rental_days": avg(rental_days),
        "as_of": today.isoformat(),
        "open_ros": len(open_ros),
        "delivered_in_data": len(delivered),
        "average_cycle_days": avg(cycle_days),
        "target_cycle_days": targets.get("cycle_time_days"),
        "average_touch_time": avg(touch),
        "target_touch_time": targets.get("touch_time_hours_per_day"),
        "average_ro_dollars": avg(sales),
        "average_supplement_ratio": avg(supp),
        "target_supplement_ratio_max": targets.get("supplement_ratio_max"),
        "labor_hours_open": round(sum(r.labor_hours() + r.paint_hours() for r in open_ros), 1),
        "rentals_open": sum(1 for r in open_ros if r.rental),
        "stalled": schedule_tools.stalled(open_ros, float(targets.get("stalled_after_days", 2)), today),
    }


def daily_brief(ros: list[RepairOrder], shop: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    k = kpis(ros, shop, today)
    board = schedule_tools.wip_board([r for r in ros if r.stage != "delivered"], today)
    due_today = [r.ro_number for r in ros if r.promised == today.isoformat() and r.stage not in {"ready", "delivered"}]
    overdue = [r.ro_number for r in ros if r.promised and r.promised < today.isoformat() and r.stage not in {"ready", "delivered"}]
    waiting = [r.ro_number for r in ros if r.stage == "approval"]
    return {
        "date": today.isoformat(),
        "headline": f"{board['open_ros']} open ROs, {board['in_production']} in production, {len(overdue)} overdue, {len(k['stalled'])} stalled.",
        "due_today": due_today,
        "overdue": overdue,
        "waiting_on_approval": waiting,
        "stalled": k["stalled"],
        "rentals_open": k["rentals_open"],
        "hours_in_production": board["hours_in_production"],
        "kpis": {kk: vv for kk, vv in k.items() if kk != "stalled"},
    }
