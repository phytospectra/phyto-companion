"""Tests for the OSF evidence uploader (ADR-0088).

Offline tests prove the fail-closed gates (embargo mandatory, no network
on refusal). Network tests run against a local fake HTTP server that
records every request, so the observed nested-folder HTTP 500 and the
authentication-failure paths are exercised without touching OSF.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import urllib.parse
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from scripts.osf_evidence import (  # noqa: E402
    Client,
    OSFError,
    AuthError,
    UploadConflict,
    contract_record_is_current,
    embargo_payload,
    preflight,
    read_manifest,
    remote_tree,
    upload_all,
    upload_file,
    validate_embargo,
)


# --------------------------------------------------------------------------
# 2. Embargo gate — proven offline: no network call may happen
# --------------------------------------------------------------------------
class _BoomClient:
    """Any use means the code tried to contact OSF — the test fails."""

    def __getattr__(self, name):
        raise AssertionError(f"network method {name} called during embargo refusal")


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45",
                                   "2020-01-01"])
def test_embargo_invalid_or_missing_refuses_before_any_network(value):
    with pytest.raises(OSFError) as exc:
        validate_embargo(value)
    assert "embargo" in str(exc.value).lower()
    # A missing/invalid date must exit before any registration request.
    _ = _BoomClient()  # would explode if anything tried to use a client


def test_embargo_future_date_accepted():
    tomorrow = date.today() + timedelta(days=1)
    assert validate_embargo(tomorrow.isoformat()) == tomorrow


def test_embargo_payload_shape():
    end = date.today() + timedelta(days=30)
    payload = embargo_payload("draft-fake-1", end)
    attrs = payload["data"]["attributes"]
    assert payload["data"]["type"] == "registrations"
    assert attrs["draft_registration_id"] == "draft-fake-1"
    assert attrs["registration_choice"] == "embargo"
    assert attrs["embargo_end_date"] == end.isoformat()
    # no non-embargoed fallback exists anywhere in the payload
    assert "embargo" in attrs["registration_choice"]


def test_draft_payload_shape():
    from scripts.osf_evidence import draft_payload
    payload = draft_payload()
    rel = payload["data"]["relationships"]["registration_schema"]["data"]
    assert payload["data"]["type"] == "draft_registrations"
    assert rel["type"] == "registration_schema"
    assert rel["id"] == SCHEMA_ID


# --------------------------------------------------------------------------
# Fake OSF server: records requests, serves a scripted remote state
# --------------------------------------------------------------------------
class FakeOSF:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.files: dict[str, str] = {}       # path -> sha256
        self.folders: set[str] = set()
        self.registrations: list[dict] = []
        self.drafts: list[dict] = []
        self.unbound_registration = False
        self.unbound_draft = False
        self.no_schema = False
        self.base = ""                        # set by the fixture
        self.behaviors: list[str] = []        # e.g. "500-on-create:adr/ADR-0085.md"
        self.fail_auth = False

    def handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _authed(self) -> bool:
                if outer.fail_auth:
                    return False
                return self.headers.get("Authorization", "").startswith("Bearer ")

            def _drain(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def do_GET(self):
                outer.requests.append(("GET", self.path, {}))
                if not self._authed():
                    self.send_response(401); self.end_headers(); return
                body = {"data": [], "links": {}}
                p = urlparse(self.path).path
                if p.rstrip("/") == f"/v2/registration_schemas/{SCHEMA_ID}":
                    if outer.no_schema:
                        self.send_response(404); self.end_headers(); return
                    body = {"data": {"id": SCHEMA_ID,
                                     "type": "registration_schemas"}}
                elif p.rstrip("/") == "/v2/nodes/t628k":
                    body = {"data": {
                        "id": "t628k",
                        "type": "nodes",
                        "attributes": {},
                        "relationships": {
                            "draft_registrations": {
                                "links": {"related": {"href":
                                    f"{outer.base}/v2/nodes/t628k/draft_registrations/"}},
                            },
                        },
                    }}
                elif p.rstrip("/") == "/v2/registrations":
                    body = {"data": [{
                        "type": "registrations",
                        "attributes": {
                            "registration_choice": "embargo",
                            "embargo_end_date": "2030-01-01",
                            "pending_embargo_approval": False,
                        },
                    }]}
                marker = "/files/osfstorage/"
                if marker in p:
                    parent = p.split(marker, 1)[1]
                    parent = urllib.parse.unquote(parent).strip("/")
                    items = []
                    for rel, sha in outer.files.items():
                        folder, _, name = rel.rpartition("/")
                        if folder == parent:
                            items.append({
                                "attributes": {"kind": "file", "name": name,
                                               "path": rel,
                                               "extra": {"hashes": {"sha256": sha}}},
                            })
                    for f in sorted(outer.folders):
                        if f.rpartition("/")[0] == parent:
                            items.append({
                                "attributes": {"kind": "folder", "name": f.rpartition("/")[2],
                                               "path": f},
                            })
                    body["data"] = items
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_PUT(self):
                outer.requests.append(("PUT", self.path, {}))
                if not self._authed():
                    self._drain()   # read the body or the client gets a reset
                    self.send_response(401); self.end_headers(); return
                p = urlparse(self.path).path
                q = parse_qs(urlparse(self.path).query)
                kind = q.get("kind", ["file"])[0]
                name = q.get("name", [""])[0]
                payload = self._drain()
                provider, _, parent = p.split("/providers/osfstorage")[1].partition("/")
                parent = parent.strip("/")
                if kind == "folder":
                    target = f"{parent}/{name}" if parent else name
                    if target not in outer.folders:
                        outer.folders.add(target)
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(
                        {"data": {"attributes": {"path": "/" + target}}}).encode())
                    return
                target = f"{parent}/{name}" if parent else name
                for b in outer.behaviors:
                    if b == f"500-on-create:{target}":
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(b"Unable to resolve")
                        return
                # Real OSF refuses a create when the name already exists
                # (409 / 500); an update must go through the file's upload
                # link instead. Reproduce that so retry logic is exercised.
                if target in outer.files:
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"file exists")
                    return
                sha = hashlib.sha256(payload).hexdigest()
                created = target not in outer.files
                outer.files[target] = sha
                self.send_response(201 if created else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(
                    {"data": {"attributes": {"extra": {"hashes": {"sha256": sha}}}}}
                ).encode())

            def do_POST(self):
                payload = json.loads(self._drain() or b"{}")
                outer.requests.append(("POST", self.path, payload))
                if not self._authed():
                    self.send_response(401); self.end_headers(); return
                p = urlparse(self.path).path.rstrip("/")
                if p == "/v2/nodes/t628k/draft_registrations":
                    if outer.fail_auth:
                        self.send_response(401); self.end_headers(); return
                    outer.drafts.append(payload)
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "data": {
                            "id": "draft-fake-1",
                            "type": "draft_registrations",
                            "attributes": {},
                            "relationships": {
                                "branched_from": {
                                    "data": ({"type": "nodes", "id": "t628k"}
                                             if not outer.unbound_draft
                                             else {"type": "nodes", "id": "other"}),
                                },
                            },
                        }
                    }).encode())
                    return
                if p == "/v2/registrations":
                    if outer.fail_auth:
                        self.send_response(401); self.end_headers(); return
                    attrs = payload.get("data", {}).get("attributes", {})
                    outer.registrations.append({"attributes": attrs})
                    self.send_response(201)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "data": {
                            "id": "reg-fake-123",
                            "type": "registrations",
                            "attributes": dict(
                                attrs, embargo_end_date=attrs.get("embargo_end_date")),
                            "relationships": {
                                "registered_from": {"links": {
                                    "related": {"href": (f"{outer.base}/v2/nodes/t628k/"
                                                         if not outer.unbound_registration
                                                         else f"{outer.base}/v2/nodes/other/")}},
                                },
                            },
                        }
                    }).encode())
                    return
                self.send_response(404); self.end_headers()

        return H


SCHEMA_ID = "69c6a4a4ccbbae0023c53d43"  # mirrors scripts.osf_evidence


@pytest.fixture
def fake_osf():
    server = FakeOSF()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.handler())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    server.base = base
    yield server, base
    httpd.shutdown()


@pytest.fixture(autouse=True)
def _never_touch_real_records(tmp_path, monkeypatch):
    """Every test in this module redirects the script's record paths into
    tmp. No test can ever write (or delete) the repo's real
    validated_runs/osf_registration.json or osf_contract_check.json —
    the leak of the `reg-fake-123` receipt into the real output directory
    must stay impossible (finding 2, ADR-0088 handoff 2026-11-01).
    """
    import scripts.osf_evidence as mod
    monkeypatch.setattr(mod, "REGISTRY_RECORD", tmp_path / "osf_registration.json")
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "osf_contract_check.json")


def _client(base):
    return Client("test-token", api_base=f"{base}/v2", files_base=f"{base}/v1/resources")


# --------------------------------------------------------------------------
# 3. Retry-safe uploads
# --------------------------------------------------------------------------
def _pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "adr").mkdir(parents=True, exist_ok=True)
    (pkg / "validated_runs" / "M1a").mkdir(parents=True, exist_ok=True)
    files = {
        "MANIFEST.sha256": b"root manifest",
        "CITATION.cff": b"citation",
        "adr/ADR-0085.md": b"adr 85 text",
        "validated_runs/M1a/run_summary.json": b"{}",
    }
    manifest = []
    for rel, data in files.items():
        if rel == "MANIFEST.sha256":
            continue
        (pkg / rel).write_bytes(data)
        manifest.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
    (pkg / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n")
    return pkg


def test_upload_nested_folder_500_is_retried_and_resolved(fake_osf, tmp_path):
    """The observed failure: create returns HTTP 500 for a nested folder."""
    server, base = fake_osf
    # the file already exists on the server with the SAME content
    data = b"adr 85 text"
    server.files["adr/ADR-0085.md"] = hashlib.sha256(data).hexdigest()
    server.folders.add("adr")
    server.behaviors.append("500-on-create:adr/ADR-0085.md")
    client = _client(base)
    outcome = upload_file(client, "t628k", "adr", "adr/ADR-0085.md",
                          data, hashlib.sha256(data).hexdigest())
    assert outcome == "skipped"
    puts = [r for r in server.requests if r[0] == "PUT" and "ADR-0085" in r[1]]
    assert puts, "create was attempted"
    # no write reached the existing file: its content is unchanged
    assert server.files["adr/ADR-0085.md"] == hashlib.sha256(data).hexdigest()


def test_upload_conflict_reports_before_replacing(fake_osf):
    server, base = fake_osf
    server.folders.add("adr")
    server.files["adr/ADR-0085.md"] = hashlib.sha256(b"different content").hexdigest()
    server.behaviors.append("500-on-create:adr/ADR-0085.md")
    client = _client(base)
    with pytest.raises(UploadConflict) as exc:
        upload_file(client, "t628k", "adr", "adr/ADR-0085.md",
                    b"adr 85 text", hashlib.sha256(b"adr 85 text").hexdigest())
    assert "conflict" in str(exc.value)
    assert "Not replacing" in str(exc.value)
    # the remote content is untouched
    assert server.files["adr/ADR-0085.md"] == hashlib.sha256(b"different content").hexdigest()


def test_upload_unresolvable_error_surfaces_original(fake_osf):
    """No file ever appears after bounded retries: original error surfaces."""
    server, base = fake_osf
    server.folders.add("adr")
    server.behaviors.append("500-on-create:adr/ADR-0085.md")
    client = _client(base)
    with pytest.raises(OSFError) as exc:
        upload_file(client, "t628k", "adr", "adr/ADR-0085.md",
                    b"adr 85 text", "0" * 64)
    msg = str(exc.value)
    assert "HTTP 500" in msg and "no file named" in msg


def test_upload_all_root_and_nested(fake_osf, tmp_path):
    server, base = fake_osf
    client = _client(base)
    counts = upload_all(_pkg(tmp_path), client, "t628k")
    assert counts == {"uploaded": 3, "skipped": 0}
    assert set(server.files) == {
        "CITATION.cff", "adr/ADR-0085.md", "validated_runs/M1a/run_summary.json",
    }


def test_upload_all_is_idempotent(fake_osf, tmp_path):
    server, base = fake_osf
    client = _client(base)
    upload_all(_pkg(tmp_path), client, "t628k")
    counts = upload_all(_pkg(tmp_path), client, "t628k")
    assert counts == {"uploaded": 0, "skipped": 3}


def test_authentication_failure_raises_auth_error(fake_osf):
    """A rejected token is a distinct error, before any file state matters."""
    server, base = fake_osf
    server.fail_auth = True
    client = _client(base)
    with pytest.raises(AuthError):
        remote_tree(client, "t628k")
    with pytest.raises(AuthError):
        upload_file(client, "t628k", "", "CITATION.cff", b"x", "0" * 64)
    # and nothing was written
    assert server.files == {}


# --------------------------------------------------------------------------
# 4. Preflight: verification separated from registration
# --------------------------------------------------------------------------
def test_preflight_reports_missing_extra_mismatched(fake_osf, tmp_path):
    server, base = fake_osf
    server.files = {
        "adr/ADR-0085.md": hashlib.sha256(b"adr 85 text").hexdigest(),
        "stray.txt": hashlib.sha256(b"stray").hexdigest(),
        "validated_runs/M1a/run_summary.json": hashlib.sha256(b"OLD").hexdigest(),
    }
    server.folders.update({"adr", "validated_runs", "validated_runs/M1a"})
    client = _client(base)
    report = preflight(_pkg(tmp_path), client, "t628k")
    assert report["missing"] == ["CITATION.cff"]
    assert report["extra"] == ["stray.txt"]
    assert report["mismatched"] == ["validated_runs/M1a/run_summary.json"]


def test_preflight_clean(fake_osf, tmp_path):
    server, base = fake_osf
    pkg = _pkg(tmp_path)
    client = _client(base)
    upload_all(pkg, client, "t628k")
    report = preflight(pkg, client, "t628k")
    assert report == {"missing": [], "extra": [], "mismatched": []}


def test_registration_blocked_when_preflight_dirty(fake_osf, tmp_path, monkeypatch,
                                                   capsys):
    """The full --embargo flow refuses when the remote tree is dirty."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "PACKAGE_DIR", _pkg(tmp_path))
    rec = {"verified": True, "script_sha256": mod.script_sha256()}
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "contract.json")
    (tmp_path / "contract.json").write_text(json.dumps(rec))
    server.files = {"stray.txt": "0" * 64}   # extra remote file
    argv = ["--embargo", (date.today() + timedelta(days=1)).isoformat()]
    assert mod.main(argv) == 1
    assert "blocked" in capsys.readouterr().err
    # no registration request was ever made
    assert not any("/registrations/" in r[1] for r in server.requests)


