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

## Turning on the CCC export for MAB

CCC ONE writes EMS files per machine, only while CCC ONE is open on that machine, and
only to the folders configured there (CCC's 2024 job aid *Adding/Removing EMS Paths*
and the SCRS data-pump release are in `sources.toml`). On each estimator PC:

1. **Configure > Machine Settings > Import/Export > File Export**, add an export type of
   **EMS 2.01 Estimate** with its own directory for MAB. Give MAB a second path rather
   than CRISMA's `EMSOUT`: CCC allows several paths, and a folder another program sweeps
   can lose files before MAB sees them.
2. **Import/Export > EMS Extract Preferences**: tick the extract rules (Create Estimate,
   Lock Estimate, Lock Supplement, Save Workfile) so supplements export without anyone
   remembering to. Manual export is WORKFILE > EMS Export.
3. Leave **Exclude customer information** and **Redact VIN** off for MAB's path; both
   break the RO matching below.
4. If you write estimates on cccone.com instead of the desktop app: Settings > Data
   Connections > EMS and Workfile Copy, install the CCC ONE Data Transfer Application
   (Windows), paste the transfer key and add the same directory.

Then lock down the layout once:

```bash
mab import-ems --fields "//Mabserver/apps/CCC/EMSOUT_MAB"
```

prints every file of the newest job with its field names, types and lengths straight
from the DBF headers. The CIECA EMS specification is members-only, but every EMS table
carries its own field dictionary, so this listing is the authority for your export.
Field names that matter to MAB: `ENV.UNQFILE_ID`, `ENV.RO_ID`, `ENV.SUPP_NO`,
`AD1.OWNR_*`, `AD1.INS_CO_NM`, `AD1.CLM_NO`, `AD1.DED_AMT`, `VEH.V_VIN`, `LIN.PART_TYPE`,
`LIN.MOD_LBR_TY`, `LIN.LBR_OP`, `TTL.G_TTL_AMT`, `TTL.N_TTL_AMT`.

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

- Lines with CCC's codes translated: `PAN`/`PAND` new OEM (list / discounted), `PAA` aftermarket, `PAL` recycled, `PAM/PAR/PAC` reconditioned, `PAG` glass, `PAS` sublet, `PAO` other; `LAB` body, `LAR` refinish, `LAM` mechanical, `LAS/LAF` structural, `LAD` diagnostic, `LAE` electrical, `LAG` glass, `LAP` paintless dent; `OP11` replace, `OP2` R&I, `OP9` repair, `OP6` refinish, `OP15` blend, `OP16` sublet. The code tables come from CCC's own Secure Share pages (in `sources.toml`); the printed estimate shows them as A/M, LKQ, RECOND, RECORE instead.
- Maryland duties ride along: non-OEM crash parts raise the §14-2302 aftermarket-disclosure finding, LKQ or reconditioned parts raise the §14-1003 used-parts invoice finding, both only when `[shop] state = "MD"`.
- Betterment lines and `WHO_PAYS = C` lines become customer-pay items, so the front desk's "what you owe" line is right.
- The rates in the estimate's labor profile are compared with your posted rates; anything lower is noted on the RO as a short-pay to address.
- The audit runs the structural checks plus your own 51-rule not-included catalogue (`mab_agents/data/not_included_rules.json`, from the Flask-EST_EMS project). Edit the JSON; no code change needed.

## P-pages, DEG and what the knowledge base may hold

- **MOTOR Guide to Estimating (CCC P-pages)** is live HTML only since 2024, at
  help.cccis.com (linked from degweb.org/ccc-motor-pathways/ and the Guide tab inside
  CCC ONE). MOTOR calls it a licensed product and stopped the PDF edition. Read it from a
  shop browser; do not bulk-copy it into `knowledge/`. Pages an estimator saves for a
  specific job stay internal under the shop's CCC licence.
- **Mitchell CEG P-pages** are free HTML (static.mymitchell.com, JavaScript-rendered).
- **Audatex Qapter DBRM** is a full PDF hosted by DEG (April 2025, in `sources.toml`,
  Audatex copyright, internal use).
- **DEG** itself is free and has no terms of use: the weekly Estimate Tips, the Knowledge
  Base charts (ASA not-included operations chart), the MOTOR revision summaries and the
  inquiry database are all in `sources.toml`. Each resolved inquiry is a plain page at
  `degweb.org/inquiries/<number>/` with an RSS feed at `/inquiries/feed/`; the search page
  is JavaScript-only. When an adjuster denies a line, search DEG for the inquiry number
  and paste it into the supplement.
- The shop's own **51-rule not-included catalogue** (`not_included_rules.json`) is the
  local, editable version of the P-page logic the auditor runs offline.

## Who to call about CRISMA

CRISMA is sold and supported by Jhenn Systems, Inc., Halethorpe MD (800-580-1627,
410-609-0750, info@JhennSys.com; remote support by Techinline). Nothing public documents
its tables or its EMS import; the `BSRO`/`BSLOC` map in MAB comes from the shop's own
CRISMA-STATUS-BOARD and CSM-CRISMA work. If a column or location code changes after a
CRISMA update, `mab sync-crisma` reports the unknown codes and the `[crisma.status_map]`
block in `shop.toml` is the only thing to edit.

## Privacy

Everything stays on the shop's machines. The EMS and CRISMA files are read in
place; MAB keeps its own copy in `mab.db`. When an agent is asked about an RO,
the fields it needs go to the model. Nothing from these files is ever committed
to this repository: the tests use synthetic fixtures written by the test suite.

## Other paths you have explored

- CCC ONE's web app exposes JSON endpoints (`estimate.cccone.com/workfile/api/...`) that the browser calls while you're logged in. They are undocumented and session-bound; useful for reading, fragile to build on, and not something CCC supports. The EMS export is the supported path.
- The `.EST` XML export carries the same sections as the DBF set and is handled by the same importer.
- Mitchell estimates arrive as PDFs; the Estimate-Rekey-Plugin parser can feed them in later through the same `EstimateLine` shape.
