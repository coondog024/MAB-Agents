"""Estimate parsing and audit: catch not-included operations before the supplement does.

The audit encodes the operations most often left off a first estimate. Each rule
is conservative and phrased as "verify", because the estimating system's
P-pages / Database Enhancement Gateway inquiries and the OEM repair procedures
are the authority, not this file.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from ..models import EstimateLine

# Panels that share a paint edge. Refinishing one usually means blending its neighbours.
ADJACENT: dict[str, list[str]] = {
    "hood": ["left fender", "right fender"],
    "left fender": ["hood", "left front door"],
    "right fender": ["hood", "right front door"],
    "left front door": ["left fender", "left rear door"],
    "right front door": ["right fender", "right rear door"],
    "left rear door": ["left front door", "left quarter panel"],
    "right rear door": ["right front door", "right quarter panel"],
    "left quarter panel": ["left rear door"],
    "right quarter panel": ["right rear door"],
    "deck lid": ["left quarter panel", "right quarter panel"],
    "liftgate": ["left quarter panel", "right quarter panel"],
    "roof": [],
}

PANEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(rx, re.I))
    for name, rx in [
        ("hood", r"\bhood\b"),
        ("left fender", r"\b(l|lt|left|driver)\b.*\bfender\b|\bfender\b.*\b(l|lt|left|driver)\b"),
        ("right fender", r"\b(r|rt|right|pass(enger)?)\b.*\bfender\b|\bfender\b.*\b(r|rt|right|pass(enger)?)\b"),
        ("left front door", r"\b(l|lt|left|driver)\b.*\bfront door\b|\bfront door\b.*\b(l|lt|left|driver)\b"),
        ("right front door", r"\b(r|rt|right|pass(enger)?)\b.*\bfront door\b|\bfront door\b.*\b(r|rt|right|pass(enger)?)\b"),
        ("left rear door", r"\b(l|lt|left|driver)\b.*\brear door\b|\brear door\b.*\b(l|lt|left|driver)\b"),
        ("right rear door", r"\b(r|rt|right|pass(enger)?)\b.*\brear door\b|\brear door\b.*\b(r|rt|right|pass(enger)?)\b"),
        ("left quarter panel", r"\b(l|lt|left|driver)\b.*\bquarter\b|\bquarter\b.*\b(l|lt|left|driver)\b"),
        ("right quarter panel", r"\b(r|rt|right|pass(enger)?)\b.*\bquarter\b|\bquarter\b.*\b(r|rt|right|pass(enger)?)\b"),
        ("deck lid", r"\b(deck ?lid|trunk lid)\b"),
        ("liftgate", r"\b(lift ?gate|tail ?gate|hatch)\b"),
        ("roof", r"\broof\b"),
    ]
]


def panel_of(description: str) -> str | None:
    for name, rx in PANEL_PATTERNS:
        if rx.search(description):
            return name
    return None


def _text(l: EstimateLine) -> str:
    return f"{l.operation} {l.description} {l.note}".lower()


def _match(text: str, needle: str) -> bool:
    """Whole-word match so 'rim' does not hit 'prime' and 'aim' does not hit 'claim'."""
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text) is not None


def _has(lines: Iterable[EstimateLine], *needles: str) -> bool:
    return any(all(_match(_text(l), n) for n in needles) for l in lines)


def _any(lines: Iterable[EstimateLine], *needles: str) -> bool:
    return any(any(_match(_text(l), n) for n in needles) for l in lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
CSV_COLUMNS = ["line", "operation", "description", "labor_hours", "labor_type", "paint_hours", "part_type", "part_number", "qty", "part_price", "note"]


def parse_estimate_csv(text: str) -> list[EstimateLine]:
    """Parse the CSV export format in data/sample_estimate.csv (header required)."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "description" not in reader.fieldnames:
        raise ValueError(f"Estimate CSV needs a header with at least: {', '.join(CSV_COLUMNS)}")
    out: list[EstimateLine] = []
    for i, row in enumerate(reader, start=1):
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not row.get("description"):
            continue
        out.append(
            EstimateLine(
                line=int(row.get("line") or i),
                operation=row.get("operation") or "repair",
                description=row["description"],
                labor_hours=float(row.get("labor_hours") or 0),
                labor_type=row.get("labor_type") or "body",
                paint_hours=float(row.get("paint_hours") or 0),
                part_type=row.get("part_type") or "none",
                part_number=row.get("part_number", ""),
                qty=int(row.get("qty") or 1),
                part_price=float(row.get("part_price") or 0),
                note=row.get("note", ""),
            ).normalized()
        )
    return out