def test_registration_blocked_without_current_contract_record(fake_osf, tmp_path,
                                                              monkeypatch):
    import scripts.osf_evidence as mod
    server, base = fake_osf
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "PACKAGE_DIR", _pkg(tmp_path))
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "absent.json")
    argv = ["--embargo", (date.today() + timedelta(days=1)).isoformat()]
    assert mod.main(argv) == 1
    assert not any("/registrations/" in r[1] for r in server.requests)


def test_registration_blocked_without_embargo_arg(fake_osf, tmp_path, monkeypatch):
    """No --embargo at all: exit 1, zero network requests."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    assert mod.main(["--embargo"]) == 1
    assert server.requests == []


def test_registration_request_binds_target_node(fake_osf, tmp_path, monkeypatch):
    """Two-step flow: draft on t628k, then embargo with that draft id."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "PACKAGE_DIR", _pkg(tmp_path))
    rec = {"verified": True, "script_sha256": mod.script_sha256()}
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "contract.json")
    monkeypatch.setattr(mod, "REGISTRY_RECORD", tmp_path / "reg")
    (tmp_path / "contract.json").write_text(json.dumps(rec))
    argv = ["--embargo", (date.today() + timedelta(days=1)).isoformat()]
    assert mod.main(argv) == 0
    posts = [r for r in server.requests if r[0] == "POST"]
    assert len(posts) == 2
    # step 1: the draft is created on the node itself (path binding)
    assert posts[0][1].endswith("/v2/nodes/t628k/draft_registrations/")
    schema = posts[0][2]["data"]["relationships"]["registration_schema"]["data"]
    assert schema == {"type": "registration_schema", "id": SCHEMA_ID}
    # step 2: the embargo carries the draft id returned by step 1
    assert posts[1][1].endswith("/v2/registrations/")
    attrs = posts[1][2]["data"]["attributes"]
    assert attrs["draft_registration_id"] == "draft-fake-1"
    assert attrs["registration_choice"] == "embargo"
    out = json.loads((tmp_path / "reg").read_text())
    assert out["osf_registration_id"] == "reg-fake-123"
    assert out["osf_node_id"] == "t628k"

