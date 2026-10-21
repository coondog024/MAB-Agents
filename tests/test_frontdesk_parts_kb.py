import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from pathlib import Path

from mab_agents import config, web
from mab_agents.agents.base import Ctx
from mab_agents.kb import KnowledgeBase, chunk_text
from mab_agents.store import Store
from mab_agents.tools import frontdesk_tools, parts_tools

TODAY = date(2026, 10, 21)
STARTER = Path(__file__).resolve().parents[1] / "mab_agents" / "data" / "knowledge"


def shop():
    return config.load_shop(config.EXAMPLE)


def store():
    s = Store(":memory:")
    s.load_sample(today=TODAY)
    return s


class PricingTests(unittest.TestCase):
    def test_oem_billed_at_list(self):
        r = parts_tools.price_part(400.0, "oem", shop())
        self.assertEqual(r["sell"], 400.0)
        self.assertEqual(r["cost"], 300.0)        # 25% dealer discount
        self.assertEqual(r["gross_profit"], 100.0)

    def test_aftermarket_markup_and_cap(self):
        r = parts_tools.price_part(400.0, "aftermarket", shop(), cost=189.0)
        self.assertEqual(r["sell"], round(189 * 1.35, 2))
        capped = parts_tools.price_part(400.0, "aftermarket", shop(), cost=189.0, max_markup=0.25)
        self.assertEqual(capped["sell"], round(189 * 1.25, 2))
        self.assertTrue(any("capped" in n for n in capped["notes"]))

    def test_non_oem_above_list_is_flagged(self):
        r = parts_tools.price_part(100.0, "aftermarket", shop(), cost=95.0)
        self.assertTrue(any("exceeds OEM list" in n for n in r["notes"]))

    def test_compare_quotes_counts_rental_days(self):
        quotes = [
            {"vendor": "Dealer", "part_type": "oem", "price": 412.50, "eta": 2},
            {"vendor": "Keystone", "part_type": "aftermarket", "price": 189.00, "eta": 5},
            {"vendor": "eBay", "part_type": "aftermarket", "price": 160.00, "eta": "2026-10-28"},
        ]
        r = parts_tools.compare_quotes(quotes, shop(), rental=True, today=TODAY)
        self.assertEqual(r["rental_cost_per_day_used"], 45.0)
        ebay = next(x for x in r["ranked"] if x["vendor"] == "eBay")
        self.assertEqual(ebay["days_to_arrive"], 7)
        self.assertEqual(ebay["total_cost_to_job"], 160 + 7 * 45)
        self.assertEqual(r["recommended"]["vendor"], "Keystone")   # 189 + 225 beats 412.5 + 90 and 160 + 315
        dealer = next(x for x in r["ranked"] if x["vendor"] == "Dealer")
        self.assertEqual(dealer["we_bill"], 550.0)                   # dealer net 412.50 at 25% off => list 550, billed at list
        self.assertGreater(dealer["gross_profit"], 0)
        self.assertIn("list assumed", dealer["notes"])
        safe = parts_tools.compare_quotes(quotes, shop(), rental=True, safety_part=True, today=TODAY)
        self.assertEqual([x["vendor"] for x in safe["ranked"]], ["Dealer"])
        self.assertEqual(len(safe["dropped"]), 2)

    def test_eta_parsing(self):
        f = parts_tools.parse_eta_days
        self.assertEqual(f(3, TODAY), 3)
        self.assertEqual(f("3", TODAY), 3)
        self.assertEqual(f("3 days", TODAY), 3)
        self.assertEqual(f("10/28", TODAY), 7)
        self.assertEqual(f("10/28/2026", TODAY), 7)
        self.assertEqual(f("2026-10-28", TODAY), 7)
        self.assertEqual(f("", TODAY), 0)
        self.assertIsNone(f("next week sometime", TODAY))
        r = parts_tools.compare_quotes([{"vendor": "x", "part_type": "oem", "price": 10, "eta": "next week sometime"}], shop(), today=TODAY)
        self.assertEqual(r["ranked"], [])
        self.assertIn("could not read ETA", r["dropped"][0]["why"])

    def test_no_rental_means_no_delay_cost(self):
        r = parts_tools.compare_quotes([{"vendor": "x", "part_type": "oem", "price": 10, "eta": 9}], shop(), rental=False, today=TODAY)
        self.assertEqual(r["ranked"][0]["delay_cost"], 0)


