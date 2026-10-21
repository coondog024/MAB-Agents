import json
import unittest
from datetime import date
from pathlib import Path

from mab_agents import config
from mab_agents.models import EstimateLine, RepairOrder
from mab_agents.store import Store
from mab_agents.tools import adas_tools, customer_tools, estimate_tools, insurer_tools, kpi_tools, parts_tools, schedule_tools

TODAY = date(2026, 10, 21)
DATA = Path(__file__).resolve().parents[1] / "mab_agents" / "data"


def shop():
    return config.load_shop(config.EXAMPLE)


def store():
    s = Store(":memory:")
    s.load_sample(today=TODAY)
    return s


class EstimateAuditTests(unittest.TestCase):
    def setUp(self):
        self.lines = estimate_tools.parse_estimate_csv((DATA / "sample_estimate.csv").read_text())

    def test_parses_sample(self):
        self.assertEqual(len(self.lines), 8)
        self.assertEqual(self.lines[0].operation, "replace")
        self.assertEqual(self.lines[6].labor_type, "mechanical")

    def test_finds_common_misses(self):
        rules = {f.rule for f in estimate_tools.audit_estimate(self.lines, model_year=2021)}
        for expected in ("blend_adjacent", "feather_prime_block", "pre_repair_scan", "post_repair_scan", "ac_evacuate_recharge", "headlamp_aim", "alignment", "set_up_and_measure", "flex_additive", "cover_car"):
            self.assertIn(expected, rules, expected)

    def test_blend_names_the_right_neighbour(self):
        blends = [f for f in estimate_tools.audit_estimate(self.lines) if f.rule == "blend_adjacent"]
        suggested = " ".join(f.suggested_line for f in blends)
        self.assertIn("left fender", suggested)          # hood refinished, left fender untouched
        self.assertIn("right front door", suggested)     # right fender refinished
        self.assertNotIn("Blend right fender", suggested)  # right fender is itself refinished

    def test_clean_estimate_has_few_findings(self):
        ro = store().get("26-1047")
        rules = {f.rule for f in estimate_tools.audit_estimate(ro.lines, model_year=ro.vehicle.year)}
        self.assertNotIn("feather_prime_block", rules)
        self.assertNotIn("pre_repair_scan", rules)
        self.assertNotIn("cover_car", rules)
        self.assertNotIn("blend_adjacent", rules)

    def test_airbag_rules(self):
        rules = {f.rule for f in estimate_tools.audit_estimate(self.lines, airbags_deployed=True)}
        self.assertIn("seat_belts_after_deployment", rules)
        self.assertIn("occupant_classification", rules)

    def test_totals_use_shop_rates(self):
        t = estimate_tools.totals(self.lines, {k: float(v) for k, v in shop()["rates"].items()})
        self.assertEqual(t["refinish_hours"], 8.4)
        self.assertAlmostEqual(t["parts"], 3271.5, places=2)
        self.assertGreater(t["total"], t["parts"])


class AdasTests(unittest.TestCase):
    def test_windshield_and_bumper(self):
        items = adas_tools.calibration_checklist(["windshield replace", "front bumper cover R&I"])
        systems = " ".join(i.system for i in items)
        self.assertIn("Forward-facing camera", systems)
        self.assertIn("Front radar", systems)
        self.assertNotIn("Blind spot", systems)

    def test_airbags_add_ocs(self):
        items = adas_tools.calibration_checklist(["hood"], airbags_deployed=True)
        self.assertTrue(any("Occupant" in i.system for i in items))

    def test_report_has_disclaimer(self):
        r = adas_tools.checklist_report(adas_tools.calibration_checklist(["alignment"]), vehicle="2022 CR-V")
        self.assertIn("OEM", r["disclaimer"])
        self.assertTrue(r["process"])


