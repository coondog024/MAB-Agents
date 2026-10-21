"""CCC ONE EMS import: turn a CIECA EMS job (DBF file set or .EST XML) into a RepairOrder.

CCC ONE's EMS export writes one job per estimate into the export folder, either
as a set of dBase files sharing the UNQFILE_ID stem (<id>.env .veh .ad1 .ad2 .lin
.ttl .stl .pfl ...) or as one <id>.EST XML file with the same sections. CRISMA
imports the same files; MAB reads them read-only and never changes them.

Codes follow the EMS 2.x conventions CCC uses (also in est-database/ems_utils.py):
  PART_TYPE  PAN new OEM, PAA aftermarket, PAL recycled/LKQ, PAM remanufactured,
             PAR recore, PAC rechrome, PAE existing, PAG glass, PAS sublet, PAP partial
  MOD_LBR_TY LAB body, LAR refinish, LAM mechanical, LAS structural, LAF frame,
             LAD diagnostic, LAE electrical, LAG glass, LA1-4 user defined
  LBR_OP     OP1 refinish+replace, OP2 R&I, OP3 add labor, OP4 align, OP5 overhaul,
             OP6 refinish, OP7 inspect, OP8 check/adjust, OP9 repair, OP10 partial repair,
             OP11 replace, OP12 partial R&R, OP13 add cost, OP14 add operation,
             OP15 blend, OP16 sublet
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Customer, EstimateLine, Note, RepairOrder, Vehicle
from .store import Store

DBF_SECTIONS = ("env", "ad1", "ad2", "veh", "lin", "ttl", "stl", "pfl", "pfp", "pfh", "pfm", "ven")

PART_TYPE_MAP = {"PAN": "oem", "PAA": "aftermarket", "PAL": "recycled", "PAM": "reconditioned", "PAR": "reconditioned", "PAC": "reconditioned", "PAE": "none", "PAG": "oem", "PAS": "none", "PAP": "none", "PAO": "none", "": "none"}
LABOR_TYPE_MAP = {"LAB": "body", "LAR": "refinish", "LAM": "mechanical", "LAS": "structural", "LAF": "structural", "LAD": "diagnostic", "LAE": "mechanical", "LAG": "glass", "LA1": "body", "LA2": "body", "LA3": "body", "LA4": "body", "": "body"}
LABOR_OP_MAP = {"OP1": "replace", "OP2": "r&i", "OP3": "additional", "OP4": "additional", "OP5": "repair", "OP6": "refinish", "OP7": "additional", "OP8": "additional", "OP9": "repair", "OP10": "repair", "OP11": "replace", "OP12": "replace", "OP13": "additional", "OP14": "additional", "OP15": "blend", "OP16": "sublet", "OP0": "additional", "": "additional"}
LABOR_OP_LABEL = {"OP1": "Refinish/Replace", "OP2": "R&I", "OP3": "Add'l labor", "OP4": "Align", "OP5": "Overhaul", "OP6": "Refinish", "OP7": "Inspect", "OP8": "Check/Adjust", "OP9": "Repair", "OP10": "Partial repair", "OP11": "Replace", "OP12": "Partial R&R", "OP13": "Add'l cost", "OP14": "Add'l operation", "OP15": "Blend", "OP16": "Sublet"}


@dataclass
class EmsJob:
    """One estimate as exported: sections keyed by EMS table name, fields upper-cased."""

    source: str
    kind: str                                   # "dbf" or "xml"
    env: dict[str, Any] = field(default_factory=dict)
    ad1: dict[str, Any] = field(default_factory=dict)
    ad2: dict[str, Any] = field(default_factory=dict)
    veh: dict[str, Any] = field(default_factory=dict)
    ttl: dict[str, Any] = field(default_factory=dict)
    lin: list[dict[str, Any]] = field(default_factory=list)
    stl: list[dict[str, Any]] = field(default_factory=list)
    pfl: list[dict[str, Any]] = field(default_factory=list)

    @property
    def estimate_id(self) -> str:
        return str(self.env.get("UNQFILE_ID") or Path(self.source).stem).strip()

    @property
    def supp_no(self) -> str:
        raw = str(self.env.get("SUPP_NO") or "0").strip()
        return raw.zfill(2) if raw.isdigit() else raw


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _clean(v: Any) -> Any:
    if isinstance(v, bytes):
        v = v.decode("latin-1", errors="replace")
    if isinstance(v, str):
        return v.strip()
    return v


def read_dbf_rows(path: Path) -> list[dict[str, Any]]:
    from dbfread import DBF

    table = DBF(str(path), ignore_missing_memofile=True, char_decode_errors="replace")
    return [{k.upper(): _clean(v) for k, v in rec.items()} for rec in table]


def read_dbf_job(env_path: Path) -> EmsJob:
    """Read the DBF file set that shares env_path's stem (case-insensitive extensions)."""
    stem = env_path.with_suffix("")
    folder = env_path.parent
    by_ext: dict[str, Path] = {}
    for p in folder.iterdir():
        if p.is_file() and p.with_suffix("").name.lower() == stem.name.lower():
            by_ext[p.suffix.lower().lstrip(".")] = p
    job = EmsJob(source=str(env_path), kind="dbf")
    for sec in DBF_SECTIONS:
        if sec not in by_ext:
            continue
        rows = read_dbf_rows(by_ext[sec])
        if sec in {"lin", "stl", "pfl", "pfp"}:
            setattr(job, sec, rows) if hasattr(job, sec) else None
        elif rows:
            if hasattr(job, sec):
                setattr(job, sec, rows[0])
    return job


