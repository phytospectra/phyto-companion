# Release reproducibility checks — 2026-11-09

## Scope and evidence identity

This is offline release-engineering evidence, not scientific validation, deployment readiness, or approval to publish. No external service, paid inference, model/training download, deployment, author contact, release/DOI publication, credential deletion/rotation, commit or push was performed by this task. See [approval packet](RELEASE-APPROVAL-PACKET-2026-11-09.md) for remaining gates and proposed staging/rollback.

Repository: `/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/phyto-companion`. No applicable AGENTS.md was found at repository/ancestor level. `docs/WORKING_PRACTICES.md` and the ADR index were inspected. Concurrent edits were present; this task did not edit executor, builder, tests, CITATION or release notes.

**Final integrated HEAD must be pinned by the parent final report after integration.** The snapshot base was `424fdda0a51d628cfdc363743d059472678bfa13`, branch `agent/phase0a-phase1a-failclosed-runtime`, with dirty working files. This is a baseline identifier, not certification that the tested content equals that commit.

Temporary evidence directory (ephemeral, not a release asset): `/var/folders/s4/2mmjynmx3dzd2n8d8k35s3680000gn/T/phyto-release-offline-20261109-3m5lf1h2`. It contains the runner, network policy, source hash manifests, dependency inventory, per-command logs and category/location-only audit output. Do not publish the whole temporary source or logs without the public-data review below.

- Initial snapshot: 667 existing tracked files; SHA256 of sorted per-file manifest `5b98481cf763f142ce166cd9165a99ef91a5d82079952ba9875ada139582a0dc`.
- Refreshed candidate snapshot: 681 existing tracked **plus nonignored untracked** working files; manifest SHA256 `0d063e7a6143726734a0acd1054ba823dadf7783b1ea342759e4291305098c82`.
- Eight indexed generated reference-library files were absent while another agent intentionally replaced them with quarantine/template artifacts. Initial `make test` and `make dry-run` both stopped at collection because the new `manifest.template.csv` was not yet tracked and therefore absent from the tracked-only snapshot. This is recorded as a transitional packaging failure, not a final source bug; parent must include the intended additions/deletions in the final artifact.

## Isolation and dependency evidence

Host: macOS arm64; CPython **3.12.15**. Created a fresh temporary venv with `with_pip=False`, then a `.pth` exposing the existing repository venv's site-packages and a copied existing Ruff executable. No dependency installer/download ran. Explicit `PYTHONPATH` selects copied source, not an editable package install. A pre-existing `phyto-companion-shared==0.1.0` distribution remains visible in reused metadata.

**This is clean environment-variable/home/cache/source isolation with dependency reuse, NOT a clean dependency installation, fully hermetic build, or portable lockfile test.** The interpreter, OS libraries, installed dependencies and some installed metadata remain shared. `requirements-reproducibility-2026-11-09.txt` records exact observed third-party versions (including pre-existing extras), omitting the local shared distribution. It is not automatically consumed by Makefile/CI/Docker, has no wheel hashes, and is not a claim of availability on other platforms. A reviewed offline wheelhouse and hash-locked clean installation remain pending.

The runner uses a minimal explicit environment, temporary HOME/TMPDIR/cache/MPLCONFIGDIR, `PYTHONNOUSERSITE=1`, bytecode/pytest/Ruff cache disabling, pytest plugin autoload disabling, `PIP_NO_INDEX=1`, `UV_OFFLINE=1`, and no inherited cloud/OSF/provider/MZmine credentials/configuration variables. Default mode is local mock science, auth enforcement off, auth dry-run on, LLM local. Tests can override those settings internally with injected collaborators.

macOS `sandbox-exec` enforced external-network denial with loopback exceptions (needed by fake HTTP servers). A probe to reserved external address `192.0.2.1:443` failed with PermissionError/errno 1; loopback bind succeeded. The policy denied writes to the live repository and reads of usual home `.config`, `.aws`, `.ssh` and `.netrc` locations. It is not a universal filesystem-secret confinement proof. No live production read-only checks were run either.

