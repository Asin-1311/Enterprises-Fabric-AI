"""SYNTHETIC audit programmes and checklists (fictional firm).

Each `##` heading is one checklist item group, kept whole by the chunker.
"""

from scripts.synthetic import FIRM, SynthDoc

ST = "checklists"

INVENTORY = SynthDoc(
    doc_id="CHK-INV-01",
    filename="inventory_observation_checklist.md",
    source_type=ST,
    title="Checklist: Attendance at Physical Inventory Counting",
    version="2.1",
    effective_date="2024-10-01",
    access_level="staff",
    body=f"""# Checklist: Attendance at Physical Inventory Counting

{FIRM}. Version 2.1, effective 1 October 2024. Reference: SA 501.

## A. Planning the attendance
<!-- source: ICAI ch4 §2.5 (pp.35-36), SA 501 -->
- [ ] Nature of inventory and stages of completion of work in progress understood.
- [ ] Risks of material misstatement related to inventory assessed.
- [ ] Internal control over inventory understood.
- [ ] Management's count instructions obtained and reviewed.
- [ ] Timing of the count agreed; perpetual inventory system noted, if any.
- [ ] Locations listed; locations to attend chosen by materiality and risk.
- [ ] Need for an auditor's expert considered.

## B. During the count
<!-- source: ICAI ch4 §2.4 (p.35), SA 501; ICAI ch5 §3.6 (p.51) -->
- [ ] Inventory inspected to confirm existence and evaluate condition.
- [ ] Compliance with management's instructions observed.
- [ ] Test counts performed and traced both ways (tags to sheets, sheets to tags).
- [ ] Consigned goods and third-party goods segregated.
- [ ] Damaged, obsolete and slow-moving items noted; alert to empty boxes.
- [ ] Cut-off: details of the last 5-10 receiving reports and shipping documents recorded.
- [ ] Tags and summary sheets controlled and reconciled.

## C. Count not at the year-end date
<!-- source: ICAI ch4 §2.6 (p.36), SA 501 -->
- [ ] Procedures performed on changes between the count date and the year end.
- [ ] Perpetual records checked for proper adjustment and reliability.
- [ ] Reasons for significant count-to-record differences obtained.

## D. Attendance not possible
<!-- source: ICAI ch4 §2.7-§2.8 (pp.36-37), SA 501 and SA 705 -->
- [ ] Unforeseen circumstances: counts made or observed on an alternative date and
      intervening transactions tested.
- [ ] Impracticable: alternative procedures performed (e.g. subsequent sales of items
      bought before the count). Inconvenience, time or cost alone is not a valid reason.
- [ ] If sufficient appropriate evidence cannot be obtained, engagement manager informed:
      SA 705 requires a modified opinion for the scope limitation.

## E. Inventory held by third parties
<!-- source: ICAI ch4 §2.9 (p.37), SA 501 -->
- [ ] Where material, confirmation from the third party and/or inspection or other procedures.
""",
)

