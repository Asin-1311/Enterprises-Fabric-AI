"""SYNTHETIC firm policies of Example & Associates (fictional firm)."""

from scripts.synthetic import FIRM, SynthDoc

ST = "firm_policy"

METHODOLOGY = SynthDoc(
    doc_id="POL-METH-01",
    filename="audit_methodology_manual_v3.md",
    source_type=ST,
    title="Audit Methodology Manual",
    version="3.0",
    effective_date="2024-04-01",
    access_level="staff",
    body=f"""# Audit Methodology Manual

{FIRM}. Version 3.0, effective 1 April 2024. Owner: Technical & Quality Desk.

## 1. Purpose
This manual describes how engagement teams of the firm plan, perform and complete
statutory audits. It supports, and does not replace, the Standards on Auditing and the
professional judgement of the engagement partner.

## 2. Audit evidence
### 2.1 Sufficient appropriate audit evidence
<!-- source: ICAI ch4 §1.5 and §1.5.1 (pp.10-11), SA 500 -->
Teams must obtain sufficient appropriate audit evidence for every significant assertion.
Sufficiency is the quantity of evidence; appropriateness is its quality, that is, its
relevance and reliability. The higher the assessed risk, the more evidence is needed;
the higher the quality of evidence, the less may be needed.

### 2.2 Reliability of evidence
<!-- source: ICAI ch4 §1.4-§1.5 (p.10), SA 500 -->
When choosing procedures, prefer evidence that is obtained from independent sources,
obtained directly by the auditor, documentary rather than oral, and original rather
than a photocopy. Evidence generated internally is more reliable when the related
controls are effective.

### 2.3 Procedures
<!-- source: ICAI ch4 §1.5.2 (p.11) -->
Use inspection, observation, external confirmation, recalculation, reperformance and
analytical procedures, often in combination, in addition to inquiry. Inquiry alone is
never enough for a significant assertion.

## 3. Specific areas
- Inventory: follow checklist CHK-INV-01 (attendance at physical inventory counting).
- Trade receivables and bank balances: follow checklist CHK-CONF-01 (external confirmations).
- Bank branch audits: follow checklist CHK-BANK-01 and the current bank audit circular.
- Going concern: follow checklist CHK-GC-01.
- Subsequent events: follow checklist CHK-SE-01.
- Sampling: follow the current sampling guidance circular.

## 4. Completion
Before the report is signed the team completes the subsequent events review, the going
concern evaluation, the summary of uncorrected misstatements and the management
representation letter (template TPL-MRL-01).
<!-- source: ICAI ch7 introduction (p.4) -->

## 5. Documentation
Documentation and file assembly follow the firm's current Documentation and Retention
Policy.
""",
)

MATERIALITY_V1 = SynthDoc(
    doc_id="POL-MAT-01",
    filename="materiality_policy_v1.md",
    source_type=ST,
    title="Materiality Policy",
    version="1.0",
    effective_date="2023-04-01",
    access_level="manager",
    body=f"""# Materiality Policy

{FIRM}. Version 1.0, effective 1 April 2023.

> Note for the author: SA 320 (materiality in planning and performing an audit) is not
> part of the ICAI chapters 4-11 used in this prototype. The benchmark percentages below
> are FICTIONAL firm choices made for the demonstration, not ICAI guidance. Check them
> against SA 320 before relying on any figure.

## 1. Scope
This policy sets the firm's default benchmarks for overall materiality, performance
materiality and the clearly trivial threshold. The engagement partner may depart from a
default with documented reasons.

## 2. Overall materiality benchmarks
| Client type | Benchmark |
|---|---|
| Manufacturing | 1% of total revenue |
| Trading | 0.5% of total revenue |
| Not-for-profit (trusts, societies) | 1% of total expenditure |
| Bank branch audits | As instructed by the central statutory auditors |

## 3. Performance materiality
Performance materiality is set at 75% of overall materiality.

## 4. Clearly trivial threshold
Misstatements below 5% of overall materiality are treated as clearly trivial and are not
accumulated.
<!-- source: ICAI ch7 §3.3 (p.25) and §3.9 (p.27), SA 450 - accumulate misstatements other than clearly trivial ones, and document the clearly trivial amount -->

## 5. Documentation
The materiality working must state the benchmark, the percentage, the amount and the
reasons for the choice, and must be reviewed by the engagement manager.
""",
)