def read_xml_job(est_path: Path) -> EmsJob:
    root = ET.parse(est_path).getroot()
    job = EmsJob(source=str(est_path), kind="xml")

    def section(tag: str) -> dict[str, Any]:
        el = root.find(tag)
        return {c.tag.upper(): _clean(c.text or "") for c in el} if el is not None else {}

    job.env, job.ad1, job.ad2, job.veh, job.ttl = section("ENV"), section("AD1"), section("AD2"), section("VEH"), section("TTL")
    job.lin = [{c.tag.upper(): _clean(c.text or "") for c in el} for el in root.findall("LIN")]
    job.stl = [{c.tag.upper(): _clean(c.text or "") for c in el} for el in root.findall("STL")]
    job.pfl = [{c.tag.upper(): _clean(c.text or "") for c in el} for el in root.findall("PFL")]
    return job


def read_job(path: str | Path) -> EmsJob:
    p = Path(path)
    if p.suffix.lower() == ".est":
        return read_xml_job(p)
    if p.suffix.lower() == ".env":
        return read_dbf_job(p)
    if p.is_dir():
        jobs = find_jobs(p)
        if len(jobs) != 1:
            raise ValueError(f"{p} holds {len(jobs)} jobs; pass one .env/.EST or use import_path")
        return read_job(jobs[0])
    raise ValueError(f"Not an EMS job: {p} (expected <id>.env or <id>.EST)")


def find_jobs(folder: str | Path) -> list[Path]:
    """Every job in a folder: .env files (DBF sets) and .EST files, oldest first."""
    f = Path(folder)
    out = [p for p in f.rglob("*") if p.is_file() and p.suffix.lower() in {".env", ".est"}]
    return sorted(out, key=lambda p: (p.stat().st_mtime, p.name))


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------
def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return 0.0


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() in {"T", "TRUE", "Y", "YES", "1"}


def _date(v: Any) -> str | None:
    """EMS dates arrive as date objects (DBF) or strings like 2/27/2023 12:00:00 AM (XML)."""
    if v in (None, "", "1/1/0001 12:00:00 AM"):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    text = str(v).strip()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y", "%Y-%m-%d", "%Y%m%d", "%m/%d/%y"):
        try:
            d = datetime.strptime(text, fmt).date()
            return None if d.year < 1900 else d.isoformat()
        except ValueError:
            continue
    return None


def _phone(v: Any) -> str:
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}" if len(digits) == 10 else str(v or "").strip()


def _year(v: Any) -> int:
    text = re.sub(r"\D", "", str(v or ""))
    if not text:
        return 0
    n = int(text)
    if n >= 1900:
        return n
    return 2000 + n if n < 50 else 1900 + n


def _name(first: Any, last: Any, company: Any = "") -> str:
    name = " ".join(x for x in (str(first or "").strip(), str(last or "").strip()) if x)
    return name or str(company or "").strip()