def test_unbound_draft_registration_is_refused(fake_osf, tmp_path, monkeypatch,
                                               capsys):
    """A draft whose branched_from is not t628k must not become a
    registration on t628k."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    server.unbound_draft = True
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "PACKAGE_DIR", _pkg(tmp_path))
    rec = {"verified": True, "script_sha256": mod.script_sha256()}
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "contract.json")
    monkeypatch.setattr(mod, "REGISTRY_RECORD", tmp_path / "reg")
    (tmp_path / "contract.json").write_text(json.dumps(rec))
    argv = ["--embargo", (date.today() + timedelta(days=1)).isoformat()]
    assert mod.main(argv) == 1
    assert "not rooted at node" in capsys.readouterr().err
    # only the draft POST happened; the embargo POST was never sent
    posts = [r for r in server.requests if r[0] == "POST"]
    assert len(posts) == 1
    assert not server.registrations


def test_unbound_registration_response_is_refused(fake_osf, tmp_path, monkeypatch,
                                                  capsys):
    """A response with no target node must not be trusted or published."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    server.unbound_registration = True
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "PACKAGE_DIR", _pkg(tmp_path))
    rec = {"verified": True, "script_sha256": mod.script_sha256()}
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "contract.json")
    (tmp_path / "contract.json").write_text(json.dumps(rec))
    argv = ["--embargo", (date.today() + timedelta(days=1)).isoformat()]
    assert mod.main(argv) == 1
    assert "not bound to node" in capsys.readouterr().err
    # nothing was published
    assert not (tmp_path / "reg").exists()


