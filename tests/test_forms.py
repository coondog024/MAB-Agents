"""Printed Maryland paperwork: which forms an RO needs, what they say, and how they are served."""
import json
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
        insurer="Example Mutual", claim_number="EM-77", deductible=500.0, stage="parts_ordered",
        opened="2026-10-15", dropped_off="2026-10-20", promised="2026-10-30", plate="ABC123",
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


class FormDataTests(unittest.TestCase):
    def test_required_forms_track_the_estimate(self):
        needed = {f["kind"]: f for f in forms_tools.required_forms(make_ro(), shop(), TODAY)}
        self.assertEqual(set(needed), set(forms_tools.FORM_KINDS))
        self.assertTrue(needed["authorization"]["required"])
        self.assertTrue(needed["aftermarket"]["required"])
        self.assertIn("1 aftermarket part", needed["aftermarket"]["why"])
        self.assertFalse(needed["supplement_consent"]["required"])
        self.assertIn("1 used or rebuilt", needed["invoice_addendum"]["why"])
        self.assertEqual(needed["aftermarket"]["url"], "/forms/26-9001/aftermarket")

        plain = make_ro(lines=[EstimateLine(line=1, operation="replace", description="Hood", part_type="oem", part_price=610.0)])
        needed = {f["kind"]: f for f in forms_tools.required_forms(plain, shop(), TODAY)}
        self.assertFalse(needed["aftermarket"]["required"])
        self.assertIn("no aftermarket parts", needed["aftermarket"]["why"].lower())

        with_supp = make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 480.0, "gross": 4600.0}])
        self.assertTrue({f["kind"]: f for f in forms_tools.required_forms(with_supp, shop(), TODAY)}["supplement_consent"]["required"])

    def test_crash_parts_and_aftermarket_statement(self):
        d = forms_tools.aftermarket_data(make_ro(), shop(), TODAY)
        by_line = {p["line"]: p for p in d["parts"]}
        self.assertEqual(set(by_line), {1, 3, 4})                   # repair and refinish lines carry no part
        self.assertTrue(by_line[1]["crash_part"])                    # bumper cover
        self.assertFalse(by_line[3]["crash_part"])                   # headlamp is not a crash part
        self.assertTrue(forms_tools.is_crash_part("Front grille"))    # 14-2301 lists grills, firewalls, roofs, wheelwells
        self.assertFalse(forms_tools.is_crash_part("A/C condenser"))
        self.assertTrue(by_line[4]["genuine"])
        self.assertTrue(d["statement_required"])
        self.assertTrue(d["statement"].startswith("THIS ESTIMATE HAS BEEN PREPARED BASED ON THE USE OF AFTERMARKET CRASH PARTS"))
        self.assertEqual(d["statement"], d["statement"].upper())    # 14-2302: capitals
        none = forms_tools.aftermarket_data(make_ro(lines=[EstimateLine(line=1, operation="replace", description="Hood", part_type="oem")]), shop(), TODAY)
        self.assertFalse(none["statement_required"])
        self.assertEqual(none["statement"], "")

    def test_authorization_carries_rights_notices_and_storage(self):
        d = forms_tools.authorization_data(make_ro(), shop(), rates(), TODAY)
        self.assertEqual(d["customer_rights_heading"], "Customer\u2019s Rights")
        self.assertEqual(len(d["customer_rights"]), 4)
        self.assertTrue(any("ten percent" in r for r in d["customer_rights"]))
        self.assertTrue(any("replaced parts" in r for r in d["customer_rights"]))
        self.assertTrue(any("Storage: $65.00 per day" in t for t in d["terms"]))
        self.assertEqual(d["deductible"], 500.0)
        self.assertGreater(d["estimate"]["total"], 0)
        self.assertEqual(d["estimate"]["completion_date"], "2026-10-30")
        cash = forms_tools.authorization_data(make_ro(insurer="", deductible=500.0), shop(), rates(), TODAY)
        self.assertEqual(cash["insurer"], "Customer pay")
        self.assertEqual(cash["deductible"], 0.0)

    def test_invoice_addendum_and_supplement_consent_numbers(self):
        ro = make_ro(stage="ready", payments=[{"date": "2026-10-28", "payer": "Example Mutual", "amount": 2000.0, "method": "eft", "reference": "1"}],
                     customer_pay_items=[{"description": "Pinstripe", "amount": 60.0, "reason": "customer requested"}],
                     supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": "2026-10-24", "amount": 480.0, "gross": 4600.0}],
                     original_estimate_total=4000.0, estimate_totals={"gross": 4600.0})
        inv = forms_tools.invoice_addendum_data(ro, shop(), rates(), TODAY)
        self.assertEqual([p["line"] for p in inv["used_parts"]], [3])
        self.assertEqual(inv["money"]["invoice_total"], 4600.0)
        self.assertEqual(inv["money"]["paid"], 2000.0)
        self.assertEqual(inv["money"]["balance"], 2600.0)
        self.assertEqual(inv["money"]["expected_from_customer"], 560.0)
        self.assertIn("Limited lifetime", inv["warranty"])
        sc = forms_tools.supplement_consent_data(ro, shop(), rates(), TODAY)
        self.assertEqual(sc["supplements"][0]["supp_no"], "01")
        self.assertTrue(sc["over_ten_percent"])                      # 4600 > 4000 * 1.10
        self.assertEqual(sc["customer_pay_items"][0]["amount"], 60.0)


