"""`mab serve`: the front-desk web app.

A small threaded HTTP server with a JSON API and one HTML page, no framework.

Every request needs the access token, even on localhost (it is what stops a
random web page in the same browser from driving the API). `mab serve` mints
one and prints the link; open that link once per browser and a cookie keeps you
signed in. To use it from a tablet on the shop Wi-Fi:
    MAB_WEB_TOKEN=some-long-secret mab serve --host 0.0.0.0
Plain HTTP: keep it on the shop's private network, never a guest network.
"""
from __future__ import annotations

import collections
import json
import os
import secrets
import sys
import threading
import time
import traceback
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import fleet as fleet_mod
from .agents.base import Ctx
from .models import STAGE_LABELS, STAGES
from .tools import customer_tools, estimate_tools, frontdesk_tools, kpi_tools, parts_tools, schedule_tools

INDEX = Path(__file__).with_name("web") / "index.html"
NO_KEY_MESSAGE = "The agents need an Anthropic API key. Ask the manager to put ANTHROPIC_API_KEY in .env and restart `mab serve`. Everything else on this page works without it."
MAX_BODY = 1_000_000          # bytes
MAX_QUESTION = 4000           # characters
MAX_SESSIONS = 50             # chat transcripts kept in memory
MAX_HISTORY_MESSAGES = 40     # beyond this a chat starts fresh (no partial edits to a transcript)
ASK_PER_MINUTE = 20           # process-wide ceiling on model calls
NEED_LINK_HTML = b"""<!doctype html><meta charset=utf-8><title>Front Desk</title>
<body style="font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em">
<h2>This screen needs the front-desk link</h2>
<p>Open the link the manager gave you. It ends in <code>?token=...</code>. Bookmark that full link, not this page.</p>
<p>If you don't have it, the person running <code>mab serve</code> sees it printed in the terminal.</p></body>"""