# ---------------------------------------------------------------------------
# Mapping to the RepairOrder model
# ---------------------------------------------------------------------------
def lines_from_job(job: EmsJob) -> list[EstimateLine]:
    out: list[EstimateLine] = []
    for r in job.lin:
        desc = str(r.get("LINE_DESC") or "").strip()
        if not desc:
            continue
        op_code = str(r.get("LBR_OP") or "").strip().upper()
        ltype = LABOR_TYPE_MAP.get(str(r.get("MOD_LBR_TY") or "").strip().upper(), "body")
        hrs = _num(r.get("MOD_LB_HRS")) or _num(r.get("DB_HRS"))
        price = _num(r.get("ACT_PRICE")) or _num(r.get("DB_PRICE"))
        ptype_code = str(r.get("PART_TYPE") or "").strip().upper()
        part_type = PART_TYPE_MAP.get(ptype_code, "none")
        operation = LABOR_OP_MAP.get(op_code, "additional")
        if operation == "additional" and part_type != "none" and price > 0:
            operation = "replace"
        if _bool(r.get("MISC_SUBLT")) or ptype_code == "PAS":
            operation = "sublet"
            price = price or _num(r.get("MISC_AMT"))
        note_bits = []
        if r.get("ALT_PARTM"):
            note_bits.append(str(r["ALT_PARTM"]))
        if op_code in LABOR_OP_LABEL and LABOR_OP_LABEL[op_code].lower() not in operation:
            note_bits.append(LABOR_OP_LABEL[op_code])
        if _num(r.get("BETT_AMT")) > 0:
            note_bits.append(f"betterment {_num(r.get('BETT_PCTG')):.0f}% = ${_num(r.get('BETT_AMT')):.2f}")
        if str(r.get("WHO_PAYS") or "").strip().upper() == "C":
            note_bits.append("customer pays")
        if _bool(r.get("CERT_PART")):
            note_bits.append("certified part")
        part_no = str(r.get("OEM_PARTNO") or r.get("ALT_PARTNO") or "").strip()
        out.append(EstimateLine(
            line=int(_num(r.get("LINE_NO")) or len(out) + 1),
            operation=operation,
            description=desc,
            labor_hours=0.0 if ltype == "refinish" else hrs,
            labor_type="refinish" if ltype == "refinish" else ltype,
            paint_hours=hrs if ltype == "refinish" else 0.0,
            part_type=part_type,
            part_number=part_no,
            qty=max(int(_num(r.get("PART_QTY")) or 1), 1),
            part_price=price,
            note="; ".join(note_bits),
        ).normalized())
    return out


def customer_pay_items_from_job(job: EmsJob) -> list[dict[str, Any]]:
    items = []
    for r in job.lin:
        amt = _num(r.get("BETT_AMT"))
        if amt > 0:
            items.append({"description": f"Betterment on {r.get('LINE_DESC', '').strip()}", "amount": round(amt, 2), "reason": "betterment"})
        elif str(r.get("WHO_PAYS") or "").strip().upper() == "C":
            total = _num(r.get("ACT_PRICE")) * max(int(_num(r.get("PART_QTY")) or 1), 1) + _num(r.get("LBR_AMT")) + _num(r.get("MISC_AMT"))
            if total > 0:
                items.append({"description": r.get("LINE_DESC", "").strip(), "amount": round(total, 2), "reason": "customer pay line"})
    return items


def ro_number_for(job: EmsJob, shop: dict[str, Any]) -> str:
    fields = shop.get("ems", {}).get("ro_number_fields", ["ENV.RO_ID", "AD2.EST_FILENO"])
    for ref in fields:
        sec, _, key = ref.partition(".")
        val = str(getattr(job, sec.lower(), {}).get(key.upper(), "") or "").strip()
        if val:
            return val
    return f"E-{job.estimate_id}"


