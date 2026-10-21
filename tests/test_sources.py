import json
import struct
import tempfile
import unittest
from datetime import date
from pathlib import Path

from mab_agents import config, crisma, ems
from mab_agents.store import Store
from mab_agents.tools import estimate_tools

TODAY = date(2026, 10, 21)


def shop():
    return config.load_shop(config.EXAMPLE)


# ---- a tiny dBase III writer so fixtures stay synthetic -----------------------------
def write_dbf(path: Path, fields: list[tuple[str, str, int, int]], rows: list[dict]) -> None:
    """fields: (name, type C/N/D/L, length, decimals)."""
    header_len = 32 + 32 * len(fields) + 1
    rec_len = 1 + sum(f[2] for f in fields)
    out = bytearray()
    out += struct.pack("<BBBBIHH20x", 0x03, 26, 10, 21, len(rows), header_len, rec_len)
    for name, ftype, length, dec in fields:
        out += struct.pack("<11sc4xBB14x", name.encode().ljust(11, b"\0"), ftype.encode(), length, dec)
    out += b"\r"
    for row in rows:
        out += b" "
        for name, ftype, length, dec in fields:
            v = row.get(name, "")
            if ftype == "N":
                txt = ("" if v in ("", None) else (f"{float(v):.{dec}f}" if dec else str(int(v)))).rjust(length)
            elif ftype == "D":
                txt = (v.replace("-", "") if v else "").ljust(length)
            elif ftype == "L":
                txt = "T" if v else "F"
            else:
                txt = str(v)[:length].ljust(length)
            out += txt.encode("latin-1")
    out += b"\x1a"
    path.write_bytes(bytes(out))


