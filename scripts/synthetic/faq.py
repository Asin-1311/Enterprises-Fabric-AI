"""SYNTHETIC internal helpdesk FAQ for article assistants (fictional firm).

Each `###` heading is one question; the chunker keeps each Q&A whole.
"""

from scripts.synthetic import FIRM, SynthDoc

FAQ = SynthDoc(
    doc_id="FAQ-01",
    filename="article_assistant_helpdesk_faq.md",
    source_type="faq",
    title="Article Assistant Helpdesk FAQ",
    version="4.0",
    effective_date="2025-09-15",
    access_level="staff",
    body=f"""# Article Assistant Helpdesk FAQ

{FIRM}. Version 4.0, updated 15 September 2025.

## Documentation

### Q: Within how many days must I finish assembling the final audit file?
A: Within 45 days of the date of the auditor's report under the firm's Documentation and
Retention Policy v2.0. SA 230 sets the outer limit at ordinarily not more than 60 days.
After assembly nothing may be deleted or discarded before the retention period ends.
<!-- source: ICAI ch6 §1.9 (p.9) -->

### Q: How long are audit files kept?
A: At least seven years from the date of the auditor's report (or the group auditor's
report, if later).
<!-- source: ICAI ch6 §1.9 (p.9) -->

### Q: Can I write up working papers after the audit is over?
A: No. Prepare documentation as you do the work. Documentation prepared afterwards is
likely to be less accurate.
<!-- source: ICAI ch6 §1.7 (p.8) -->

## Fieldwork

### Q: The client's internal audit team offered to send and collect our confirmations. Can they?
A: No. The engagement team must keep control of external confirmation requests: deciding
what to confirm, choosing the confirming party, designing the request, and sending it,
with replies coming directly to us. See checklist CHK-CONF-01.
<!-- source: ICAI ch4 §3.5 (p.46) -->

### Q: What is the difference between positive and negative confirmations?
A: A positive request asks the party to reply in every case; a negative request asks for
a reply only if the party disagrees. Negative requests give less persuasive evidence.
<!-- source: ICAI ch4 §3.4 (p.44) and §3.7 (p.51) -->

### Q: I could not attend the inventory count because of a sudden emergency. What now?
A: Tell your senior. The team must make or observe some counts on an alternative date and
test the transactions in between.
<!-- source: ICAI ch4 §2.7 (p.36) -->

### Q: When is a term loan an NPA?
A: When interest or instalment of principal is overdue for more than 90 days. See
checklist CHK-BANK-01 for cash credit, bills and the borrower-wise rule.
<!-- source: ICAI ch10 §12.5 (p.27) -->

## Ethics and access

### Q: When do I sign the independence declaration?
A: Every year by 30 April, and also before starting work on each engagement (the
engagement declaration was added in Independence and Ethics Policy v2.0, July 2025).

### Q: Can I look at files of a client I am not assigned to?
A: No. Engagement files are open only to that engagement team and to partners. See
Technical Circular 2025/06.

### Q: Where do I find the materiality figures for my engagement?
A: Ask your engagement manager. The Materiality Policy and engagement materiality
workings are restricted to managers and partners.
""",
)

DOCS: list[SynthDoc] = [FAQ]
