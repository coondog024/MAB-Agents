"""ADAS calibration triggers.

Maps repair operations to the driver-assistance systems that commonly need
calibration, initialization or aiming afterwards. This is a checklist generator,
not an authority: the OEM repair procedure for the exact VIN decides. Every
output says so, and the agents are told to never state a procedure as fact
without a looked-up OEM reference.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable

# (trigger regex, system, calibration type, why)
TRIGGERS: list[tuple[str, str, str, str]] = [
    (r"windshield|windscreen|w/s glass", "Forward-facing camera (lane keep, AEB, traffic sign)", "static and/or dynamic calibration", "Camera is mounted to the glass; glass R&R or replacement changes its aim."),
    (r"front bumper|front fascia|bumper cover.*front|absorber|grille|emblem|front radar|radar sensor", "Front radar (adaptive cruise, AEB)", "static aim / dynamic calibration", "Radar sits behind the bumper cover/emblem; R&I or impact changes alignment."),
    (r"front bumper|front fascia|rear bumper|rear fascia|park(ing)? sensor|ultrasonic", "Park assist ultrasonic sensors", "initialization / check", "Sensors are bumper-mounted; replacement bumper covers must have correct sensor holders and paint thickness."),
    (r"rear bumper|rear fascia|quarter panel|blind spot|bsm|rear corner radar", "Blind spot monitor / rear cross traffic radars", "static calibration", "Rear corner radars mount to the body behind the quarter/bumper; any R&I needs calibration."),
    (r"mirror|side camera|surround|360|around view", "Side cameras (surround view) / BSM indicators", "camera calibration (surround view target mat)", "Mirror-mounted cameras move with mirror R&I or replacement."),
    (r"liftgate|tailgate|deck ?lid|trunk lid|rear camera|backup camera", "Rear view camera", "camera calibration (static target)", "Camera mounts to liftgate/deck lid; replacement or alignment shifts the image."),
    (r"alignment|control arm|knuckle|strut|tie rod|subframe|cradle|steering|rack|wheel", "Steering angle sensor + forward camera + radar", "SAS reset + recalibration after alignment", "Thrust angle change moves every forward sensor's reference."),
    (r"headlamp|headlight", "Adaptive / matrix headlamps", "aim + leveling sensor initialization", "Replaced or R&I lamps need aiming; adaptive lamps may need initialization."),
    (r"battery|disconnect|module|bcm|ecm|srs|airbag", "Modules after power loss or replacement", "relearn / initialization (windows, sunroof, SAS, occupant sensor)", "Loss of power or module replacement clears learned values."),
    (r"seat|airbag|srs|occupant", "Occupant classification system", "zeroing / calibration", "Passenger seat weight sensor calibration after seat R&I or deployment."),
    (r"roof|headliner|rain sensor|sunroof", "Rain/light sensor, interior camera (driver monitoring)", "initialization", "Headliner/roof work disturbs roof-mounted sensors and cameras."),
    (r"lidar|night vision|thermal", "Lidar / night vision", "OEM-specific calibration", "Rare, high-cost sensors; check OEM procedure before quoting."),
]

STEPS = [
    "Pull the OEM repair procedure for this VIN (OEM1Stop / ALLDATA / OEM portal) and print the calibration section into the RO file.",
    "Document pre-repair scan with DTCs, build data and which ADAS options the VIN actually has.",
    "Complete all body, glass and alignment work first; calibrations are last.",
    "Verify fuel level, tire pressure, ride height, no cargo, level floor and target distances per OEM before a static calibration.",
    "Perform the calibration (in-house or sublet), save the printout/screenshot of the completed calibration to the RO.",
    "Post-repair scan with no stored DTCs, then road test to confirm dynamic systems operate.",
    "Give the customer the calibration report with the final invoice.",
]


@dataclass
class CalibrationItem:
    system: str
    calibration: str
    why: str
    triggered_by: list[str]


def calibration_checklist(operations: Iterable[str], *, airbags_deployed: bool = False) -> list[CalibrationItem]:
    ops = [o.strip() for o in operations if o and o.strip()]
    found: dict[str, CalibrationItem] = {}
    for op in ops:
        for rx, system, cal, why in TRIGGERS:
            if re.search(rx, op, re.I):
                item = found.setdefault(system, CalibrationItem(system=system, calibration=cal, why=why, triggered_by=[]))
                if op not in item.triggered_by:
                    item.triggered_by.append(op)
    if airbags_deployed:
        found.setdefault("Occupant classification system", CalibrationItem(
            system="Occupant classification system",
            calibration="zeroing / calibration",
            why="Airbag deployment; OEMs require OCS verification after deployment.",
            triggered_by=["airbag deployment"],
        ))
        found.setdefault("SRS modules and seat belt pretensioners", CalibrationItem(
            system="SRS modules and seat belt pretensioners",
            calibration="module replacement/reset + pretensioner replacement per OEM",
            why="Airbag deployment; many SRS modules are one-time-use after a crash event.",
            triggered_by=["airbag deployment"],
        ))
    return list(found.values())


def checklist_report(items: list[CalibrationItem], vehicle: str = "") -> dict:
    return {
        "vehicle": vehicle,
        "disclaimer": "Checklist only. The OEM repair procedure for this VIN is the authority; verify each item there before writing it on the estimate.",
        "calibrations": [asdict(i) for i in items],
        "process": STEPS,
    }