def write_ems_dbf_job(folder: Path, stem: str = "ab12cd34", ro_id: str = "", supp: str = "00") -> Path:
    write_dbf(folder / f"{stem}.env", [("EST_SYSTEM", "C", 1, 0), ("UNQFILE_ID", "C", 8, 0), ("RO_ID", "C", 8, 0), ("SUPP_NO", "C", 3, 0), ("CREATE_DT", "D", 8, 0)],
              [{"EST_SYSTEM": "C", "UNQFILE_ID": stem, "RO_ID": ro_id, "SUPP_NO": supp, "CREATE_DT": "2026-10-20"}])
    write_dbf(folder / f"{stem}.ad1", [("INS_CO_NM", "C", 35, 0), ("CLM_NO", "C", 30, 0), ("DED_AMT", "N", 9, 2), ("CUST_PR", "C", 1, 0), ("OWNR_FN", "C", 35, 0), ("OWNR_LN", "C", 35, 0), ("OWNR_PH1", "C", 10, 0), ("OWNR_EA", "C", 80, 0), ("CLM_CT_FN", "C", 35, 0), ("CLM_CT_LN", "C", 35, 0), ("LOSS_DESC", "C", 38, 0), ("TLOS_IND", "L", 1, 0)],
              [{"INS_CO_NM": "Example Mutual", "CLM_NO": "EM-1", "DED_AMT": 500, "CUST_PR": "I", "OWNR_FN": "Test", "OWNR_LN": "Owner", "OWNR_PH1": "4105550199", "OWNR_EA": "t@example.com", "CLM_CT_FN": "Chris", "CLM_CT_LN": "Park", "LOSS_DESC": "Front end", "TLOS_IND": False}])
    write_dbf(folder / f"{stem}.ad2", [("EST_FILENO", "C", 25, 0), ("RO_IN_DATE", "D", 8, 0), ("TAR_DATE", "D", 8, 0), ("DATE_OUT", "D", 8, 0)],
              [{"EST_FILENO": "", "RO_IN_DATE": "2026-10-21", "TAR_DATE": "2026-10-30", "DATE_OUT": ""}])
    write_dbf(folder / f"{stem}.veh", [("V_MODEL_YR", "C", 2, 0), ("V_MAKEDESC", "C", 20, 0), ("V_MODEL", "C", 50, 0), ("V_VIN", "C", 25, 0), ("V_COLOR", "C", 20, 0), ("PAINT_CD1", "C", 15, 0), ("V_MILEAGE", "C", 6, 0), ("PLATE_NO", "C", 10, 0), ("IMPACT_1", "C", 2, 0)],
              [{"V_MODEL_YR": "21", "V_MAKEDESC": "TOYOTA", "V_MODEL": "RAV4 XLE", "V_VIN": "2T3P1RFV5MC000001", "V_COLOR": "GRAY", "PAINT_CD1": "1G3", "V_MILEAGE": "41230", "PLATE_NO": "ABC123", "IMPACT_1": "12"}])
    lin_fields = [("LINE_NO", "N", 3, 0), ("LINE_DESC", "C", 40, 0), ("PART_TYPE", "C", 4, 0), ("OEM_PARTNO", "C", 25, 0), ("ACT_PRICE", "N", 9, 2), ("PART_QTY", "N", 2, 0), ("MOD_LBR_TY", "C", 4, 0), ("MOD_LB_HRS", "N", 5, 1), ("LBR_OP", "C", 4, 0), ("WHO_PAYS", "C", 2, 0), ("BETT_PCTG", "N", 8, 2), ("BETT_AMT", "N", 9, 2), ("MISC_SUBLT", "L", 1, 0), ("MISC_AMT", "N", 9, 2)]
    write_dbf(folder / f"{stem}.lin", lin_fields, [
        {"LINE_NO": 1, "LINE_DESC": "Front bumper cover", "PART_TYPE": "PAA", "OEM_PARTNO": "52119-0R918", "ACT_PRICE": 412.50, "PART_QTY": 1, "MOD_LBR_TY": "LAB", "MOD_LB_HRS": 2.1, "LBR_OP": "OP11", "WHO_PAYS": "I"},
        {"LINE_NO": 2, "LINE_DESC": "Front bumper cover", "PART_TYPE": "", "ACT_PRICE": 0, "PART_QTY": 1, "MOD_LBR_TY": "LAR", "MOD_LB_HRS": 2.8, "LBR_OP": "OP6", "WHO_PAYS": "I"},
        {"LINE_NO": 3, "LINE_DESC": "Hood", "PART_TYPE": "", "ACT_PRICE": 0, "PART_QTY": 1, "MOD_LBR_TY": "LAB", "MOD_LB_HRS": 3.5, "LBR_OP": "OP9", "WHO_PAYS": "I"},
        {"LINE_NO": 4, "LINE_DESC": "LT front tire", "PART_TYPE": "PAN", "OEM_PARTNO": "TIRE1", "ACT_PRICE": 180.00, "PART_QTY": 1, "MOD_LBR_TY": "LAM", "MOD_LB_HRS": 0.3, "LBR_OP": "OP11", "WHO_PAYS": "I", "BETT_PCTG": 50, "BETT_AMT": 90.00},
        {"LINE_NO": 5, "LINE_DESC": "Pre-repair scan", "PART_TYPE": "", "ACT_PRICE": 0, "PART_QTY": 1, "MOD_LBR_TY": "LAD", "MOD_LB_HRS": 0.5, "LBR_OP": "OP14", "WHO_PAYS": "I"},
        {"LINE_NO": 6, "LINE_DESC": "Wheel alignment", "PART_TYPE": "PAS", "ACT_PRICE": 0, "PART_QTY": 1, "MOD_LBR_TY": "LAM", "MOD_LB_HRS": 0, "LBR_OP": "OP16", "WHO_PAYS": "I", "MISC_SUBLT": True, "MISC_AMT": 129.00},
        {"LINE_NO": 7, "LINE_DESC": "Pinstripe customer requested", "PART_TYPE": "PAS", "ACT_PRICE": 60.00, "PART_QTY": 1, "MOD_LBR_TY": "LAB", "MOD_LB_HRS": 0, "LBR_OP": "OP16", "WHO_PAYS": "C"},
    ])
    write_dbf(folder / f"{stem}.ttl", [("G_TTL_AMT", "N", 10, 2), ("G_DED_AMT", "N", 10, 2), ("G_CUST_AMT", "N", 10, 2), ("G_BETT_AMT", "N", 10, 2), ("N_TTL_AMT", "N", 10, 2), ("SUPP_AMT", "N", 10, 2)],
              [{"G_TTL_AMT": 4120.00, "G_DED_AMT": 500.00, "G_CUST_AMT": 650.00, "G_BETT_AMT": 90.00, "N_TTL_AMT": 3470.00, "SUPP_AMT": 0}])
    write_dbf(folder / f"{stem}.pfl", [("LBR_TYPE", "C", 4, 0), ("LBR_DESC", "C", 20, 0), ("LBR_RATE", "N", 6, 2)],
              [{"LBR_TYPE": "LAB", "LBR_DESC": "Body", "LBR_RATE": 52.00}, {"LBR_TYPE": "LAR", "LBR_DESC": "Refinish", "LBR_RATE": 52.00}, {"LBR_TYPE": "LAM", "LBR_DESC": "Mechanical", "LBR_RATE": 110.00}])
    return folder / f"{stem}.env"