class RenderTests(unittest.TestCase):
    def test_every_form_renders_escaped_html(self):
        ro = make_ro(supplements=[{"supp_no": "01", "submitted": "2026-10-22", "approved": None, "amount": 480.0, "gross": 4600.0}])
        for kind in forms_tools.FORM_KINDS:
            page = forms_tools.render(kind, ro, shop(), rates(), TODAY)
            self.assertTrue(page.startswith("<!doctype html>"), kind)
            self.assertNotIn("<script>alert", page, kind)              # customer name is escaped
            self.assertIn("&lt;script&gt;", page, kind)
            self.assertIn("Mullikin", page, kind)
            self.assertIn("26-9001", page, kind)
            self.assertIn("October 21, 2026", page, kind)
            self.assertIn("window.print()", page, kind)

    def test_authorization_page_layout(self):
        page = forms_tools.render("authorization", make_ro(), shop(), rates(), TODAY)
        rights = page.index("Customer\u2019s Rights")
        signature = page.index("Customer signature")
        self.assertLess(rights, signature)                              # 14-1008: rights immediately above the signature line
        self.assertIn("may not be responsible for damage", page)       # 14-1002 / 14-1008(e) notice
        self.assertIn("Manufacturer Special Policy Adjustment Programs", page)
        self.assertIn("National Highway Traffic Safety Administration (N.H.T.S.A.)", page)
        self.assertIn("towed to the shop or left when the shop was not open", page)
        self.assertIn("Storage: $65.00 per day", page)
        self.assertIn("Example Mutual", page)

    def test_aftermarket_page_has_ten_point_statement_only_when_needed(self):
        page = forms_tools.render("aftermarket", make_ro(), shop(), rates(), TODAY)
        self.assertIn('class="statement"', page)
        self.assertIn("font: 10pt", page)
        self.assertIn("AFTERMARKET (NON-OEM)", page)                    # 14-2302(c): the list is in capitals
        self.assertIn("USED / RECYCLED OEM", page)
        self.assertIn("FRONT BUMPER COVER", page)
        none = forms_tools.render("aftermarket", make_ro(lines=[EstimateLine(line=1, operation="replace", description="Hood", part_type="oem")]), shop(), rates(), TODAY)
        self.assertNotIn('class="statement"', none)
        self.assertIn("No aftermarket crash parts", none)

    def test_invoice_addendum_discloses_used_parts(self):
        page = forms_tools.render("invoice_addendum", make_ro(stage="ready"), shop(), rates(), TODAY)
        self.assertIn("LT headlamp assembly", page)
        self.assertIn("USED / recycled OEM", page)
        self.assertIn("Customer took the replaced parts", page)
        self.assertIn("Balance due", page)
        clean = forms_tools.render("invoice_addendum", make_ro(lines=[EstimateLine(line=1, operation="replace", description="Hood", part_type="oem")]), shop(), rates(), TODAY)
        self.assertIn("No used, rebuilt or reconditioned parts", clean)

    def test_write_forms_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = forms_tools.write_forms(make_ro(ro_number="26/90 01"), shop(), rates(), tmp, today=TODAY)
            self.assertEqual(len(paths), 4)
            self.assertTrue(all(p.exists() and "/" not in p.name.replace(tmp, "") for p in paths))
            self.assertTrue(paths[0].name.startswith("26_90_01-"))
            st = Store(Path(tmp) / "t.db"); st.load_sample(today=TODAY)
            import os
            old = dict(os.environ); os.environ["MAB_DB"] = str(Path(tmp) / "t.db")
            try:
                rc = cli.main(["--today", TODAY.isoformat(), "forms", "26-1042", "--out", str(Path(tmp) / "out")])
            finally:
                os.environ.clear(); os.environ.update(old)
            self.assertEqual(rc, 0)
            written = sorted(p.name for p in (Path(tmp) / "out").iterdir())
            self.assertIn("26-1042-authorization.html", written)
            self.assertIn("26-1042-aftermarket.html", written)        # the sample RO has one aftermarket part
            self.assertNotIn("26-1042-supplement_consent.html", written)


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
        self.assertIn("Customer\u2019s Rights", body)
        with self._open("/api/forms/26-1042") as r:
            forms = json.loads(r.read())
        self.assertEqual([f["kind"] for f in forms], list(forms_tools.FORM_KINDS))
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
