"""Create the synthetic document management system (DMS) database for change data
capture (M12d).

    .venv\\Scripts\\python.exe -m scripts.make_synthetic_dms

Creates, in the full profile's PostgreSQL (DATABASE_URL's server):
- database `dms`, schema `dms`, table `dms.documents` (one row = one document; the
  Markdown body is in the row);
- REPLICA IDENTITY FULL, so a deleted row's change event still carries its doc_id;
- a few SYNTHETIC documents (inserted only if missing).
Debezium (deploy/compose.yaml, scripts/register_debezium_connector.py) then streams every
insert, update and delete in that table to Kafka.

Content rule (CLAUDE.md section 5): fictional firm procedures only; no statement about
auditing standards.
"""

import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app.config import get_settings
from app.ingest.md_loader import SYNTHETIC_BANNER

DDL = """
CREATE SCHEMA IF NOT EXISTS dms;
CREATE TABLE IF NOT EXISTS dms.documents (
    doc_id          TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    access_level    TEXT NOT NULL DEFAULT 'staff',
    engagement_id   TEXT,
    effective_date  DATE NOT NULL,
    version         TEXT NOT NULL DEFAULT '1',
    owner           TEXT,
    classification  TEXT,
    body            TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE dms.documents REPLICA IDENTITY FULL;
"""

ROWS = [
    ("DMS-POL-001", "Engagement file archiving procedure", "firm_policy", "staff", "2026-04-01", """# Engagement file archiving procedure

## 1. Who archives
The engagement senior prepares the file for archiving; the practice office locks it.

## 2. Steps
Check that every review note is cleared, export the electronic file to the archive store,
and record the archive code in the practice management system.
"""),
    ("DMS-POL-002", "Use of personal devices for engagement work", "firm_policy", "staff", "2026-06-15", """# Use of personal devices for engagement work

## 1. Rule
Client data is opened only on firm-issued laptops. Personal phones may be used for
two-factor sign-in codes, never to store client files.

## 2. Exceptions
An exception needs the IT manager's written approval, recorded in the IT register.
"""),
    ("DMS-TPL-001", "Engagement budget template: how to fill it in", "templates", "staff", "2026-05-10", """# Engagement budget template: how to fill it in

## 1. Hours
Enter budgeted hours per area and per grade before fieldwork starts.

## 2. Variances
Explain any area where actual hours exceed the budget by more than twenty per cent.
"""),
]


def dms_url(base: str) -> URL:
    return make_url(base).set(database="dms")


def ensure_database(base: str) -> None:
    """Create database `dms` on the server of `base` if it does not exist."""
    admin = create_engine(make_url(base).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'dms'")).scalar():
            conn.execute(text("CREATE DATABASE dms"))
    admin.dispose()


def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        print("Set DATABASE_URL in .env (the full profile's PostgreSQL; see .env.example).")
        return 1
    ensure_database(settings.database_url)
    engine = create_engine(dms_url(settings.database_url))
    with engine.begin() as conn:
        conn.execute(text(DDL))
        for doc_id, title, source_type, level, effective, body in ROWS:
            conn.execute(text(
                "INSERT INTO dms.documents (doc_id, title, source_type, access_level, "
                "effective_date, body) VALUES (:d, :t, :s, :a, :e, :b) ON CONFLICT (doc_id) DO NOTHING"),
                dict(d=doc_id, t=title, s=source_type, a=level, e=effective,
                     b=f"{SYNTHETIC_BANNER}\n\n{body}"))
        count = conn.execute(text("SELECT count(*) FROM dms.documents")).scalar()
    engine.dispose()
    print(f"DMS ready: database dms, table dms.documents ({count} rows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
