#!/usr/bin/env python3
"""Publish desktop installers to the public download bucket.

Used by .github/workflows/desktop-release.yml. The service-account JSON
arrives in GCP_SA_JSON (either raw JSON or base64-encoded).
"""
import base64
import json
import os

from google.cloud import storage

# Primary bucket is named after the domain (GCS CNAME hosting requires the
# bucket name to match the Host header); the -downloads bucket is kept as a
# mirror so legacy storage.googleapis.com links stay valid.
BUCKETS = ["phytospectra.com", "phytospectra-downloads"]
FILES = [
    ("Phytospectra-macos.zip", "application/zip"),
    ("Phytospectra-windows.exe", "application/octet-stream"),
    ("SHA256SUMS.txt", "text/plain"),
]


def main() -> None:
    raw = os.environ["GCP_SA_JSON"]
    info = json.loads(base64.b64decode(raw)) if not raw.lstrip().startswith("{") else json.loads(raw)
    client = storage.Client.from_service_account_info(info)
    for bucket_name in BUCKETS:
        bucket = client.bucket(bucket_name)
        for name, content_type in FILES:
            blob = bucket.blob(f"downloads/{name}")
            blob.upload_from_filename(name, content_type=content_type)
            print(f"uploaded gs://{bucket_name}/downloads/{name}")


if __name__ == "__main__":
    main()