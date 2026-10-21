"""Printed Maryland paperwork: which forms an RO needs, what they say, and how they are served."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from mab_agents import cli, config, web
from mab_agents.agents.base import Ctx
from mab_agents.models import Customer, EstimateLine, RepairOrder, Vehicle
from mab_agents.store import Store
from mab_agents.tools import forms_tools

TODAY = date(2026, 10, 21)


def shop():
    return config.load_shop(config.EXAMPLE)


def rates():
    return {k: float(v) for k, v in shop()["rates"].items()}


def make_ro(**over) -> RepairOrder:
    ro = RepairOrder(
        ro_number="26-9001",
        customer=Customer(name='Dana <script>alert("x")</script> O\'Neil', phone="4105550101", email="dana@example.com"),
        vehicle=Vehicle(year=2021, make="Toyota", model="RAV4", vin="2T3P1RFV5MC000001", color="Gray", mileage=41230),
        insurer="Example Mutual", claim_number="EM-77", adjuster="Chris Park", deductible=500.0, stage="parts_ordered",
        opened="2026-10-15", dropped_off="2026-10-20", promised="2026-10-30", plate="ABC123", loss_date="2026-10-12",
        original_estimate_total=4000.0,
        lines=[
            EstimateLine(line=1, operation="replace", description="Front bumper cover", part_type="aftermarket", part_number="52119-0R918", part_price=412.5, labor_hours=2.1),
            EstimateLine(line=2, operation="refinish", description="Front bumper cover", paint_hours=2.8),
            EstimateLine(line=3, operation="replace", description="LT headlamp assembly", part_type="recycled", part_number="81150-0R160", part_price=380.0, labor_hours=0.5),
            EstimateLine(line=4, operation="replace", description="Hood", part_type="oem", part_number="53301-0R130", part_price=610.0, labor_hours=1.2),
            EstimateLine(line=5, operation="repair", description="LT fender", labor_hours=2.0, paint_hours=2.0),
        ],
    )
    for k, v in over.items():
        setattr(ro, k, v)
    return ro


def oem_hood():
    return [EstimateLine(line=1, operation="replace", description="Hood", part_type="oem", part_number="53301-0R130", part_price=610.0, labor_hours=1.2)]


class FormDataTests(unittest.TestCase):
    def test_required_and_due_now_track_the_estimate_and_stage(self):
        needed = {f["kind"]: f for f in forms_tools.required_forms(make_ro(), shop(), TODAY)}
        self.assertEqual(set(needed), set(forms_tools.FORM_KINDS))
        self.assertTrue(needed["authorization"]["required"] and needed["authorization"]["due_now"])
        self.assertTrue(needed["aftermarket"]["required"] and needed["aftermarket"]["due_now"])
        self.assertIn("1 aftermarket part", needed["aftermarket"]["why"])
        self.assertFalse(needed["supplement_consent"]["required"])
        self.assertTrue(needed["invoice_addendum"]["required"])
        self.assertFalse(needed["invoice_addendum"]["due_now"])               # not at delivery yet
        self.assertIn("1 used or rebuilt", needed["invoice_addendum"]["why"])
        self.assertEqual(needed["aftermarket"]["url"], "/forms/26-9001/aftermarket")

        # all-OEM crash parts: 14-2302(a) still wants the list, before parts arrive
        needed = {f["kind"]: f for f in forms_tools.required_forms(make_ro(lines=oem_hood()), shop(), TODAY)}
        self.assertTrue(needed["aftermarket"]["required"] and needed["aftermarket"]["due_now"])
        self.assertIn("all genuine", needed["aftermarket"]["why"])
        later = {f["kind"]: f for f in forms_tools.required_forms(make_ro(lines=oem_hood(), stage="paint"), shop(), TODAY)}
        self.assertTrue(later["aftermarket"]["required"])
        self.assertFalse(later["aftermarket"]["due_now"])
        self.assertFalse(later["authorization"]["due_now"])          # in paint: signed long ago or overdue, not "print now"

        # no crash parts at all
        lamp = [EstimateLine(line=1, operation="replace", description="LT headlamp assembly", part_type="oem", part_price=380.0)]
        self.assertFalse({f["kind"]: f for f in forms_tools.required_forms(make_ro(lines=lamp), shop(), TODAY)}["aftermarket"]["required"])

        ready = {f["kind"]: f for f in forms_tools.required_forms(make_ro(stage="ready"), shop(), TODAY)}
        self.assertTrue(ready["invoice_addendum"]["due_now"])
        with_supp = make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 480.0, "gross": 4600.0}])
        self.assertTrue({f["kind"]: f for f in forms_tools.required_forms(with_supp, shop(), TODAY)}["supplement_consent"]["due_now"])

    def test_crash_part_classifier(self):
        yes = ["Front bumper cover", "Front grille", "RT fender", "Radiator support", "LT front door shell", "Rear body panel", "Hood", "LT quarter panel", "Roof panel"]
        no = ["LT headlamp assembly", "A/C condenser", "Door mirror", "Hood latch", "Shock absorber", "Fuel rail", "Door glass", "Front wheel", "Bumper cover sensor", "Hood insulator"]
        for d in yes:
            self.assertTrue(forms_tools.is_crash_part(d), d)
        for d in no:
            self.assertFalse(forms_tools.is_crash_part(d), d)

    def test_aftermarket_statement_only_for_aftermarket_crash_parts(self):
        d = forms_tools.aftermarket_data(make_ro(), shop(), TODAY)
        by_line = {p["line"]: p for p in d["parts"]}
        self.assertEqual(set(by_line), {1, 3, 4})                   # repair and refinish lines carry no part
        self.assertTrue(by_line[1]["crash_part"] and by_line[1]["genuine"] is False)
        self.assertFalse(by_line[3]["crash_part"])
        self.assertTrue(by_line[3]["genuine"])                       # used OEM is still a genuine crash part (14-2301(e))
        self.assertTrue(d["statement_required"])
        self.assertTrue(d["statement"].startswith("THIS ESTIMATE HAS BEEN PREPARED BASED ON THE USE OF AFTERMARKET CRASH PARTS"))
        self.assertEqual(d["statement"], d["statement"].upper())    # 14-2302(c): capitals
        # an aftermarket headlamp is not a crash part: list it, do not print the statement
        lamp = [EstimateLine(line=1, operation="replace", description="LT headlamp assembly", part_type="aftermarket", part_price=210.0)]
        none = forms_tools.aftermarket_data(make_ro(lines=lamp), shop(), TODAY)
        self.assertFalse(none["statement_required"])
        self.assertEqual(none["statement"], "")
        # an aftermarket part the classifier cannot place still prints the statement (over-disclosure is safe)
        odd = [EstimateLine(line=1, operation="replace", description="Widget assembly", part_type="aftermarket", part_price=50.0)]
        self.assertTrue(forms_tools.aftermarket_data(make_ro(lines=odd), shop(), TODAY)["statement_required"])
        recon = [EstimateLine(line=1, operation="replace", description="Front bumper cover", part_type="reconditioned", part_price=150.0)]
        rd = forms_tools.aftermarket_data(make_ro(lines=recon), shop(), TODAY)
        self.assertIsNone(rd["parts"][0]["genuine"])
        self.assertEqual(len(rd["needs_origin"]), 1)

    def test_authorization_uses_the_written_estimate_and_payer_terms(self):
        d = forms_tools.authorization_data(make_ro(), shop(), rates(), TODAY)
        self.assertEqual(d["customer_rights_heading"], "Customer’s Rights")
        self.assertEqual(len(d["customer_rights"]), 4)
        self.assertTrue(any("ten percent" in r for r in d["customer_rights"]))
        self.assertEqual((d["estimate"]["total"], d["estimate"]["total_source"]), (4000.0, "written"))
        self.assertEqual(d["estimate"]["completion_date"], "2026-10-30")
        self.assertEqual(d["deductible"], 500.0)
        self.assertEqual(d["adjuster"], "Chris Park")
        self.assertEqual(d["loss_date"], "2026-10-12")
        self.assertTrue(any("direct my insurance company" in t for t in d["terms"]))
        self.assertTrue(any("teardown fee of $250.00" in t for t in d["terms"]))
        self.assertTrue(any("Storage: $65.00 per day" in t for t in d["terms"]))
        self.assertTrue(any(s.startswith("Teardown fee") for s in d["estimate"]["surcharges"]))
        self.assertEqual(d["title"], "Repair authorization and direction to pay")

        ems = forms_tools.authorization_data(make_ro(estimate_totals={"gross": 4120.0}), shop(), rates(), TODAY)
        self.assertEqual((ems["estimate"]["total"], ems["estimate"]["total_source"]), (4120.0, "estimate"))
        cash = forms_tools.authorization_data(make_ro(insurer="", deductible=500.0, original_estimate_total=0.0), shop(), rates(), TODAY)
        self.assertEqual(cash["insurer"], "Customer pay")
        self.assertEqual(cash["deductible"], 0.0)
        self.assertEqual(cash["title"], "Repair authorization")
        self.assertEqual(cash["estimate"]["total_source"], "lines")
        self.assertFalse(any("insurance company" in t for t in cash["terms"]))
        self.assertTrue(any("customer-pay repair" in t for t in cash["terms"]))
        blank = forms_tools.authorization_data(make_ro(lines=[], original_estimate_total=0.0), shop(), rates(), TODAY)
        self.assertEqual(blank["estimate"]["total_source"], "none")

    def test_supplement_consent_baseline_and_ten_percent(self):
        ro = make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": "2026-10-24", "amount": 480.0, "gross": 4480.0}],
                     customer_pay_items=[{"description": "Pinstripe", "amount": 60.0, "reason": "customer requested"}])
        sc = forms_tools.supplement_consent_data(ro, shop(), rates(), TODAY)
        self.assertEqual(sc["original_estimate_total"], 4000.0)
        self.assertEqual(sc["current_total"], 4540.0)                 # written + supplements + customer-pay
        self.assertTrue(sc["over_ten_percent"])
        # first import was a supplement: baseline is the pre-supplement total, not the supplement's gross
        ro2 = make_ro(original_estimate_total=0.0, estimate_totals={"gross": 4480.0},
                      supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 480.0, "gross": 4480.0}])
        sc2 = forms_tools.supplement_consent_data(ro2, shop(), rates(), TODAY)
        self.assertEqual(sc2["original_estimate_total"], 4000.0)
        self.assertEqual(sc2["current_total"], 4480.0)
        self.assertTrue(sc2["over_ten_percent"])
        small = forms_tools.supplement_consent_data(make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 100.0, "gross": 4100.0}]), shop(), rates(), TODAY)
        self.assertFalse(small["over_ten_percent"])
        # no supplements and no written estimate: nothing is flagged
        none = forms_tools.supplement_consent_data(make_ro(original_estimate_total=0.0), shop(), rates(), TODAY)
        self.assertEqual(none["original_estimate_total"], 0.0)
        self.assertFalse(none["over_ten_percent"])

    def test_invoice_addendum_splits_money_by_payer(self):
        ro = make_ro(stage="ready", estimate_totals={"gross": 4600.0},
                     payments=[{"date": "2026-10-28", "payer": "Example Mutual", "amount": 2000.0, "method": "eft", "reference": "1"}],
                     customer_pay_items=[{"description": "Pinstripe", "amount": 60.0, "reason": "customer requested"}])
        inv = forms_tools.invoice_addendum_data(ro, shop(), rates(), TODAY)
        self.assertEqual([p["line"] for p in inv["used_parts"]], [3])
        m = inv["money"]
        self.assertEqual((m["invoice_total"], m["paid"], m["balance"]), (4600.0, 2000.0, 2600.0))
        self.assertEqual(m["expected_from_customer"], 560.0)
        self.assertEqual(m["expected_from_carrier"], 4040.0)
        self.assertEqual(m["carrier_short"], 2040.0)
        self.assertEqual(m["customer_balance"], 560.0)
        self.assertIn("Limited lifetime", inv["warranty"])


class RenderTests(unittest.TestCase):
    def test_every_form_renders_escaped_html(self):
        ro = make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 480.0, "gross": 4600.0}])
        for kind in forms_tools.FORM_KINDS:
            page = forms_tools.render(kind, ro, shop(), rates(), TODAY)
            self.assertTrue(page.startswith("<!doctype html>"), kind)
            self.assertNotIn("<script>alert", page, kind)              # customer name is escaped in the body ...
            self.assertIn("&lt;script&gt;", page, kind)
            self.assertNotIn("</style><", page.split("<style>")[1].split("</style>")[0], kind)   # ... and in the @page footer string
            self.assertIn("@bottom-left { content: \"RO 26-9001", page, kind)
            self.assertIn("counter(pages)", page, kind)
            self.assertIn('class="ident">RO 26-9001', page, kind)      # ident line above every signature block
            self.assertIn("Mullikin", page, kind)
            self.assertIn("October 21, 2026", page, kind)
            self.assertIn("window.print()", page, kind)

    def test_css_string_neutralises_style_breakouts(self):
        evil = 'x"} </style><script>alert(1)</script>'
        out = forms_tools._css_string(evil)
        for ch in '"<>}':
            self.assertNotIn(ch, out)                      # only hex escapes remain; "/" alone cannot close a string or the style block
        self.assertNotIn("</style>", out)

    def test_authorization_page_layout(self):
        page = forms_tools.render("authorization", make_ro(), shop(), rates(), TODAY)
        rights = page.index("Customer’s Rights")
        signature = page.index("Customer signature")
        self.assertLess(rights, signature)                              # 14-1008(b): rights immediately above the signature line
        self.assertIn('<div class="signblock"><div class="rights">', page)   # and kept on one page together
        self.assertNotIn("text-transform: uppercase", page)             # heading printed as the statute prints it
        self.assertIn("may not be responsible for damage", page)       # 14-1002 / 14-1008(e) notice
        self.assertIn("Manufacturer Special Policy Adjustment Programs", page)
        self.assertIn("National Highway Traffic Safety Administration (N.H.T.S.A.)", page)
        self.assertIn("towed to the shop or left when the shop was not open", page)
        self.assertIn("teardown fee of $250.00", page)
        self.assertIn("Storage: $65.00 per day", page)
        self.assertIn("Vehicle received", page)
        self.assertIn("Date of loss", page)
        self.assertIn("Chris Park", page)
        self.assertIn("I want my replaced parts returned", page)
        self.assertIn("Example Mutual", page)
        nodeduct = forms_tools.render("authorization", make_ro(deductible=0.0), shop(), rates(), TODAY)
        self.assertIn("not on file: confirm with the adjuster", nodeduct)
        cash = forms_tools.render("authorization", make_ro(insurer="", original_estimate_total=0.0, promised=None), shop(), rates(), TODAY)
        self.assertNotIn("Deductible", cash)
        self.assertNotIn("insurance company", cash)
        self.assertIn("customer-pay repair", cash)
        self.assertIn("entered by the estimator before signature", cash)
        self.assertIn("at posted rates", cash)

    def test_aftermarket_page_has_ten_point_capitals_only_when_needed(self):
        page = forms_tools.render("aftermarket", make_ro(), shop(), rates(), TODAY)
        self.assertRegex(page, r'<div class="statement">THIS ESTIMATE HAS BEEN PREPARED')
        self.assertIn(".statement { font: 10pt", page)
        self.assertIn("table.list.caps th { font-size: 10pt; }", page)
        self.assertIn("AFTERMARKET (NON-OEM)", page)                    # 14-2302(c): the list is in capitals
        self.assertIn("GENUINE OEM (USED / RECYCLED)", page)
        self.assertIn("FRONT BUMPER COVER", page)
        none = forms_tools.render("aftermarket", make_ro(lines=oem_hood()), shop(), rates(), TODAY)
        self.assertNotIn('class="statement"', none)
        self.assertIn("No aftermarket crash parts", none)
        lamp = forms_tools.render("aftermarket", make_ro(lines=[EstimateLine(line=1, operation="replace", description="LT headlamp assembly", part_type="aftermarket", part_price=210.0)]), shop(), rates(), TODAY)
        self.assertNotIn('class="statement"', lamp)
        self.assertIn("are not crash parts", lamp)
        recon = forms_tools.render("aftermarket", make_ro(lines=[EstimateLine(line=1, operation="replace", description="Front bumper cover", part_type="reconditioned", part_price=150.0)]), shop(), rates(), TODAY)
        self.assertIn("GENUINE [ ]", recon)
        self.assertIn("ESTIMATOR MARKS GENUINE OR AFTERMARKET", recon)

    def test_invoice_addendum_discloses_used_parts_and_shows_the_customer_their_number(self):
        page = forms_tools.render("invoice_addendum", make_ro(stage="ready", estimate_totals={"gross": 4600.0}), shop(), rates(), TODAY)
        self.assertIn("LT headlamp assembly", page)
        self.assertIn("Genuine OEM (used / recycled)", page)
        self.assertIn("Customer took the replaced parts", page)
        self.assertIn("under a warranty agreement (Com. Law 14-1004(b))", page)
        self.assertNotIn("warranty or exchange", page)
        self.assertIn("Core-exchange part tendered", page)
        self.assertIn("Customer balance due", page)
        self.assertIn("Example Mutual portion", page)
        self.assertNotIn("Balance due</td>", page)                      # the insurer's share is not headlined to the customer
        cash = forms_tools.render("invoice_addendum", make_ro(insurer="", stage="ready"), shop(), rates(), TODAY)
        self.assertIn("Balance due", cash)
        clean = forms_tools.render("invoice_addendum", make_ro(lines=oem_hood()), shop(), rates(), TODAY)
        self.assertIn("No used, rebuilt or reconditioned parts", clean)

    def test_bad_dates_are_escaped_once(self):
        page = forms_tools.render("supplement_consent", make_ro(supplements=[{"supp_no": "01", "submitted": "soon & <b>", "approved": None, "amount": 1.0, "gross": 4001.0}]), shop(), rates(), TODAY)
        self.assertIn("soon &amp; &lt;b&gt;", page)
        self.assertNotIn("&amp;amp;", page)

    def test_write_forms_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = forms_tools.write_forms(make_ro(ro_number="26/90 01"), shop(), rates(), tmp, today=TODAY)
            self.assertEqual(len(paths), 4)
            self.assertTrue(paths[0].name.startswith("26_90_01-"))
            st = Store(Path(tmp) / "t.db"); st.load_sample(today=TODAY)
            old = dict(os.environ); os.environ["MAB_DB"] = str(Path(tmp) / "t.db")
            try:
                rc = cli.main(["--today", TODAY.isoformat(), "forms", "26-1042", "--out", str(Path(tmp) / "out")])
                rc_all = cli.main(["--today", TODAY.isoformat(), "forms", "26-1033", "--all", "--out", str(Path(tmp) / "all")])
            finally:
                os.environ.clear(); os.environ.update(old)
            self.assertEqual((rc, rc_all), (0, 0))
            written = sorted(p.name for p in (Path(tmp) / "out").iterdir())
            self.assertIn("26-1042-authorization.html", written)        # parts ordered: still due
            self.assertIn("26-1042-aftermarket.html", written)           # one aftermarket crash part
            self.assertNotIn("26-1042-invoice_addendum.html", written)   # not at delivery yet
            self.assertNotIn("26-1042-supplement_consent.html", written)
            self.assertEqual(len(list((Path(tmp) / "all").iterdir())), 4)


class FormWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        st = Store(Path(cls.tmp.name) / "t.db"); st.load_sample(today=TODAY)
        ctx = Ctx(shop=shop(), store=st, today=TODAY, kb_path=str(Path(cls.tmp.name) / "kb.db"))
        cls.token = "test-token-0123456789"
        cls.httpd = web.serve(ctx, host="127.0.0.1", port=0, token=cls.token)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.tmp.cleanup()

    def _open(self, path, token=True):
        headers = {"X-MAB-Token": self.token} if token else {}
        return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers))

    def test_form_pages_and_listing(self):
        with self._open("/forms/26-1042/authorization") as r:
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn("default-src 'none'", r.headers["Content-Security-Policy"])
            body = r.read().decode()
        self.assertIn("Customer’s Rights", body)
        with self._open("/api/forms/26-1042") as r:
            forms = json.loads(r.read())
        self.assertEqual([f["kind"] for f in forms], list(forms_tools.FORM_KINDS))
        self.assertTrue(all("due_now" in f for f in forms))
        with self._open("/api/ro/26-1042") as r:
            self.assertIn("forms", json.loads(r.read()))
        for bad in ("/forms/26-1042/nope", "/forms/no-such-ro/authorization", "/forms/26-1042"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._open(bad)
            self.assertEqual(cm.exception.code, 404, bad)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._open("/forms/26-1042/authorization", token=False)
        self.assertEqual(cm.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