MATERIALITY_V2 = SynthDoc(
    doc_id="POL-MAT-02",
    filename="materiality_policy_v2.md",
    source_type=ST,
    title="Materiality Policy",
    version="2.0",
    effective_date="2025-04-01",
    access_level="manager",
    supersedes="POL-MAT-01",
    body=f"""# Materiality Policy

{FIRM}. Version 2.0, effective 1 April 2025. Supersedes version 1.0 (effective 1 April 2023).

> Note for the author: SA 320 is not part of the ICAI chapters 4-11 used in this
> prototype. The benchmark percentages below are FICTIONAL firm choices made for the
> demonstration, not ICAI guidance. Check them against SA 320 before relying on any figure.

## 1. Scope
This policy sets the firm's default benchmarks for overall materiality, performance
materiality and the clearly trivial threshold. The engagement partner may depart from a
default with documented reasons.

## 2. Overall materiality benchmarks
| Client type | Benchmark |
|---|---|
| Manufacturing | 5% of profit before tax from continuing operations |
| Manufacturing, where profit before tax is volatile or close to nil | 1% of total revenue |
| Trading | 0.5% of total revenue |
| Not-for-profit (trusts, societies) | 1% of total expenditure |
| Bank branch audits | As instructed by the central statutory auditors |

## 3. Performance materiality
Performance materiality is set between 50% and 75% of overall materiality, depending on
the assessed risk of material misstatement: 50% for higher-risk engagements and first-year
audits, 75% for lower-risk recurring engagements.

## 4. Clearly trivial threshold
Misstatements below 3% of overall materiality are treated as clearly trivial and are not
accumulated.
<!-- source: ICAI ch7 §3.3 (p.25) and §3.9 (p.27), SA 450 -->

## 5. Documentation
The materiality working must state the benchmark, the percentage, the amount and the
reasons for the choice, and must be reviewed by the engagement manager.

## 6. What changed from version 1.0
- Manufacturing benchmark changed from 1% of total revenue to 5% of profit before tax,
  with revenue kept as the fallback when profit is volatile or close to nil.
- Performance materiality changed from a flat 75% to a risk-based 50%-75% range.
- Clearly trivial threshold reduced from 5% to 3% of overall materiality.
""",
)

DOCUMENTATION_V1 = SynthDoc(
    doc_id="POL-DOC-01",
    filename="documentation_retention_policy_v1.md",
    source_type=ST,
    title="Documentation and Retention Policy",
    version="1.0",
    effective_date="2022-04-01",
    access_level="staff",
    body=f"""# Documentation and Retention Policy

{FIRM}. Version 1.0, effective 1 April 2022.

## 1. Timely preparation
<!-- source: ICAI ch6 §1.7 (p.8), SA 230 -->
Working papers are prepared as the work is performed, not afterwards. Documentation
prepared after the work is likely to be less accurate.

## 2. Assembly of the final audit file
<!-- source: ICAI ch6 §1.9 (p.9), SA 230; ICAI ch11 SQC 1 element E (p.31) -->
The final audit file must be assembled within 60 days after the date of the auditor's
report. Assembly is an administrative process: no new audit procedures are performed and
no new conclusions are drawn.

Administrative changes allowed during assembly include deleting or discarding superseded
documentation, sorting, collating and cross-referencing working papers, signing off
completion checklists, and documenting evidence that was obtained, discussed and agreed
with the engagement team before the date of the auditor's report.

## 3. After assembly
<!-- source: ICAI ch6 §1.9 (p.9) -->
After the final audit file has been assembled, no documentation of any nature may be
deleted or discarded before the end of the retention period.

## 4. Retention
<!-- source: ICAI ch6 §1.9 (p.9); ICAI ch11 SQC 1 element E (p.31) -->
Audit engagement files are retained for at least seven years from the date of the
auditor's report, or, if later, the date of the group auditor's report.

## 5. Ownership
<!-- source: ICAI ch11 SQC 1 element E (p.31) -->
Unless law or regulation says otherwise, engagement documentation is the property of the firm.
""",
)