CONFIRMATIONS = SynthDoc(
    doc_id="CHK-CONF-01",
    filename="external_confirmations_checklist.md",
    source_type=ST,
    title="Checklist: External Confirmations",
    version="1.3",
    effective_date="2024-10-01",
    access_level="staff",
    body=f"""# Checklist: External Confirmations

{FIRM}. Version 1.3, effective 1 October 2024. Reference: SA 505.

## A. Keeping control of the process
<!-- source: ICAI ch4 §3.5 (p.46), SA 505 -->
The engagement team, not the client and not the client's internal audit department,
controls every step:
- [ ] Information to be confirmed decided by the team.
- [ ] Appropriate, knowledgeable confirming party selected.
- [ ] Request designed with return details pointing directly to the auditor.
- [ ] Addresses validated before dispatch.
- [ ] Requests sent, and follow-up requests sent, by the team.

## B. Choosing positive or negative requests
<!-- source: ICAI ch4 §3.4 (p.44), §3.5(c) (p.47) and §3.7 (p.51), SA 505 -->
- Positive request: the confirming party replies in all cases, agreeing, disagreeing or
  providing the information. A "blank" positive request (amount not stated) reduces the
  risk of unthinking agreement but may lower the response rate.
- Negative request: the confirming party replies only if it disagrees. Negative requests
  give less persuasive evidence.
- [ ] Negative requests used as the sole substantive procedure ONLY if all four hold:
      risk assessed as low with evidence that controls operate effectively; a large number
      of small, homogeneous balances; a very low expected exception rate; and no reason to
      expect recipients to disregard the requests.

## C. Management refuses permission
<!-- source: ICAI ch4 §3.6 (pp.48-50), SA 505 -->
- [ ] Reasons obtained and evidence sought on their validity and reasonableness.
- [ ] Effect on risk assessment, including fraud risk, evaluated.
- [ ] Alternative procedures performed (receivables: subsequent cash receipts, shipping
      documents, sales near period end; payables: subsequent payments, third-party
      correspondence, goods received notes).
- [ ] If the refusal is unreasonable or alternative evidence is not obtained: communicate
      with those charged with governance (SA 260) and consider the opinion (SA 705).

## D. Evaluating responses
<!-- source: ICAI ch4 §3.4 (p.45) and §3.8 (p.52), SA 505 -->
- [ ] Each result classified: agreement, unreliable response, non-response, or exception.
- [ ] Exceptions investigated and their effect assessed for the whole population.
""",
)

BANK_BRANCH = SynthDoc(
    doc_id="CHK-BANK-01",
    filename="bank_branch_audit_checklist.md",
    source_type=ST,
    title="Checklist: Bank Branch Audit - Advances and NPA Classification",
    version="3.0",
    effective_date="2025-03-01",
    access_level="staff",
    body=f"""# Checklist: Bank Branch Audit - Advances and NPA Classification

{FIRM}. Version 3.0, effective 1 March 2025. Use with the current bank audit circular.

## A. Coverage of advances
<!-- source: ICAI ch10 §14 (pp.36-37) -->
- [ ] All large advances examined; other advances examined on a sample basis.
- [ ] Problem accounts examined in detail unless insignificant.
- [ ] Advances sanctioned during the year, and those adversely commented on by RBI
      inspection, concurrent auditors or internal inspection, included in the review.

## B. NPA classification checks
<!-- source: ICAI ch10 §12.5 (pp.26-29) -->
- [ ] Term loans: interest or principal overdue for more than 90 days classified as NPA.
- [ ] Cash credit / overdraft: accounts "out of order" classified as NPA. Out of order
      means the balance is continuously above the sanctioned limit or drawing power, OR
      there are no credits for 90 days continuously as on the balance sheet date, OR the
      credits are not enough to cover the interest debited in that period.
- [ ] Bills purchased and discounted overdue for more than 90 days classified as NPA.
- [ ] Classification is borrower-wise: if one facility of a borrower is NPA, all
      facilities of that borrower are NPA.
- [ ] Classification is based on record of recovery; security value or net worth of the
      borrower or guarantor is NOT taken into account.
- [ ] Accounts regularised by one or a few credits just before year end reviewed for
      inherent weakness and for reversals just after year end.
- [ ] Central Government guaranteed advances (guarantee not invoked) treated as standard
      for classification but NPA for income recognition; State Government guaranteed
      advances overdue more than 90 days treated as NPA for both.

## C. Sub-classification and provisioning
<!-- source: ICAI ch10 §12.5 (p.28) -->
- [ ] Substandard (NPA for up to 12 months): 15%.
- [ ] Doubtful up to 1 year (D1): 25% of secured portion + 100% of unsecured portion.
- [ ] Doubtful 1 to 3 years (D2): 40% of secured portion + 100% of unsecured portion.
- [ ] Doubtful more than 3 years (D3): 100% + 100%.
- [ ] Loss assets: 100%.

## D. Internal controls over advances
<!-- source: ICAI ch10 §14 (pp.37-38) -->
- [ ] Sanction by the appropriate authority after credit appraisal.
- [ ] Documents executed before disbursement.
- [ ] Margin maintained as per sanction; drawing power register updated monthly and
      checked by an officer.
- [ ] Accounts kept within both drawing power and sanctioned limit; irregular accounts
      reported to the controlling authority.
- [ ] Each advance account reviewed at least once a year (more often for large advances).
""",
)

