# Placeholders to fill in (author checklist)

Every value below was invented for the demo or is unverified. Replace each one with a value
you have checked, or confirm it is fine as a fictional firm choice. Tick the box when done.

## How to edit
1. Edit the **source** file in `scripts/synthetic/` (or `scripts/make_synthetic_docs.py`),
   not the generated `.md` files in `data/corpus/`. Those are overwritten on regeneration.
2. Where a value appears in several places (see "Also in"), change all of them so the
   documents stay consistent.
3. Regenerate and check:
   ```powershell
   .venv\Scripts\python.exe -m scripts.make_synthetic_docs --force
   .venv\Scripts\python.exe -m app.ingest.manifest
   .venv\Scripts\python.exe -m pytest
   ```
4. Finish this before M7. The evaluation gold answers will quote these values.

Line numbers are as of M1 (2026-11-07) and may shift slightly after edits.

---

## A. Unverified statutory facts (verify against the current official source)

| ✓ | What | Current placeholder | Where | Also in |
|---|---|---|---|---|
| [ ] | Tax audit report due date, FY 2024-25 | 30 September 2025 | `circulars.py:178` | `calendar_docs.py:25` (FY 2025-26 calendar) |
| [ ] | Tax audit report due date, FY 2025-26 | 30 September 2026 | `circulars.py:201` | `calendar_docs.py:25` (FY 2026-27 calendar) |
| [ ] | Income tax return due date, audited cases | 31 October (each year) | `calendar_docs.py:26` | none |
| [ ] | ICAI study material edition date (manifest `effective_date` of all 8 chapters) | 2025-04-01 | `make_synthetic_docs.py:45` | none |
| [ ] | ICAI CA Final study material edition date (manifest `effective_date` of the 153 CAF- chapters) | 2025-04-01 | `scripts/add_ca_final.py` (`EDITION_DATE_PLACEHOLDER`) | re-run `python -m scripts.add_ca_final` after changing it |
| [ ] | ICAI Taxation, Income Tax Law chapters: date of the law position (the files state none) | 2025-04-01 | `scripts/add_tax_law.py` (`INCOME_TAX_DATE_PLACEHOLDER`) | re-run `python -m scripts.add_tax_law`. (GST chapters use 2026-04-30, which they state themselves.) |

Note: the calendar builds both years from one template, so `calendar_docs.py:25-26` sets
the date for both. If the two years differ, the template needs a per-year value.

## B. Materiality (SA 320 is not in ICAI ch4-11, so every figure is invented)

| ✓ | What | Current value | Where | Also in |
|---|---|---|---|---|
| [ ] | v1 manufacturing benchmark | 1% of total revenue | `firm_policy.py:90` | `firm_policy.py:155`; `engagement.py:77` |
| [ ] | v1 and v2 trading benchmark | 0.5% of total revenue | `firm_policy.py:91`, `:136` | none |
| [ ] | v1 and v2 not-for-profit benchmark | 1% of total expenditure | `firm_policy.py:92`, `:137` | none |
| [ ] | v1 performance materiality | 75% of overall | `firm_policy.py:96` | none |
| [ ] | v1 clearly trivial threshold | 5% of overall | `firm_policy.py:99` | `firm_policy.py:158` |
| [ ] | v2 manufacturing benchmark | 5% of profit before tax | `firm_policy.py:134` | `firm_policy.py:155`; `engagement.py:66` |
| [ ] | v2 fallback when PBT volatile | 1% of total revenue | `firm_policy.py:135` | none |
| [ ] | v2 performance materiality range | 50%-75% | `firm_policy.py:141` | `engagement.py` (Northwind uses 60%) |
| [ ] | v2 clearly trivial threshold | 3% of overall | `firm_policy.py:146` | `firm_policy.py:158`; `engagement.py:74` |
| [ ] | Northwind materiality working figures (PBT ₹12.40 cr → ₹62 lakh / ₹37.20 lakh / ₹1.86 lakh; prior year revenue ₹198 cr → ₹1.98 cr) | as shown | `engagement.py:66-82` | recalculate if any percentage above changes |

## C. Fictional firm rules (check they are sensible, or keep as demo choices)

| ✓ | What | Current value | Where | Also in |
|---|---|---|---|---|
| [ ] | Firm file-assembly deadline (must stay ≤ SA 230's 60 days) | 45 days | `firm_policy.py:229`, `:257` | `calendar_docs.py:38`; `faq.py:23` |
| [ ] | Bank branch EQCR threshold, FY 2024-25 circular | ₹150 crore advances | `circulars.py:38` | `circulars.py:80`, `:86` |
| [ ] | Bank branch EQCR threshold, FY 2025-26 circular | ₹100 crore advances | `circulars.py:79` | `circulars.py:86`; `engagement.py:151` (Meridian's ₹128 crore must stay above it) |
| [ ] | Sampling v1: flat minimum for tests of controls | 25 items | `circulars.py:114` | `circulars.py:157` |
| [ ] | Sampling v2: risk-based minimums | 25 / 40 / 60 | `circulars.py:150-154` | `circulars.py:157` |
| [ ] | Fee-dependence review trigger | 15% of total firm fees | `firm_policy.py:429` | `circulars.py` (Partner Circular 2026/02 refers to "the 15% review trigger") |
| [ ] | EQCR criteria for non-listed engagements | 3 criteria listed | `firm_policy.py:385-390` | none |
| [ ] | Annual independence declaration date | 30 April | `firm_policy.py:298`, `:336` | `calendar_docs.py:31`; `faq.py:64` |
| [ ] | Internal deadline: draft tax audit reports | 31 August | `calendar_docs.py:32` | `circulars.py:179`, `:202` |
| [ ] | Fee-dependence review deadline | 31 March | `calendar_docs.py:33` | `circulars.py` (Partner Circular 2026/02) |
| [ ] | Bank audit season planning and timing | March meeting / April audits | `calendar_docs.py:34-35` | `circulars.py:182`, `:205` |

## D. Spot-checks of ICAI source comments (not placeholders, but please verify)

Every `<!-- source: ICAI chN §x.y (p.n) -->` comment in `scripts/synthetic/` names the
chapter, section and PDF page the statement came from. Please spot-check at least these:

| ✓ | Statement | Source comment |
|---|---|---|
| [ ] | File assembly ordinarily ≤ 60 days; retention ≥ 7 years | ICAI ch6 §1.9 (p.9) |
| [ ] | Positive vs negative confirmations; 4 conditions for negative-only | ICAI ch4 §3.4 (p.44), §3.7 (p.51) |
| [ ] | NPA: > 90 days overdue; out of order; borrower-wise; provisioning % | ICAI ch10 §12.5 (pp.26-29) |
| [ ] | Five threats to independence | ICAI ch11 §6 (pp.10-12) |
| [ ] | EQCR mandatory for listed entities | ICAI ch11 SQC 1 element E (p.30) |
| [ ] | Representation letter dated as near as practicable to, not after, the report | ICAI ch7 (p.36) |
| [ ] | Qualified / adverse / disclaimer criteria | ICAI ch8 §5.3 (p.36) |
