"""SYNTHETIC demo users and client engagements (all fictional)."""

# user_id, name, role
USERS: list[tuple[str, str, str]] = [
    ("S01", "Riya Kapoor", "staff"),
    ("S02", "Kabir Nair", "staff"),
    ("S03", "Meera Iyer", "staff"),
    ("M01", "Arjun Desai", "manager"),
    ("M02", "Neha Kulkarni", "manager"),
    ("P01", "Vikram Sethi", "partner"),
    ("P02", "Anjali Bose", "partner"),
    # M11: the firm's IT administrator. Role STAFF on purpose: the admin flag lets them
    # manage sources and the admin console, but gives NO extra document access.
    ("A01", "Kiran Rao", "staff"),
]

# Users with the admin capability flag (CLAUDE.md section 5: grants no document access).
ADMINS = {"A01"}

# Sign-in emails (added 2026-11-14). ".test" is a reserved top-level domain, so these
# addresses can never belong to anyone.
EMAIL_DOMAIN = "example-associates.test"


def demo_email(name: str) -> str:
    """firstname.lastname@example-associates.test"""
    return ".".join(name.lower().split()) + "@" + EMAIL_DOMAIN

# engagement_id, client_name, industry, fy, partner_id, member_ids
# Team design (used by the engagement-isolation tests and the eval set):
#   S01 and M01 are ONLY on Northwind, so they must never see the bank or NGO files.
#   S03 is on the bank and the NGO but NOT on Northwind.
#   M02 is a manager who is not on Northwind (cross-engagement denial case).
ENGAGEMENTS: list[tuple[str, str, str, str, str, list[str]]] = [
    (
        "ENG-NWT-FY26",
        "Northwind Textiles Private Limited",
        "manufacturing",
        "2025-26",
        "P01",
        ["M01", "S01", "S02"],
    ),
    (
        "ENG-MBL-FY26",
        "Meridian Bank Limited - Lakeview Branch",
        "banking",
        "2025-26",
        "P02",
        ["M02", "S02", "S03"],
    ),
    (
        "ENG-BRT-FY26",
        "Brightpath Rural Education Trust",
        "non_profit",
        "2025-26",
        "P01",
        ["M02", "S03"],
    ),
]
