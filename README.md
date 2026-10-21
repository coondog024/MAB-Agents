# MAB Agents

A fleet of AI agents built for **Mullikin's Auto Body**, a collision repair shop.
Eight agents, each pairing a Claude model with deterministic shop tools, so the
model does the reading and writing while the numbers, dates and rules come from
code you can test.

This replaces the general-purpose demos in `AI-Agents` (finance, movies, plants)
with agents that know what a supplement is, why the rental clock matters, and
that a windshield means a camera calibration.

## The fleet

| Agent | Key | What it does | Tools |
|---|---|---|---|
| Intake & Damage Triage | `intake` | First contact: drivability, claim path, rental, photo checklist, opens the RO | `photo_checklist`, `likely_calibrations`, `draft_repair_order` |
| Estimate Auditor | `estimator` | Finds not-included operations (blends, FPB, scans, corrosion protection, A/C evac, alignment) and totals at posted rates | `audit_estimate_text`, `audit_repair_order`, `estimate_totals` |
| ADAS & OEM Procedures | `adas` | Calibration checklist from the operations, pre-conditions, documentation, billing lines | `calibration_checklist`, `calibration_checklist_for_ro` |
| Parts Coordinator | `parts` | Builds the order per carrier rule (safety and structural stay OEM), tracks late parts, drafts RFQs | `build_order`, `parts_status`, `rfq_email`, `update_part` |
| Production Manager | `production` | WIP board, stalled ROs, promise dates, tech load, stage moves | `wip_board`, `stalled_ros`, `promise_date`, `move_stage`, `tech_load` |
| Customer Communications | `customer` | Stage updates, delay and hidden-damage texts, review requests and replies | `status_update`, `delay_notice`, `supplement_notice`, `review_request`, `review_reply` |
| Insurance & Claims Liaison | `insurance` | Supplement emails, total-loss math by state, rental extensions, storage notices | `supplement_request`, `total_loss_check`, `rental_extension`, `storage_notice`, `carrier_rule` |
| Shop Manager | `manager` | Morning brief and KPIs; holds every other agent's tools for cross-department questions | `daily_brief`, `shop_kpis`, `repair_order`, `add_note` + all of the above |

Every agent shares the same ground rules (in `mab_agents/agents/base.py`):
shop facts come only from `shop.toml`, arithmetic comes only from tools, OEM
procedures are never asserted without a source, and **agents draft, humans send**.

## Quick start

```bash
git clone https://github.com/coondog024/MAB-Agents.git && cd MAB-Agents
pip install -e .
mab init                      # writes shop.toml and loads 4 sample repair orders
mab board                     # WIP board + stalled ROs (no API key needed)
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

## Offline commands

These need no model and are what the tests and CI exercise:

| Command | Does |
|---|---|
| `mab init` | Create `shop.toml`, load sample ROs into `mab.db` |
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
  shop.example.toml   shop profile: rates, hours, DRPs, carrier rules, targets
  models.py           RepairOrder / EstimateLine / Part / stages
  store.py            SQLite store (swap for CCC ONE / Mitchell / Rome later)
  llm.py              Anthropic SDK tool runner: model, effort, caching, fallbacks
  tools/              pure functions: estimate audit, ADAS triggers, parts, scheduling,
                      customer templates, carrier/total-loss rules, KPIs
  agents/             one module per agent: system prompt + @beta_tool wrappers
  fleet.py            registry + keyword router (manager catches the rest)
  cli.py              the `mab` command
tests/                33 unit tests, all offline
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
| `store.py` sample ROs | Export/API from your management system; keep the `RepairOrder` shape |
| `data/sample_estimate.csv` | CCC / Mitchell / Audatex line export (map the columns in `estimate_tools.CSV_COLUMNS`) |
| Message log | Twilio or your shop's texting platform, behind an approve button |
| `carrier_rules` in `shop.toml` | Your actual DRP agreements |

## Tests

```bash
python -m unittest discover -s tests -v
```