class FrontDeskTests(unittest.TestCase):
    def test_lookup_by_phone_digits_and_name(self):
        s = store()
        self.assertEqual([r.ro_number for r in s.find("410-555-0142")], ["26-1042"])
        self.assertEqual([r.ro_number for r in s.find("5550142")], ["26-1042"])
        self.assertEqual([r.ro_number for r in s.find("marcus")], ["26-1047"])
        self.assertEqual([r.ro_number for r in s.find("26-1051")], ["26-1051"])
        self.assertEqual(s.find("zzz"), [])

    def test_phone_script_exact_and_fuzzy(self):
        self.assertEqual(frontdesk_tools.phone_script("deductible")["situation"], "deductible")
        self.assertEqual(frontdesk_tools.phone_script("customer angry about delay")["situation"], "delay_complaint")
        self.assertEqual(frontdesk_tools.phone_script("tow truck arriving")["situation"], "tow_arrival")
        self.assertIn("error", frontdesk_tools.phone_script("quantum physics"))

    def test_payment_summary(self):
        s = store()
        insured = frontdesk_tools.payment_summary(s.get("26-1051"), shop())
        self.assertEqual(insured["customer_owes_at_pickup"], 1000.0)
        self.assertIn("deductible", insured["who_pays"])
        self.assertIn("before pickup", insured["say"])          # never 'exactly'
        cash = frontdesk_tools.payment_summary(s.get("26-1047"), shop(), storage_days=2)
        self.assertGreater(cash["customer_owes_at_pickup"], 1000)
        self.assertEqual(cash["storage"], 130.0)
        ro = s.get("26-1042")
        ro.customer_pay_items = [{"description": "tire betterment", "amount": 80, "reason": "betterment"}]
        extra = frontdesk_tools.payment_summary(ro, shop())
        self.assertEqual(extra["customer_owes_at_pickup"], 580.0)
        ro.deductible, ro.customer_pay_items = 0, []
        unknown = frontdesk_tools.payment_summary(ro, shop())
        self.assertIn("confirm", unknown["say"].lower())

    def test_steering_script_does_not_cite_law(self):
        s = frontdesk_tools.phone_script("steering")
        text = " ".join(s["say"]).lower()
        self.assertNotIn("every state", text)
        self.assertNotIn("law", text)
        self.assertIn("choose", text)

    def test_update_queue_prioritises(self):
        s = store()
        q = frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)
        ros = [x["ro"] for x in q]
        self.assertEqual(set(ros), {"26-1042", "26-1047", "26-1051"})   # nobody has been texted yet
        self.assertTrue(all("No message ever logged" in " ".join(x["reasons"]) for x in q))
        # Text one of them: it drops off unless another reason remains
        s.log_message("26-1047", "sms", "443", "hi", status="sent")
        q1 = frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)
        self.assertNotIn("26-1047", [x["ro"] for x in q1])              # texted today, promised in 2 days: nothing to do
        s.log_message("26-1047", "sms", "443", "draft only")          # a draft is not contact
        ro = s.set_stage("26-1047", "body")
        ro.stage_changed = "2026-10-21T23:30:00"                          # stage moved after the text
        s.upsert(ro)
        q2 = frontdesk_tools.update_queue(s.list(), s.last_messages(), today=TODAY)
        reasons = " ".join(next(x for x in q2 if x["ro"] == "26-1047")["reasons"])
        self.assertIn("Stage changed", reasons)
        rav = next(x for x in q if x["ro"] == "26-1042")
        self.assertTrue(rav["rental"])

    def test_checklists_exist(self):
        self.assertGreaterEqual(len(frontdesk_tools.CHECKIN_CHECKLIST), 6)
        self.assertGreaterEqual(len(frontdesk_tools.DELIVERY_CHECKLIST), 6)
        self.assertIn("opening", frontdesk_tools.DAILY_ROUTINE)


