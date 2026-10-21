import json
import unittest
from datetime import date

from mab_agents import config, fleet
from mab_agents.agents.base import Ctx
from mab_agents.models import EstimateLine
from mab_agents.store import Store
from mab_agents.tools import ar_tools, customer_tools, estimate_tools, frontdesk_tools, kpi_tools

TODAY = date(2026, 10, 21)


def shop():
    return config.load_shop(config.EXAMPLE)


def store():
    s = Store(":memory:")
    s.load_sample(today=TODAY)
    return s


class RentalAndSupplementQueueTests(unittest.TestCase):
    def test_rental_cap_warnings(self):
        s = store()
        ro = s.get("26-1042")                       # rental, dropped off 7 days ago
        ro.rental_covered_days = 10
        s.upsert(ro)
        q = {x["ro"]: x for x in frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)}
        self.assertTrue(any("ask Example Mutual for an extension" in r for r in q["26-1042"]["reasons"]))
        self.assertEqual(q["26-1042"]["priority"], 1)
        ro.rental_covered_days = 5
        s.upsert(ro)
        q = {x["ro"]: x for x in frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)}
        self.assertTrue(any("coverage has run out" in r for r in q["26-1042"]["reasons"]))
        ro.rental_covered_days = 0
        ro.dropped_off = "2026-10-01"
        s.upsert(ro)
        q = {x["ro"]: x for x in frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)}
        self.assertTrue(any("no covered-days cap" in r for r in q["26-1042"]["reasons"]))
        self.assertEqual(ro.rental_days_used(TODAY), 20)

    def test_supplement_chase(self):
        s = store()
        ro = s.get("26-1051")                       # awaiting approval
        ro.supplements = [{"supp_no": "01", "submitted": "2026-10-18", "approved": None, "gross": 3400.0, "amount": 510.0}]
        s.upsert(ro)
        q = {x["ro"]: x for x in frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)}
        self.assertTrue(any("escalate to the manager" in r for r in q["26-1051"]["reasons"]))
        ro.supplements[0]["submitted"] = "2026-10-19"
        s.upsert(ro)
        q = {x["ro"]: x for x in frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)}
        self.assertTrue(any("call the adjuster today" in r for r in q["26-1051"]["reasons"]))


class TextAndScriptTests(unittest.TestCase):
    def test_status_text_promises_next_contact_when_it_fits(self):
        ro = store().get("26-1047")
        d = customer_tools.stage_update(ro, shop(), "body")
        self.assertIn("hear from us again", d["text"])
        self.assertLessEqual(len(d["text"]), 300)
        self.assertNotIn("hear from us again", customer_tools.stage_update(ro, shop(), "ready")["text"])

    def test_new_scripts_and_opener(self):
        nc = frontdesk_tools.phone_script("new_claim")
        self.assertIn("everyone OK", nc["ask"][0])
        self.assertTrue(any("filed a claim yet" in n for n in nc["never"]))
        tl = frontdesk_tools.phone_script("insurer says it may be a total loss")
        self.assertEqual(tl["situation"], "total_loss")
        self.assertTrue(any("10 business days" in x for x in tl["say"]))
        self.assertEqual(frontdesk_tools.phone_script("charge for tires battery")["situation"], "betterment")
        self.assertEqual(frontdesk_tools.phone_script("should I file a claim or pay myself")["situation"], "claim_or_cash")
        self.assertIn("800-492-6116", frontdesk_tools.phone_script("steering")["then"])
        self.assertTrue(any("Missed call" in x for x in frontdesk_tools.DAILY_ROUTINE["during"]))


class MarylandAuditTests(unittest.TestCase):
    def test_aftermarket_disclosure_finding_only_in_md(self):
        lines = [EstimateLine(line=1, operation="replace", description="Front bumper cover", part_type="aftermarket", part_price=189, labor_hours=2.1)]
        md = {f.rule for f in estimate_tools.audit_estimate(lines, state="MD")}
        va = {f.rule for f in estimate_tools.audit_estimate(lines, state="VA")}
        self.assertIn("md_aftermarket_disclosure", md)
        self.assertNotIn("md_aftermarket_disclosure", va)
        oem = [EstimateLine(line=1, operation="replace", description="Front bumper cover", part_type="oem", part_price=412, labor_hours=2.1)]
        self.assertNotIn("md_aftermarket_disclosure", {f.rule for f in estimate_tools.audit_estimate(oem, state="MD")})

    def test_customer_pay_note_cites_14_1002(self):
        ro = store().get("26-1047")
        p = frontdesk_tools.payment_summary(ro, shop())
        self.assertIn("14-1002", p["who_pays"])


