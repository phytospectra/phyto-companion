#!/usr/bin/env python3
"""OSF acceptance-evidence upload, preflight, and registration (ADR-0088).

Modes (exactly one):

  --preflight             read-only: compare the remote tree with
                          MANIFEST.sha256 and report missing / extra /
                          mismatched files. Never writes to OSF.
  --verify-contract       read-only: check the embargo registration payload
                          against the live OSF API contract and record the
                          result locally. Required before registration.
  --embargo YYYY-MM-DD    upload the package and create an embargoed
                          registration. Requires a valid FUTURE date, a
                          clean preflight, and a current contract record.

Safety rules enforced here:
  * no OSF write happens without OSF_TOKEN and a built package;
  * the embargo date is mandatory and must be in the future — there is no
    non-embargoed fallback (ADR-0088: the owner, not the agent, makes a
    registration public);
  * uploads are retry-safe: a failed create is resolved by looking up the
    exact filename in the intended parent folder. Identical content is
    skipped, different content is reported as a conflict and never
    silently replaced, and an unresolvable failure surfaces the original
    error;
  * registration is blocked until the remote tree matches the manifest and
    the payload has been checked against the live API contract.

The token is read from the environment and is never printed or written.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Overridable for tests.
PACKAGE_DIR = REPO / "data" / "osf_package"
REGISTRY_RECORD = REPO / "data" / "validated_runs" / "osf_registration.json"
CONTRACT_RECORD = REPO / "data" / "validated_runs" / "osf_contract_check.json"

API_BASE = os.environ.get("OSF_API_BASE", "https://api.osf.io/v2")
FILES_BASE = os.environ.get("OSF_FILES_BASE", "https://files.osf.io/v1/resources")
RETRY_ATTEMPTS = int(os.environ.get("OSF_RETRY_ATTEMPTS", "3"))
RETRY_SLEEP = float(os.environ.get("OSF_RETRY_SLEEP", "1.0"))

#: Fields this client relies on when the owner later makes the registration
#: public. A contract record is only valid when the live API confirms them.
CONTRACT_FIELDS = ("embargo_end_date", "registration_choice",
                   "draft_registration_id")


class OSFError(Exception):
    """Any refusal, with an operator-readable message."""


class AuthError(OSFError):
    """The token was rejected (401/403)."""


class UploadConflict(OSFError):
    """A file exists at the target with different content."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def script_sha256() -> str:
    return sha256_file(Path(__file__).resolve())


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Client:
    def __init__(self, token: str, api_base: str | None = None,
                 files_base: str | None = None) -> None:
        self.token = token
        self.api_base = (api_base or API_BASE).rstrip("/")
        self.files_base = (files_base or FILES_BASE).rstrip("/")

    def _request(self, method: str, url: str, data: bytes | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        hdrs = {"Authorization": f"Bearer {self.token}"}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise OSFError(f"network error contacting OSF: {exc.reason}") from exc

    def get_json(self, url: str) -> dict:
        status, body = self._request("GET", url)
        if status in (401, 403):
            raise AuthError(
                f"OSF rejected the token (HTTP {status}) — rotate/replace "
                f"OSF_TOKEN and retry."
            )
        if status != 200:
            raise OSFError(f"GET {url} -> HTTP {status}: {body[:300]!r}")
        return json.loads(body)

    def put_binary(self, url: str, payload: bytes) -> tuple[int, bytes]:
        return self._request(
            "PUT", url, data=payload,
            headers={"Content-Type": "application/octet-stream"},
        )

    def post_json(self, url: str, payload: dict) -> tuple[int, bytes]:
        return self._request(
            "POST", url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )


# --------------------------------------------------------------------------
# Local package
# --------------------------------------------------------------------------
def read_manifest(pkg: Path) -> dict[str, str]:
    path = pkg / "MANIFEST.sha256"
    if not path.is_file():
        raise OSFError(f"{path} missing — run scripts/build_osf_package.py first")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        if not _ or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise OSFError(f"malformed manifest line: {line!r}")
        out[rel] = digest
    if not out:
        raise OSFError("MANIFEST.sha256 is empty")
    return out


def verify_local_package(pkg: Path, manifest: dict[str, str]) -> None:
    """The package on disk must match its own manifest exactly."""
    for rel, digest in manifest.items():
        f = pkg / rel
        if not f.is_file():
            raise OSFError(f"package file missing locally: {rel}")
        if sha256_file(f) != digest:
            raise OSFError(f"local checksum mismatch: {rel}")
    on_disk = {
        str(p.relative_to(pkg)) for p in pkg.rglob("*")
        if p.is_file() and p.name != "MANIFEST.sha256"
    }
    extra = sorted(on_disk - set(manifest))
    if extra:
        raise OSFError(f"package contains files not in the manifest: {extra}")


def redaction_ok(pkg: Path) -> None:
    # CITATION.cff legitimately carries the authored citation identity.
    offenders = []
    for f in pkg.rglob("*"):
        if not f.is_file() or f.name in {"CITATION.cff", "MANIFEST.sha256"}:
            continue
        text = f.read_text(errors="replace")
        if "subhadip" in text or "/Users/" in text:
            offenders.append(str(f.relative_to(pkg)))
    if offenders:
        raise OSFError(f"un-redacted local identity in: {offenders}")


# --------------------------------------------------------------------------
# Remote tree
# --------------------------------------------------------------------------
def _listing(client: Client, node_id: str, parent: str) -> list[dict]:
    # OSF lists a folder's children at /nodes/<id>/files/osfstorage/<id>/
    # where <id> is the folder's own path id; empty parent = root listing.
    url = f"{client.api_base}/nodes/{node_id}/files/osfstorage/"
    if parent:
        url += urllib.parse.quote(parent, safe="") + "/"
    return client.get_json(url).get("data", [])


def _walk(client: Client, node_id: str, url: str, out: dict[str, str]) -> None:
    page = client.get_json(url)
    for item in page.get("data", []):
        attrs = item.get("attributes", {})
        if attrs.get("kind") == "folder":
            folder_id = str(attrs.get("path", "")).strip("/")
            if folder_id:
                _walk(
                    client, node_id,
                    f"{client.api_base}/nodes/{node_id}/files/osfstorage/"
                    f"{urllib.parse.quote(folder_id, safe='')}/",
                    out,
                )
            continue
        rel = str(attrs.get("path", "")).strip("/")
        hashes = (attrs.get("extra") or {}).get("hashes") or {}
        out[rel] = hashes.get("sha256", "")
    nxt = page.get("links", {}).get("next")
    if nxt:
        _walk(client, node_id, nxt, out)


def remote_tree(client: Client, node_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    _walk(client, node_id, f"{client.api_base}/nodes/{node_id}/files/osfstorage/", out)
    return out


def preflight(pkg: Path, client: Client, node_id: str) -> dict[str, list[str]]:
    manifest = read_manifest(pkg)
    verify_local_package(pkg, manifest)
    remote = remote_tree(client, node_id)
    missing = sorted(set(manifest) - set(remote))
    extra = sorted(set(remote) - set(manifest))
    mismatched = sorted(
        rel for rel in set(manifest) & set(remote)
        # An empty sha means OSF never gave us one: fail closed, not "match".
        if remote[rel] != manifest[rel]
    )
    return {"missing": missing, "extra": extra, "mismatched": mismatched}


def _child_id(client: Client, node_id: str, parent: str, name: str,
              kind: str) -> str | None:
    for item in _listing(client, node_id, parent):
        attrs = item.get("attributes", {})
        if attrs.get("name") == name and attrs.get("kind") == kind:
            return str(attrs.get("path", "")).strip("/")
    return None


def _ensure_folder(client: Client, node_id: str, folder: str) -> str:
    parent, _, leaf = folder.rpartition("/")
    parent = _ensure_folder(client, node_id, parent) if parent else ""
    existing = _child_id(client, node_id, parent, leaf, "folder")
    if existing:
        return existing
    url = (
        f"{client.files_base}/{node_id}/providers/osfstorage/"
        f"{urllib.parse.quote(parent, safe='/') + '/' if parent else ''}"
        f"?kind=folder&name={urllib.parse.quote(leaf)}"
    )
    status, body = client.put_binary(url, b"")
    if status not in (200, 201):
        # A concurrent create is the only benign explanation; confirm it.
        existing = _child_id(client, node_id, parent, leaf, "folder")
        if existing:
            return existing
        raise OSFError(f"could not create folder {folder!r}: HTTP {status} {body[:200]!r}")
    return str(json.loads(body)["data"]["attributes"]["path"]).strip("/")


def _remote_file_sha(client: Client, node_id: str, folder: str,
                     name: str) -> str | None:
    for item in _listing(client, node_id, folder):
        attrs = item.get("attributes", {})
        if attrs.get("name") == name and attrs.get("kind") == "file":
            return ((attrs.get("extra") or {}).get("hashes") or {}).get("sha256")
    return None


def upload_file(client: Client, node_id: str, parent: str, rel: str,
                payload: bytes, digest: str) -> str:
    """Upload one file, retry-safe. Returns 'uploaded' or 'skipped'."""
    name = rel.rsplit("/", 1)[-1]
    url = (
        f"{client.files_base}/{node_id}/providers/osfstorage/"
        f"{urllib.parse.quote(parent, safe='/') + '/' if parent else ''}"
        f"?kind=file&name={urllib.parse.quote(name)}"
    )
    status, body = client.put_binary(url, payload)
    if status in (200, 201):
        got = ((json.loads(body).get("data", {}).get("attributes", {})
                .get("extra") or {}).get("hashes") or {}).get("sha256")
        if not got:
            raise OSFError(
                f"upload of {rel} succeeded but the server returned no "
                f"sha256; treat it as unverified, not uploaded"
            )
        if got != digest:
            raise OSFError(
                f"checksum drift after upload of {rel}: server {got}, local {digest}"
            )
        return "uploaded"

    # Create failed. OSF returns 409 or 500 when the name already exists;
    # resolve the actual state of the intended parent folder rather than
    # guessing from the status code.
    first_status, first_body = status, body
    for attempt in range(RETRY_ATTEMPTS):
        remote = _remote_file_sha(client, node_id, parent, name)
        if remote is not None:
            if remote == digest:
                return "skipped"
            raise UploadConflict(
                f"conflict at {rel}: remote sha256 {remote} differs from local "
                f"{digest}. Not replacing it; resolve the difference first."
            )
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(RETRY_SLEEP)

    raise OSFError(
        f"upload of {rel} failed (HTTP {first_status}) and no file named "
        f"{name!r} appeared in {parent or '/'} after {RETRY_ATTEMPTS} checks: "
        f"{first_body[:300]!r}"
    )


def upload_all(pkg: Path, client: Client, node_id: str) -> dict[str, int]:
    manifest = read_manifest(pkg)
    verify_local_package(pkg, manifest)
    redaction_ok(pkg)
    counts = {"uploaded": 0, "skipped": 0}
    for rel in sorted(manifest):
        parent = rel.rpartition("/")[0]
        if parent:
            parent = _ensure_folder(client, node_id, parent)
        outcome = upload_file(
            client, node_id, parent, rel, (pkg / rel).read_bytes(), manifest[rel]
        )
        counts[outcome] += 1
        print(f"    {outcome}: {rel}")
    return counts


# --------------------------------------------------------------------------
# Embargo + registration
# --------------------------------------------------------------------------
def validate_embargo(value: str | None) -> _dt.date:
    if not value:
        raise OSFError(
            "an explicit --embargo YYYY-MM-DD is required; ADR-0088 forbids "
            "creating a non-embargoed (immediately public) registration"
        )
    try:
        end = _dt.date.fromisoformat(value)
    except ValueError:
        raise OSFError(f"--embargo {value!r} is not a valid YYYY-MM-DD date") from None
    if end <= _dt.date.today():
        raise OSFError(f"--embargo {value} must be in the future (today is {_dt.date.today()})")
    return end


#: OSF v2 schema id for the open-ended registration template (used for the
#: draft registration). Verified against the live API by --verify-contract.
REGISTRATION_SCHEMA_ID = "69c6a4a4ccbbae0023c53d43"


def draft_payload() -> dict:
    """Step 1 of the documented OSF v2 registration flow: POST to
    ``/v2/nodes/{node_id}/draft_registrations/``.

    Official sources (fetched 2026-11-01):
      * developer.osf.io swagger-spec ``draft_registrations_list.yaml``
        (``draft_registrations_create``): requires ``schema_id`` /
        ``registration_schema``; the draft is linked to the project via
        ``branched_from`` (path parameter on the node endpoint).
      * osf.io ``api_tests/nodes/views/test_node_draft_registration_list.py``
        payload: ``relationships.registration_schema.data =
        {type: 'registration_schema', id: <schema id>}``.
    """
    return {
        "data": {
            "type": "draft_registrations",
            "attributes": {},
            "relationships": {
                "registration_schema": {
                    "data": {
                        "type": "registration_schema",
                        "id": REGISTRATION_SCHEMA_ID,
                    }
                },
            },
        }
    }


def embargo_payload(draft_id: str, end: _dt.date) -> dict:
    """Step 2 of the documented OSF v2 registration flow: POST to
    ``/v2/registrations/`` with a draft id.

    Official sources (fetched 2026-11-01):
      * osf.io ``api/registrations/serializers.py``:
        ``RegistrationCreateSerializer`` — for API version >= 2.19 the
        write-only field is ``draft_registration_id`` (before 2.19 it was
        ``draft_registration``); embargo fields are ``registration_choice``
        (choices 'immediate' | 'embargo') and ``embargo_end_date``.
      * osf.io ``api_tests/registrations/views/test_registration_list.py``
        ``test_embargo_registration``: POST body
        ``{data: {type: 'registrations', attributes: {
        draft_registration_id, registration_choice: 'embargo',
        embargo_end_date}}}`` → 201, response attribute
        ``pending_embargo_approval`` is True. The embargo end date must be
        in the future by at least three days and at most four years.

    There is deliberately NO immediate/non-embargoed fallback: ADR-0088
    reserves the visibility decision for the owner.
    """
    return {
        "data": {
            "type": "registrations",
            "attributes": {
                "draft_registration_id": draft_id,
                "registration_choice": "embargo",
                "embargo_end_date": end.isoformat(),
            },
        }
    }


def contract_record_is_current(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, (
            f"no contract check at {path}; run --verify-contract (read-only) "
            "before any registration"
        )
    try:
        rec = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False, "contract record is not valid JSON; re-run --verify-contract"
    if rec.get("script_sha256") != script_sha256():
        return False, "contract record predates this script; re-run --verify-contract"
    if not rec.get("verified"):
        return False, "contract record says the payload was not verified"
    return True, ""


def verify_contract(client: Client, node_id: str) -> dict:
    """Read-only check of the documented two-step creation procedure
    against the live API. Registration stays blocked unless every check
    passes; anything unobservable is a problem, never a pass.
    """
    observed: dict[str, object] = {}
    problems: list[str] = []

    # 1. The registration schema used for the draft must exist on the
    #    live provider (GET /registration_schemas/{id}/) — read-only.
    try:
        schema = client.get_json(
            f"{client.api_base}/registration_schemas/{REGISTRATION_SCHEMA_ID}/"
        )
        observed["registration_schema"] = 200
        schema_id = schema.get("data", {}).get("id")
        if schema_id != REGISTRATION_SCHEMA_ID:
            problems.append(
                f"registration schema {REGISTRATION_SCHEMA_ID} not found on the "
                "live provider (got id=" + repr(schema_id) + ")"
            )
    except OSFError as exc:
        observed["registration_schema"] = str(exc)
        problems.append(f"registration schema check failed: {exc}")

    # 2. The node must expose the draft_registrations relationship (the
    #    creation path is /nodes/{id}/draft_registrations/). GET the
    #    node itself and confirm the relationship link exists.
    try:
        node = client.get_json(f"{client.api_base}/nodes/{node_id}/")
        observed["node_read"] = 200
        rels = (node.get("data", {}) or {}).get("relationships", {}) or {}
        if "draft_registrations" not in rels:
            problems.append(
                f"node {node_id} response lacks a draft_registrations "
                "relationship; the documented creation path is "
                "/v2/nodes/{id}/draft_registrations/"
            )
    except OSFError as exc:
        observed["node_read"] = str(exc)
        problems.append(f"node read failed: {exc}")

    # 3. Sample the live registrations listing and confirm the fields this
    #    client relies on (embargo metadata) are part of the served shape.
    try:
        page = client.get_json(f"{client.api_base}/registrations/?page%5Bsize%5D=1")
        observed["registrations_list"] = 200
        sample = page.get("data") or []
        if not sample:
            # Nothing to compare against: verification is impossible, not
            # vacuously successful. Fail closed — registration stays blocked.
            problems.append(
                "no registrations were returned to sample; the payload shape "
                "could not be verified against the live API"
            )
        else:
            attrs = sample[0].get("attributes", {})
            observed["sampled_attributes"] = sorted(attrs)
            for field in ("embargo_end_date", "registration_choice",
                          "pending_embargo_approval"):
                if field not in attrs:
                    problems.append(f"live registration objects lack {field!r}")
    except OSFError as exc:
        observed["registrations_list"] = str(exc)
        problems.append(str(exc))

    end = _dt.date.today() + _dt.timedelta(days=1)
    rec = {
        "verified": not problems,
        "checked_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "api_base": client.api_base,
        "node_id": node_id,
        "script_sha256": script_sha256(),
        "procedure": [
            {"step": 1, "method": "POST",
             "url": f"/v2/nodes/{node_id}/draft_registrations/",
             "body": draft_payload()},
            {"step": 2, "method": "POST", "url": "/v2/registrations/",
             "body": embargo_payload("<draft_id>", end)},
        ],
        "documentation": [
            "developer.osf.io swagger-spec draft_registrations_list.yaml "
            "(draft_registrations_create; required: schema_id; linked via "
            "branched_from), fetched 2026-11-01",
            "osf.io api/registrations/serializers.py "
            "RegistrationCreateSerializer (v2>=2.19: draft_registration_id; "
            "registration_choice 'immediate'|'embargo'; embargo_end_date), "
            "fetched 2026-11-01",
            "osf.io api_tests/registrations/views/test_registration_list.py "
            "test_embargo_registration (201 + pending_embargo_approval), "
            "fetched 2026-11-01",
        ],
        "observed": observed,
        "problems": problems,
    }
    CONTRACT_RECORD.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_RECORD.write_text(json.dumps(rec, indent=2) + "\n")
    if problems:
        raise OSFError(
            "embargo payload could not be verified against the live contract: "
            + "; ".join(problems)
        )
    return rec


def submit_registration(client: Client, node_id: str, end: _dt.date) -> str:
    """Documented two-step creation (sources cited in verify_contract).

    Step 1: create a draft registration on the node (the draft's
    ``branched_from`` is the node). Step 2: turn the draft into an
    embargoed registration. The final response is only trusted when its
    ``registered_from`` relationship points back at ``node_id`` — a
    response with no origin, or a different one, must never be published.
    """
    # --- step 1: draft registration rooted at the node --------------------
    status, body = client.post_json(
        f"{client.api_base}/nodes/{node_id}/draft_registrations/",
        draft_payload(),
    )
    if status in (401, 403):
        raise AuthError(f"draft registration rejected the token (HTTP {status})")
    if status not in (200, 201):
        raise OSFError(f"draft registration failed: HTTP {status} {body[:300]!r}")
    draft = json.loads(body)["data"]
    draft_id = draft.get("id")
    if not draft_id:
        raise OSFError(
            "draft registration response has no id; refusing to continue"
        )
    branched = ((draft.get("relationships", {}) or {})
                .get("branched_from", {}) or {}).get("data", {}) or {}
    if branched.get("id") != node_id:
        raise OSFError(
            f"draft registration is not rooted at node {node_id!r} "
            f"(branched_from: {branched!r}); refusing to register it"
        )

    # --- step 2: embargoed registration from the draft ---------------------
    status, body = client.post_json(
        f"{client.api_base}/registrations/", embargo_payload(draft_id, end)
    )
    if status in (401, 403):
        raise AuthError(f"registration rejected the token (HTTP {status})")
    if status not in (200, 201):
        raise OSFError(f"registration failed: HTTP {status} {body[:300]!r}")
    reg = json.loads(body)["data"]
    if (reg.get("attributes", {}).get("embargo_end_date") or "")[:10] != end.isoformat():
        raise OSFError(
            "registration response does not carry the requested embargo end date; "
            "treat it as unverified and do not publish anything"
        )
    # The response must point back at the node we registered via
    # registered_from. A response without it, or with a different node,
    # must not be trusted.
    origin = ((reg.get("relationships", {}) or {}).get("registered_from", {}) or {}
              ).get("links", {}) or {}
    related = origin.get("related", {}) or {}
    href = str(related.get("href") or "")
    if f"/nodes/{node_id}/" not in href:
        raise OSFError(
            f"registration response is not bound to node {node_id!r} "
            f"(registered_from: {href!r}); treat it as unverified and do not publish"
        )
    return str(reg["id"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
USAGE = __doc__


def _token() -> str:
    token = os.environ.get("OSF_TOKEN", "")
    if not token:
        raise OSFError("OSF_TOKEN not set — refusing to contact OSF (ADR-0088 fail-closed)")
    return token


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    try:
        if mode == "--preflight":
            token = _token()
            node_id = os.environ.get("OSF_NODE_ID", "")
            if not node_id:
                raise OSFError("OSF_NODE_ID is required for --preflight")
            pkg = PACKAGE_DIR
            read_manifest(pkg)
            report = preflight(pkg, Client(token), node_id)
            print(json.dumps(report, indent=2))
            if any(report.values()):
                print("preflight: remote tree does NOT match the manifest", file=sys.stderr)
                return 1
            print("preflight: remote tree matches the manifest")
            return 0

        if mode == "--verify-contract":
            token = _token()
            node_id = os.environ.get("OSF_NODE_ID", "")
            if not node_id:
                raise OSFError("OSF_NODE_ID is required for --verify-contract")
            rec = verify_contract(Client(token), node_id)
            print(json.dumps(rec, indent=2))
            print(f"contract check written to {CONTRACT_RECORD}")
            return 0

        if mode == "--embargo":
            value = argv[1] if len(argv) > 1 else None
            end = validate_embargo(value)          # before any network call
            token = _token()
            node_id = os.environ.get("OSF_NODE_ID", "")
            if not node_id:
                raise OSFError("OSF_NODE_ID is required (reuse the private project)")
            pkg = PACKAGE_DIR
            manifest = read_manifest(pkg)
            verify_local_package(pkg, manifest)
            redaction_ok(pkg)

            ok, why = contract_record_is_current(CONTRACT_RECORD)
            if not ok:
                raise OSFError(f"registration blocked: {why}")

            client = Client(token)
            print("==> uploading package")
            counts = upload_all(pkg, client, node_id)
            print(f"    {counts}")

            # Registration stays blocked until the remote tree matches the
            # manifest exactly (missing, extra, and mismatched all empty).
            after = preflight(pkg, client, node_id)
            if any(after.values()):
                print(json.dumps(after, indent=2), file=sys.stderr)
                raise OSFError(
                    "registration blocked: remote tree still differs after upload"
                )

            print(f"==> creating embargoed registration (ends {end.isoformat()})")
            reg_id = submit_registration(client, node_id, end)
            REGISTRY_RECORD.parent.mkdir(parents=True, exist_ok=True)
            REGISTRY_RECORD.write_text(json.dumps({
                "adr": "ADR-0088",
                "osf_node_id": node_id,
                "osf_registration_id": reg_id,
                "embargo_end_date": end.isoformat(),
                "recorded_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "note": ("embargoed registration; making it public is the "
                         "owner's decision, not the agent's"),
            }, indent=2) + "\n")
            print(f"    registration: {reg_id}")
            return 0

        print(USAGE, file=sys.stderr)
        return 2
    except OSFError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