def test_empty_contract_listing_never_verifies(fake_osf, tmp_path, monkeypatch,
                                               capsys):
    """A registrations list with no entries must fail --verify-contract."""
    import scripts.osf_evidence as mod
    server, base = fake_osf
    monkeypatch.setenv("OSF_TOKEN", "t")
    monkeypatch.setenv("OSF_NODE_ID", "t628k")
    monkeypatch.setattr(mod, "API_BASE", f"{base}/v2")
    monkeypatch.setattr(mod, "FILES_BASE", f"{base}/v1/resources")
    monkeypatch.setattr(mod, "CONTRACT_RECORD", tmp_path / "contract.json")
    # make the sampled registrations listing empty
    original_get = mod.Client.get_json

    def get_json(self, url):
        if urlparse(url).path.rstrip("/") == "/v2/registrations":
            return {"data": [], "links": {}}
        return original_get(self, url)

    monkeypatch.setattr(mod.Client, "get_json", get_json)
    assert mod.main(["--verify-contract"]) == 1
    err = capsys.readouterr().err
    assert "could not be verified" in err
    rec = json.loads((tmp_path / "contract.json").read_text())
    assert rec["verified"] is False




def test_real_registration_record_absent_and_untouched():
    """The real output directory holds no fake receipt, and running this
    module's tests cannot create one: the autouse fixture redirects the
    real record path for every test, so a green run here is itself the
    proof (finding 2, ADR-0088 handoff 2026-11-01).
    """
    import scripts.osf_evidence as mod
    # reg-fake-123 was leaked here by an earlier test run; removed 2026-11-01
    assert not mod.REGISTRY_RECORD.is_file(), (
        f"{mod.REGISTRY_RECORD} exists — a test wrote the real registration "
        "record again; do not publish or upload it"
    )