class KnowledgeBaseTests(unittest.TestCase):
    def test_chunking_overlaps_and_respects_size(self):
        text = "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(10))
        chunks = chunk_text(text, size=1500, overlap=200)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 1500 + 2 for c in chunks))
        self.assertTrue(chunks[1].startswith(chunks[0][-200:][:50]) or "Paragraph" in chunks[1])

    def test_sync_and_search_starter_docs(self):
        kb = KnowledgeBase(":memory:", STARTER)
        st = kb.sync()
        self.assertGreaterEqual(len(st["added"]), 2)
        hits = kb.search("why do I pay the deductible to the shop")
        self.assertTrue(hits)
        self.assertIn("front desk playbook", hits[0].title)
        self.assertIn("deductible", hits[0].text.lower())
        again = kb.sync()
        self.assertEqual(again["added"], [])
        self.assertEqual(again["unchanged"], len(kb.documents()))

    def test_sync_handles_pdf_and_removed_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pdf = root / "ppages.pdf"
            pdf.write_bytes(_tiny_pdf("Blend time is not included in refinish time for adjacent panels"))
            (root / "note.txt").write_text("Rental extensions must be requested before the covered days run out.")
            (root / "image.png").write_bytes(b"\x89PNG")
            kb = KnowledgeBase(":memory:", root)
            st = kb.sync()
            self.assertEqual(sorted(st["added"]), ["note.txt", "ppages.pdf"])
            self.assertEqual(st["skipped"], ["image.png"])
            self.assertIn("ppages.pdf", kb.search("blend not included")[0].path)
            self.assertEqual(kb.search("blend not included")[0].page, 1)
            (root / "note.txt").unlink()
            st2 = kb.sync()
            self.assertEqual(st2["removed"], ["note.txt"])
            self.assertEqual(kb.search("rental extensions"), [])

    def test_bad_query_characters_do_not_crash(self):
        kb = KnowledgeBase(":memory:", STARTER)
        kb.sync()
        self.assertIsInstance(kb.search('"unbalanced (quote OR'), list)
        self.assertEqual(kb.search("   "), [])

    def test_add_text_note_survives_sync(self):
        with tempfile.TemporaryDirectory() as d:
            kb = KnowledgeBase(":memory:", Path(d))
            n = kb.add_text("Example Mutual rental email", "Example Mutual covers rental for 30 days max per claim.")
            self.assertEqual(n, 1)
            self.assertIn("notes/", kb.search("rental 30 days")[0].path)
            self.assertTrue((Path(d) / "notes" / "example-mutual-rental-email.txt").exists())
            st = kb.sync()
            self.assertEqual(st["removed"], [])
            self.assertTrue(kb.search("rental 30 days"))

    def test_chunk_text_bad_config_terminates(self):
        self.assertTrue(chunk_text("x" * 5000, size=2200, overlap=2200))
        self.assertTrue(chunk_text("y" * 500, size=0, overlap=0))