## Check results

Initial snapshot, actual results:

| Command/check | Result |
|---|---|
| reused environment `python -m pip check` | exit 0, no broken requirements |
| `make stage-auth` | exit 0 |
| `make lint` | exit 0; shared/services/scripts/tests/**benchmark** |
| `python scripts/check_adr_numbers.py` | exit 0 |
| `python -m pytest tests/test_docs -q` | 6 passed in 0.37s |
| `make test`; `make dry-run` | both exit 2 during collection; transitional missing template described above |
| first API smoke | startup poll expired at 10 seconds; rerun with 60-second bound, not evidence of a source defect |

Refreshed candidate results: `make stage-auth`, `make lint` and the ADR script exited 0; docs tests **6 passed in 0.33s**. The full sandboxed `make test` hit the runner's **600-second subprocess timeout**; no full-suite pass or test-failure verdict was obtained. The runner exited before its API smoke stage, so the longer-bound smoke was **not executed**. A subsequent process check found no remaining process referencing the temporary evidence directory. Do not interpret the initial 10-second startup timeout as successful boot or a diagnosed source failure. `make dry-run` is the identical pytest recipe to `make test`; no second full refreshed run was made.

Parent-reported integration evidence, not independently queried by this task: GitHub CI run **42507585832**, revision **02f54cc**, **1766 passed, 4 skipped, 14 warnings**, success. This is distinct from the timed-out sandbox reproduction and is not a pin of the final integrated HEAD. Later ownership/security edits and final documentation still require the parent to establish selected-revision CI coverage. The bounded offline quickstart is therefore **partial/blocked, not a clean pass**.

## Repeatable offline commands

Run in a **disposable source copy**, never reuse an ambient cloud shell environment. Select reviewed final SHA after integration; the dirty working-copy procedure used here also includes explicitly reviewed untracked candidate files, which a Git archive would omit. For final reproducibility prefer `git archive "$REVIEWED_SHA"` into a new directory, plus a recorded per-file manifest, once final files are committed. Do not run installs or archive publication under this offline authorization.

For the same host with already installed dependencies (substitute approved paths):

```sh
REPO='/absolute/path/to/phyto-companion'
TMP=$(mktemp -d -t phyto-release-offline)
# Populate "$TMP/source" with the reviewed source artifact; do not copy .git,
# .env, frontend, tools, ignored bulk data, or the existing .venv.
"$REPO/.venv/bin/python" -m venv --without-pip "$TMP/source/.venv"
SITE=$("$REPO/.venv/bin/python" -c 'import site; print(site.getsitepackages()[0])')
printf '%s\n' "$SITE" > "$TMP/source/.venv/lib/python3.12/site-packages/reused-existing-deps.pth"
cp "$REPO/.venv/bin/ruff" "$TMP/source/.venv/bin/ruff"
mkdir -p "$TMP/home" "$TMP/tmp" "$TMP/cache" "$TMP/mpl"
printf '%s\n' '(version 1) (allow default) (deny network*) (allow network-inbound (local ip "localhost:*")) (allow network-outbound (remote ip "localhost:*"))' > "$TMP/offline.sb"
cd "$TMP/source"
sandbox-exec -f "$TMP/offline.sb" env -i \
  PATH="$TMP/source/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin" \
  HOME="$TMP/home" TMPDIR="$TMP/tmp" XDG_CACHE_HOME="$TMP/cache" \
  MPLCONFIGDIR="$TMP/mpl" MPLBACKEND=Agg PYTHONNOUSERSITE=1 \
  PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTEST_ADDOPTS='-p no:cacheprovider' RUFF_NO_CACHE=true \
  PIP_NO_INDEX=1 UV_OFFLINE=1 PHYTO_ENV=local PHYTO_DRY_RUN=1 \
  PHYTO_AUTH_ENABLED=0 PHYTO_SCIENTIFIC_EXECUTION=mock PHYTO_LLM_PROVIDER=local \
  /bin/sh -c 'make stage-auth && .venv/bin/python -m pip check && make lint && .venv/bin/python scripts/check_adr_numbers.py && make test'
```

The recorded runner additionally denies live-repository writes and common credential-directory reads as described above; retain those protections when adapting the example. On another OS require an equivalent network namespace/firewall boundary; env flags alone are not a network guarantee. Never use `make install` as an offline step: it may recreate `.venv` and performs unpinned resolution/downloads.

After stage-auth, the local API smoke uses explicit `PYTHONPATH=shared:build/auth-path:.`, the same uvicorn app/app-dir as `make serve`, but a free loopback port instead of fixed 8000. Poll `/health` for up to 60 seconds, assert mock mode and `scientific_ready:false`, then terminate the child. This is a serve-path equivalent smoke, not a literal `make serve` execution or real science request. For a manual isolated local session the intended equivalent is:

```sh
PHYTO_ENV=local PHYTO_DRY_RUN=1 PHYTO_AUTH_ENABLED=0 \
PHYTO_SCIENTIFIC_EXECUTION=mock PHYTO_LLM_PROVIDER=local make serve
```

Never expose that auth-off/mock configuration publicly. README quickstart omits explicit scientific mode and has stale speed/architecture descriptions; its claimed sub-second test run is not evidence. Real acceptance tests inject offline auth/libraries and do not establish cloud login, rights or scientific accuracy.

## CI, deployment and docs-gate inspection

- Current CI: Ubuntu/Python 3.12, 30-minute timeout, pushes to main and `agent/phase0a-phase1a-failclosed-runtime`, pull requests; unpinned pip installs, stage-auth, lint, local test. No CI service/network restriction or hash-locked dependencies are specified. Action versions use major tags, not immutable SHAs. No remote CI/settings query was performed here; November 8 observations are historical.
- Current Makefile lint includes benchmark; previous readiness report's narrower lint scope is historical. `make test` and `make dry-run` are the same root pytest command. Test bootstrap pins local/mock mode at import and injects auth aliases; it does not prove the literal API startup command works without configuration.
- `infrastructure/Dockerfile.fullplane`: mutable `python:3.12-slim`, broad pip dependencies, cloud env default, no explicit scientific mode/default library. `cloudbuild-fullplane.yaml` targets mutable `latest`. No container build was run; final deployment must use approved immutable digest and reviewed configuration.
- `DEPLOYMENT_GCP.md` begins with superseding readiness/source-only caveats but retains historical mutation snippets including bucket deletion and DNS changes. None are release authorization or rollback instructions. Real auth is not mock auth, and health/readiness/library counts are not scientific validation. Keep historical facts distinct from source changes and current live facts.
- ADR checker implements duplicate **definitions**, missing index coverage and unresolved citations; it passes. The index contains duplicate ADR-0077 rows and claims an additional citation-use invariant the script does not implement. Report to docs owner; no ADR renumbering or new decision was made here. Root docs pytest additionally verifies rubric byte equality and QA wiring.

## Tracked public-tree audit (redacted)

Scope: `git ls-files` working-copy bytes, not Git history, remote hosting, ignored files, or full binary/compressed acquisition contents. At audit time: **675 indexed paths**, **667 existing files**, **8 intentional pending reference-library deletions**, **31,527,438 bytes** read. Binary files were listed/classified, not semantically decoded. Concurrent additions require a final-tree repeat before release. Pattern reporting contained categories, paths and line numbers only, never matched values.

High-specificity patterns checked: private-key blocks/service-account private fields, AWS access IDs, GitHub/Stripe/Google/OpenAI/OpenRouter credential forms, JWT/bearer literals, credential-bearing URLs, credential filenames. **No matches found in scanned text for those high-specificity categories. This is not proof of absence of secrets.** Three broader assignment candidates were contextualized as the documented development-only HMAC fallback in `shared/phyto_shared/share.py` and mock-transport test keys in LLM/GNPS tests. No secret values are reproduced here; production must continue rejecting development defaults.

Publication gates found:

1. `benchmark/blinded_pilot/keys/answer_keys.json` is tracked; `review/worksheets.json` is evaluator-only per PROTOCOL. The protocol explicitly forbids sending these to model arms. A full source archive includes evaluator material, despite model-visible packet leak checks. Keep smoke-test/evaluator packaging separate; any intentionally public answers invalidate secrecy/held-out claims. No files were removed.
2. `tests/fixtures/nmr_case_study/archive_a/` and `archive_b/` contain tracked compressed acquisitions; one `2rr.gz` is **13,419,889 bytes**. PROTOCOL calls private CaseStudy archives local-only. Owner/curator must resolve provenance, privacy and redistribution before unrestricted archive release; do not assume .gitignore excludes already tracked files.
3. Tracked Curcuma Bruker-style raw/processed fixtures are described as teaching approximations in `README_Curcuma.md`. Confirm explicit synthetic labelling and reuse rights rather than presenting them as independent experimental validation. Binary content itself was not audited for identifiers.
4. `data/structure_libraries/tea_seed_cake_TSC_2026-11-03.csv` contains sample-specific structure/evidence records. Public provenance/reuse approval was not established in this audit. Treat as review-required, not conclusively private or automatically open.
5. Five text files matched local-user path patterns (some placeholders/redactions); eight matched email-like metadata (largely citation/research/test material). This does not establish credential exposure, but metadata/minimization review is needed, including `data/inventory/dataset_inventory.json`, operational logs/batches under validated runs and copied `data/osf_package` metadata. No addresses/personal values are printed here. Root citation is parent-owned; archive citation metadata needs separate consistency review.

No deletion, history rewrite, rotation or public-private switch was attempted. An Apache software license or data CC0 notice does not blanket-relicense third-party inputs. Final release requires reviewed public artifact boundaries, license-cleared evidence and a fresh audit of the selected final revision.

## Clean-install isolated reproduction — final revision evidence (2026-11-09)

Exact archive at backend `a64d9c0472d267edf1f8c4cf558f0209e90f52eb` plus the
committed `7e36f13` sidecar-support pair (harness/test identical bytes),
fetched from GitHub into a temporary directory outside the cloud-managed
Documents tree (macOS `dataless` hydration had caused earlier stalls).

- Environment setup (network-enabled): `make install` via uv on CPython
  3.12.15; 48 resolved packages installed. Not air-gapped.
- Offline execution after setup, with minimal environment, no ambient
  credentials and explicit mode settings:
  - `make stage-auth`, `make lint` (includes benchmark) — exit 0.
  - `make test` — **1,843 passed, four skipped, 14 warnings in 104.03 s**, exit 0.
  - `scripts/verify_offline.py` (real mode) — health, fail-closed config
    contracts, synthetic real workflow, 422 unsupported workflow, checkpoint
    completion — exit 0.
  - `benchmark/reference_hsqc_acceptance/run_evaluation.py` — 6/6 synthetic
    contracts, measured validation count 0.
  - Full suite run at 251.08 s on the cold snapshot and 104.03 s after
    hydration; both passes recorded. The earlier 600-second timeout was the
    cloud-managed tree's `dataless` file reads, not a test defect.
- Source archives require provenance: `PHYTO_SOURCE_REVISION` (40 hex) or a
  `.source-revision` sidecar; harness and runner agree.

Final CI on the pushed revision is recorded in the completion ledger; this
isolated run is local reproduction evidence for the same source, not a
substitute for GitHub CI. Scientific accuracy remains unvalidated: every
fixture above is explicitly synthetic.
