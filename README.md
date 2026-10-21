# MAB Agents

A fleet of AI agents built for **Mullikin's Auto Body**, a collision repair shop.
Ten agents, each pairing a Claude model with deterministic shop tools, so the
model does the reading and writing while the numbers, dates and rules come from
code you can test. A browser front-desk app sits on top so a new hire can run
the front office on day one. See [docs/FRONT_DESK.md](docs/FRONT_DESK.md).

This replaces the general-purpose demos in `AI-Agents` (finance, movies, plants)
with agents that know what a supplement is, why the rental clock matters, and
that a windshield means a camera calibration.

## The fleet

| Agent | Key | What it does | Tools |
|---|---|---|---|
| Front Desk | `front_desk` | Trainer and safety net for whoever answers the phone: lookups, scripts, today's call list, what the customer owes, checklists | `lookup_customer`, `status_text`, `phone_script`, `todays_calls`, `checklist`, `daily_routine`, `what_customer_owes`, `log_sent` |
| Intake & Damage Triage | `intake` | First contact: drivability, claim path, rental, photo checklist, opens the RO | `photo_checklist`, `likely_calibrations`, `draft_repair_order` |
| Estimate Auditor | `estimator` | Finds not-included operations (blends, FPB, scans, corrosion protection, A/C evac, alignment) and totals at posted rates | `audit_estimate_text`, `audit_repair_order`, `estimate_totals` |
| ADAS & OEM Procedures | `adas` | Calibration checklist from the operations, pre-conditions, documentation, billing lines | `calibration_checklist`, `calibration_checklist_for_ro` |
| Parts Coordinator | `parts` | Builds the order per carrier rule (safety and structural stay OEM), tracks late parts, sources parts online with web search, prices through the shop matrix, ranks quotes against the rental clock | `build_order`, `parts_status`, `rfq_email`, `update_part`, `price_part`, `compare_quotes`, `vendors`, `web_search` |
| Production Manager | `production` | WIP board, stalled ROs, promise dates, tech load, stage moves | `wip_board`, `stalled_ros`, `promise_date`, `move_stage`, `tech_load` |
| Customer Communications | `customer` | Stage updates, delay and hidden-damage texts, review requests and replies | `status_update`, `delay_notice`, `supplement_notice`, `review_request`, `review_reply` |
| Insurance & Claims Liaison | `insurance` | Supplement emails with a per-item basis, Maryland total-loss math and timelines, rental extensions, storage notices | `supplement_request`, `total_loss_check`, `rental_extension`, `storage_notice`, `carrier_rule` |
| Accounts Receivable & Short-Pay | `ar` | Open balances by age, payment recording, short-pay letters with the basis per line, Maryland Insurance Administration complaint outline | `ar_aging`, `ro_balance`, `record_payment`, `short_pay_letter`, `mia_complaint_outline` |
| Shop Manager | `manager` | Morning brief and KPIs; holds every other agent's tools for cross-department questions | `daily_brief`, `shop_kpis`, `repair_order`, `add_note` + all of the above |

Every agent also has `search_knowledge`, which searches the shop's own documents
(see Knowledge base below), and shares the same ground rules (in
`mab_agents/agents/base.py`): shop facts come only from `shop.toml`, policy and
procedure come only from the knowledge base, arithmetic comes only from tools,
OEM procedures are never asserted without a source, and **agents draft, humans send**.

## Three ways to use it

| Surface | Who | How |
|---|---|---|
| **Front-desk web app** | front office, on the shop PC or a tablet | `mab serve` prints a link ending in `?token=...`; open and bookmark it. Today's call list, customer lookup, one-click text drafts, check-in form, scripts, parts pricing, knowledge search, and "Ask the fleet". Only the Ask box needs an API key. |
| **Command line** | manager, estimator, anyone comfortable in a terminal | `mab board`, `mab audit`, `mab ask "..."`, `mab chat parts` |
| **Claude Desktop** | staff who already use Claude | `pip install "mab-agents[mcp]"` then add `mab mcp` as an MCP server; every shop tool appears inside Claude Desktop with its own login. |

Every request needs the token, even on localhost; that is what keeps a stray web
page in the same browser from driving the API. To put the app on the shop
network: `MAB_WEB_TOKEN=<16+ character secret> mab serve --host 0.0.0.0` and
open `http://<pc-ip>:8765/?token=<secret>` once per device. It is plain HTTP,
so keep it on the shop's private Wi-Fi (never a guest network) or put a TLS
reverse proxy in front of it. Without a token it refuses to listen beyond
localhost.

## Quick start

```bash
git clone https://github.com/coondog024/MAB-Agents.git && cd MAB-Agents
pip install -e .
mab init                      # writes shop.toml, loads 4 sample ROs, creates knowledge/ with the starter playbook
mab serve                     # front-desk app; open the printed link
mab queue                     # who needs a call or text today (no API key needed)
mab board                     # WIP board + stalled ROs
mab audit mab_agents/data/sample_estimate.csv --year 2021
mab msg 26-1042 --delay "the bumper cover is on backorder" --date 2026-10-30
```

