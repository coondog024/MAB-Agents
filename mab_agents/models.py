"""Repair-order data model shared by every agent and tool."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

# Production stages in order. Keep names short: they appear in SMS drafts and the WIP board.
STAGES: list[str] = [
    "scheduled",
    "checked_in",
    "blueprint",        # teardown + damage analysis
    "approval",         # waiting on insurer / customer authorization
    "parts_ordered",
    "parts_received",
    "body",             # body / structural repair
    "paint_prep",
    "paint",
    "reassembly",
    "qc",               # detail + quality control + post-repair scan
    "ready",
    "delivered",
]

STAGE_LABELS: dict[str, str] = {
    "scheduled": "Scheduled",
    "checked_in": "Checked in",
    "blueprint": "Teardown & blueprint",
    "approval": "Awaiting approval",
    "parts_ordered": "Parts ordered",
    "parts_received": "Parts received",
    "body": "Body & structural repair",
    "paint_prep": "Paint prep",
    "paint": "In paint",
    "reassembly": "Reassembly",
    "qc": "Detail & quality control",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
}

# Stages that count as "in production" for touch-time math.
PRODUCTION_STAGES = {"blueprint", "body", "paint_prep", "paint", "reassembly", "qc"}

OPERATIONS = {"repair", "replace", "r&i", "r&r", "refinish", "blend", "sublet", "additional", "diagnostic"}
LABOR_TYPES = {"body", "refinish", "mechanical", "structural", "diagnostic", "glass", "sublet"}
PART_TYPES = {"oem", "aftermarket", "recycled", "reconditioned", "opt_oem", "none"}


@dataclass
class Vehicle:
    year: int
    make: str
    model: str
    vin: str = ""
    color: str = ""
    mileage: int | None = None
    paint_code: str = ""

    def label(self) -> str:
        return f"{self.year} {self.make} {self.model}".strip()


@dataclass
class Customer:
    name: str
    phone: str = ""
    email: str = ""
    preferred_contact: str = "sms"   # sms | email | call


@dataclass
class EstimateLine:
    line: int
    operation: str                   # repair | replace | r&i | r&r | refinish | blend | sublet | additional
    description: str
    labor_hours: float = 0.0
    labor_type: str = "body"         # body | refinish | mechanical | structural | diagnostic | glass
    paint_hours: float = 0.0
    part_type: str = "none"          # oem | aftermarket | recycled | reconditioned | opt_oem | none
    part_number: str = ""
    qty: int = 1
    part_price: float = 0.0
    note: str = ""

    def normalized(self) -> "EstimateLine":
        self.operation = self.operation.strip().lower().replace("&amp;", "&")
        self.labor_type = self.labor_type.strip().lower()
        self.part_type = self.part_type.strip().lower() or "none"
        return self


@dataclass
class Part:
    description: str
    part_number: str = ""
    part_type: str = "oem"
    qty: int = 1
    vendor: str = ""
    price: float = 0.0
    status: str = "needed"           # needed | ordered | backordered | received | returned
    eta: str | None = None           # ISO date
    line: int | None = None


@dataclass
class Note:
    at: str
    author: str
    text: str


@dataclass
class RepairOrder:
    ro_number: str
    customer: Customer
    vehicle: Vehicle
    insurer: str = ""                # "" means customer pay
    claim_number: str = ""
    adjuster: str = ""
    deductible: float = 0.0
    stage: str = "scheduled"
    drivable: bool = True
    rental: bool = False
    opened: str = ""                 # ISO date RO was written
    dropped_off: str | None = None
    promised: str | None = None
    delivered: str | None = None
    stage_changed: str | None = None # ISO datetime of last stage change
    original_estimate_total: float = 0.0
    lines: list[EstimateLine] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    airbags_deployed: bool = False
    adas_equipped: bool = True
    # Items the customer pays beyond the deductible: betterment, carrier-denied operations,
    # upgrades, prior damage they asked us to fix. [{description, amount, reason}]
    customer_pay_items: list[dict] = field(default_factory=list)

    # ---- derived helpers -------------------------------------------------
    def labor_hours(self) -> float:
        return round(sum(l.labor_hours for l in self.lines), 1)

    def paint_hours(self) -> float:
        return round(sum(l.paint_hours for l in self.lines), 1)

    def parts_total(self) -> float:
        return round(sum(l.part_price * l.qty for l in self.lines), 2)

    def customer_pay_total(self) -> float:
        return round(sum(float(i.get("amount", 0)) for i in self.customer_pay_items), 2)

    def days_in_shop(self, today: date | None = None) -> int:
        if not self.dropped_off:
            return 0
        start = date.fromisoformat(self.dropped_off)
        end = date.fromisoformat(self.delivered) if self.delivered else (today or date.today())
        return max((end - start).days, 0)

    def days_in_stage(self, now: datetime | None = None) -> float:
        if not self.stage_changed:
            return 0.0
        changed = datetime.fromisoformat(self.stage_changed)
        delta = (now or datetime.now()) - changed
        return max(round(delta.total_seconds() / 86400, 1), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RepairOrder":
        d = dict(d)
        d["customer"] = Customer(**d["customer"])
        d["vehicle"] = Vehicle(**d["vehicle"])
        d["lines"] = [EstimateLine(**l).normalized() for l in d.get("lines", [])]
        d["parts"] = [Part(**p) for p in d.get("parts", [])]
        d["notes"] = [Note(**n) for n in d.get("notes", [])]
        return cls(**d)
