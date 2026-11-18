"""SYNTHETIC engagement files for three fictional clients.

Identifiers (PAN, GSTIN, account numbers, emails, phones) are deliberately FAKE but in
the correct format, so the masking tests have something to mask. Values such as
ZZZZN9999Z are not real PANs.
"""

from scripts.synthetic import FIRM, SynthDoc

ST = "engagement"
NWT, MBL, BRT = "ENG-NWT-FY26", "ENG-MBL-FY26", "ENG-BRT-FY26"

# --- Northwind Textiles Private Limited (manufacturing) ---------------------------

NWT_PLAN = SynthDoc(
    doc_id="ENG-NWT-PLAN",
    filename="northwind_textiles/planning_memo_fy2025_26.md",
    source_type=ST,
    title="Northwind Textiles: Audit Planning Memo FY 2025-26",
    version="1.0",
    effective_date="2026-01-12",
    access_level="staff",
    engagement_id=NWT,
    body=f"""# Northwind Textiles Private Limited: Audit Planning Memo FY 2025-26

{FIRM}. Engagement ENG-NWT-FY26. Prepared by M01, approved by P01, 12 January 2026.

## 1. Client details
- Industry: manufacturer of cotton yarn and fabric, two plants and three godowns.
- PAN: ZZZZN9999Z. GSTIN: 27ZZZZN9999Z1Z9.
- Main bank account (cash credit): 000111222333.
- Client contact: finance@northwind-textiles.example, +91 99999 00001.

## 2. Significant risks identified
1. Inventory existence and valuation: large stocks of yarn at three godowns, including
   slow-moving dyed yarn. Response: attend counts at all three godowns (CHK-INV-01).
2. Revenue cut-off: heavy dispatches in the last week of March. Response: cut-off testing
   of the last dispatches before and first dispatches after year end.
3. Related party purchases: raw cotton bought from a firm in which a director is a
   partner. Response: identify related party relationships and transactions (SA 550).
<!-- source: ICAI ch4 §7 (pp.105-108), SA 550 -->

## 3. Other planned procedures
- Positive confirmations for the 20 largest trade receivables (CHK-CONF-01).
- Going concern: no indicators noted at planning.

## 4. Team
Partner P01, manager M01, staff S01 and S02. Independence engagement declarations filed.
""",
)

NWT_MATERIALITY = SynthDoc(
    doc_id="ENG-NWT-MAT",
    filename="northwind_textiles/materiality_working_fy2025_26.md",
    source_type=ST,
    title="Northwind Textiles: Materiality Working FY 2025-26",
    version="1.0",
    effective_date="2026-01-12",
    access_level="manager",
    engagement_id=NWT,
    body=f"""# Northwind Textiles: Materiality Working FY 2025-26

{FIRM}. Engagement ENG-NWT-FY26. Prepared by M01, 12 January 2026. Manager and partner only.

## 1. Benchmark
Policy: Materiality Policy v2.0 (effective 1 April 2025), manufacturing benchmark 5% of
profit before tax. Profit is stable, so the revenue fallback is not used.

| Item | Amount |
|---|---|
| Profit before tax (projected FY 2025-26) | ₹12.40 crore |
| Overall materiality (5%) | ₹62.00 lakh |
| Performance materiality (60%, moderate risk) | ₹37.20 lakh |
| Clearly trivial (3% of overall) | ₹1.86 lakh |

## 2. Comparison with last year
FY 2024-25 used Materiality Policy v1.0: 1% of revenue of ₹198 crore = ₹1.98 crore.
The change to a profit-based benchmark lowers overall materiality substantially.

## 3. Reasons for performance materiality
60% chosen (within the 50%-75% range of policy v2.0) because of the significant inventory
and related-party risks in the planning memo.
""",
)

NWT_RISK = SynthDoc(
    doc_id="ENG-NWT-RISK",
    filename="northwind_textiles/risk_assessment_fy2025_26.md",
    source_type=ST,
    title="Northwind Textiles: Risk Assessment Summary FY 2025-26",
    version="1.0",
    effective_date="2026-01-20",
    access_level="staff",
    engagement_id=NWT,
    body=f"""# Northwind Textiles: Risk Assessment Summary FY 2025-26

{FIRM}. Engagement ENG-NWT-FY26. Prepared by S01, reviewed by M01, 20 January 2026.

| Area | Assertion | Risk | Planned response |
|---|---|---|---|
| Inventories | Existence, valuation | Significant | Count attendance at all godowns; NRV testing of dyed yarn |
| Revenue | Cut-off, occurrence | Significant | Cut-off tests; dispatch-to-invoice matching |
| Related party purchases | Completeness, disclosure | Significant | Director's interest register; purchase ledger review |
| Trade receivables | Existence | Moderate | Positive confirmations, top 20 balances |
| Borrowings | Completeness | Low | Bank confirmation for cash credit account 000111222333 |
""",
)