class ArTests(unittest.TestCase):
    def test_balance_and_aging(self):
        s = store()
        rates = {k: float(v) for k, v in shop()["rates"].items()}
        ro = s.get("26-1033")                       # delivered Subaru, Example Mutual, $250 deductible
        total = ar_tools.invoice_total(ro, rates)
        self.assertGreater(total, 1000)
        ro.payments = [{"date": "2026-10-09", "payer": "customer", "amount": 250.0, "method": "card", "reference": ""},
                       {"date": "2026-10-15", "payer": "Example Mutual", "amount": round(total - 250 - 180, 2), "method": "eft", "reference": "E123"}]
        s.upsert(ro)
        b = ar_tools.balance(ro, rates)
        self.assertEqual(b["customer_balance"], 0.0)
        self.assertEqual(b["carrier_short"], 180.0)
        self.assertEqual(b["balance"], 180.0)
        ag = ar_tools.aging(s.list(include_delivered=True), rates, today=TODAY)
        self.assertEqual(ag["open_items"], 1)
        self.assertEqual(ag["rows"][0]["ro"], "26-1033")
        self.assertEqual(ag["rows"][0]["bucket"], "0-30")
        self.assertEqual(ag["total_outstanding"], 180.0)

    def test_short_pay_letter_and_mia_outline(self):
        ro = store().get("26-1042")
        items = [{"description": "Post-repair scan", "amount": 62.50, "justification": "Required by Toyota CRIB 191 OEM procedure"}, {"description": "Cover car for overspray", "amount": 29.0}]
        letter = ar_tools.short_pay_letter(ro, shop(), items, 3470.0, today=TODAY)
        self.assertIn("balance $91.50", letter["subject"])
        self.assertIn("per the OEM repair procedure", letter["body"])
        self.assertIn("not-included operation", letter["body"])
        self.assertIn("Maryland Insurance Administration", letter["body"])
        outline = ar_tools.mia_complaint_outline(ro, items, ["2026-10-02 supplement sent"])
        self.assertEqual(outline["where"]["consumer_line"], "800-492-6116")
        self.assertTrue(any("EM-2026-778812" in x for x in outline["include"]))

    def test_ar_agent_in_fleet_and_router(self):
        ctx = Ctx(shop=shop(), store=store(), today=TODAY, kb_path=":memory:")
        fl = fleet.build_fleet(ctx)
        self.assertIn("ar", fl)
        self.assertEqual(len(fl), 10)
        self.assertIn("ar_aging", {t.name for t in fl["manager"].tools if hasattr(t, "name")})
        self.assertEqual(fleet.route("Example Mutual short paid us $180 on 26-1033, draft the letter", fl)[0], "ar")
        self.assertEqual(fleet.route("What's outstanding over 30 days and who do I call?", fl)[0], "ar")
        tools = {t.name: t for t in fl["ar"].tools if hasattr(t, "name")}
        out = json.loads(tools["record_payment"].call({"ro_number": "26-1033", "payer": "customer", "amount": 250, "method": "card"}))
        self.assertTrue(out["recorded"])
        self.assertEqual(out["customer_paid"], 250.0)


class KpiTests(unittest.TestCase):
    def test_scorecard_rates(self):
        k = kpi_tools.kpis(store().list(include_delivered=True), shop(), today=TODAY)
        self.assertIsNotNone(k["scan_rate"])
        self.assertGreaterEqual(k["scan_rate"], 0.5)        # 3 of 4 sample ROs carry a scan line
        self.assertIsNotNone(k["calibration_rate"])
        self.assertIn("supplement_rate", k)
        self.assertIsNotNone(k["average_rental_days"])