def to_repair_order(job: EmsJob, shop: dict[str, Any], today: date | None = None) -> RepairOrder:
    env, ad1, ad2, veh, ttl = job.env, job.ad1, job.ad2, job.veh, job.ttl
    today = today or date.today()
    customer_pay = str(ad1.get("CUST_PR") or "").strip().upper() == "C"
    insurer = "" if customer_pay else str(ad1.get("INS_CO_NM") or "").strip()
    owner = Customer(
        name=_name(ad1.get("OWNR_FN"), ad1.get("OWNR_LN"), ad1.get("OWNR_CO_NM")) or _name(ad1.get("INSD_FN"), ad1.get("INSD_LN"), ad1.get("INSD_CO_NM")),
        phone=_phone(ad1.get("OWNR_PH1") or ad1.get("OWNR_PH2") or ad1.get("INSD_PH1")),
        email=str(ad1.get("OWNR_EA") or ad1.get("INSD_EA") or "").strip(),
    )
    vehicle = Vehicle(
        year=_year(veh.get("V_MODEL_YR")),
        make=str(veh.get("V_MAKEDESC") or veh.get("V_MAKECODE") or "").strip().title(),
        model=str(veh.get("V_MODEL") or "").strip(),
        vin=str(veh.get("V_VIN") or "").strip().upper(),
        color=str(veh.get("V_COLOR") or "").strip(),
        mileage=int(_num(veh.get("V_MILEAGE"))) or None,
        paint_code=str(veh.get("PAINT_CD1") or "").strip(),
    )
    lines = lines_from_job(job)
    adjuster = _name(ad1.get("CLM_CT_FN"), ad1.get("CLM_CT_LN")) or _name(ad1.get("INS_CT_FN"), ad1.get("INS_CT_LN"))
    airbags = any("air bag" in l.description.lower() or "airbag" in l.description.lower() for l in lines if l.operation in {"replace", "r&r"})
    supp = job.supp_no
    gross = _num(ttl.get("G_TTL_AMT"))
    rates = {}
    for r in job.pfl:
        lt = LABOR_TYPE_MAP.get(str(r.get("LBR_TYPE") or "").strip().upper())
        if lt and _num(r.get("LBR_RATE")) > 0:
            rates[lt] = _num(r.get("LBR_RATE"))
    opened = _date(env.get("CREATE_DT")) or today.isoformat()
    ro = RepairOrder(
        ro_number=ro_number_for(job, shop),
        customer=owner,
        vehicle=vehicle,
        insurer=insurer,
        claim_number=str(ad1.get("CLM_NO") or "").strip(),
        adjuster=adjuster,
        deductible=_num(ttl.get("G_DED_AMT")) or _num(ad1.get("DED_AMT")),
        stage="scheduled",
        drivable=True,
        rental=False,
        opened=opened,
        dropped_off=_date(ad2.get("RO_IN_DATE")),
        promised=_date(ad2.get("TAR_DATE")),
        delivered=_date(ad2.get("DATE_OUT")) or _date(ad2.get("RO_CMPDATE")),
        stage_changed=f"{opened}T08:00:00",
        original_estimate_total=gross if supp in {"00", "0", ""} else 0.0,
        lines=lines,
        airbags_deployed=airbags,
        adas_equipped=True,
        customer_pay_items=customer_pay_items_from_job(job),
        flags=["total_loss"] if _bool(ad1.get("TLOS_IND")) else [],
        estimate_id=job.estimate_id,
        supp_no=supp,
        estimate_system={"C": "CCC", "M": "Mitchell", "A": "Audatex"}.get(str(env.get("EST_SYSTEM") or "").strip().upper(), str(env.get("EST_SYSTEM") or "")),
        estimate_totals={
            "gross": gross, "net": _num(ttl.get("N_TTL_AMT")), "deductible": _num(ttl.get("G_DED_AMT")), "customer": _num(ttl.get("G_CUST_AMT")),
            "betterment": _num(ttl.get("G_BETT_AMT")), "supplement": _num(ttl.get("SUPP_AMT")), "previous_net": _num(ttl.get("PREV_NET")), "tax": _num(ttl.get("G_TAX")),
        },
        labor_rates=rates,
        plate=str(veh.get("PLATE_NO") or "").strip(),
        notes=[Note(at=f"{opened}T08:00:00", author="ems-import", text=f"{job.kind.upper()} EMS import: estimate {job.estimate_id} supp {supp}; loss: {str(ad1.get('LOSS_DESC') or '').strip() or 'n/a'}; impact {str(veh.get('IMPACT_1') or '').strip()}")],
    )
    if ro.dropped_off and ro.stage == "scheduled":
        ro.stage = "checked_in"
    if ro.delivered:
        ro.stage = "delivered"
    return ro


# ---------------------------------------------------------------------------
# Merge and import
# ---------------------------------------------------------------------------
def _match_existing(store: Store, new: RepairOrder) -> RepairOrder | None:
    if store.get(new.ro_number):
        return store.get(new.ro_number)
    if new.estimate_id:
        for ro in store.list(include_delivered=True):
            if ro.estimate_id == new.estimate_id:
                return ro
    if new.vehicle.vin:
        for ro in store.list(include_delivered=False):
            if ro.vehicle.vin == new.vehicle.vin:
                return ro
    last = new.customer.name.split()[-1].lower() if new.customer.name else ""
    if last:
        for ro in store.list(include_delivered=False):
            if ro.customer.name.split()[-1].lower() == last and ro.vehicle.year == new.vehicle.year and ro.vehicle.make.lower()[:4] == new.vehicle.make.lower()[:4]:
                return ro
    return None


