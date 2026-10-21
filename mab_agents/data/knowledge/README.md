# knowledge/

Drop the shop's own documents in this folder and run `mab kb sync`. Every agent
can then search them with `search_knowledge` and will cite the file and page.

Good candidates:

- **MOTOR Guide to Estimating procedure pages (P-pages)** from your estimating system. The Estimate Auditor cites "not included" operations from it.
- **DRP agreements** for each carrier, so the Parts and Insurance agents quote the real rule.
- **OEM repair procedures and position statements** you have downloaded for jobs in the shop (scans, calibrations, sectioning, one-time-use parts).
- **Employee handbook, SOPs, warranty wording, price lists, vendor contacts.**
- **Your own notes**: paste a carrier email into a .txt file and it becomes searchable.

Supported: PDF, .md, .txt, .csv. Only load documents the shop is licensed to
use; the index stays on this machine and only the matching passages are sent to
the model when an agent answers.
