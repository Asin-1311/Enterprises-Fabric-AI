"""SYNTHETIC compliance calendars (fictional firm). Statutory dates are placeholders."""

from scripts.synthetic import FIRM, SynthDoc

ST = "calendar"

_WARNING = (
    "> ILLUSTRATIVE DATES. Statutory due dates in this calendar are placeholders for the\n"
    "> demonstration. They are NOT from the ICAI chapters and have NOT been verified against\n"
    "> the current notifications. Internal firm dates are fictional firm choices."
)


def _calendar(fy: str, start_year: int) -> str:
    y0, y1 = start_year, start_year + 1
    return f"""# Compliance Calendar FY {fy}

{FIRM}. Covers 1 April {y0} to 31 March {y1}.

{_WARNING}

## Statutory filing dates (illustrative placeholders)
| Item | Due date |
|---|---|
| Tax audit report for the previous year (FY {y0 - 1}-{str(y0)[2:]}) | 30 September {y0} |
| Income tax return, audited cases (FY {y0 - 1}-{str(y0)[2:]}) | 31 October {y0} |

## Internal firm deadlines
| Item | Date |
|---|---|
| Annual independence declaration (all staff and partners) | 30 April {y0} |
| Draft tax audit reports to engagement manager | 31 August {y0} |
| Fee dependence review for next year's continuance (partners) | 31 March {y1} |
| Bank branch audit season planning meeting | 1st week of March {y1} |
| Bank branch audits for FY {fy} | April {y1} |

## File assembly
Final audit files: within 45 days of the auditor's report (firm rule, Documentation and
Retention Policy v2.0), and never later than the 60-day limit in SA 230.
<!-- source: ICAI ch6 §1.9 (p.9) for the 60-day limit -->
"""


FY2025_26 = SynthDoc(
    doc_id="CAL-FY2526",
    filename="compliance_calendar_fy2025_26.md",
    source_type=ST,
    title="Compliance Calendar FY 2025-26",
    version="1.0",
    effective_date="2025-04-01",
    access_level="staff",
    body=_calendar("2025-26", 2025),
)

FY2026_27 = SynthDoc(
    doc_id="CAL-FY2627",
    filename="compliance_calendar_fy2026_27.md",
    source_type=ST,
    title="Compliance Calendar FY 2026-27",
    version="1.0",
    effective_date="2026-04-01",
    access_level="staff",
    body=_calendar("2026-27", 2026),
)

DOCS: list[SynthDoc] = [FY2025_26, FY2026_27]