def merge_into(store: Store, new: RepairOrder, rate_check: dict[str, float] | None = None) -> tuple[RepairOrder, bool]:
    """Write the estimate onto the matching RO. The estimate owns lines, totals, contact
    details and vehicle data; the RO (CRISMA / front desk) owns stage, notes, parts,
    messages, rental and promised date."""
    existing = _match_existing(store, new)
    stamp = datetime.now().isoformat(timespec="seconds")
    if existing is None:
        if rate_check:
            new.notes.extend(Note(at=stamp, author="ems-import", text=t) for t in rate_notes(new, rate_check))
        store.insert(new)
        return new, True

    def apply(ro: RepairOrder) -> None:
        prev_supp = ro.supp_no
        ro.lines = new.lines
        ro.estimate_id, ro.supp_no, ro.estimate_system = new.estimate_id, new.supp_no, new.estimate_system
        ro.estimate_totals, ro.labor_rates = new.estimate_totals, new.labor_rates
        ro.customer_pay_items = new.customer_pay_items or ro.customer_pay_items
        if new.original_estimate_total and not ro.original_estimate_total:
            ro.original_estimate_total = new.original_estimate_total
        if not ro.customer.phone and new.customer.phone:
            ro.customer.phone = new.customer.phone
        if not ro.customer.email and new.customer.email:
            ro.customer.email = new.customer.email
        if len(new.customer.name) > len(ro.customer.name):
            ro.customer.name = new.customer.name
        for attr in ("vin", "color", "paint_code", "mileage"):
            if getattr(new.vehicle, attr) and not getattr(ro.vehicle, attr):
                setattr(ro.vehicle, attr, getattr(new.vehicle, attr))
        if new.insurer and not ro.insurer:
            ro.insurer = new.insurer
        for attr in ("claim_number", "adjuster", "plate"):
            if getattr(new, attr) and not getattr(ro, attr):
                setattr(ro, attr, getattr(new, attr))
        if new.deductible and not ro.deductible:
            ro.deductible = new.deductible
        if new.airbags_deployed:
            ro.airbags_deployed = True
        for f in new.flags:
            if f not in ro.flags:
                ro.flags.append(f)
        if new.supp_no not in {"00", "0", ""} and "has_supplement" not in ro.flags:
            ro.flags.append("has_supplement")
        text = f"EMS import: estimate {new.estimate_id} supp {new.supp_no} ({len(new.lines)} lines, gross ${new.estimate_totals.get('gross', 0):,.2f})"
        if prev_supp and prev_supp != new.supp_no:
            text += f"; replaces supp {prev_supp}"
        ro.notes.append(Note(at=stamp, author="ems-import", text=text))
        if rate_check:
            ro.notes.extend(Note(at=stamp, author="ems-import", text=t) for t in rate_notes(new, rate_check))

    return store.mutate(existing.ro_number, apply), False


def rate_notes(ro: RepairOrder, posted: dict[str, float]) -> list[str]:
    """Flag estimate labor rates below the shop's posted rates (a short-pay in waiting)."""
    out = []
    for lt, rate in ro.labor_rates.items():
        p = float(posted.get(lt, 0) or 0)
        if p and rate < p - 0.005:
            out.append(f"Estimate {lt} rate ${rate:.2f}/hr is below posted ${p:.2f}/hr")
    return out


def import_path(path: str | Path, store: Store, shop: dict[str, Any], *, today: date | None = None, seen: set[str] | None = None) -> list[dict[str, Any]]:
    """Import one job or every job under a folder. Returns a summary per job."""
    p = Path(path)
    jobs = find_jobs(p) if p.is_dir() else [p]
    rates = {k: float(v) for k, v in shop.get("rates", {}).items()}
    results = []
    for jp in jobs:
        key = f"{jp.resolve()}:{int(jp.stat().st_mtime)}"
        if seen is not None and key in seen:
            continue
        try:
            job = read_job(jp)
            ro = to_repair_order(job, shop, today)
            saved, created = merge_into(store, ro, rate_check=rates)
            results.append({"file": str(jp), "ro": saved.ro_number, "estimate_id": job.estimate_id, "supp_no": job.supp_no, "lines": len(ro.lines), "created": created, "gross": ro.estimate_totals.get("gross")})
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the folder
            results.append({"file": str(jp), "error": str(exc)})
        if seen is not None:
            seen.add(key)
    return results


def watch(folder: str | Path, store: Store, shop: dict[str, Any], *, poll_seconds: float = 30, on_result: Callable[[dict[str, Any]], None] | None = None, stop_after: int | None = None) -> None:
    """Poll the export folder and import new or changed jobs. Ctrl-C to stop."""
    seen: set[str] = set()
    cycles = 0
    while True:
        for r in import_path(folder, store, shop, seen=seen):
            if on_result:
                on_result(r)
        cycles += 1
        if stop_after is not None and cycles >= stop_after:
            return
        time.sleep(poll_seconds)