DOCUMENTATION_V2 = SynthDoc(
    doc_id="POL-DOC-02",
    filename="documentation_retention_policy_v2.md",
    source_type=ST,
    title="Documentation and Retention Policy",
    version="2.0",
    effective_date="2025-04-01",
    access_level="staff",
    supersedes="POL-DOC-01",
    body=f"""# Documentation and Retention Policy

{FIRM}. Version 2.0, effective 1 April 2025. Supersedes version 1.0 (effective 1 April 2022).

## 1. Timely preparation
<!-- source: ICAI ch6 §1.7 (p.8), SA 230 -->
Working papers are prepared as the work is performed, not afterwards. Documentation
prepared after the work is likely to be less accurate.

## 2. Assembly of the final audit file
<!-- source: ICAI ch6 §1.9 (p.9), SA 230; ICAI ch11 SQC 1 element E (p.31) -->
SA 230 and SQC 1 set the outer limit: assembly of the final audit file is ordinarily
completed not more than 60 days after the date of the auditor's report.

Firm rule from version 2.0: engagement teams must complete assembly within 45 days after
the date of the auditor's report. The 45-day firm deadline is stricter than, and within,
the 60-day limit in SA 230. Any file not assembled by day 45 is escalated to the
engagement partner and must still be completed before day 60.

Assembly is an administrative process: no new audit procedures are performed and no new
conclusions are drawn. Administrative changes allowed during assembly include deleting or
discarding superseded documentation, sorting, collating and cross-referencing working
papers, signing off completion checklists, and documenting evidence that was obtained,
discussed and agreed with the engagement team before the date of the auditor's report.

## 3. After assembly
<!-- source: ICAI ch6 §1.9 (p.9) -->
After the final audit file has been assembled, no documentation of any nature may be
deleted or discarded before the end of the retention period.

## 4. Retention and electronic archiving
<!-- source: ICAI ch6 §1.9 (p.9); ICAI ch11 SQC 1 element E (p.31) -->
Audit engagement files are retained for at least seven years from the date of the
auditor's report, or, if later, the date of the group auditor's report. From version 2.0
all assembled files are archived in the firm's read-only electronic archive; paper
originals are scanned and indexed before archiving.

## 5. Ownership
<!-- source: ICAI ch11 SQC 1 element E (p.31) -->
Unless law or regulation says otherwise, engagement documentation is the property of the firm.

## 6. What changed from version 1.0
- Firm deadline for file assembly reduced from 60 days to 45 days after the date of the
  auditor's report (the SA 230 outer limit of 60 days is unchanged).
- Electronic read-only archiving made mandatory.
""",
)

INDEPENDENCE_V1 = SynthDoc(
    doc_id="POL-IND-01",
    filename="independence_ethics_policy_v1.md",
    source_type=ST,
    title="Independence and Ethics Policy",
    version="1.0",
    effective_date="2023-04-01",
    access_level="staff",
    body=f"""# Independence and Ethics Policy

{FIRM}. Version 1.0, effective 1 April 2023.

## 1. Independence in fact and in appearance
<!-- source: ICAI ch11 §5 (p.10) -->
Independence must exist in fact and must also appear to exist to a reasonable person.
Every member of staff must judge whether they can act independently in the circumstances
and must not place themselves in a position that compromises independence.

## 2. Threats to independence
<!-- source: ICAI ch11 §6 (pp.10-12) -->
Staff must be alert to the five types of threat:
- Self-interest: for example a financial interest in a client, a loan to or from a client,
  undue dependence on a client's fees, a close business relationship, or potential
  employment with a client.
- Self-review: for example reviewing work the firm itself performed, or a team member who
  was recently a director or senior officer of the client.
- Advocacy: for example dealing in the client's shares or acting as the client's advocate
  in litigation.
- Familiarity: for example a close relative in a senior position at the client, long
  association with the client, or accepting significant gifts or hospitality.
- Intimidation: for example threats of replacement over accounting disagreements or
  pressure to cut work because of reduced fees.

## 3. Annual independence declaration
Every partner and member of staff signs the firm's independence declaration form once a
year, by 30 April, covering all clients of the firm.

## 4. Gifts and hospitality
Gifts or hospitality from a client must be declared to the engagement manager.
""",
)

INDEPENDENCE_V2 = SynthDoc(
    doc_id="POL-IND-02",
    filename="independence_ethics_policy_v2.md",
    source_type=ST,
    title="Independence and Ethics Policy",
    version="2.0",
    effective_date="2025-07-01",
    access_level="staff",
    supersedes="POL-IND-01",
    body=f"""# Independence and Ethics Policy

{FIRM}. Version 2.0, effective 1 July 2025. Supersedes version 1.0 (effective 1 April 2023).

## 1. Independence in fact and in appearance
<!-- source: ICAI ch11 §5 (p.10) -->
Independence must exist in fact and must also appear to exist to a reasonable person.
Every member of staff must judge whether they can act independently in the circumstances
and must not place themselves in a position that compromises independence.

## 2. Threats to independence
<!-- source: ICAI ch11 §6 (pp.10-12) -->
Staff must be alert to the five types of threat: self-interest, self-review, advocacy,
familiarity and intimidation. Examples are given in Annex A.

## 3. Safeguards
<!-- source: ICAI ch11 §7 (p.12) -->
Where a threat is identified, the engagement partner applies safeguards that reduce it
to an acceptable level, or the person is removed from the engagement.

## 4. Independence declarations
From version 2.0 there are two declarations:
- Annual declaration: signed by every partner and member of staff by 30 April each year.
- Engagement declaration (new): signed by every engagement team member before starting
  work on each engagement, naming that client. Work may not start until it is filed.

## 5. Gifts and hospitality
Gifts or hospitality from a client must be declared to the engagement manager. Anything
other than token items must be declined.

## Annex A: examples of threats
<!-- source: ICAI ch11 §6 (pp.11-12) -->
- Self-interest: financial interest in a client, loan to or from a client, undue
  dependence on a client's fees, close business relationship, potential employment.
- Self-review: reviewing the firm's own earlier work; a team member recently a director
  or senior officer of the client.
- Advocacy: dealing in the client's shares; acting as the client's advocate in litigation.
- Familiarity: close relative in a senior position at the client; long association;
  significant gifts or hospitality.
- Intimidation: threat of replacement over disagreements; pressure to reduce work
  because of reduced fees; threat of litigation.

## 6. What changed from version 1.0
- A per-engagement independence declaration was added to the annual declaration.
- A section on safeguards was added.
- Non-token gifts must now be declined, not only declared.
""",
)