EST_XML = """<?xml version="1.0" standalone="yes" ?><EMS>
<ENV><EST_SYSTEM>C</EST_SYSTEM><UNQFILE_ID>c8e12aeb</UNQFILE_ID><RO_ID>26-2001</RO_ID><SUPP_NO>01</SUPP_NO><CREATE_DT>2/27/2026 12:00:00 AM</CREATE_DT></ENV>
<AD1><INS_CO_NM>Sample Insurance</INS_CO_NM><CLM_NO>SI-9</CLM_NO><DED_AMT>1000</DED_AMT><CUST_PR>I</CUST_PR><OWNR_FN>Xml</OWNR_FN><OWNR_LN>Person</OWNR_LN><OWNR_PH1>3015550100</OWNR_PH1><TLOS_IND>F</TLOS_IND></AD1>
<AD2><EST_FILENO></EST_FILENO><RO_IN_DATE>1/1/0001 12:00:00 AM</RO_IN_DATE><TAR_DATE></TAR_DATE></AD2>
<VEH><V_MODEL_YR>22</V_MODEL_YR><V_MAKEDESC>HONDA</V_MAKEDESC><V_MODEL>CR-V EX-L</V_MODEL><V_VIN>7FARW2H84NE000001</V_VIN><V_COLOR>WHITE</V_COLOR><PAINT_CD1>NH-883P</PAINT_CD1></VEH>
<LIN><LINE_NO>1</LINE_NO><LINE_DESC>Rear bumper cover</LINE_DESC><PART_TYPE>PAN</PART_TYPE><OEM_PARTNO>04715-3A0-A90ZZ</OEM_PARTNO><ACT_PRICE>455.00</ACT_PRICE><PART_QTY>1</PART_QTY><MOD_LBR_TY>LAB</MOD_LBR_TY><MOD_LB_HRS>1.9</MOD_LB_HRS><LBR_OP>OP11</LBR_OP></LIN>
<LIN><LINE_NO>2</LINE_NO><LINE_DESC>Rear bumper cover</LINE_DESC><PART_TYPE></PART_TYPE><ACT_PRICE>0</ACT_PRICE><MOD_LBR_TY>LAR</MOD_LBR_TY><MOD_LB_HRS>2.6</MOD_LB_HRS><LBR_OP>OP6</LBR_OP></LIN>
<LIN><LINE_NO>3</LINE_NO><LINE_DESC>Blind spot radar sensor</LINE_DESC><PART_TYPE>PAN</PART_TYPE><OEM_PARTNO>36931-TLA-A01</OEM_PARTNO><ACT_PRICE>310.00</ACT_PRICE><PART_QTY>1</PART_QTY><MOD_LBR_TY>LAB</MOD_LBR_TY><MOD_LB_HRS>0.4</MOD_LB_HRS><LBR_OP>OP11</LBR_OP></LIN>
<TTL><G_TTL_AMT>2890.00</G_TTL_AMT><G_DED_AMT>1000</G_DED_AMT><N_TTL_AMT>1890</N_TTL_AMT><SUPP_AMT>540.00</SUPP_AMT><PREV_NET>1350</PREV_NET></TTL>
<PFL><LBR_TYPE>LAB</LBR_TYPE><LBR_RATE>58.00</LBR_RATE></PFL>
</EMS>"""


class EmsDbfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.env = write_ems_dbf_job(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_sections_and_maps_lines(self):
        job = ems.read_job(self.env)
        self.assertEqual(job.kind, "dbf")
        self.assertEqual(job.estimate_id, "ab12cd34")
        self.assertEqual(job.supp_no, "00")
        self.assertEqual(len(job.lin), 7)
        ro = ems.to_repair_order(job, shop(), TODAY)
        self.assertEqual(ro.ro_number, "E-ab12cd34")           # no RO_ID / EST_FILENO
        self.assertEqual(ro.customer.name, "Test Owner")
        self.assertEqual(ro.customer.phone, "410-555-0199")
        self.assertEqual((ro.vehicle.year, ro.vehicle.make, ro.vehicle.model), (2021, "Toyota", "RAV4 XLE"))
        self.assertEqual(ro.insurer, "Example Mutual")
        self.assertEqual(ro.adjuster, "Chris Park")
        self.assertEqual(ro.deductible, 500.0)
        self.assertEqual(ro.dropped_off, "2026-10-21")
        self.assertEqual(ro.promised, "2026-10-30")
        self.assertEqual(ro.stage, "checked_in")
        ops = [(l.operation, l.labor_type, l.labor_hours, l.paint_hours, l.part_type) for l in ro.lines]
        self.assertEqual(ops[0], ("replace", "body", 2.1, 0.0, "aftermarket"))
        self.assertEqual(ops[1], ("refinish", "refinish", 0.0, 2.8, "none"))
        self.assertEqual(ops[2], ("repair", "body", 3.5, 0.0, "none"))
        self.assertEqual(ops[4], ("additional", "diagnostic", 0.5, 0.0, "none"))
        self.assertEqual(ops[5][0], "sublet")
        self.assertEqual(ro.lines[5].part_price, 129.0)
        self.assertEqual(ro.estimate_totals["gross"], 4120.0)
        self.assertEqual(ro.labor_rates, {"body": 52.0, "refinish": 52.0, "mechanical": 110.0})
        self.assertEqual(ro.original_estimate_total, 4120.0)
        items = {i["reason"]: i["amount"] for i in ro.customer_pay_items}
        self.assertEqual(items, {"betterment": 90.0, "customer pay line": 60.0})
        self.assertIn("tire", ro.customer_pay_items[0]["description"].lower())

    def test_import_merges_and_flags_low_rates(self):
        st = Store(":memory:")
        st.load_sample(today=TODAY)
        res = ems.import_path(self.folder, st, shop(), today=TODAY)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["created"])
        ro = st.get("E-ab12cd34")
        self.assertTrue(any("below posted" in n.text for n in ro.notes))     # 52 vs posted 58
        self.assertEqual(ro.deductible, 500.0)
        # second import of the same job: no duplicate, same RO updated
        res2 = ems.import_path(self.folder, st, shop(), today=TODAY)
        self.assertFalse(res2[0]["created"])
        self.assertEqual(len([r for r in st.list(include_delivered=True) if r.estimate_id == "ab12cd34"]), 1)

    def test_import_matches_existing_ro_by_ro_id(self):
        st = Store(":memory:")
        st.load_sample(today=TODAY)
        env = write_ems_dbf_job(self.folder, stem="ff00ff00", ro_id="26-1042", supp="01")
        before = st.get("26-1042")
        res = ems.import_path(env, st, shop(), today=TODAY)
        self.assertFalse(res[0]["created"])
        after = st.get("26-1042")
        self.assertEqual(after.stage, before.stage)                 # RO keeps its stage
        self.assertEqual(after.rental, before.rental)
        self.assertEqual(after.promised, before.promised)           # and promised date
        self.assertEqual(len(after.parts), len(before.parts))       # and parts tracking
        self.assertEqual(len(after.lines), 7)                       # estimate owns the lines
        self.assertEqual(after.supp_no, "01")
        self.assertIn("has_supplement", after.flags)
        self.assertEqual(after.customer.phone, before.customer.phone)

    def test_watch_imports_new_jobs_once(self):
        st = Store(":memory:")
        seen = []
        ems.watch(self.folder, st, shop(), poll_seconds=0, on_result=seen.append, stop_after=2)
        self.assertEqual(len(seen), 1)