def parse_estimate_json(text: str) -> list[EstimateLine]:
    return [EstimateLine(**d).normalized() for d in json.loads(text)]


def parse_estimate(text: str) -> list[EstimateLine]:
    stripped = text.lstrip()
    return parse_estimate_json(text) if stripped.startswith("[") else parse_estimate_csv(text)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
def totals(lines: list[EstimateLine], rates: dict[str, float]) -> dict[str, Any]:
    labor: dict[str, float] = {}
    for l in lines:
        labor[l.labor_type] = round(labor.get(l.labor_type, 0.0) + l.labor_hours, 1)
    paint_hours = round(sum(l.paint_hours for l in lines), 1)
    labor_dollars = 0.0
    for lt, hrs in labor.items():
        labor_dollars += hrs * float(rates.get(lt, rates.get("body", 0.0)))
    refinish_rate = float(rates.get("refinish", rates.get("body", 0.0)))
    paint_labor = paint_hours * refinish_rate
    paint_materials = paint_hours * float(rates.get("paint_materials_per_hour", 0.0))
    all_hours = sum(labor.values()) + paint_hours
    shop_materials = all_hours * float(rates.get("shop_materials_per_hour", 0.0))
    parts = sum(l.part_price * l.qty for l in lines)
    sublet = sum(l.part_price * l.qty for l in lines if l.operation == "sublet")
    total = labor_dollars + paint_labor + paint_materials + shop_materials + parts
    return {
        "labor_hours_by_type": labor,
        "refinish_hours": paint_hours,
        "labor_dollars": round(labor_dollars, 2),
        "refinish_labor_dollars": round(paint_labor, 2),
        "paint_materials": round(paint_materials, 2),
        "shop_materials": round(shop_materials, 2),
        "parts": round(parts, 2),
        "sublet_included_in_parts": round(sublet, 2),
        "total": round(total, 2),
        "line_count": len(lines),
    }


# ---------------------------------------------------------------------------
# Audit rules
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    rule: str
    severity: str          # high | medium | low
    message: str
    suggested_line: str    # how it would read on the estimate
    trigger_lines: list[int]


def _lines_matching(lines: list[EstimateLine], *needles: str) -> list[int]:
    return [l.line for l in lines if any(_match(_text(l), n) for n in needles)]


