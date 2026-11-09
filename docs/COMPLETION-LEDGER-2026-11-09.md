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

## Final certification (root record; no source change after tested HEAD)

Backend final clean/pushed HEAD: `148599284adc04786ae56e21cb52c0327c4639a6`.
Final CI https://github.com/phytospectra/phyto-companion/actions/runs/42508943153
**success: 1,770 passed, four skipped, 14 warnings in 146.31 s; Ruff passed**.
CI on fa3629b found one checkpoint test bypassing identity after the owner guard;
fixed by retaining owner authentication while isolating only the test pricing
gate. Focused rerun: 104 passed. This failure is not concealed.
Frontend final clean HEAD `9632bb151316417822040d86019c9053899826b5`, verified
on GitHub feature branch. Frontend tests/build remain unverified; no CI configured.
No release tag, PR merge, deployment, DOI, model inference/training or paid call.

The source-controlled ledger at fa3629b predates this last test correction and
CI settlement; this root record and final response pin the actual final revision.
All requested source work is delivered or explicitly blocked. Scientific-library
completion, measured validation, frontend verification and full offline reproduction
are NOT marked complete. Publication/submission/production decision: NO-GO.

## Continuation requested after final certification

Baseline reverified: backend 148599284adc04786ae56e21cb52c0327c4639a6 and frontend
9632bb151316417822040d86019c9053899826b5 initially clean; CI 42508943153 verified
success directly. No AGENTS.md found in workspace or checked ancestors.

Active independent work: frontend timeout diagnosis/tests/build; isolated offline
reproduction diagnosis; authoritative quercetin/rights investigation and import
metadata; paired A/B scientific protocol/harness; operational middleware integration;
study numerical reconciliation and new review exports. Parent owns top-level claims,
release packet, final integration, commits and CI. No scientific result or clearance
is presumed from the handoff. Final statuses above are historical until this
continuation's final certification is recorded. Active subprocesses are owned by
the relevant workstream and will be bounded and recorded.

### Share-revocation authorization correction

Confirmed DELETE /runs/{run_id}/share omitted ownership even though creation
was protected. Fixed with shared _owned_run before registry or token mutation.
Cross-user/anonymous denial and successful owner revocation covered. Focused:
85 passed; Ruff passed. Committed/pushed f9d4ed4c275c2a382d4ea39db730efe6a07490b9.
CI https://github.com/phytospectra/phyto-companion/actions/runs/42512862375
success: 1,771 passed, four skipped, 14 warnings in 126.68 seconds.
README and paired-protocol partial drafts remain uncommitted; reference agent
is independently tracing/fixing persistent quercetin misidentity mappings.
Several continuation agents stopped with provider HTTP402 credits errors;
no purchases/retries implying new cost authorization were made. Their tasks
are incomplete, not marked complete. Reference investigation continued and
identified incorrect structure in structure_db/shift_predictor/tool examples;
corrections require tests and no unsupported assignment transfer.

### Identity cleanup and frontend verification delivered

Backend b3bee2e150b1172e722361c2ac800c0123e8d2f7 corrected quercetin and kaempferol
structure-only mock identities; removed rather than reassigned their unverifiable
shift tables. Builder v3 requires acquisition/conditions/measurement/assignment/
rights-scope/original-file-hash/transformation assertions; assertions remain curator
claims, not independently verified clearance. No eligible measured reference/query
pair established. CI 42513601847 success: 1,783 passed, four skipped, 14 warnings.

Frontend source 9632bb1 reverified in fresh tracked-source temporary checkout,
public network npm ci --ignore-scripts, then typecheck exit0; Vitest seven files,
28 tests in2.70s; production build exit0 (13 static pages). Installed dependency
files in Documents had dataless flags and idle reader; fresh dependency checkout
resolved stalls. Verification document committed/pushed e8bdbe6. No code upgrade,
assertion weakening or timeout increase. No live Firebase/billing/staging claim.