class Api:
    """All endpoints as methods; the handler only parses HTTP."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.fleet = fleet_mod.build_fleet(ctx)
        self.histories: collections.OrderedDict[str, list] = collections.OrderedDict()
        self.lock = threading.Lock()
        self.ask_times: collections.deque[float] = collections.deque()

    # ---- GET ---------------------------------------------------------------
    def get(self, path: str, q: dict[str, str]) -> Any:
        ctx = self.ctx
        if path == "/api/health":
            return {"ok": True, "shop": ctx.shop["shop"]["name"], "today": ctx.today.isoformat(), "model_ready": bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))}
        if path == "/api/brief":
            return kpi_tools.daily_brief(ctx.store.list(include_delivered=True), ctx.shop, today=ctx.today)
        if path == "/api/board":
            return schedule_tools.wip_board(ctx.store.list(), today=ctx.today)
        if path == "/api/queue":
            return frontdesk_tools.update_queue(ctx.store.list(), ctx.store.last_messages(), today=ctx.today)
        if path == "/api/lookup":
            return [frontdesk_tools.ro_summary(r, ctx.today) for r in ctx.store.find(q.get("q", ""))]
        if path.startswith("/api/ro/"):
            ro = ctx.store.require(path.rsplit("/", 1)[1])
            d = ro.to_dict()
            d["messages"] = ctx.store.messages(ro.ro_number)
            d["summary"] = frontdesk_tools.ro_summary(ro, ctx.today)
            d["payment"] = frontdesk_tools.payment_summary(ro, ctx.shop)
            d["parts_status"] = parts_tools.parts_status(ro, today=ctx.today)
            return d
        if path == "/api/stages":
            return [{"key": s, "label": STAGE_LABELS[s]} for s in STAGES]
        if path == "/api/scripts":
            s = q.get("situation")
            return frontdesk_tools.phone_script(s) if s else {k: v["title"] for k, v in frontdesk_tools.SCRIPTS.items()}
        if path == "/api/checklists":
            return {"checkin": frontdesk_tools.CHECKIN_CHECKLIST, "delivery": frontdesk_tools.DELIVERY_CHECKLIST, "routine": frontdesk_tools.DAILY_ROUTINE}
        if path == "/api/kb":
            kb = ctx.kb()
            k = max(1, min(int(q.get("k", 6) or 6), 20))
            return {"results": kb.search_dicts(q.get("q", ""), k), "documents": kb.documents()}
        if path == "/api/agents":
            return fleet_mod.describe(self.fleet)
        raise KeyError(f"No such endpoint: {path}")

    # ---- POST --------------------------------------------------------------
    def post(self, path: str, body: dict[str, Any]) -> Any:
        ctx = self.ctx
        if path == "/api/stage":
            ro = ctx.store.set_stage(body["ro"], body["stage"], author="front-desk", note=body.get("note", ""))
            draft = customer_tools.stage_update(ro, ctx.shop)
            mid = ctx.store.log_message(ro.ro_number, draft["channel"], draft["to"], draft["text"])
            return {"ro": ro.ro_number, "stage": ro.stage, "draft": {**draft, "id": mid}}
        if path == "/api/message":
            ro = ctx.store.require(body["ro"])
            kind = body.get("kind", "stage")
            if kind == "delay":
                reason, new_date = str(body["reason"]).strip(), str(body["date"]).strip()
                if not reason or not new_date:
                    raise ValueError("A delay text needs a reason and a new date (YYYY-MM-DD)")
                ro = ctx.store.set_promised(ro.ro_number, new_date, reason)   # the RO's promised date moves with the text
                d = customer_tools.delay_notice(ro, ctx.shop, reason, new_date)
            elif kind == "review":
                d = customer_tools.review_request(ro, ctx.shop)
            elif kind == "supplement":
                d = customer_tools.supplement_notice(ro, ctx.shop, body["what"], int(body.get("days", 2)))
            else:
                d = customer_tools.stage_update(ro, ctx.shop, body.get("stage") or None)
            d["id"] = ctx.store.log_message(ro.ro_number, "sms", d.get("to", ro.customer.phone), d["text"])
            return d
        if path == "/api/sent":
            ro = ctx.store.require(body["ro"])
            channel = body.get("channel", "sms") if body.get("channel") in {"sms", "email", "call"} else "sms"
            if body.get("id"):
                mid = int(body["id"])
                ctx.store.mark_sent(mid, ro.ro_number)
            else:
                mid = ctx.store.log_message(ro.ro_number, channel, body.get("to", ro.customer.phone), str(body["text"])[:2000], status="sent")
            ctx.store.add_note(ro.ro_number, f"{channel} sent: {str(body.get('text', ''))[:80]}", author="front-desk")
            return {"ok": True, "id": mid}
        if path == "/api/note":
            text = str(body["text"]).strip()
            if not text:
                raise ValueError("Note is empty")
            ctx.store.add_note(body["ro"], text[:2000], author="front-desk")
            return {"ok": True}
        if path == "/api/promised":
            ro = ctx.store.set_promised(body["ro"], str(body["date"]), str(body.get("reason", "updated by front desk")))
            return {"ro": ro.ro_number, "promised": ro.promised}
        if path == "/api/intake":
            tools = {t.name: t for t in self.fleet["intake"].tools if hasattr(t, "name")}
            created = json.loads(tools["draft_repair_order"].call({
                "customer_name": body["customer_name"], "phone": body["phone"], "year": int(body["year"]), "make": body["make"], "model": body["model"],
                "loss_description": body.get("loss_description", ""), "insurer": body.get("insurer", ""), "claim_number": body.get("claim_number", ""),
                "drivable": bool(body.get("drivable", True)), "rental": bool(body.get("rental", False)), "airbags_deployed": bool(body.get("airbags_deployed", False)),
                "vin": body.get("vin", ""), "email": body.get("email", ""),
                "deductible": float(body.get("deductible") or 0), "scheduled_for": str(body.get("scheduled_for") or ""),
            }))
            photos = json.loads(tools["photo_checklist"].call({"damage_area": body.get("loss_description", "general")}))
            return {**created, "photos": photos["photos"], "checkin": frontdesk_tools.CHECKIN_CHECKLIST}
        if path == "/api/parts/price":
            return parts_tools.price_part(float(body["list_price"]), body.get("part_type", "oem"), ctx.shop, cost=body.get("cost"), max_markup=body.get("max_markup"))
        if path == "/api/parts/compare":
            return parts_tools.compare_quotes(body["quotes"], ctx.shop, rental=bool(body.get("rental")), safety_part=bool(body.get("safety_part")), list_price=body.get("list_price"), today=ctx.today)
        if path == "/api/audit":
            lines = estimate_tools.parse_estimate(body["estimate"])
            findings = estimate_tools.audit_estimate(lines, airbags_deployed=bool(body.get("airbags")), model_year=body.get("year") or None)
            return {**estimate_tools.audit_report(findings), "totals": estimate_tools.totals(lines, ctx.rates())}
        if path == "/api/kb/note":
            n = ctx.kb().add_text(body["title"], body["text"])
            return {"chunks": n}
        if path == "/api/ask":
            return self.ask(body)
        raise KeyError(f"No such endpoint: {path}")

    def ask(self, body: dict[str, Any]) -> Any:
        from . import llm

        question = str(body.get("question", "")).strip()
        if not question:
            raise ValueError("Type a question first")
        if len(question) > MAX_QUESTION:
            raise ValueError(f"Keep a question under {MAX_QUESTION} characters; paste long documents into Knowledge instead")
        key = body.get("agent") or fleet_mod.route(question, self.fleet)[0]
        if key not in self.fleet:
            raise ValueError(f"No agent called '{key}'")
        agent = self.fleet[key]
        session = f"{str(body.get('session', 'default'))[:64]}:{key}"
        calls: list[dict] = []
        now = time.monotonic()
        with self.lock:
            while self.ask_times and now - self.ask_times[0] > 60:
                self.ask_times.popleft()
            if len(self.ask_times) >= ASK_PER_MINUTE:
                raise RateLimited(f"More than {ASK_PER_MINUTE} questions in a minute; wait a moment")
            self.ask_times.append(now)
            history = list(self.histories.get(session, []))
            if len(history) > MAX_HISTORY_MESSAGES:
                history = []   # start a fresh conversation rather than editing an old transcript
        prompt = (f"Today is {self.ctx.today.isoformat()}.\n\n" if not history else "") + question
        reply = llm.run(agent, prompt, history=history, on_tool=lambda n, i: calls.append({"name": n, "input": i}))
        with self.lock:
            self.histories[session] = reply.messages
            self.histories.move_to_end(session)
            while len(self.histories) > MAX_SESSIONS:
                self.histories.popitem(last=False)
        return {"agent": key, "agent_name": agent.name, "text": reply.text, "tool_calls": calls, "stop_reason": reply.stop_reason, "usage": reply.usage,
                "note": "Stopped at the tool-call limit; the answer may be partial." if reply.hit_iteration_cap else ""}


class RateLimited(Exception):
    pass


def make_handler(api: Api, token: str):
    def token_ok(candidate: str | None) -> bool:
        return bool(candidate) and secrets.compare_digest(str(candidate), token)

    class Handler(BaseHTTPRequestHandler):
        server_version = "mab/0.1"

        def _authorized(self, q: dict[str, str]) -> bool:
            if token_ok(q.get("token")) or token_ok(self.headers.get("X-MAB-Token")):
                return True
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:  # noqa: BLE001 - malformed cookie header
                return False
            morsel = cookie.get("mab_token")
            return token_ok(morsel.value if morsel else None)

        def _send(self, status: int, payload: Any, content_type: str = "application/json", extra: dict[str, str] | None = None) -> None:
            data = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, default=str, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            is_page = u.path in {"/", "/index.html"}
            if not self._authorized(q):
                if is_page:
                    return self._send(HTTPStatus.UNAUTHORIZED, NEED_LINK_HTML, "text/html")
                return self._send(HTTPStatus.UNAUTHORIZED, {"error": "Open the front-desk link (the one ending in ?token=...) first."})
            if is_page:
                extra = {"Set-Cookie": f"mab_token={token}; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly"} if token_ok(q.get("token")) else None
                return self._send(HTTPStatus.OK, INDEX.read_bytes(), "text/html", extra)
            try:
                return self._send(HTTPStatus.OK, api.get(u.path, q))
            except KeyError:
                return self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except ValueError as e:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=sys.stderr)
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong on the server; the terminal running `mab serve` has the details."})

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not self._authorized(q):
                return self._send(HTTPStatus.UNAUTHORIZED, {"error": "Open the front-desk link (the one ending in ?token=...) first."})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON only"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                return self._send(HTTPStatus.OK, api.post(u.path, body))
            except RateLimited as e:
                return self._send(HTTPStatus.TOO_MANY_REQUESTS, {"error": str(e)})
            except KeyError as e:
                key = e.args[0] if e.args else ""
                msg = "Not found" if str(key).startswith(("RO ", "No draft", "No such")) else f"Missing field: {key}"
                return self._send(HTTPStatus.NOT_FOUND if msg == "Not found" else HTTPStatus.BAD_REQUEST, {"error": msg})
            except (ValueError, TypeError) as e:
                msg = str(e)
                if "authentication" in msg.lower() or "api_key" in msg.lower():
                    return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": NO_KEY_MESSAGE})
                if "could not convert" in msg or "invalid literal" in msg:
                    msg = "A number field has text in it; check the price, days or year."
                return self._send(HTTPStatus.BAD_REQUEST, {"error": msg})
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "authentication" in msg.lower() or "api_key" in msg.lower():
                    return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": NO_KEY_MESSAGE})
                traceback.print_exc(file=sys.stderr)
                return self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong on the server; the terminal running `mab serve` has the details."})

        def log_message(self, fmt: str, *args: Any) -> None:
            if os.getenv("MAB_WEB_LOG"):
                line = (fmt % args).replace(token, "***")
                sys.stderr.write("%s - %s\n" % (self.address_string(), line))

    return Handler


def serve(ctx: Ctx, host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> ThreadingHTTPServer:
    """Start the app. A token is always required; one is minted if none is configured.
    Binding beyond localhost requires an explicit MAB_WEB_TOKEN of 16+ characters."""
    token = token or os.getenv("MAB_WEB_TOKEN")
    local = host in {"127.0.0.1", "localhost", "::1"}
    if not local and not token:
        raise ValueError("Refusing to listen on the network without MAB_WEB_TOKEN set. Example: MAB_WEB_TOKEN=" + secrets.token_urlsafe(16))
    if token and len(token) < 16:
        raise ValueError("MAB_WEB_TOKEN must be at least 16 characters. Example: MAB_WEB_TOKEN=" + secrets.token_urlsafe(16))
    if not token:
        token = secrets.token_urlsafe(18)
    httpd = ThreadingHTTPServer((host, port), make_handler(Api(ctx), token))
    httpd.daemon_threads = True
    httpd.token = token  # type: ignore[attr-defined]
    return httpd