def audit_estimate(
    lines: list[EstimateLine],
    *,
    airbags_deployed: bool = False,
    adas_equipped: bool = True,
    model_year: int | None = None,
) -> list[Finding]:
    f: list[Finding] = []
    refinished = [l for l in lines if l.operation in {"refinish", "replace", "repair"} and l.paint_hours > 0]
    refinished_panels = {panel_of(l.description) for l in refinished} - {None}
    blended_panels = {panel_of(l.description) for l in lines if l.operation == "blend"} - {None}
    replaced = [l for l in lines if l.operation in {"replace", "r&r"}]
    repaired = [l for l in lines if l.operation == "repair"]
    any_refinish = bool(refinished) or any(l.paint_hours > 0 for l in lines)

    # 1. Blend adjacent panels
    for panel in sorted(refinished_panels):
        for neighbour in ADJACENT.get(panel, []):
            if neighbour not in refinished_panels and neighbour not in blended_panels:
                f.append(Finding(
                    rule="blend_adjacent",
                    severity="high",
                    message=f"{panel.capitalize()} is refinished but the adjacent {neighbour} is neither refinished nor blended. Metallic/pearl colours almost always need a blend for match.",
                    suggested_line=f"Blend {neighbour} (refinish, per P-page blend formula)",
                    trigger_lines=_lines_matching(lines, panel.split()[-1]),
                ))

    # 2. Refinish not-included items
    if any_refinish:
        checks = [
            ("cover car", ("cover car", "mask for overspray", "overspray"), "Cover car / mask for overspray (not included in refinish time)", "medium"),
            ("color tint", ("tint", "color match", "colour match", "spray-out", "spray out"), "Color tint / spray-out test panel (not included)", "medium"),
            ("sand and buff", ("buff", "denib", "color sand", "nib", "nibs"), "Color sand & buff (not included; per panel)", "low"),
            ("hazardous waste", ("hazardous", "haz waste", "waste disposal"), "Hazardous waste disposal", "low"),
            ("paint materials", ("paint material", "paint materials", "materials", "refinish material", "refinish materials"), "Paint & materials line (usually auto-calculated; confirm it printed)", "low"),
        ]
        for key, needles, suggestion, sev in checks:
            if not _any(lines, *needles):
                f.append(Finding(rule=key.replace(" ", "_"), severity=sev, message=f"No '{key}' line found although refinish operations are present.", suggested_line=suggestion, trigger_lines=[l.line for l in refinished][:3]))

    # 3. Feather, prime & block on repaired panels
    if repaired and not _any(lines, "feather", "fpb"):
        f.append(Finding(
            rule="feather_prime_block",
            severity="high",
            message="Repaired panels present with no feather, prime & block time. Repair time in the estimating databases stops at 150-grit; FPB is a separate operation.",
            suggested_line="Feather, prime & block - per repaired panel (body + refinish time)",
            trigger_lines=[l.line for l in repaired],
        ))

    # 4. Flexible bumper covers
    bumper_cover_lines = _lines_matching(lines, "bumper cover", "bumper fascia", "fascia")
    if bumper_cover_lines:
        if not _any(lines, "flex additive", "flex agent"):
            f.append(Finding(rule="flex_additive", severity="medium", message="Bumper cover refinished/replaced with no flex additive line.", suggested_line="Flex additive (per bumper cover)", trigger_lines=bumper_cover_lines))
        if any(l.operation in {"replace", "r&r"} and "bumper" in l.description.lower() for l in lines) and not _any(lines, "raw plastic", "prep raw", "adhesion promoter"):
            f.append(Finding(rule="raw_plastic_prep", severity="medium", message="New bumper cover with no raw/unprimed plastic prep or adhesion promoter line.", suggested_line="Prep raw plastic bumper cover + adhesion promoter", trigger_lines=bumper_cover_lines))
        if not _any(lines, "clip", "clips", "fastener", "fasteners", "retainer", "retainers", "push pin", "push pins"):
            f.append(Finding(rule="one_time_fasteners", severity="low", message="Bumper cover work with no one-time-use clips/fasteners line.", suggested_line="One-time-use clips/fasteners (bumper cover)", trigger_lines=bumper_cover_lines))

    # 5. Welded panel replacement
    welded = _lines_matching(lines, "quarter", "rocker", "uniside", "aperture", "rail", "apron", "floor", "roof", "pillar", "wheelhouse", "radiator support", "rad support", "core support")
    welded_replaced = [l.line for l in replaced if l.line in welded]
    if welded_replaced:
        for key, needles, suggestion in [
            ("corrosion_protection", ("corrosion", "cavity wax", "epoxy primer"), "Corrosion protection (weld-through primer, epoxy, cavity wax) per OEM"),
            ("seam_sealer", ("seam seal",), "Seam sealer - replicate factory seams"),
            ("set_up_and_measure", ("set up", "setup", "measure", "frame rack", "bench"), "Set up & measure on frame rack (structural)"),
            ("anti_flutter_foam", ("foam", "anti-flutter", "anti flutter", "nvh"), "Replace structural/NVH foam & anti-flutter where OEM shows it"),
        ]:
            if not _any(lines, *needles):
                f.append(Finding(rule=key, severity="high", message=f"Welded panel replacement with no {key.replace('_', ' ')} line.", suggested_line=suggestion, trigger_lines=welded_replaced))

    # 6. Pre / post repair scans
    if adas_equipped or (model_year and model_year >= 2010) or airbags_deployed:
        if not _any(lines, "pre-repair scan", "pre repair scan", "pre-scan", "prescan", "pre scan", "diagnostic scan"):
            f.append(Finding(rule="pre_repair_scan", severity="high", message="No pre-repair diagnostic scan line. Most OEM position statements require one on any collision repair.", suggested_line="Pre-repair diagnostic scan (diagnostic labor or sublet)", trigger_lines=[]))
        if not _any(lines, "post-repair scan", "post repair scan", "post-scan", "postscan", "post scan"):
            f.append(Finding(rule="post_repair_scan", severity="high", message="No post-repair diagnostic scan line.", suggested_line="Post-repair diagnostic scan + clear codes", trigger_lines=[]))

    # 7. Airbags
    if airbags_deployed:
        if not _any(lines, "seat belt", "seatbelt", "pretensioner"):
            f.append(Finding(rule="seat_belts_after_deployment", severity="high", message="Airbags deployed but no seat belt / pretensioner inspection or replacement lines. Most OEMs require replacing pretensioners that fired and inspecting all belts.", suggested_line="Inspect/replace seat belt pretensioners per OEM after deployment", trigger_lines=[]))
        if not _any(lines, "occupant", "ocs", "weight sensor"):
            f.append(Finding(rule="occupant_classification", severity="medium", message="Airbags deployed; verify whether the occupant classification system (passenger seat) needs recalibration or replacement.", suggested_line="Occupant classification system calibration (verify OEM)", trigger_lines=[]))

    # 8. Suspension / wheel / steering → alignment
    if _any(lines, "control arm", "knuckle", "strut", "tie rod", "wheel", "rim", "subframe", "cradle", "steering") and not _any(lines, "alignment", "align", "aligned"):
        f.append(Finding(rule="alignment", severity="high", message="Suspension, wheel or steering parts on the estimate with no 4-wheel alignment line.", suggested_line="4-wheel alignment (mechanical) + test drive", trigger_lines=_lines_matching(lines, "control arm", "knuckle", "strut", "tie rod", "wheel", "subframe", "steering")))

    # 9. Cooling / A/C
    if _any(lines, "condenser", "compressor", "a/c line", "ac line", "receiver drier", "evaporator") and not _any(lines, "evacuate", "evac", "recharge", "refrigerant"):
        f.append(Finding(rule="ac_evacuate_recharge", severity="high", message="A/C component replaced with no evacuate & recharge line (plus refrigerant and oil).", suggested_line="Evacuate & recharge A/C system + refrigerant", trigger_lines=_lines_matching(lines, "condenser", "compressor", "a/c", "drier")))
    if _any(lines, "radiator", "water pump", "coolant hose") and not _any(lines, "coolant", "antifreeze"):
        f.append(Finding(rule="coolant", severity="medium", message="Cooling system part replaced with no coolant line.", suggested_line="Coolant / antifreeze refill", trigger_lines=_lines_matching(lines, "radiator", "water pump")))

    # 10. Headlamps
    if _any(lines, "headlamp", "headlight") and not _any(lines, "aim", "aiming", "aimed"):
        f.append(Finding(rule="headlamp_aim", severity="medium", message="Headlamp R&I or replaced with no aiming line.", suggested_line="Aim headlamps", trigger_lines=_lines_matching(lines, "headlamp", "headlight")))

    # 11. Doors
    if any(l.operation in {"replace", "r&r"} and "door" in l.description.lower() and "handle" not in l.description.lower() for l in lines):
        if not _any(lines, "transfer", "trim panel", "door trim", "r&i door", "door glass", "regulator"):
            f.append(Finding(rule="door_transfer_hardware", severity="medium", message="Door shell replaced; confirm R&I of trim panel, glass, regulator, latch and hardware transfer are on the estimate (not included in shell replacement).", suggested_line="R&I door trim panel + transfer hardware/glass/regulator", trigger_lines=_lines_matching(lines, "door")))

    # 12. Battery / power disconnect
    if _any(lines, "battery", "disconnect") and not _any(lines, "initialize", "initialization", "initialise", "relearn", "relearns", "reset", "post-repair scan", "post repair scan"):
        f.append(Finding(rule="post_disconnect_relearn", severity="medium", message="Battery disconnect present; windows, sunroof, steering angle and idle relearns may be required.", suggested_line="Relearn/initialize systems after battery disconnect (verify OEM list)", trigger_lines=_lines_matching(lines, "battery", "disconnect")))

    # 13. Test drive & final detail
    if lines and not _any(lines, "test drive", "road test"):
        f.append(Finding(rule="test_drive", severity="low", message="No road test line. Needed after any suspension, steering, ADAS or drivability-related repair.", suggested_line="Test drive / road test", trigger_lines=[]))
    if lines and not _any(lines, "detail", "wash", "clean up", "cleanup", "clean and detail", "clean & detail"):
        f.append(Finding(rule="detail", severity="low", message="No delivery wash/detail line.", suggested_line="Clean & detail vehicle for delivery", trigger_lines=[]))

    order = {"high": 0, "medium": 1, "low": 2}
    f.sort(key=lambda x: order[x.severity])
    return f


def audit_report(findings: list[Finding]) -> dict[str, Any]:
    return {
        "count": len(findings),
        "high": sum(1 for x in findings if x.severity == "high"),
        "medium": sum(1 for x in findings if x.severity == "medium"),
        "low": sum(1 for x in findings if x.severity == "low"),
        "findings": [asdict(x) for x in findings],
    }