Backend docs revision 1583c6d clarifies README preview boundaries and preserves
paired protocol explicitly as INCOMPLETE REVIEW DRAFT, not locked or executed.
Full backend isolated reproduction, paired harness implementation, independent
measured evaluation, staging integration, manuscript export and author approval
remain outstanding. No paid inference/training/deployment/publication occurred.

## Final continuation certification — 2026-11-09 (second pass)

Implemented and verified this pass:
- Exactly-once idempotency claims in the auth middleware (shared ledger claim
  before budget selection; duplicates 409 without handler rerun; cross-UID/
  operation scoping; covered by tests/test_operational_integration.py).
- Operational offline acceptance with real middleware + mock verifier (tokens,
  CORS, reservations, ownership boundaries, capability shares).
- Paired A/B evaluation harness (A production executor vs B nearest-reference
  baseline), private evaluator keys outside the repo, scorer with compound
  clusters and failure accounting, archive revision provenance (.source-revision
  or PHYTO_SOURCE_REVISION). Protocol remains an INCOMPLETE REVIEW DRAFT, not
  locked; only synthetic software contracts run (no measured cases cleared).
- scripts/verify_offline.py: offline real-workflow quickstart verifier.
- Clean-install isolated reproduction at the pinned revision outside the
  cloud-managed tree: lint exit 0; full suite 1,843 passed, four skipped,
  14 warnings in 104.03 s; quickstart and 6/6 synthetic contracts exit 0.
- Manuscript: independent-evaluation status paragraph added; review DOCX export
  generated and read back (166 paragraphs, NOT AUTHOR APPROVED banner);
  author-declaration placeholders remain explicitly pending.
- Staging acceptance procedure with exact live prerequisites committed
  (docs/STAGING-ACCEPTANCE-2026-11-09.md); no live staging attempted.

Backend revisions this pass: f9d4ed4 (share-revocation ownership),
b3bee2e (identity/provenance), 1583c6d (scope docs), a64d9c0 (idempotency +
paired harness + quickstart), 7e36f13 (archive sidecar), plus documentation
commits. Final CI links and test totals for the last pushed revision are the
authoritative record; local suites match CI totals at each pinned revision.

Remaining external inputs (exact): (1) author-confirmed names/order/
affiliations/ORCIDs, contributions, funding/conflicts/ethics, data publication
permissions and final approval; (2) a rights-cleared measured reference and
independently acquired query set with source evidence, or confirmation of a
dataset-specific license; (3) an isolated non-production Firebase project,
two disposable test identities and approved staging URL/origins for live
acceptance; (4) explicit authorization before any merge to main, release/DOI,
deployment, paid inference or training. None occurred.

Readiness split remains: source-code research preview GO-with-caveats;
scientifically evaluated workflow NO-GO; production service NO-GO;
manuscript submission NO-GO.

## Third-pass closure — 2026-11-09

- **Accounting guarantee audited.** At-most-once handler admission per
  (user, key) with exactly-once accounting settlement; content-blind keys,
  no expiry, process-local mock window, crash recovery = new key plus
  operator reconciliation. Fault-injection tests added
  (tests/test_accounting_guarantees.py, 7 tests). Precise statement and
  failure/recovery matrix: docs/ACCOUNTING-GUARANTEE.md; wording narrowed in
  staging/frontend docs. "Exactly-once" claims are now scoped correctly.
- **Manuscript visual QA complete.** New export builder renders Markdown
  correctly (bold/italic/lists/tables/references), chunks large tables with
  repeated headers and embedded captions, keeps the NOT AUTHOR APPROVED
  banner. DOCX + PDF in phytospectra-paper/review-export-2026-11-09/;
  24/24 rendered pages passed the visual acceptance judge after iterative
  fixes (QA-RECORD.md). [TBD-USER] placeholders remain by design.