class PartsTests(unittest.TestCase):
    def test_safety_parts_stay_oem(self):
        line = EstimateLine(line=1, operation="replace", description="Front radar sensor", part_type="aftermarket")
        rule = config.carrier_rule(shop(), "Example Mutual")
        ptype, why = parts_tools.recommend_part_type(line, model_year=2015, carrier_rule=rule, today=TODAY)
        self.assertEqual(ptype, "oem")

    def test_structured_carrier_rule_decides_part_type(self):
        rule = config.carrier_rule(shop(), "Example Mutual")
        bumper = EstimateLine(line=1, operation="replace", description="Front bumper cover")
        self.assertEqual(parts_tools.recommend_part_type(bumper, model_year=2019, carrier_rule=rule, today=TODAY)[0], "aftermarket")
        self.assertEqual(parts_tools.recommend_part_type(bumper, model_year=2026, carrier_rule=rule, today=TODAY)[0], "oem")   # too new
        # Sample Insurance allows aftermarket on bumper covers and lamps only: a fender stays OEM even on an old truck
        sample = config.carrier_rule(shop(), "Sample Insurance")
        fender = EstimateLine(line=2, operation="replace", description="RT fender")
        self.assertEqual(parts_tools.recommend_part_type(fender, model_year=2015, carrier_rule=sample, today=TODAY)[0], "oem")
        self.assertEqual(parts_tools.recommend_part_type(bumper, model_year=2015, carrier_rule=sample, today=TODAY)[0], "aftermarket")
        # free text alone never unlocks aftermarket
        loose = {"name": "Loose Co", "parts_preference": "aftermarket everything"}
        self.assertEqual(parts_tools.recommend_part_type(bumper, model_year=2010, carrier_rule=loose, today=TODAY)[0], "oem")

    def test_status_flags_late_parts(self):
        ro = store().get("26-1042")
        st = parts_tools.parts_status(ro, today=TODAY)
        self.assertFalse(st["all_received"])
        self.assertEqual({p["description"] for p in st["late"]}, {"RT headlamp assembly"})  # ETA yesterday, still 'ordered'
        self.assertEqual(st["latest_eta"], "2026-10-24")

    def test_rfq_email(self):
        ro = store().get("26-1051")
        parts = parts_tools.build_parts_order(ro, config.carrier_rule(shop(), ro.insurer), today=TODAY)
        self.assertEqual(len(parts), 3)
        mail = parts_tools.rfq_email(ro, parts, shop())
        self.assertIn("26-1051", mail["subject"])
        self.assertIn("04715-3A0-A90ZZ", mail["body"])


class ScheduleTests(unittest.TestCase):
    def test_stalled_detects_the_truck(self):
        s = schedule_tools.stalled(store().list(), 2, today=TODAY)
        self.assertIn("26-1047", [x["ro"] for x in s])
        self.assertNotIn("26-1051", [x["ro"] for x in s])

    def test_wip_board_counts(self):
        b = schedule_tools.wip_board(store().list(), today=TODAY)
        self.assertEqual(b["open_ros"], 3)
        self.assertEqual(b["in_production"], 1)

    def test_promise_date_respects_parts_eta(self):
        ro = store().get("26-1042")
        p = schedule_tools.promise_date(ro, parts_eta="2026-10-24", start=TODAY)
        self.assertGreater(date.fromisoformat(p["estimated_delivery"]), date(2026, 10, 24))
        self.assertLess(date.fromisoformat(p["estimated_delivery"]).weekday(), 5)

    def test_business_days_skip_weekends(self):
        self.assertEqual(schedule_tools.business_days_after(date(2026, 10, 23), 1), date(2026, 10, 26))  # Fri -> Mon


class CustomerTests(unittest.TestCase):
    def test_stage_update_fits_sms_and_uses_facts(self):
        ro = store().get("26-1042")
        d = customer_tools.stage_update(ro, shop())
        self.assertLessEqual(len(d["text"]), 300)
        self.assertIn("Dana", d["text"])
        self.assertIn("RAV4", d["text"])
        self.assertIn("Oct 24", d["text"])  # parts ETA

    def test_ready_mentions_deductible(self):
        ro = store().get("26-1051")
        d = customer_tools.stage_update(ro, shop(), "ready")
        self.assertIn("$1,000 deductible", d["text"])

    def test_delay_notice_never_claims_extension_was_requested(self):
        ro = store().get("26-1042")
        d = customer_tools.delay_notice(ro, shop(), "the bumper cover is on backorder", "2026-10-30")
        self.assertIn("rental", d["text"].lower())
        self.assertNotIn("we've asked", d["text"].lower())
        self.assertIn("Oct 30", d["text"])

    def test_delivered_text_uses_profile_warranty_only(self):
        sh = shop()
        ro = store().get("26-1033")
        with_w = customer_tools.stage_update(ro, sh, "delivered")["text"]
        self.assertIn("Limited lifetime warranty", with_w)
        sh["shop"]["warranty"] = ""
        without = customer_tools.stage_update(ro, sh, "delivered")["text"]
        self.assertNotIn("warranty", without.lower())

    def test_review_reply_guidelines(self):
        self.assertTrue(any("offline" in g for g in customer_tools.review_reply_guidelines(2)))