if __name__ == "__main__":
    unittest.main()


class SecondSweepTests(unittest.TestCase):
    """Findings from the DEG / EMS / Maryland research sweep folded into code."""

    def test_ems_code_maps_cover_ccc_secure_share_lists(self):
        from mab_agents import ems

        self.assertEqual(ems.PART_TYPE_MAP["PAND"], "oem")
        self.assertEqual(ems.PART_TYPE_MAP["PAA"], "aftermarket")
        self.assertEqual(ems.LABOR_TYPE_MAP["LAP"], "body")
        for code in ("LAB", "LAR", "LAM", "LAS", "LAF", "LAD", "LAE", "LAG", "LAP", "LA1", "LA2", "LA3", "LA4"):
            self.assertIn(code, ems.LABOR_TYPE_MAP)

    def test_describe_job_reads_field_dictionary_from_headers(self):
        import tempfile
        from pathlib import Path

        from test_sources import write_ems_dbf_job

        from mab_agents import ems

        with tempfile.TemporaryDirectory() as tmp:
            env = write_ems_dbf_job(Path(tmp))
            schema = ems.describe_job(env)
            self.assertEqual(set(schema) >= {"env", "ad1", "ad2", "veh", "lin", "ttl", "pfl"}, True)
            names = {f["name"] for f in schema["lin"]}
            self.assertIn("PART_TYPE", names)
            self.assertIn("MOD_LBR_TY", names)
            dec = {f["name"]: f for f in schema["ttl"]}["G_TTL_AMT"]
            self.assertEqual((dec["type"], dec["length"], dec["decimals"]), ("N", 10, 2))
            with self.assertRaises(ValueError):
                ems.describe_job(Path(tmp) / "nope.lin")

    def test_used_parts_invoice_finding_only_in_md(self):
        lines = [
            EstimateLine(line=1, description="Front door shell", operation="replace", part_type="recycled", part_price=400, labor_hours=3.0),
            EstimateLine(line=2, description="Front door shell", operation="refinish", paint_hours=2.5),
        ]
        md = {f.rule for f in estimate_tools.audit_estimate(lines, state="MD")}
        pa = {f.rule for f in estimate_tools.audit_estimate(lines, state="PA")}
        self.assertIn("md_used_parts_disclosure", md)
        self.assertNotIn("md_used_parts_disclosure", pa)
        oem = [EstimateLine(line=1, description="Front door shell", operation="replace", part_type="oem", part_price=900, labor_hours=3.0)]
        self.assertNotIn("md_used_parts_disclosure", {f.rule for f in estimate_tools.audit_estimate(oem, state="MD")})

    def test_diminished_value_script_and_checklists(self):
        s = frontdesk_tools.phone_script("is my car worth less now")
        self.assertEqual(s["situation"], "diminished_value")
        self.assertTrue(any("not" in n.lower() or "never" in n.lower() or "quote" in n.lower() for n in s["never"]))
        self.assertTrue(any("14-1008" in item for item in frontdesk_tools.CHECKIN_CHECKLIST))
        self.assertTrue(any("14-1004" in item for item in frontdesk_tools.DELIVERY_CHECKLIST))
        self.assertTrue(any("14-1003" in item for item in frontdesk_tools.DELIVERY_CHECKLIST))

    def test_mia_contact_has_complaint_channels(self):
        c = ar_tools.MIA_CONTACT
        self.assertIn("@", c["email"])
        self.assertIn("31.15.07", c["claim_clocks"])
        self.assertTrue(c["portal"].startswith("https://"))

    def test_sources_toml_lists_second_sweep_documents(self):
        import tomllib

        with open(config.PKG_DIR / "data" / "knowledge" / "sources.toml", "rb") as fh:
            urls = {s["url"] for s in tomllib.load(fh)["source"]}
        for needle in ("section=14-1003", "section=16-203", "comar/31.15.12.02", "comar/03.06.01.03", "comar/26.11.19.23",
                       "24-8-Statutorily-Required", "AddRemoveEMSPaths.pdf", "limitedpartsmessages", "ASA-Not-Included-Chart", "jhennsys.com"):
            self.assertTrue(any(needle in u for u in urls), needle)
