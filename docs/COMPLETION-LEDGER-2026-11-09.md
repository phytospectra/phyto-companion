# Phytospectra completion ledger — 2026-11-09

## Decision

Reviewable source hardening and Markdown manuscript corrections are achievable;
**scientific release, production deployment and submission readiness are NO-GO**.
Implemented, tested, scientifically validated and deployed are separate states.
No deployment, release/DOI publication, paid inference, training or author contact.

## Baseline independently verified

Backend branch `agent/phase0a-phase1a-failclosed-runtime`, initial clean HEAD
`d3dffc14148d732cfc1c117b3c7ffa1c7262ee17`, matching origin.
CI [42483221849](https://github.com/phytospectra/phyto-companion/actions/runs/42483221849):
1,738 passed / four skipped / 14 warnings; local baseline reproduced (94.12 s).

## Workstreams

| Workstream | Acceptance criterion | Current status / evidence | Remaining action / external prerequisite |
|---|---|---|---|
| Execution boundary | Explicit selection, fail-closed providers, review, provenance, no fake real DOI | Implemented/tested; initial hardening `4c11b82`, CI 42505421461 passed. Full-set assessment corrections under final integration. | Scientific suitability not established by software tests. |
| Workflow scope | Honest real/mock/unsupported matrix and rejection | Verified backend scope in `WORKFLOW_CAPABILITY_MATRIX.md`; all but reference-HSQC unsupported in real orchestrator. | Frontend integration remains distinct; live unsupported UI must fail closed. |
| Reference library | Rights-cleared measured records plus reproducible import | Pipeline implemented; `424fdda` attempted starter **superseded/quarantined**, missing record-level rights and questionable quercetin identity. Original public fixtures untouched. | Original sources, verified identities, acquisition/assignment provenance and intended-use permissions. No usable measured library shipped. |
| Evaluation | Predeclared protocol, hashes, failures, independent queries | Synthetic-only revised protocol: 6/6 contracts, byte-identical rerun, zero measured validation. Exploratory negative isomer-mix result (0/3 flags) retained as SUPERSEDED, not scientific evidence. | Rights-cleared independent measured queries and expert adjudication. |
| Models / tuning | Locked comparison harness and documented gate decision | Harness implemented/tested; `docs/MODEL-COMPARISON-DECISION-2026-11-09.md`. Fine-tuning NOT JUSTIFIED. No inference run. | A reachable approved local model or specifically budget-approved provider; data rights and expert-reviewed held-out cases. |
| Frontend/auth/accounting | Isolated tests; honest availability; no mock live balances | Separate repo base d7d7a491; bounded safeguards committed as 9632bb1 on agent/frontend-auth-accounting-2026-11-09. New tests added but frontend test workers/typecheck timed out; no frontend verified-pass claim. | No staging identity used; legacy frontend/backend run API mismatch; controlled staging approval/access. |
| Study correction | Re-run verifier and frozen hashes | Verified: 14,585 rows; 8,548 annotation changes; 46/46 baseline originals and 8/8 outputs match; six measurement fields preserved. `STUDY-MANUSCRIPT-AUDIT-2026-11-09.md`. | Older manifest has 3 pre-existing mismatches (MANIFEST.md and historical DOCX/PDF); raw vendor/mzML relocated, not independently replayed. |
| Manuscript | Evidence-aligned methods/results/limitations | Markdown corrected; no binary replaced. M6-4 prior identity and absent independent validation made explicit. | Author facts, contributions, declarations, permissions and final approval. Not submission-ready. |
| Citation / RC | Valid metadata, scope, reproduction, checks | Invalid optional ORCID/date placeholders removed; abstract narrowed; release notes updated. Reproduction and approval packets being integrated. | CITATION.cff official schema passed via isolated jsonschema/PyYAML environment; author confirmation still outstanding; final report commit/CI pending. |
| Public-tree safety | No new secrets/restricted public data; precise findings | Audit found no high-specificity credentials; existing owner-derived fixtures, tea structure library and public answer keys need rights/blinding review. | No historical deletion can restore blinding or revoke leaked credentials. Final publication cannot bypass permission gate. |
| Publication / deployment | Explicit final-action authorization | Blocked by design; no tag, DOI, production deploy, IAM/traffic/billing action. | Concrete final approval after gates, not silence or elapsed time. |

## Source commits and CI

- `4c11b82`: assessment signals/cloud real+mock-auth rejection; CI
  https://github.com/phytospectra/phyto-companion/actions/runs/42505421461 passed.
- `424fdda`: initial import infrastructure; CI
  https://github.com/phytospectra/phyto-companion/actions/runs/42506249550 passed.
  Data claims and attempted starter from this commit are superseded by quarantine.
- `02f54cc083a91e4441486c5343179f6c6a1408ee`: integrity/quarantine, synthetic evaluation and model harness. CI https://github.com/phytospectra/phyto-companion/actions/runs/42507585832 passed: 1,766 passed / four skipped / 14 warnings in 92.59 s. Final ownership/report integration remains pending.

## Scope and active work

Agents independently own frontend fixes, sandboxed reproduction, model harness,
and manuscript audit. Model/manuscript/library reviews returned; no frozen
artifacts overwritten. Parent integrating reports and final tests. Final ledger
will be copied into backend docs for source-controlled review; root manuscript
and audit artifacts are outside Git and must not be silently published.

## Integrated completion record

- Backend integrity revision: `02f54cc083a91e4441486c5343179f6c6a1408ee`, CI 42507585832 **success**: 1,766 passed, four skipped, 14 warnings. Local full suite before runner addition: 1,765 passed; runner separately passed. Repository-wide Ruff passed.
- Backend ownership revision: `b5f3a39`, committed/pushed; 74 targeted tests passed including four new cross-user guards. Final CI recorded in final delivery below when settled.
- Frontend revision: `9632bb1`, committed and verified pushed to `github` branch `agent/frontend-auth-accounting-2026-11-09`. Clean working tree. No frontend CI configured. Vitest worker boot failed; bounded retries and typecheck timed out; no build/test pass claimed. Package/lock versions match; earlier suspected mismatch was withdrawn.
- Offline reproduction: dependency consistency, stage-auth, lint, ADR and six docs tests passed. Temporary dependency-reuse sandbox full suite timed out after 600 seconds; API smoke incomplete. This is not a clean-install/full-quickstart pass.
- Citation metadata official-schema validation passed. Observed 90 dependency versions are evidence, not a portable cryptographic lock.
- Client cache/token/error/retry state clears across identities. Server run GET/approval/citation/share creation now bind owner UID; other identities or legacy entries without owner are denied 404. Anonymous runs exist only on explicit local auth-disabled plane. Public tokenized shares remain capability-based access, not private run access.

## Consolidated remaining inputs — no invented answers

Author-only: confirm author order/names, corresponding contact and affiliations/ORCIDs; approve CRediT; supply funding/conflict/ethics/data/specimen provenance declarations; confirm rights for owner acquisitions, original datasets, assignment tables and metadata; select archive/release strategy; approve final submission/release separately. Detailed requested facts are in `STUDY-MANUSCRIPT-AUDIT-2026-11-09.md`.

Technical gates: rights-cleared measured reference and independent-query acquisition; expert adjudication; reachable approved inference provider if comparison is pursued; frontend live API integration and runnable test/build environment; clean dependency installation and complete offline quickstart; controlled authenticated staging/rollback verification; reconcile three pre-existing freeze discrepancies and restore raw files before claiming fresh replay; public artifact and binary/history rights review.

Deliverables are reviewable source and Markdown evidence, not a release, deployment, DOI, fine-tune, validated identification system or submission-ready manuscript. No PR merge or external publication action was performed.