class InsurerTests(unittest.TestCase):
    def test_maryland_threshold(self):
        r = insurer_tools.total_loss_check(14200, 17500, "MD")
        self.assertEqual(r["threshold_pct"], 75)
        self.assertTrue(r["likely_total_loss"])
        self.assertIn("unverified", r["rule_source"])
        r = insurer_tools.total_loss_check(12000, 17500, "MD")
        self.assertFalse(r["likely_total_loss"])

    def test_shop_profile_rule_overrides_table(self):
        sh = shop()
        sh["shop"]["state"], sh["shop"]["total_loss_rule"] = "OH", "80"
        r = insurer_tools.total_loss_check(8500, 10000, "OH", shop=sh)
        self.assertEqual(r["threshold_pct"], 80.0)
        self.assertTrue(r["likely_total_loss"])
        self.assertIn("shop.toml", r["rule_source"])
        self.assertEqual(insurer_tools.total_loss_check(8500, 10000, "OH")["rule"], "TLF")   # table, no override

    def test_tlf_state_needs_salvage(self):
        r = insurer_tools.total_loss_check(9000, 12000, "CA")
        self.assertIsNone(r["likely_total_loss"])
        r = insurer_tools.total_loss_check(9000, 12000, "CA", salvage_value=3500)
        self.assertTrue(r["likely_total_loss"])

    def test_supplement_email_totals(self):
        ro = store().get("26-1042")
        items = [{"description": "Radiator support", "labor_hours": 4.5, "labor_type": "body", "paint_hours": 0, "part_price": 610, "justification": "Bent; photo 12"}]
        mail = insurer_tools.supplement_request(ro, shop(), items)
        self.assertIn("EM-2026-778812", mail["subject"])
        self.assertIn("Supplement #1", mail["subject"])
        self.assertEqual(mail["total"], f"{4.5 * 58 + 610:.2f}")
        self.assertIn("found on teardown", mail["body"])
        self.assertNotIn("per OEM repair procedures", mail["body"])
        cover = insurer_tools.supplement_request(ro, shop(), [{"description": "Cover car for overspray", "labor_hours": 0.5, "labor_type": "refinish"}], supplement_number=2)
        self.assertIn("not-included operation", cover["body"])
        self.assertIn("Supplement #2", cover["subject"])

    def test_storage_notice(self):
        n = insurer_tools.storage_notice(store().get("26-1042"), shop(), 4, today=TODAY)
        self.assertEqual(n["total_due"], 65 * 4 + 250)


class KpiTests(unittest.TestCase):
    def test_daily_brief(self):
        b = kpi_tools.daily_brief(store().list(include_delivered=True), shop(), today=TODAY)
        self.assertEqual(b["waiting_on_approval"], ["26-1051"])
        self.assertIn("26-1047", [s["ro"] for s in b["stalled"]])
        self.assertEqual(b["kpis"]["average_cycle_days"], 8.0)
        self.assertEqual(b["rentals_open"], 2)


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_stage_change(self):
        s = store()
        ro = s.set_stage("26-1047", "paint", author="test")
        self.assertEqual(ro.stage, "paint")
        self.assertEqual(s.get("26-1047").notes[-1].author, "test")
        with self.assertRaises(ValueError):
            s.set_stage("26-1047", "nonsense")
        with self.assertRaises(KeyError):
            s.require("99-9999")

    def test_ro_numbers_never_reused_and_insert_refuses_overwrite(self):
        from mab_agents.models import Customer, RepairOrder, Vehicle

        s = store()
        self.assertEqual(s.next_ro_number("26"), "26-1052")
        new = RepairOrder(ro_number="26-1033", customer=Customer(name="X"), vehicle=Vehicle(year=2020, make="Kia", model="Soul"))
        with self.assertRaises(ValueError):
            s.insert(new)
        self.assertEqual(s.get("26-1033").customer.name, "Ellen Ortiz")

    def test_concurrent_notes_are_not_lost(self):
        import tempfile
        import threading
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "c.db")
            s.load_sample(today=TODAY)
            before = len(s.get("26-1047").notes)

            def work(i):
                for k in range(10):
                    s.add_note("26-1047", f"t{i}-{k}")

            threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(len(s.get("26-1047").notes), before + 80)

    def test_sample_dates_rebased(self):
        s = Store(":memory:")
        s.load_sample(today=date(2027, 1, 1))
        self.assertEqual(s.get("26-1042").dropped_off, "2026-12-25")


if __name__ == "__main__":
    unittest.main()