QUALITY = SynthDoc(
    doc_id="POL-QM-01",
    filename="quality_management_policy.md",
    source_type=ST,
    title="Quality Management Policy (SA 220 / SQC 1 aligned)",
    version="1.0",
    effective_date="2024-04-01",
    access_level="manager",
    body=f"""# Quality Management Policy (SA 220 / SQC 1 aligned)

{FIRM}. Version 1.0, effective 1 April 2024.

## 1. Engagement performance and consultation
<!-- source: ICAI ch11 SQC 1 element E (p.30) -->
Engagement teams are briefed on objectives, supervised and reviewed. Difficult or
contentious matters must be referred for consultation with the Technical & Quality Desk,
and the consultation and its conclusion are documented.

## 2. Engagement quality control review (EQCR)
<!-- source: ICAI ch11 SQC 1 element E (pp.30-31) -->
Engagement quality control review is mandatory for all audits of financial statements of
listed entities. SQC 1 requires the firm to set criteria for other engagements; the
firm's criteria (a fictional firm choice) are:
- bank branch audits where the branch's advances exceed the threshold set in the current
  bank audit circular;
- any engagement where a going concern material uncertainty is being considered;
- any engagement where a modified opinion is proposed.

The EQCR does not reduce the responsibilities of the engagement partner. The report is
issued only after differences between the engagement partner and the reviewer are
resolved, using the firm's consultation procedure if needed.

## 3. Monitoring
<!-- source: ICAI ch11 SQC 1 element F (p.31) -->
The Technical & Quality Desk inspects a selection of completed engagements each year.
""",
)

ACCEPTANCE = SynthDoc(
    doc_id="POL-ACC-01",
    filename="client_acceptance_continuance_procedure.md",
    source_type=ST,
    title="Client Acceptance and Continuance Procedure",
    version="1.0",
    effective_date="2024-04-01",
    access_level="partner",
    body=f"""# Client Acceptance and Continuance Procedure

{FIRM}. Version 1.0, effective 1 April 2024. Partner-only document.

## 1. Competence and resources
<!-- source: ICAI ch11, Test Your Knowledge answer 10 (p.58) restating SQC 1 -->
Before accepting an engagement the partner must conclude that the firm has the
competence, including capabilities, time and resources, to perform it. An engagement in
a sector where the firm has no experience and no trained personnel is not accepted.

## 2. Preconditions and terms
<!-- source: ICAI ch11 §9 and §9A (pp.15-16), SA 210 -->
An audit is accepted or continued only when the preconditions for an audit are present
(an acceptable financial reporting framework, and management's agreement to its
responsibilities) and the terms are agreed in an engagement letter (template TPL-EL-01).

## 3. Fee dependence review
<!-- source: ICAI ch11 §6 (p.11) - undue dependence on a client's fees is a self-interest threat -->
At each continuance decision the partner reviews the share of the firm's total fees
earned from the client. The firm's internal review trigger (a fictional firm choice) is
15% of total firm fees from one client; above it, the matter is referred to the
managing partner.

## 4. Approval
Acceptance and continuance decisions are approved by two partners and recorded in the
client register.
""",
)

DOCS: list[SynthDoc] = [
    METHODOLOGY,
    MATERIALITY_V1,
    MATERIALITY_V2,
    DOCUMENTATION_V1,
    DOCUMENTATION_V2,
    INDEPENDENCE_V1,
    INDEPENDENCE_V2,
    QUALITY,
    ACCEPTANCE,
]