Then add your key to `.env` (copy `.env.example`) and talk to the fleet:

```bash
mab ask "What's late on 26-1042 and what do I do about it?"       # routes to parts
mab ask "Is $14,200 on a $17,500 ACV a total loss?"                 # routes to insurance
mab ask "Give me the morning brief" -v                              # manager, shows tool calls
mab chat estimator                                                  # multi-turn with one agent
mab agents                                                          # list the fleet
```

Edit `shop.toml` first: name, phone, hours, posted rates, DRP partners and the
per-carrier rules. The agents quote those verbatim and nothing else.

## Connecting CRISMA and CCC ONE

The shop runs CRISMA (FoxPro) and writes estimates in CCC ONE with EMS export.
MAB reads both, read-only:

```bash
mab sync-crisma --watch       # BSRO/BSLOC -> stage, dates, rental, status tag (LOCCD map in shop.toml)
mab import-ems --watch        # EMSOUT folder -> estimate lines, totals, deductible, rates, supplements
```

See [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for the field mapping, how the
two systems are linked (put the CRISMA RO number in CCC's RO field), and what
each source is allowed to change. The estimate audit also runs the shop's own
51-rule not-included catalogue, kept as editable JSON.

## Knowledge base: your PDFs and SOPs

The old `AI-Agents` repo had a demo that vectorised recipe PDFs. This is the
shop version of that idea, without a vector database to run: documents are
chunked and indexed in SQLite full-text search (BM25) and every agent searches
them with `search_knowledge`, citing file and page.

```bash
cp ~/Downloads/MOTOR-Guide-to-Estimating.pdf knowledge/     # P-pages from your estimating system
cp ~/Downloads/ExampleMutual-DRP-Agreement.pdf knowledge/
mab kb sync
mab kb search "blend not included"
```

`mab init` seeds `knowledge/` with a front-desk playbook, a collision glossary,
three reference documents written for this shop (`maryland_collision_rules.md`,
`oem_position_statements.md`, `industry_benchmarks.md`) and `sources.toml`, a
list of 125 verified URLs (every major OEM's position statements on scanning,
parts, restraints, wheels, glass and sectioning; the Maryland statutes on
steering, written estimates, aftermarket-parts disclosure, liens and total loss;
EPA 6H and OSHA respirator rules; CCC and DEG references) that `mab kb fetch`
downloads into the folder. Edit them; they are shop policy as far as the agents
are concerned. Supported:
PDF, .md, .txt, .csv. Only load documents the shop is licensed to use. The index
stays on the machine; only matching passages go into a prompt. (If you later
want semantic search, the `KnowledgeBase.search` method is the one place to add
embeddings.)

## Paperwork the law expects (Maryland)

Every RO page has **Paperwork** buttons, and `mab forms <ro>` writes the same pages to
`forms/` for printing. The text comes from the Maryland Code and sits in one place
(`forms_tools.STATUTES`) so it can be re-checked each session:

| Form | When | What it carries |
|---|---|---|
| Repair authorization (and direction to pay on insurance jobs) | check-in, before any work | the **Customer's Rights** block immediately above the signature line (Com. Law 14-1008), the written-estimate figure the customer was given and the damage-responsibility notice (14-1002), the "Manufacturer Special Policy Adjustment Programs" notice, teardown and storage terms, the replaced-parts choice, and the oral-notice record for tow-ins and after-hours drops |
| Replacement crash parts list and aftermarket disclosure | before parts are ordered | every replacement part with genuine or aftermarket stated (used OEM counts as genuine, reconditioned is marked by the estimator) and the statutory statement when an aftermarket crash part is used, both in 10-point capitals (14-2302) |
| Authorization for additional repairs | when a supplement or customer-pay item is added | the supplement log, customer-pay items, the 10% consent against the written estimate (14-1002) and the permission record (14-1006) |
| Final invoice addendum | at delivery | used / rebuilt parts disclosed (14-1003), replaced parts tendered (14-1004), the balance split by payer so the customer sees their own number, the warranty wording from `shop.toml`, the notices repeated |

Each RO's Paperwork row says which forms are due now, which come later and
which this job does not need. The shop's own terms (authorization, direction to
pay, customer-pay wording, storage grace days, estimate fee) are in `[forms]` in
`shop.toml`; teardown and storage amounts come from `[rates]`. Every printed
page carries the RO, customer and vehicle in its footer. The pages print from
the browser (Ctrl+P, or the Print button) to paper or PDF.

## Parts: sourcing and pricing

- **Pricing matrix** in `shop.toml` `[parts]`: OEM is billed at list with the dealer discount as margin; aftermarket, recycled and sublet are cost plus markup; carrier caps are honoured per quote.
- **Compare quotes**: total cost to the job = landed cost + days to arrive × rental cost per day. A cheaper part that lands five days later usually loses. Safety and structural parts drop every non-OEM quote.
- **Online sourcing**: the Parts agent has Claude's web search tool and is told to look up the OEM part number on the brand's dealer parts site, RockAuto, Amazon, eBay Motors, LKQ Online and CarParts.com, then price and rank what it finds. It never orders. Turn it off with `MAB_WEB_SEARCH=0`.
- Dealer and salvage networks (CollisionLink, OPSTrax, PartsTrader, LKQ) have no public APIs; those stay a human step for now.

## Offline commands

These need no model and are what the tests and CI exercise:

| Command | Does |
|---|---|
| `mab init` | Create `shop.toml`, load sample ROs into `mab.db`, seed and index `knowledge/` |
| `mab serve [--host --port]` | Front-desk web app |
| `mab queue` | Who needs a call or text today, with reasons |
| `mab lookup <phone|name|RO>` | Find a customer |
| `mab script <situation>` | Phone script (new_claim, deductible, delay_complaint, …) |
| `mab price <list> [--type --cost --max-markup]` | Price a part through the matrix |
| `mab forms <ro> [--kind k] [--out dir] [--list]` | Print-ready Maryland paperwork for an RO (authorization with Customer's Rights, aftermarket crash-parts disclosure, additional-repairs consent, invoice addendum) |
| `mab kb sync|search|list|fetch` | Knowledge base (fetch downloads `knowledge/sources.toml`) |
| `mab import-ems [path] [--watch]` | CCC ONE EMS jobs -> repair orders |
| `mab import-ems --fields [path]` | print the field dictionary of your real CCC export (DBF headers) |
| `mab sync-crisma [--dir] [--watch]` | CRISMA status -> repair orders (read-only) |
| `mab ros` / `mab ro <ro>` | List or show repair orders |
| `mab stage <ro> <stage>` | Move an RO and print the customer text draft |
| `mab audit <file.csv>` or `--ro <ro>` | Estimate audit with findings and totals |
| `mab adas --ops "windshield, front bumper"` | ADAS calibration checklist |
| `mab board` | WIP board and stalled ROs |
| `mab msg <ro> [--stage s] [--delay reason --date d] [--review]` | Customer message drafts |
| `mab brief` / `mab kpi` | Morning brief and KPIs as JSON |

`--today YYYY-MM-DD` on any command pins the date for planning math.

## How it is built

```
mab_agents/
  shop.example.toml   shop profile: rates, hours, DRPs, carrier rules, parts matrix, targets
  models.py           RepairOrder / EstimateLine / Part / stages
  store.py            SQLite store
  ems.py              CCC ONE EMS importer (DBF sets and .EST XML)
  crisma.py           read-only CRISMA bridge (BSRO/BSLOC -> stages)
  kb.py, fetch.py     knowledge base: PDF/markdown -> chunks -> SQLite FTS5; URL fetcher
  llm.py              Anthropic SDK tool runner: model, effort, caching, fallbacks, web search
  tools/              pure functions: estimate audit, ADAS triggers, parts pricing/quotes,
                      scheduling, customer templates, front-desk scripts, carrier rules, KPIs
  agents/             one module per agent: system prompt + @beta_tool wrappers
  fleet.py            registry + keyword router (manager catches the rest)
  web.py, web/        the front-desk app (stdlib HTTP server + one HTML page)
  mcp_server.py       the same tools for Claude Desktop
  cli.py              the `mab` command
  data/knowledge/     starter front-desk playbook and glossary, copied by `mab init`
docs/FRONT_DESK.md    the one page a new hire reads
tests/                unit tests, all offline (incl. the web API)
```

Model defaults (override with env vars): `claude-opus-5-5`, effort `medium`,
prompt caching on the system prompt, server-side refusal fallbacks on
(`MAB_FALLBACKS=0` turns them off). Tool results go back to the model as JSON;
transcripts are append-only so `mab chat` keeps history intact.

## What the agents will not do

- Send anything. Texts, emails and orders are returned as drafts and logged with
  status `draft` in `mab.db`. Wire Twilio / SMTP behind a human approval step.
- Invent labor times or OEM procedures. They say "per database" and "verify in
  the OEM procedure" and name where to look.
- Quote the total-loss threshold as law. The table in `insurer_tools.py` is a
  reference; the agent says the carrier's policy and current statute govern.

## Connecting real systems

| Replace | With |
|---|---|
| sample ROs | `mab sync-crisma` + `mab import-ems` (done; see docs/DATA_SOURCES.md) |
| `data/sample_estimate.csv` | Mitchell/Audatex PDFs via a parser that emits `EstimateLine` rows |
| Message log | Twilio or your shop's texting platform, behind the "Mark sent" button |
| `knowledge/` starter docs | Your real SOPs, DRP agreements, P-pages, OEM procedures |
| `carrier_rules` in `shop.toml` | Your actual DRP agreements |

## Tests

```bash
python -m unittest discover -s tests -v
```