GOING_CONCERN = SynthDoc(
    doc_id="CHK-GC-01",
    filename="going_concern_checklist.md",
    source_type=ST,
    title="Checklist: Going Concern Evaluation",
    version="1.0",
    effective_date="2024-04-01",
    access_level="manager",
    body=f"""# Checklist: Going Concern Evaluation

{FIRM}. Version 1.0, effective 1 April 2024. Completed by the engagement manager.
Reference: SA 570.

## A. Risk assessment
<!-- source: ICAI ch7 §2.5 (pp.16-17), SA 570 -->
- [ ] Management's preliminary assessment obtained and discussed, or, if none, the basis
      for using the going concern basis discussed with management.
- [ ] Financial indicators considered: net liability or net current liability position;
      borrowings near maturity without realistic renewal; withdrawal of creditor support;
      negative operating cash flows; adverse key ratios; substantial operating losses;
      arrears of dividends; inability to pay creditors on due dates; breach of loan terms;
      suppliers moving to cash-on-delivery; inability to finance essential investment.

## B. Evaluating management's assessment
<!-- source: ICAI ch7 §2.6 (p.19), SA 570 -->
- [ ] Assessment covers the same period management used; if less than twelve months from
      the date of the financial statements, management asked to extend it to at least
      twelve months.

## C. Additional procedures when events or conditions are identified
<!-- source: ICAI ch7 §2.7 (pp.19-20), SA 570 -->
- [ ] Management's plans evaluated for feasibility.
- [ ] Cash flow forecast: reliability of data and support for assumptions evaluated.
- [ ] Facts arising after management's assessment considered.
- [ ] Loan and debenture terms read for breaches; minutes read for financing difficulties.
- [ ] Legal counsel inquired of on litigation and claims.
- [ ] Written representations obtained on management's plans.
- [ ] Consultation under Technical Circular 2025/07 and EQCR where required.
""",
)

SUBSEQUENT_EVENTS = SynthDoc(
    doc_id="CHK-SE-01",
    filename="subsequent_events_checklist.md",
    source_type=ST,
    title="Checklist: Subsequent Events Review",
    version="1.1",
    effective_date="2024-04-01",
    access_level="staff",
    body=f"""# Checklist: Subsequent Events Review

{FIRM}. Version 1.1, effective 1 April 2024. Reference: SA 560.

## A. Two types of events
<!-- source: ICAI ch7 §1 (pp.4-5), SA 560 -->
- Events giving evidence of conditions that existed at the balance sheet date (e.g.
  insolvency of a major debtor after year end; settlement of a legal claim at a different
  amount).
- Events giving evidence of conditions that arose after the balance sheet date (e.g. issue
  of new share capital; a planned merger; destruction of inventory by fire after year end).

## B. Procedures up to the date of the auditor's report
<!-- source: ICAI ch7 §1 (p.6), SA 560 -->
- [ ] Management's procedures for identifying subsequent events understood.
- [ ] Management and those charged with governance asked whether any subsequent events
      have occurred.
- [ ] Minutes of meetings held after year end read; matters from meetings without minutes
      inquired about.
- [ ] Latest subsequent interim financial statements read, if any.
- [ ] Procedures cover the period up to the date of the auditor's report, or as near as
      practicable.

## C. Written representation
<!-- source: ICAI ch7 §1 (p.7), SA 560 and SA 580 -->
- [ ] Written representation obtained that all subsequent events requiring adjustment or
      disclosure have been adjusted or disclosed (see template TPL-MRL-01).

## D. Facts known after the report date but before issue
<!-- source: ICAI ch7 §1 (p.7), SA 560 -->
- [ ] Matter discussed with management; need to amend the financial statements
      determined; engagement partner informed immediately.
""",
)

DOCS: list[SynthDoc] = [INVENTORY, CONFIRMATIONS, BANK_BRANCH, GOING_CONCERN, SUBSEQUENT_EVENTS]
