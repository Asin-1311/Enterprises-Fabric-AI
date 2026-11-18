"""SYNTHETIC templates (fictional firm). Placeholders are in [square brackets]."""

from scripts.synthetic import FIRM, SynthDoc

ST = "templates"

ENGAGEMENT_LETTER = SynthDoc(
    doc_id="TPL-EL-01",
    filename="engagement_letter_template.md",
    source_type=ST,
    title="Template: Audit Engagement Letter (SA 210)",
    version="2.0",
    effective_date="2024-04-01",
    access_level="manager",
    body=f"""# Template: Audit Engagement Letter (SA 210)

{FIRM}. Version 2.0, effective 1 April 2024. Issued by the engagement partner only.

## 1. When to use
<!-- source: ICAI ch11 §9 and §9A (pp.15-16), SA 210 -->
Before accepting or continuing an audit, confirm the preconditions for an audit: an
acceptable financial reporting framework, and management's agreement that it acknowledges
and understands its responsibilities. Then agree the terms in this letter.

## 2. Required contents
<!-- source: ICAI ch11 §9 (pp.17-18), SA 210 -->
The letter must cover the objective and scope of the audit, the responsibilities of the
auditor, the responsibilities of management, the applicable financial reporting framework,
and the expected form and content of reports, with a statement that a report may differ
from its expected form and content.

## 3. Letter text
<!-- source: structure follows the ICAI example engagement letter, ch11 §10 (pp.18-21) -->
[Date]

To the Board of Directors of [Client name]

**The objective and scope of the audit.** You have requested that we audit the financial
statements of [Client name], which comprise the Balance Sheet as at [date], the Statement
of Profit and Loss and the Cash Flow Statement for the year then ended, and notes to the
financial statements. The objective of our audit is to obtain reasonable assurance about
whether the financial statements as a whole are free from material misstatement, whether
due to fraud or error, and to issue an auditor's report that includes our opinion.

**Our responsibilities.** [Standard paragraph: audit conducted in accordance with the
Standards on Auditing; reasonable assurance is not a guarantee.]

**Management's responsibilities.** [Standard paragraph: preparation of the financial
statements; internal control; access to information and persons; written representations.]

**Reporting.** [Expected form of report; may differ in the circumstances.]

**Fees and other terms.** [To be completed by the engagement partner.]

For {FIRM}
[Engagement partner], Partner
""",
)

REPRESENTATION_LETTER = SynthDoc(
    doc_id="TPL-MRL-01",
    filename="management_representation_letter_template.md",
    source_type=ST,
    title="Template: Management Representation Letter (SA 580)",
    version="1.2",
    effective_date="2024-04-01",
    access_level="staff",
    body=f"""# Template: Management Representation Letter (SA 580)

{FIRM}. Version 1.2, effective 1 April 2024.

## 1. Rules for using this template
<!-- source: ICAI ch7 §4.1 (p.30), §4.4 (p.31), §4.12 and date rules (p.36), SA 580 -->
- Request the letter from management with appropriate responsibility for the financial
  statements and knowledge of the matters concerned.
- Date it as near as practicable to, but not after, the date of the auditor's report. The
  auditor's report cannot be dated before the representation letter.
- Cover all financial statements and periods referred to in the auditor's report.
- Written representations are necessary evidence but are NOT sufficient appropriate
  evidence on their own for any matter they deal with.

## 2. Letter text
[Client letterhead] [Date]

To [Firm name], Chartered Accountants

This representation letter is provided in connection with your audit of the financial
statements of [Client name] for the year ended [date].

**Preparation of the financial statements.** We have fulfilled our responsibility for the
preparation of the financial statements in accordance with [framework], as set out in the
terms of the audit engagement.
<!-- source: ICAI ch7 §4.5 (p.31) -->

**Information provided.** We have provided you with all relevant information and access
as agreed in the terms of the audit engagement, and all transactions have been recorded.

**Subsequent events.** All events occurring after the date of the financial statements for
which [framework] requires adjustment or disclosure have been adjusted or disclosed.
<!-- source: ICAI ch7 §1 (p.7), SA 560 -->

**Uncorrected misstatements.** We believe the effects of uncorrected misstatements are
immaterial, individually and in aggregate. A list is attached.
<!-- source: ICAI ch7 §3.8 (p.27), SA 450 -->

[Other specific representations as required by the engagement team.]

[Signed by the CEO and CFO]
""",
)

AUDIT_REPORT_FORMATS = SynthDoc(
    doc_id="TPL-AR-01",
    filename="audit_report_formats.md",
    source_type=ST,
    title="Template: Auditor's Report Formats (SA 700 / 705 / 706)",
    version="1.0",
    effective_date="2024-04-01",
    access_level="manager",
    body=f"""# Template: Auditor's Report Formats (SA 700 / 705 / 706)

{FIRM}. Version 1.0, effective 1 April 2024.

> Drafting aid only. The form of opinion is decided and signed by the engagement partner.
> Nothing produced from this template is a final opinion.

## 1. Unmodified opinion (SA 700)
<!-- source: ICAI ch8 §1 and §2 (p.8) -->
Used when the auditor concludes that the financial statements are prepared, in all
material respects, in accordance with the applicable financial reporting framework.

## 2. Choosing a modified opinion (SA 705)
<!-- source: ICAI ch8 §5.2-§5.3 (pp.35-36) -->
A modification is needed when the financial statements as a whole are not free from
material misstatement, or when sufficient appropriate audit evidence cannot be obtained.

| Situation | Material, not pervasive | Material and pervasive |
|---|---|---|
| Misstatement found (evidence obtained) | Qualified opinion | Adverse opinion |
| Unable to obtain sufficient appropriate evidence | Qualified opinion | Disclaimer of opinion |

## 3. Emphasis of Matter paragraph (SA 706)
<!-- source: ICAI ch8 §6.2 and §6.2.1 (p.49) -->
Include one only when the matter is fundamental to users' understanding, the opinion would
not need to be modified for it, and (where SA 701 applies) it is not a key audit matter.
Put it in a separate section headed "Emphasis of Matter", refer to the relevant disclosure,
and state that the opinion is not modified in respect of the matter. Examples: uncertainty
over exceptional litigation, a significant subsequent event, early application of a new
accounting standard, a major catastrophe.

## 4. Report skeleton
- Title: Independent Auditor's Report
- Addressee
- Opinion section [type decided by the engagement partner]
- Basis for Opinion
- [Emphasis of Matter, if any]
- [Key Audit Matters, where SA 701 applies]
- Responsibilities of management; Auditor's responsibilities
- Report on other legal and regulatory requirements
- Signature, place, date [not earlier than the date of the written representations]
""",
)

DOCS: list[SynthDoc] = [ENGAGEMENT_LETTER, REPRESENTATION_LETTER, AUDIT_REPORT_FORMATS]