NWT_REVIEW = SynthDoc(
    doc_id="ENG-NWT-REV",
    filename="northwind_textiles/review_notes_fy2025_26.md",
    source_type=ST,
    title="Northwind Textiles: Manager Review Notes FY 2025-26",
    version="1.0",
    effective_date="2026-05-18",
    access_level="manager",
    engagement_id=NWT,
    body=f"""# Northwind Textiles: Manager Review Notes FY 2025-26

{FIRM}. Engagement ENG-NWT-FY26. Reviewer M01, 18 May 2026. Manager and partner only.

1. Godown 3 count: test counts showed 4% shortage in dyed yarn. Extend testing and obtain
   management's explanation before concluding. (Open)
2. Two receivable confirmations returned with exceptions; the team must investigate and
   project the effect to the population. (Open)
3. Related party: purchases from the director's firm not yet agreed to board approvals. (Open)
4. Contact for follow-up: accounts.head@northwind-textiles.example, +91 99999 00002.
""",
)

# --- Meridian Bank Limited, Lakeview Branch (bank branch audit) ---------------------

MBL_PLAN = SynthDoc(
    doc_id="ENG-MBL-PLAN",
    filename="meridian_bank_lakeview/planning_memo_fy2025_26.md",
    source_type=ST,
    title="Meridian Bank Lakeview Branch: Audit Planning Memo FY 2025-26",
    version="1.0",
    effective_date="2026-03-20",
    access_level="staff",
    engagement_id=MBL,
    body=f"""# Meridian Bank Limited, Lakeview Branch: Audit Planning Memo FY 2025-26

{FIRM}. Engagement ENG-MBL-FY26. Prepared by M02, approved by P02, 20 March 2026.

## 1. Branch profile
- Total advances at 31 March 2026 (provisional): ₹128 crore; deposits ₹310 crore.
- Branch contact: lakeview.manager@meridian-bank.example, +91 99999 00010.

## 2. Engagement quality control review
Total advances of ₹128 crore exceed the ₹100 crore threshold in Technical Circular 2026/03,
so an EQCR is required before signing.

## 3. Focus areas
1. NPA classification of cash credit accounts, especially accounts regularised just before
   31 March 2026 (Circular 2026/03, section 3).
2. Drawing power computation for stock-based cash credit limits.
3. Provisioning for doubtful assets by age category.

## 4. Team
Partner P02, manager M02, staff S02 and S03.
""",
)

MBL_NPA = SynthDoc(
    doc_id="ENG-MBL-NPA",
    filename="meridian_bank_lakeview/npa_review_working_fy2025_26.md",
    source_type=ST,
    title="Meridian Bank Lakeview Branch: NPA Review Working FY 2025-26",
    version="1.0",
    effective_date="2026-04-14",
    access_level="staff",
    engagement_id=MBL,
    body=f"""# Meridian Bank Lakeview Branch: NPA Review Working FY 2025-26

{FIRM}. Engagement ENG-MBL-FY26. Prepared by S03, 14 April 2026. Borrower names are fictional.

| Borrower (fictional) | Facility | Account no. | Branch classification | Audit finding |
|---|---|---|---|---|
| Sunrise Agro Traders | Cash credit | 000444555666 | Standard | No credits for 90 days to 31 March: out of order, should be NPA |
| Lotus Weaves | Term loan + cash credit | 000777888999 | Term loan NPA, CC standard | Borrower-wise rule: CC also NPA |
| Harbour Logistics | Cash credit | 000123123123 | Standard | Large credit on 30 March reversed on 3 April: deemed NPA |
| Greenleaf Foods | Term loan | 000321321321 | Doubtful D1 | Provision at 25% secured + 100% unsecured verified |

Borrower PAN on file for Sunrise Agro Traders: ZZZZS1111Z (fake, for masking tests).
<!-- source: ICAI ch10 §12.5 (pp.27-29) for the classification rules applied -->
""",
)