def _tiny_pdf(text: str) -> bytes:
    """A minimal single-page PDF with one text run, enough for pypdf to extract."""
    stream = f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        st = Store(Path(cls.tmp.name) / "t.db")
        st.load_sample(today=TODAY)
        ctx = Ctx(shop=shop(), store=st, today=TODAY, kb_path=str(Path(cls.tmp.name) / "kb.db"))
        ctx.kb().docs_dir = STARTER
        ctx.kb().sync()
        cls.token = "test-token-0123456789"
        cls.httpd = web.serve(ctx, host="127.0.0.1", port=0, token=cls.token)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def _get(self, path, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers={"X-MAB-Token": token or self.token})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def _post(self, path, body, headers=None):
        h = {"Content-Type": "application/json", "X-MAB-Token": self.token}
        h.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(), headers=h, method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_index_and_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/?token={self.token}") as r:
            self.assertIn(b"Front Desk", r.read())
            self.assertIn("Max-Age", r.headers.get("Set-Cookie", ""))
        self.assertTrue(self._get("/api/health")["ok"])

    def test_token_required_even_on_localhost(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health")
        self.assertEqual(cm.exception.code, 401)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/")
        self.assertEqual(cm.exception.code, 401)
        self.assertIn("text/html", cm.exception.headers.get("Content-Type", ""))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/health", headers={"Cookie": f"mab_token={self.token}"})
        self.assertTrue(json.loads(urllib.request.urlopen(req).read())["ok"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/api/health", token="wrong-token-00000000")
        self.assertEqual(cm.exception.code, 401)

    def test_post_needs_json_content_type(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/note", {"ro": "26-1047", "text": "x"}, headers={"Content-Type": "text/plain"})
        self.assertEqual(cm.exception.code, 415)

    def test_queue_lookup_and_ro(self):
        self.assertTrue(self._get("/api/queue"))
        found = self._get("/api/lookup?q=dana")
        self.assertEqual(found[0]["ro"], "26-1042")
        ro = self._get("/api/ro/26-1042")
        self.assertEqual(ro["payment"]["customer_owes_at_pickup"], 500.0)

    def test_message_and_mark_sent(self):
        d = self._post("/api/message", {"ro": "26-1051", "kind": "stage"})
        self.assertIn("Priya", d["text"])
        with self.assertRaises(urllib.error.HTTPError) as cm:     # a draft id must belong to the RO
            self._post("/api/sent", {"ro": "26-1042", "id": d["id"], "text": d["text"]})
        self.assertEqual(cm.exception.code, 404)
        ok = self._post("/api/sent", {"ro": "26-1051", "id": d["id"], "text": d["text"]})
        self.assertTrue(ok["ok"])
        msgs = self._get("/api/ro/26-1051")["messages"]
        self.assertEqual(msgs[-1]["status"], "sent")

    def test_delay_text_moves_the_promised_date(self):
        d = self._post("/api/message", {"ro": "26-1047", "kind": "delay", "reason": "the painter is out sick", "date": "2026-10-27"})
        self.assertIn("Oct 27", d["text"])
        ro = self._get("/api/ro/26-1047")
        self.assertEqual(ro["promised"], "2026-10-27")
        self.assertIn("Promised", ro["notes"][-1]["text"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/message", {"ro": "26-1047", "kind": "delay", "reason": "", "date": "2026-10-27"})
        self.assertEqual(cm.exception.code, 400)

    def test_intake_creates_ro(self):
        r = self._post("/api/intake", {"customer_name": "Test Person", "phone": "555-0100", "year": 2020, "make": "Kia", "model": "Soul", "loss_description": "rear end", "insurer": "Example Mutual", "deductible": 250, "scheduled_for": "2026-10-23"})
        self.assertEqual(r["created"], "26-1052")
        self.assertTrue(any("Tail lamps" in p for p in r["photos"]))
        ro = self._get("/api/ro/26-1052")
        self.assertEqual(ro["deductible"], 250.0)
        self.assertEqual(ro["dropped_off"], "2026-10-23")
        self.assertEqual(ro["payment"]["customer_owes_at_pickup"], 250.0)
        r2 = self._post("/api/intake", {"customer_name": "Second", "phone": "555-0101", "year": 2021, "make": "Kia", "model": "Soul", "loss_description": "side"})
        self.assertEqual(r2["created"], "26-1053")

    def test_parts_and_kb_endpoints(self):
        p = self._post("/api/parts/price", {"list_price": 400, "part_type": "oem"})
        self.assertEqual(p["sell"], 400)
        c = self._post("/api/parts/compare", {"quotes": [{"vendor": "a", "part_type": "oem", "price": 1, "eta": 1}], "rental": True})
        self.assertEqual(c["recommended"]["vendor"], "a")
        k = self._get("/api/kb?q=deductible")
        self.assertTrue(k["results"])

    def test_unknown_endpoint_and_ro_are_404_without_detail(self):
        for path in ("/api/nope", "/api/ro/99-9999"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(path)
            self.assertEqual(cm.exception.code, 404)
            self.assertEqual(json.loads(cm.exception.read())["error"], "Not found")

    def test_kb_k_is_clamped_and_big_bodies_rejected(self):
        self.assertLessEqual(len(self._get("/api/kb?q=the&k=100000")["results"]), 20)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/note", data=b"{}", headers={"Content-Type": "application/json", "X-MAB-Token": self.token, "Content-Length": str(web.MAX_BODY + 1)}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 413)

    def test_network_bind_requires_token(self):
        with self.assertRaises(ValueError):
            web.serve(Ctx(shop=shop(), store=Store(":memory:"), today=TODAY), host="0.0.0.0", port=0)
        with self.assertRaises(ValueError):
            web.serve(Ctx(shop=shop(), store=Store(":memory:"), today=TODAY), host="0.0.0.0", port=0, token="short")
        httpd = web.serve(Ctx(shop=shop(), store=Store(":memory:"), today=TODAY), host="127.0.0.1", port=0)
        self.assertGreaterEqual(len(httpd.token), 16)   # minted automatically on localhost
        httpd.server_close()

    def test_live_today_unless_pinned(self):
        c = Ctx(shop=shop(), store=Store(":memory:"))
        self.assertEqual(c.today, date.today())
        self.assertEqual(Ctx(shop=shop(), store=Store(":memory:"), today=TODAY).today, TODAY)


if __name__ == "__main__":
    unittest.main()
