# Connecting MAB to CRISMA and CCC ONE

MAB reads two things the shop already produces and never writes to either:

| Source | What it gives MAB | How |
|---|---|---|
| **CRISMA** (`BSRO.DBF` + `BSLOC.DBF` in the CRISMA data folder) | RO number, customer last name, vehicle, insurer, rental flag, scheduled-in / in / scheduled-out dates, balance due, and the location code (`LOCCD`) that drives the shop's status board | `mab sync-crisma` (or `--watch`) |
| **CCC ONE EMS export** (the `EMSOUT` folder) | Full estimate: owner contact, vehicle, claim, deductible, every line with part type / labor type / hours / price, totals, the labor rates the estimate was written at, supplements | `mab import-ems` (or `--watch`) |

Together they make the Today queue, the board, the payment line and the
estimate audit run on the real shop instead of sample data.

## One-time setup

In `shop.toml`:

```toml
[crisma]
data_dir = "M:/Crisma/data"            # or //Mabserver/share/Crisma/data
[ems]
export_dir = "//Mabserver/apps/CCC/EMSOUT"
```

The `[crisma.status_map]` block maps every `LOCCD` in your `BSLOC` table to a
MAB stage. The defaults cover the 24 codes in your table (OL, OLW, OLP, TD,
SUP, UPL, NTC, CPH, IB, IBS, SUB, CBK, REF, IRA, CLN, FI, VF, VFS, CA, CAS,
CSS, TL; NS and DNR are skipped). A code MAB has not seen is reported by
`mab sync-crisma` so you can add it. Rows with a blank code fall back on dates:
an in-date means checked in, otherwise scheduled.

## Running it

On the server or the front-desk PC, two terminals (or two scheduled tasks):

```bash
mab sync-crisma --watch --poll 60      # status, dates, rental from CRISMA
mab import-ems --watch --poll 30       # estimates and supplements from CCC
mab serve                              # the front-desk app
```

`import-ems` remembers which files it has processed in the current run and
re-imports a job when its file changes (a supplement overwrites the lines, the
RO keeps its stage, notes, parts and messages). `sync-crisma` only changes
stage, promised date, rental, insurer and the status tag; it never touches
estimate lines.

## How the two line up

CRISMA knows the RO number; CCC knows the estimate. MAB links them in this order:

1. CCC's RO field (`ENV.RO_ID`) or `AD2.EST_FILENO` equals a CRISMA RO number. **Type the CRISMA RO number into the CCC RO field at write-up and the link is exact.**
2. Same CCC estimate id already imported (a supplement).
3. Same VIN on an open RO.
4. Same last name, year and make on an open RO.
5. Otherwise a new RO numbered `E-<estimate id>`.

## What the estimate adds

- Lines with CCC's codes translated: `PAN` new OEM, `PAA` aftermarket, `PAL` recycled, `PAM/PAR/PAC` reconditioned; `LAB` body, `LAR` refinish, `LAM` mechanical, `LAS/LAF` structural, `LAD` diagnostic, `LAG` glass; `OP11` replace, `OP2` R&I, `OP9` repair, `OP6` refinish, `OP15` blend, `OP16` sublet.
- Betterment lines and `WHO_PAYS = C` lines become customer-pay items, so the front desk's "what you owe" line is right.
- The rates in the estimate's labor profile are compared with your posted rates; anything lower is noted on the RO as a short-pay to address.
- The audit runs the structural checks plus your own 51-rule not-included catalogue (`mab_agents/data/not_included_rules.json`, from the Flask-EST_EMS project). Edit the JSON; no code change needed.

## Privacy

Everything stays on the shop's machines. The EMS and CRISMA files are read in
place; MAB keeps its own copy in `mab.db`. When an agent is asked about an RO,
the fields it needs go to the model. Nothing from these files is ever committed
to this repository: the tests use synthetic fixtures written by the test suite.

## Other paths you have explored

- CCC ONE's web app exposes JSON endpoints (`estimate.cccone.com/workfile/api/...`) that the browser calls while you're logged in. They are undocumented and session-bound; useful for reading, fragile to build on, and not something CCC supports. The EMS export is the supported path.
- The `.EST` XML export carries the same sections as the DBF set and is handled by the same importer.
- Mitchell estimates arrive as PDFs; the Estimate-Rekey-Plugin parser can feed them in later through the same `EstimateLine` shape.
