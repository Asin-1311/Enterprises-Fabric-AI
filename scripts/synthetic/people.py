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

# Who is responsible for each kind of firm document (SYNTHETIC assignment, M12a). Engagement
# files belong to their engagement partner; ICAI study material has no firm owner.
OWNER_BY_SOURCE_TYPE = {"firm_policy": "P02", "circulars": "P02", "checklists": "M01",
                        "templates": "M01", "calendar": "M02", "faq": "M02"}


def governance(row: dict[str, str]) -> dict[str, str]:
    """source_id, owner and classification for one manifest row (CLAUDE.md section 5).

    - source_id: the corpus folder's registered source.
    - owner: the engagement partner for engagement files, else the role above.
    - classification: engagement files and anything above staff level are confidential;
      the rest is internal. A document marked restricted in its definition (M13) keeps
      that classification; its text is sent only to the private model.
    """
    source_type = row["source_type"]
    partner = {e[0]: e[4] for e in ENGAGEMENTS}
    owner = partner.get(row["engagement_id"], "") if source_type == "engagement" \
        else OWNER_BY_SOURCE_TYPE.get(source_type, "")
    confidential = source_type == "engagement" or row["access_level"] in ("manager", "partner")
    return {"source_id": f"fs-{source_type}", "owner": owner,
            "classification": "confidential" if confidential else "public_internal"}

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