def test_contract_record_staleness(tmp_path, monkeypatch):
    import scripts.osf_evidence as mod
    rec_path = tmp_path / "contract.json"
    rec_path.write_text(json.dumps({"verified": True,
                                    "script_sha256": "stale"}))
    ok, why = contract_record_is_current(rec_path)
    assert not ok and "re-run --verify-contract" in why
    rec_path.write_text(json.dumps({"verified": True,
                                    "script_sha256": mod.script_sha256()}))
    ok, why = contract_record_is_current(rec_path)
    assert ok and why == ""


# --------------------------------------------------------------------------
# 1. Package scope — the builder's allowlist, checked on the real build
# --------------------------------------------------------------------------
def test_built_package_has_no_third_party_data():
    repo = Path(__file__).resolve().parent.parent
    pkg = repo / "data" / "osf_package"
    if not pkg.is_dir():
        pytest.skip("package not built in this checkout")
    names = {str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file()}
    assert not any(n.lower().endswith((".mzml", ".raw")) for n in names)
    ref = json.loads((pkg / "dataset_reference.json").read_text())
    assert ref["dataset"].startswith("MSV000090047")
    assert ref["files"], "accession + checksums must stay by reference"
    assert all("sha256" in f for f in ref["files"])
    manifest = read_manifest(pkg)
    assert set(manifest) == names - {"MANIFEST.sha256"}