- **Evaluation protocol review packet.** DECISION-SHEET.md enumerates the
  seven scientific decisions requiring adjudication and the lock checklist;
  protocol remains NOT locked/NOT preregistered. Harness/scorer execute
  synthetic contracts only (67 focused tests green).
- **Measured-data clearance record.** DATA-CLEARANCE-2026-11-09.md lists
  the empty cleared manifest, the unresolved records with exact reasons,
  and one consolidated request for a single integration pair plus the
  evaluation-set question. Quarantine unchanged.
- **Staging packet.** Read-only gcloud shows only production project
  (no staging exists); docs/STAGING-ACCEPTANCE-2026-11-09.md now contains
  the exact approval packet for an isolated staging project with pinned
  revisions, cost boundary, cleanup and the full acceptance checklist.
- **Author form.** phytospectra-paper/AUTHOR-INPUT-FORM-2026-11-09.md
  consolidates every unresolved author item and the version-hash approval
  requirement.

Final revisions and CI are updated after the last commit; no deployment,
publication, paid inference/training or third-party contact occurred.
Readiness split unchanged: preview GO-with-caveats; scientific evaluation,
production service, and manuscript submission NO-GO pending the exact
inputs listed in the three request documents.

## Enterprise increment — 2026-11-09 (durability)

Implemented and verified:
- Durable SQLite run store (WAL, BEGIN IMMEDIATE, versioned migrations,
  backup API): runs, checkpoints, approvals, response metadata; ownership
  enforced on every path incl. restart hydration; cloud fails closed
  without durable storage. Content-bound idempotency: same content replays
  the stored response; different content 409; keys never cleared; crash
  between admission and persistence fails closed to operator
  reconciliation (scripts/reconcile_runs.py marks uncertain, never
  re-executes). Backend d68f287 (CI 42624312078 success), final 3b85b1b
  (CI 42634113290 success: 1,891 passed, four skipped, 14 warnings; Ruff
  clean). Local full suite on the same tree: 1,888 passed pre-correlation
  tests; 11 store + 11 API durability tests incl. restart recovery,
  concurrent transitions/admissions, conflict rejection, backup/restore
  round-trip, 12-writer concurrency smoke, X-Request-ID echo/mint,
  readiness run_storage reporting. docs/DURABLE-RUN-STORE.md documents
  guarantees, non-goals (no exactly-once external execution; no
  multi-replica), operator backup/restore/rollback/reconciliation.
- Frontend 4ef2716 (CI 42624330119 success): durable owner-scoped run
  history (GET /runs) with explicit outcome states incl. "needs operator
  reconciliation"; storage-truthful persistence copy; 64 unit tests,
  TypeScript and production build verified in a hydrated checkout outside
  Documents. No browser-based visual check was performed this increment
  (no browser tooling invoked); UI verification is component-test level.
- Operations: correlation IDs on every response (validated echo or minted
  uuid); tracked-file scan found no high-specificity credential patterns;
  existing dependency snapshot retained.
- Agent-comparison runner audited against the brief: distinct arms;
  Antigravity explicitly not_run_not_available; key preflight enforces a
  dedicated non-resetting cap <= US$50 INCLUDING BYOK and fails closed.

Implemented but not verified here: live staging acceptance (no staging
project exists; production project untouched), browser-based UI
inspection, load/soak beyond the 12-writer smoke.

Blocked on external inputs: OPENROUTER_API_KEY absent from this
environment, so the live $50 ceiling recheck cannot run (runner fails
closed without it); Colab compute units remain 0.00 (no allocation);
rights-cleared measured data, scientific review/lock, and author
declarations unchanged from prior packets.

Spending this increment: US$0 inference, US$0 compute, no training, no
deployment, no release/DOI/publication, no third-party contact.

Readiness: software research preview GO with caveats (now with durable
single-instance storage); scientifically evaluated workflow, production
service, and manuscript submission remain NO-GO for the reasons in the
prior packets.