MBL_REVIEW = SynthDoc(
    doc_id="ENG-MBL-REV",
    filename="meridian_bank_lakeview/review_notes_fy2025_26.md",
    source_type=ST,
    title="Meridian Bank Lakeview Branch: Manager Review Notes FY 2025-26",
    version="1.0",
    effective_date="2026-04-22",
    access_level="manager",
    engagement_id=MBL,
    body=f"""# Meridian Bank Lakeview Branch: Manager Review Notes FY 2025-26

{FIRM}. Engagement ENG-MBL-FY26. Reviewer M02, 22 April 2026. Manager and partner only.

1. Three accounts reclassified to NPA by the audit team (see NPA review working). Branch
   management has agreed to two; Harbour Logistics is disputed. Escalate to P02.
2. EQCR reviewer to be appointed; draft report not to be released until EQCR is complete.
""",
)

# --- Brightpath Rural Education Trust (non-profit) ----------------------------------

BRT_PLAN = SynthDoc(
    doc_id="ENG-BRT-PLAN",
    filename="brightpath_trust/planning_memo_fy2025_26.md",
    source_type=ST,
    title="Brightpath Rural Education Trust: Audit Planning Memo FY 2025-26",
    version="1.0",
    effective_date="2026-02-02",
    access_level="staff",
    engagement_id=BRT,
    body=f"""# Brightpath Rural Education Trust: Audit Planning Memo FY 2025-26

{FIRM}. Engagement ENG-BRT-FY26. Prepared by M02, approved by P01, 2 February 2026.

## 1. Entity
- Public charitable trust running six rural schools; receives donations, grants and some
  foreign contributions.
- PAN: ZZZZB2222Z. Donations account: 000999000111.
- Contact: trustee.office@brightpath-trust.example, +91 99999 00020.

## 2. Legal framework to consider
<!-- source: ICAI ch9 §14 (p.86) -->
Public Trust Act of the relevant State (or the Indian Trusts Act, 1882 if there is none),
the Income Tax Act, 1961, and, because of foreign contributions, the Foreign Contribution
(Regulation) Act, 2010.

## 3. Books of account to verify
<!-- source: ICAI ch9 §14 (p.87) -->
Cash book, ledger, journal, copies of serially numbered bills and receipts issued, and
original bills and receipts for payments.

## 4. Significant risks
1. Completeness of cash donations received at school events.
2. Utilisation of restricted grants for the stated purpose.

## 5. Team
Partner P01, manager M02, staff S03.
""",
)

BRT_RISK = SynthDoc(
    doc_id="ENG-BRT-RISK",
    filename="brightpath_trust/risk_assessment_fy2025_26.md",
    source_type=ST,
    title="Brightpath Rural Education Trust: Risk Assessment Summary FY 2025-26",
    version="1.0",
    effective_date="2026-02-10",
    access_level="staff",
    engagement_id=BRT,
    body=f"""# Brightpath Rural Education Trust: Risk Assessment Summary FY 2025-26

{FIRM}. Engagement ENG-BRT-FY26. Prepared by S03, reviewed by M02, 10 February 2026.

| Area | Risk | Planned response |
|---|---|---|
| Cash donations | Significant (completeness) | Receipt book sequence check; event-wise collection reports |
| Restricted grants | Significant (utilisation) | Grant agreement terms vs. expenditure ledger |
| Foreign contributions | Moderate (compliance) | Separate designated account, utilisation records |
| Fixed assets of schools | Low | Physical verification sample |
""",
)

BRT_PARTNER_NOTE = SynthDoc(
    doc_id="ENG-BRT-REV",
    filename="brightpath_trust/partner_review_note_fy2025_26.md",
    source_type=ST,
    title="Brightpath Rural Education Trust: Partner Review Note FY 2025-26",
    version="1.0",
    effective_date="2026-06-05",
    access_level="partner",
    engagement_id=BRT,
    body=f"""# Brightpath Rural Education Trust: Partner Review Note FY 2025-26

{FIRM}. Engagement ENG-BRT-FY26. P01, 5 June 2026. Partners only.

1. Grant utilisation: ₹18 lakh of a restricted grant was spent on general administration.
   Discuss with the trustees before deciding the effect on the report.
2. Consider whether the matter needs consultation with the Technical & Quality Desk.
""",
)

DOCS: list[SynthDoc] = [
    NWT_PLAN,
    NWT_MATERIALITY,
    NWT_RISK,
    NWT_REVIEW,
    MBL_PLAN,
    MBL_NPA,
    MBL_REVIEW,
    BRT_PLAN,
    BRT_RISK,
    BRT_PARTNER_NOTE,
]
