"""`mab` command line.

Offline commands (no API key): init, agents, ros, ro, stage, audit, adas, board, msg, brief, kpi,
lookup, queue, script, price, kb, serve (the web app; only "Ask" needs a key).
Model commands (need ANTHROPIC_API_KEY): ask, chat. `mcp` serves the tools to Claude Desktop.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from . import config, fleet as fleet_mod
from .agents.base import Ctx
from .store import Store


def _ctx(args: argparse.Namespace) -> Ctx:
    pinned = date.fromisoformat(args.today) if getattr(args, "today", None) else None
    return Ctx(shop=config.load_shop(), store=Store(), today=pinned)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


# ---- offline commands ---------------------------------------------------------
def cmd_init(args) -> int:
    target = Path("shop.toml")
    if not target.exists():
        shutil.copy(config.EXAMPLE, target)
        print(f"Wrote {target}. Edit it with your real name, address, rates and DRPs.")
    else:
        print(f"{target} already exists; leaving it.")
    n = Store().load_sample()
    print(f"Loaded {n} sample repair orders into {config.db_path()}.")
    kdir = config.knowledge_dir(config.load_shop())
    if not kdir.exists():
        shutil.copytree(config.PKG_DIR / "data" / "knowledge", kdir)
        print(f"Created {kdir}/ with the starter front-desk playbook and glossary. Add your PDFs there.")
    stats = _ctx(args).kb().sync()
    print(f"Knowledge base: {len(stats['added'])} added, {len(stats['updated'])} updated, {stats['unchanged']} unchanged.")
    if not Path(".env").exists():
        print("No .env found. Copy .env.example and add ANTHROPIC_API_KEY to use `mab ask` / `mab chat`.")
    return 0


def cmd_agents(args) -> int:
    ctx = _ctx(args)
    for a in fleet_mod.describe(fleet_mod.build_fleet(ctx)):
        print(f"{a['key']:<11} {a['name']}")
        print(f"{'':<11} {a['role']}")
        print(f"{'':<11} tools: {', '.join(a['tools'])}")
        print(f"{'':<11} e.g. \"{a['examples'][0]}\"\n")
    return 0


def cmd_ros(args) -> int:
    ctx = _ctx(args)
    for ro in ctx.store.list(include_delivered=args.all):
        print(f"{ro.ro_number}  {ro.vehicle.label():<26} {ro.customer.name:<18} {ro.stage:<15} {ro.insurer or 'customer pay':<16} days {ro.days_in_shop(ctx.today)}")
    return 0


def cmd_ro(args) -> int:
    _print(_ctx(args).store.require(args.ro).to_dict())
    return 0


def cmd_stage(args) -> int:
    from .tools import customer_tools

    ctx = _ctx(args)
    ro = ctx.store.set_stage(args.ro, args.stage, author="cli", note=args.note or "")
    draft = customer_tools.stage_update(ro, ctx.shop)
    ctx.store.log_message(ro.ro_number, draft["channel"], draft["to"], draft["text"])
    print(f"{ro.ro_number} -> {ro.stage}\n\nCustomer update draft ({draft['chars']} chars):\n{draft['text']}")
    return 0


def cmd_audit(args) -> int:
    from .tools import estimate_tools

    ctx = _ctx(args)
    if args.ro:
        ro = ctx.store.require(args.ro)
        lines, airbags, year = ro.lines, ro.airbags_deployed, ro.vehicle.year
    else:
        lines = estimate_tools.parse_estimate(Path(args.file).read_text(encoding="utf-8"))
        airbags, year = args.airbags, args.year
    findings = estimate_tools.audit_estimate(lines, airbags_deployed=airbags, model_year=year)
    report = estimate_tools.audit_report(findings)
    print(f"{len(lines)} lines, {report['count']} findings ({report['high']} high / {report['medium']} medium / {report['low']} low)\n")
    for f in findings:
        print(f"[{f.severity.upper():<6}] {f.rule}: {f.message}\n         add: {f.suggested_line}")
    print()
    _print(estimate_tools.totals(lines, ctx.rates()))
    return 0


def cmd_adas(args) -> int:
    from .tools import adas_tools

    ops = [o.strip() for o in args.ops.split(",")]
    items = adas_tools.calibration_checklist(ops, airbags_deployed=args.airbags)
    _print(adas_tools.checklist_report(items, vehicle=args.vehicle))
    return 0


def cmd_board(args) -> int:
    from .tools import schedule_tools

    ctx = _ctx(args)
    board = schedule_tools.wip_board(ctx.store.list(), today=ctx.today)
    print(f"{board['date']}: {board['open_ros']} open, {board['in_production']} in production, {board['hours_in_production']} hrs on the floor\n")
    for stage, info in board["stages"].items():
        print(f"== {info['label']}")
        for r in info["ros"]:
            flag = " RENTAL" if r["rental"] else ""
            print(f"   {r['ro']}  {r['vehicle']:<26} {r['labor_hours']:>5} hrs  day {r['days_in_shop']:>2}  in stage {r['days_in_stage']}{flag}")
    stalled = schedule_tools.stalled(ctx.store.list(), float(ctx.shop['targets']['stalled_after_days']), today=ctx.today)
    if stalled:
        print("\nStalled:")
        for s in stalled:
            print(f"   {s['ro']} {s['vehicle']} in {s['stage']} for {s['days_in_stage']} days ({s['why_it_matters']})")
    return 0


def cmd_msg(args) -> int:
    from .tools import customer_tools

    ctx = _ctx(args)
    ro = ctx.store.require(args.ro)
    if args.delay:
        d = customer_tools.delay_notice(ro, ctx.shop, args.delay, args.date)
    elif args.review:
        d = customer_tools.review_request(ro, ctx.shop)
    else:
        d = customer_tools.stage_update(ro, ctx.shop, args.stage)
    print(d["text"])
    return 0


def cmd_brief(args) -> int:
    from .tools import kpi_tools

    ctx = _ctx(args)
    _print(kpi_tools.daily_brief(ctx.store.list(include_delivered=True), ctx.shop, today=ctx.today))
    return 0


def cmd_kpi(args) -> int:
    from .tools import kpi_tools

    ctx = _ctx(args)
    _print(kpi_tools.kpis(ctx.store.list(include_delivered=True), ctx.shop, today=ctx.today))
    return 0


def cmd_lookup(args) -> int:
    from .tools import frontdesk_tools

    ctx = _ctx(args)
    rows = ctx.store.find(args.query)
    if not rows:
        print("No match.")
        return 1
    for ro in rows:
        s = frontdesk_tools.ro_summary(ro, ctx.today)
        print(f"{s['ro']}  {s['customer']:<18} {s['phone']:<14} {s['vehicle']:<24} {s['stage_label']:<26} {s['insurer']:<16} promised {s['promised'] or 'TBD'}")
    return 0


def cmd_queue(args) -> int:
    from .tools import frontdesk_tools

    ctx = _ctx(args)
    q = frontdesk_tools.update_queue(ctx.store.list(), ctx.store.last_messages(), today=ctx.today)
    if not q:
        print("Everyone is up to date.")
    for x in q:
        print(f"[P{x['priority']}] {x['ro']} {x['customer']} ({x['phone']}) {x['vehicle']} - {x['stage']}{' RENTAL' if x['rental'] else ''}")
        for r in x["reasons"]:
            print(f"      - {r}")
    return 0


def cmd_script(args) -> int:
    from .tools import frontdesk_tools

    s = frontdesk_tools.phone_script(args.situation)
    if "error" in s:
        print(s["error"])
        return 1
    print(f"== {s['title']}")
    for section in ("ask", "say", "never"):
        if s.get(section):
            print(f"\n{section.upper()}:")
            for line in s[section]:
                print(f"  - {line}")
    print(f"\nTHEN: {s['then']}")
    return 0


def cmd_price(args) -> int:
    from .tools import parts_tools

    ctx = _ctx(args)
    _print(parts_tools.price_part(args.list_price, args.type, ctx.shop, cost=args.cost, max_markup=args.max_markup))
    return 0


def cmd_kb(args) -> int:
    ctx = _ctx(args)
    kb = ctx.kb()
    if args.kb_cmd == "sync":
        st = kb.sync()
        print(f"added {len(st['added'])}, updated {len(st['updated'])}, removed {len(st['removed'])}, unchanged {st['unchanged']}, new passages {st['chunks']}")
        for x in st["added"] + st["updated"]:
            print(f"  + {x}")
        for x in st["skipped"]:
            print(f"  skipped {x}")
        return 0
    if args.kb_cmd == "list":
        for d in kb.documents():
            print(f"{d['path']:<50} {d['pages']:>4} pages {d['chunks']:>5} passages  synced {d['synced_at'][:16]}")
        return 0
    if args.kb_cmd == "search":
        hits = kb.search(args.query, args.k)
        if not hits:
            print("Nothing matched.")
            return 1
        for h in hits:
            print(f"--- {h.title} ({h.path}, page {h.page}) score {h.score}\n    …{h.snippet}…\n{h.text[:600]}\n")
        return 0
    return 2


def cmd_serve(args) -> int:
    from . import web

    ctx = _ctx(args)
    if ctx.shop.get("_is_example"):
        print("warning: no shop.toml found here; serving the example shop profile. Run `mab init` in the shop's folder first.", file=sys.stderr)
    httpd = web.serve(ctx, host=args.host, port=args.port)
    shown_host = "localhost" if args.host in {"127.0.0.1", "localhost", "::1"} else args.host
    print(f"Front desk app: http://{shown_host}:{args.port}/?token={httpd.token}")
    print("Open that exact link once per browser (bookmark it); a cookie keeps you signed in. Ctrl-C to stop.")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Listening on the network over plain HTTP: keep this on the shop's private Wi-Fi, not a guest network.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_mcp(args) -> int:
    from . import mcp_server

    mcp_server.run(_ctx(args))
    return 0


# ---- model commands -------------------------------------------------------------
def _trace(name: str, inp: dict) -> None:
    print(f"  ↳ {name}({', '.join(f'{k}={str(v)[:40]!r}' for k, v in inp.items())})", file=sys.stderr)


def cmd_ask(args) -> int:
    from . import llm

    ctx = _ctx(args)
    fleet = fleet_mod.build_fleet(ctx)
    key = args.agent or fleet_mod.route(args.question, fleet)[0]
    agent = fleet[key]
    print(f"[{key}]", file=sys.stderr)
    reply = llm.run(agent, f"Today is {ctx.today.isoformat()}.\n\n{args.question}", on_tool=_trace if args.verbose else None)
    print(reply.text)
    if args.verbose:
        print(f"\n({reply.stop_reason}; served by {reply.served_by}; usage {reply.usage})", file=sys.stderr)
    return 0


def cmd_chat(args) -> int:
    from . import llm

    ctx = _ctx(args)
    fleet = fleet_mod.build_fleet(ctx)
    agent = fleet[args.agent]
    print(f"Chatting with {agent.name}. Ctrl-D or 'quit' to exit.")
    history: list = []
    first = True
    while True:
        try:
            q = input("\nyou> ").strip()
        except EOFError:
            break
        if not q or q.lower() in {"quit", "exit"}:
            break
        prompt = (f"Today is {ctx.today.isoformat()}.\n\n" if first else "") + q
        first = False
        reply = llm.run(agent, prompt, history=history, on_tool=_trace if args.verbose else None)
        history = reply.messages
        print(f"\n{agent.key}> {reply.text}")
    return 0


# ---- parser ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mab", description="Mullikin's Auto Body agent fleet")
    p.add_argument("--today", help="override today's date (YYYY-MM-DD) for planning math")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create shop.toml and load sample repair orders").set_defaults(fn=cmd_init)
    sub.add_parser("agents", help="list the fleet").set_defaults(fn=cmd_agents)
    s = sub.add_parser("ros", help="list repair orders"); s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_ros)
    s = sub.add_parser("ro", help="show one repair order"); s.add_argument("ro"); s.set_defaults(fn=cmd_ro)
    s = sub.add_parser("stage", help="move an RO to a stage and draft the customer text"); s.add_argument("ro"); s.add_argument("stage"); s.add_argument("--note"); s.set_defaults(fn=cmd_stage)
    s = sub.add_parser("audit", help="audit an estimate file or an RO's lines")
    s.add_argument("file", nargs="?"); s.add_argument("--ro"); s.add_argument("--airbags", action="store_true"); s.add_argument("--year", type=int); s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("adas", help="ADAS calibration checklist for operations"); s.add_argument("--ops", required=True); s.add_argument("--vehicle", default=""); s.add_argument("--airbags", action="store_true"); s.set_defaults(fn=cmd_adas)
    sub.add_parser("board", help="WIP board and stalled ROs").set_defaults(fn=cmd_board)
    s = sub.add_parser("msg", help="draft a customer message"); s.add_argument("ro"); s.add_argument("--stage"); s.add_argument("--delay", help="reason"); s.add_argument("--date"); s.add_argument("--review", action="store_true"); s.set_defaults(fn=cmd_msg)
    sub.add_parser("brief", help="morning brief (offline)").set_defaults(fn=cmd_brief)
    sub.add_parser("kpi", help="shop KPIs (offline)").set_defaults(fn=cmd_kpi)
    s = sub.add_parser("lookup", help="find a customer by phone, name, RO or vehicle"); s.add_argument("query"); s.set_defaults(fn=cmd_lookup)
    sub.add_parser("queue", help="who needs a call or text today").set_defaults(fn=cmd_queue)
    s = sub.add_parser("script", help="phone script for a situation"); s.add_argument("situation"); s.set_defaults(fn=cmd_script)
    s = sub.add_parser("price", help="price a part through the shop matrix"); s.add_argument("list_price", type=float); s.add_argument("--type", default="oem"); s.add_argument("--cost", type=float); s.add_argument("--max-markup", type=float, dest="max_markup"); s.set_defaults(fn=cmd_price)
    s = sub.add_parser("kb", help="knowledge base: sync | search | list"); kb = s.add_subparsers(dest="kb_cmd", required=True)
    kb.add_parser("sync"); kb.add_parser("list"); ks = kb.add_parser("search"); ks.add_argument("query"); ks.add_argument("-k", type=int, default=5); s.set_defaults(fn=cmd_kb)
    s = sub.add_parser("serve", help="run the front-desk web app"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765); s.set_defaults(fn=cmd_serve)
    sub.add_parser("mcp", help="serve the shop tools to Claude Desktop over MCP (stdio)").set_defaults(fn=cmd_mcp)
    s = sub.add_parser("ask", help="ask the fleet (routes to an agent)"); s.add_argument("question"); s.add_argument("--agent", choices=[m.__name__.split(".")[-1] for m in fleet_mod.MODULES]); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("chat", help="multi-turn chat with one agent"); s.add_argument("agent", choices=[m.__name__.split(".")[-1] for m in fleet_mod.MODULES]); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(fn=cmd_chat)
    return p


NO_KEY_HELP = (
    "No Anthropic credentials found. Put ANTHROPIC_API_KEY in .env (see .env.example) or run `ant auth login`.\n"
    "Offline commands (board, audit, msg, brief, kpi, adas, stage) work without a key."
)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except BrokenPipeError:
        return 0
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except TypeError as e:
        if "authentication" in str(e).lower():
            print(NO_KEY_HELP, file=sys.stderr)
            return 2
        raise
    except Exception as e:  # noqa: BLE001 - surface SDK errors without a stack trace
        import anthropic

        if isinstance(e, anthropic.AuthenticationError):
            print(f"Anthropic rejected the API key: {e.message}", file=sys.stderr)
            return 2
        if isinstance(e, anthropic.APIError):
            print(f"Anthropic API error: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
