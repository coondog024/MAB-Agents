"""CRISMA bridge: read repair-order status straight from the FoxPro tables, read-only.

CRISMA keeps one row per RO in BSRO.DBF with a location code (LOCCD) whose
meaning lives in BSLOC.DBF (OL = ON LOT, IB = IN BODY REPAIR, REF = IN PAINT
PROCESS, VF = READY FOR CUSTOMER PICKUP, ...). The status board you built read
those two tables; this module does the same and writes the result into MAB's
store so the Today queue, the board and the agents run on the real shop.

MAB never writes to CRISMA. FoxPro holds its own locks and a stray write could
corrupt a table the whole shop depends on.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ems import _date, read_dbf_rows
from .models import STAGES, Customer, Note, RepairOrder, Vehicle
from .store import Store

DEFAULT_STATUS_MAP = {
    "OL": "checked_in", "OLW": "approval", "OLP": "parts_ordered", "TD": "blueprint", "SUP": "approval", "UPL": "parts_ordered",
    "NTC": "parts_ordered", "CPH": "parts_received", "IB": "body", "IBS": "body", "SUB": "body", "CBK": "body", "REF": "paint",
    "IRA": "reassembly", "CLN": "qc", "FI": "qc", "VF": "ready", "VFS": "ready", "CA": "delivered", "CAS": "delivered", "CSS": "delivered",
    "TL": "approval",
}
FLAG_CODES = {"TL": "total_loss", "VFS": "has_supplement", "CAS": "supplement_to_send", "CSS": "supplement_sent", "IBS": "supp_parts_on_order", "UPL": "backorder", "SUB": "sublet_out", "CBK": "comeback"}


def _cfg(shop: dict[str, Any]) -> dict[str, Any]:
    c = dict(shop.get("crisma", {}))
    c.setdefault("data_dir", "M:/Crisma/data")
    c.setdefault("ro_table", "BSRO.DBF")
    c.setdefault("status_table", "BSLOC.DBF")
    c.setdefault("skip_codes", ["NS", "DNR"])
    c["status_map"] = {k.upper(): v for k, v in {**DEFAULT_STATUS_MAP, **c.get("status_map", {})}.items()}
    return c


def _find(folder: Path, name: str) -> Path | None:
    for p in folder.iterdir():
        if p.is_file() and p.name.lower() == name.lower():
            return p
    return None


def read_status_codes(data_dir: str | Path, shop: dict[str, Any]) -> dict[str, str]:
    cfg = _cfg(shop)
    p = _find(Path(data_dir), cfg["status_table"])
    if not p:
        return {}
    return {str(r.get("CODE") or "").strip().upper(): str(r.get("NAME") or "").strip() for r in read_dbf_rows(p) if r.get("CODE")}


def read_ros(data_dir: str | Path, shop: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = _cfg(shop)
    p = _find(Path(data_dir), cfg["ro_table"])
    if not p:
        raise FileNotFoundError(f"{cfg['ro_table']} not found in {data_dir}")
    return read_dbf_rows(p)


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def stage_for(row: dict[str, Any], cfg: dict[str, Any]) -> str:
    code = _s(row.get("LOCCD")).upper()
    if code in cfg["status_map"] and cfg["status_map"][code] in STAGES:
        return cfg["status_map"][code]
    if _date(row.get("INDT")):
        return "checked_in"
    return "scheduled"


def to_repair_order(row: dict[str, Any], shop: dict[str, Any], codes: dict[str, str], existing: RepairOrder | None, today: date | None = None) -> RepairOrder:
    cfg = _cfg(shop)
    today = today or date.today()
    code = _s(row.get("LOCCD")).upper()
    stage = stage_for(row, cfg)
    insurer = _s(row.get("INSNAME"))
    rental = _s(row.get("RENTCAR")).upper() in {"Y", "T", "TRUE", "1"}
    promised = _date(row.get("SCHOUTDT"))
    dropped = _date(row.get("INDT"))
    sched_in = _date(row.get("SCHINDT"))
    due_cust = float(row.get("DUECUST") or 0) if str(row.get("DUECUST") or "").strip() not in {"", "None"} else 0.0
    status_text = f"{code} - {codes.get(code, '')}".strip(" -") if code else ""
    flags = [FLAG_CODES[code]] if code in FLAG_CODES else []
    if existing is None:
        ro = RepairOrder(
            ro_number=_s(row.get("RONO")),
            customer=Customer(name=_s(row.get("LNAME")).title(), phone=_phone_from(row)),
            vehicle=Vehicle(year=int(_s(row.get("YR")) or 0) if _s(row.get("YR")).isdigit() else 0, make=_s(row.get("MAKE")).title(), model=_s(row.get("MODEL")).title(), color=_s(row.get("COLOR")).title(), vin=_s(row.get("VIN"))),
            insurer=insurer,
            deductible=due_cust if insurer else 0.0,
            stage=stage,
            rental=rental,
            opened=sched_in or dropped or today.isoformat(),
            dropped_off=dropped,
            promised=promised,
            scheduled_in=sched_in,
            stage_changed=f"{today.isoformat()}T07:00:00",
            source_status=status_text,
            flags=flags,
            plate=_s(row.get("TAG")),
            notes=[Note(at=f"{today.isoformat()}T07:00:00", author="crisma-sync", text=f"Imported from CRISMA RO {_s(row.get('RONO'))}; location {status_text or 'blank'}" + (f"; hat tag {_s(row.get('HATTAG'))}" if _s(row.get("HATTAG")) else "") + (f"; note: {_s(row.get('FLAGCOMM'))}" if _s(row.get("FLAGCOMM")) else ""))],
        )
        if not insurer and due_cust:
            ro.customer_pay_items = [{"description": "Balance due per CRISMA", "amount": due_cust, "reason": "customer pay"}]
        if stage == "delivered" and not ro.delivered:
            ro.delivered = promised or today.isoformat()
        return ro
    ro = existing
    changed = []
    if stage != ro.stage:
        changed.append(f"stage {ro.stage} -> {stage} (CRISMA {status_text or 'blank'})")
        ro.stage = stage
        ro.stage_changed = datetime.now().isoformat(timespec="seconds")
        if stage == "delivered" and not ro.delivered:
            ro.delivered = today.isoformat()
    if promised and promised != ro.promised:
        changed.append(f"promised {ro.promised or 'TBD'} -> {promised}")
        ro.promised = promised
    if dropped and dropped != ro.dropped_off:
        ro.dropped_off = dropped
    if sched_in:
        ro.scheduled_in = sched_in
    if rental != ro.rental:
        changed.append(f"rental -> {'yes' if rental else 'no'}")
        ro.rental = rental
    if insurer and not ro.insurer:
        ro.insurer = insurer
    if due_cust and insurer and not ro.deductible:
        ro.deductible = due_cust
    if not ro.customer.name and _s(row.get("LNAME")):
        ro.customer.name = _s(row.get("LNAME")).title()
    if not ro.plate and _s(row.get("TAG")):
        ro.plate = _s(row.get("TAG"))
    ro.source_status = status_text
    for f in flags:
        if f not in ro.flags:
            ro.flags.append(f)
    if changed:
        ro.notes.append(Note(at=datetime.now().isoformat(timespec="seconds"), author="crisma-sync", text="; ".join(changed)))
    return ro


def _phone_from(row: dict[str, Any]) -> str:
    from .ems import _phone

    for key in ("PHONE", "HPHONE", "CPHONE", "WPHONE", "CELL", "PH1"):
        if _s(row.get(key)):
            return _phone(row[key])
    return ""


def sync(store: Store, shop: dict[str, Any], data_dir: str | Path | None = None, *, today: date | None = None, open_only: bool = True) -> dict[str, Any]:
    cfg = _cfg(shop)
    folder = Path(data_dir or cfg["data_dir"])
    codes = read_status_codes(folder, shop)
    rows = read_ros(folder, shop)
    skip = {c.upper() for c in cfg["skip_codes"]}
    summary: dict[str, Any] = {"rows": len(rows), "created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": [], "unknown_codes": sorted({_s(r.get("LOCCD")).upper() for r in rows if _s(r.get("LOCCD")) and _s(r.get("LOCCD")).upper() not in cfg["status_map"] and _s(r.get("LOCCD")).upper() not in skip})}
    for row in rows:
        rono = _s(row.get("RONO"))
        code = _s(row.get("LOCCD")).upper()
        if not rono or code in skip or (open_only and "OPENRO" in row and not row.get("OPENRO")):
            summary["skipped"] += 1
            continue
        try:
            existing = store.get(rono)
            if existing is None:
                store.insert(to_repair_order(row, shop, codes, None, today))
                summary["created"] += 1
            else:
                before = (existing.stage, existing.promised, existing.rental, existing.source_status)
                updated = store.mutate(rono, lambda ro: to_repair_order(row, shop, codes, ro, today))
                after = (updated.stage, updated.promised, updated.rental, updated.source_status)
                summary["updated" if before != after else "unchanged"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"RO {rono}: {exc}")
    return summary
