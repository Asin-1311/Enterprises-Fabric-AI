"""Put the synthetic S3 documents into the MinIO bucket `firm-documents` (M12c).

    .venv\\Scripts\\python.exe -m scripts.seed_minio

Needs the full profile's MinIO (docker compose ... --profile ingest up -d) and
MINIO_ROOT_USER / MINIO_ROOT_PASSWORD in .env. Creates the bucket if it is missing and
uploads the documents below under the prefix `firm/` (re-running overwrites them, which
changes nothing unless their text changed).

Content rule (CLAUDE.md section 5): fictional firm procedures only; no statement about
auditing standards.
"""

import os
import sys

from app.ingest.md_loader import SYNTHETIC_BANNER

BUCKET = "firm-documents"
ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:9000")

DOCS = {
    "firm/client-document-request-list.md": """# Client document request list procedure

## 1. Sending the list
The engagement senior sends the document request list to the client at the planning
meeting and records the date in the practice management system.

## 2. Follow-up
Items still outstanding after ten working days are raised with the client's finance head.
""",
    "firm/archived-file-retrieval.md": """# Retrieving an archived engagement file

## 1. Request
Staff request an archived file from the practice office, quoting the file's archive code.

## 2. Access
A retrieved file is read-only; any change needs the engagement partner's written approval.
""",
}


def client():
    import boto3

    from app.config import env_reference

    return boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1",
                        aws_access_key_id=env_reference("env:MINIO_ROOT_USER"),
                        aws_secret_access_key=env_reference("env:MINIO_ROOT_PASSWORD"))


def seed(s3, prefix: str = "", docs: dict[str, str] = DOCS) -> list[str]:
    """Create the bucket if needed and upload `docs` under `prefix`. Returns the keys."""
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if BUCKET not in existing:
        s3.create_bucket(Bucket=BUCKET)
    keys = []
    for key, body in docs.items():
        s3.put_object(Bucket=BUCKET, Key=prefix + key, ContentType="text/markdown",
                      Body=f"{SYNTHETIC_BANNER}\n\n{body}".encode("utf-8"))
        keys.append(prefix + key)
    return keys


def main() -> int:
    keys = seed(client())
    print(f"Uploaded {len(keys)} documents to s3://{BUCKET}/ at {ENDPOINT}: {', '.join(keys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