class EmsXmlTests(unittest.TestCase):
    def test_est_xml(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c8e12aeb.EST"
            p.write_text(EST_XML)
            job = ems.read_job(p)
            self.assertEqual(job.kind, "xml")
            ro = ems.to_repair_order(job, shop(), TODAY)
            self.assertEqual(ro.ro_number, "26-2001")
            self.assertEqual(ro.supp_no, "01")
            self.assertEqual(ro.opened, "2026-02-27")
            self.assertIsNone(ro.dropped_off)                         # 1/1/0001 means empty
            self.assertEqual(ro.vehicle.year, 2022)
            self.assertEqual(len(ro.lines), 3)
            self.assertEqual(ro.estimate_totals["supplement"], 540.0)
            self.assertEqual(ro.original_estimate_total, 0.0)         # a supplement is not the original
            jobs = ems.find_jobs(d)
            self.assertEqual([j.name for j in jobs], ["c8e12aeb.EST"])


class CrismaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        write_dbf(self.folder / "BSLOC.DBF", [("CODE", "C", 4, 0), ("NAME", "C", 40, 0), ("STATUS", "C", 1, 0)], [
            {"CODE": "OL", "NAME": "ON LOT (READY FOR REPAIRS)", "STATUS": "A"}, {"CODE": "IB", "NAME": "IN BODY REPAIR", "STATUS": "A"},
            {"CODE": "REF", "NAME": "IN PAINT PROCESS", "STATUS": "A"}, {"CODE": "VF", "NAME": "READY FOR CUSTOMER PICKUP", "STATUS": "A"},
            {"CODE": "TL", "NAME": "TOTAL LOSS", "STATUS": "A"}, {"CODE": "DNR", "NAME": "DID NOT DO REPAIRS", "STATUS": "A"}, {"CODE": "ZZ", "NAME": "MYSTERY", "STATUS": "A"}])
        fields = [("RONO", "C", 8, 0), ("LNAME", "C", 30, 0), ("YR", "C", 4, 0), ("MAKE", "C", 20, 0), ("MODEL", "C", 20, 0), ("COLOR", "C", 10, 0), ("TAG", "C", 10, 0), ("HATTAG", "C", 6, 0), ("FLAGCOMM", "C", 40, 0), ("OPENRO", "L", 1, 0), ("SUPPLAMT", "N", 10, 2), ("DUECUST", "N", 10, 2), ("SCHINDT", "D", 8, 0), ("SCHOUTDT", "D", 8, 0), ("INDT", "D", 8, 0), ("STARTDT", "D", 8, 0), ("RENTCAR", "C", 1, 0), ("LOCCD", "C", 4, 0), ("INSNAME", "C", 30, 0)]
        write_dbf(self.folder / "BSRO.DBF", fields, [
            {"RONO": "26-1047", "LNAME": "LEE", "YR": "2019", "MAKE": "FORD", "MODEL": "F-150", "OPENRO": True, "DUECUST": 0, "SCHINDT": "2026-10-15", "SCHOUTDT": "2026-10-24", "INDT": "2026-10-15", "RENTCAR": "N", "LOCCD": "REF", "INSNAME": ""},
            {"RONO": "26-3001", "LNAME": "NEWMAN", "YR": "2020", "MAKE": "KIA", "MODEL": "SOUL", "COLOR": "RED", "TAG": "XYZ789", "HATTAG": "42", "FLAGCOMM": "wants call before paint", "OPENRO": True, "DUECUST": 250, "SCHINDT": "2026-10-20", "SCHOUTDT": "2026-10-29", "INDT": "2026-10-20", "RENTCAR": "Y", "LOCCD": "IB", "INSNAME": "Example Mutual"},
            {"RONO": "26-3002", "LNAME": "SCHEDULED", "YR": "2018", "MAKE": "SUBARU", "MODEL": "OUTBACK", "OPENRO": True, "SCHINDT": "2026-10-27", "RENTCAR": "N", "LOCCD": "", "INSNAME": "Sample Insurance"},
            {"RONO": "26-3003", "LNAME": "TOTALLED", "YR": "2012", "MAKE": "HONDA", "MODEL": "CIVIC", "OPENRO": True, "INDT": "2026-10-10", "RENTCAR": "Y", "LOCCD": "TL", "INSNAME": "Example Mutual"},
            {"RONO": "26-3004", "LNAME": "NOSHOW", "YR": "2015", "MAKE": "GMC", "MODEL": "SIERRA", "OPENRO": True, "LOCCD": "DNR", "INSNAME": ""},
            {"RONO": "26-3005", "LNAME": "MYSTERY", "YR": "2016", "MAKE": "BMW", "MODEL": "X3", "OPENRO": True, "INDT": "2026-10-19", "LOCCD": "ZZ", "INSNAME": ""},
            {"RONO": "26-3006", "LNAME": "DONE", "YR": "2017", "MAKE": "AUDI", "MODEL": "A4", "OPENRO": False, "LOCCD": "VF", "INSNAME": ""},
        ])

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_codes_and_sync(self):
        codes = crisma.read_status_codes(self.folder, shop())
        self.assertEqual(codes["REF"], "IN PAINT PROCESS")
        st = Store(":memory:")
        st.load_sample(today=TODAY)
        s = crisma.sync(st, shop(), self.folder, today=TODAY)
        self.assertEqual(s["rows"], 7)
        self.assertEqual(s["skipped"], 2)                 # DNR and closed RO
        self.assertEqual(s["created"], 4)                 # 3001, 3002, 3003, 3005
        self.assertEqual(s["updated"], 1)                 # 26-1047 body -> paint
        self.assertEqual(s["unknown_codes"], ["ZZ"])
        truck = st.get("26-1047")
        self.assertEqual(truck.stage, "paint")
        self.assertEqual(truck.promised, "2026-10-24")
        self.assertEqual(truck.source_status, "REF - IN PAINT PROCESS")
        self.assertIn("stage body -> paint", truck.notes[-1].text)
        self.assertEqual(len(truck.lines), 8)             # CRISMA never touches estimate lines
        kia = st.get("26-3001")
        self.assertEqual((kia.stage, kia.rental, kia.deductible, kia.plate, kia.customer.name), ("body", True, 250.0, "XYZ789", "Newman"))
        self.assertIn("hat tag 42", kia.notes[0].text)
        self.assertEqual(st.get("26-3002").stage, "scheduled")         # blank code, no in-date
        self.assertEqual(st.get("26-3002").scheduled_in, "2026-10-27")
        tl = st.get("26-3003")
        self.assertEqual(tl.stage, "approval")
        self.assertIn("total_loss", tl.flags)
        self.assertEqual(st.get("26-3005").stage, "checked_in")        # unknown code, in-date set
        self.assertIsNone(st.get("26-3004"))
        again = crisma.sync(st, shop(), self.folder, today=TODAY)
        self.assertEqual((again["created"], again["updated"]), (0, 0))
        self.assertEqual(again["unchanged"], 5)

    def test_status_map_override_from_shop_toml(self):
        sh = shop()
        sh["crisma"] = {"status_map": {"ZZ": "qc"}}
        st = Store(":memory:")
        crisma.sync(st, sh, self.folder, today=TODAY)
        self.assertEqual(st.get("26-3005").stage, "qc")


class CatalogueTests(unittest.TestCase):
    def test_rules_load_and_evaluate(self):
        rules = estimate_tools.load_rules()
        self.assertEqual(len(rules), 51)
        lines = estimate_tools.parse_estimate_csv((Path(__file__).resolve().parents[1] / "mab_agents" / "data" / "sample_estimate.csv").read_text())
        r = estimate_tools.evaluate_rules(lines)
        keys = {x["key"] for x in r["triggered"]}
        self.assertIn("pre_scan", keys)
        self.assertIn("aim_headlamps", keys)
        self.assertIn("wheel_alignment_check", keys)
        verify = {x["key"] for x in r["verify_if_equipped"]}
        self.assertIn("front_radar_cal", verify)                        # needs the feature confirmed
        r2 = estimate_tools.evaluate_rules(lines, features={"front_radar"})
        self.assertIn("front_radar_cal", {x["key"] for x in r2["triggered"]})
        self.assertLessEqual(r["score"], 100)

    def test_satisfied_when_already_on_estimate(self):
        lines = estimate_tools.parse_estimate_json(json.dumps([
            {"line": 1, "operation": "replace", "description": "Headlamp assembly", "labor_hours": 0.6},
            {"line": 2, "operation": "additional", "description": "Aim headlamps", "labor_hours": 0.3},
        ]))
        r = estimate_tools.evaluate_rules(lines)
        self.assertIn("aim_headlamps", {x["key"] for x in r["satisfied"]})
        self.assertNotIn("aim_headlamps", {x["key"] for x in r["triggered"]})


if __name__ == "__main__":
    unittest.main()
