# Release approval packet — 2026-11-09

## Decision requested, not authorization granted

**Hold public production/scientific release.** Offline software checks permit continued source-alpha preparation only. No deployment, author contact, release/tag publication, DOI registration, external inference, training, download, paid operation, credential deletion/rotation, commit or push was performed by this release-check task.

This packet is advisory. Approval must identify a named accountable operator, exact action, target, source SHA, data scope, budget where relevant, and rollback/abort criteria. There is no blanket approval implied by a passing test or by this document. Final integrated HEAD and final-tree CI evidence must be pinned by the parent release report after concurrent edits settle; the working snapshot below is not that release revision.

See [reproducibility evidence](RELEASE-REPRODUCIBILITY-2026-11-09.md), [execution contract](ORCHESTRATOR_EXECUTION.md), [deployment guide](DEPLOYMENT_GCP.md), and [historical readiness report](RELEASE-AUTH-READINESS-2026-11-08.md). Historical cloud observations are not re-certified here. The source-only execution appendix, not the older broad mock-only description, defines the narrow local reference-ranking capability.

## Required gates

| Gate | Evidence / unresolved requirement | Release effect |
|---|---|---|
| Final source identity | Concurrent working edits; parent must select final reviewed SHA, record clean/intentionally-dirty status, archive/tree hashes and new CI run for that SHA | No release provenance claim yet |
| Software checks | Offline dependency-reuse lint/ADR/docs checks pass; refreshed full suite timed out at 600 seconds and API smoke did not complete. Parent reports CI 42507585832 on 02f54cc passing 1766 tests; later security edits need final coverage | Partial/blocked isolated reproduction; not a clean dependency installation or final-tree CI certification |
| Dependency closure | Makefile/CI use broad unpinned pip installs; container base and cloudbuild image use mutable tags. Observed version snapshot added, without artifact hashes/wheelhouse | Clean, portable, hash-verified reconstruction pending |
| Public data boundary | Tracked evaluator keys/worksheets; case-study acquisitions called private/local-only by benchmark protocol; metadata paths, tea-seed library and rights need owner/curator review | Hold unrestricted archive publication until reviewed disposition |
| Citation/version | Parent owns CITATION.cff and release notes; independently review actual version, citation metadata, licensing and included package metadata | No fabricated affiliation, author identifiers, release date or DOI |
| Scientific scope | Only bounded local measured-reference HSQC ranking is claimed by source contract; provenance assertions and fixture passes are not independent validation | No accuracy/identification or general multimodal readiness claim |
| Auth/browser/accounting | No live calls made here. Valid-user login, refresh/revocation, supported-domain/CORS alignment, least-privilege IAM, ledger/audit and built frontend remain separate staging acceptance | Production no-go |
| Rollback | No current serving revision/digest/configuration re-read; November 8 evidence is historical only | Operator must establish a known-safe target before change |

The ADR checker passes its implemented checks, but duplicate ADR-0077 rows remain in `docs/adr/INDEX.md`; it does not test index-row uniqueness or every stated index invariant. Resolve/disclose through the documentation owner rather than silently treating the passing script as full ADR governance validation.

## Approval-dependent action register

1. **Source/release owner:** approve the reviewed final source SHA and actual alpha version, PR/CI policy, public artifact allowlist and caveat wording. Review CITATION, LICENSE/NOTICE, third-party rights and final diff. Any commit/push/tag, GitHub draft creation, or publication is a separate explicit operation, not performed here. Publishing an alpha still requires the public-data gate.
2. **Data owner/curator:** approve redistribution of each included fixture/library/log/metadata group, or approve a separately reviewed sanitized artifact. Decide whether exposed benchmark keys are intentionally public smoke-test material; they cannot constitute a genuinely secret held-out benchmark. No deletion, history rewrite or credential rotation is authorized here. If credible secret exposure is later found, escalate privately to the owner; do not paste values into an issue.
3. **Dependency/build operator:** approve external artifact acquisition/network/budget separately; produce reviewed wheelhouse or lock with hashes for supported platforms, immutable base/image digests, build provenance/SBOM and clean-room install results. Do not run `make install` under the present offline authorization.
4. **Staging operator:** obtain explicit approval for the exact isolated project/service/domain, approved test identities, workload identity/IAM, storage and secret references, immutable image digest, curated non-sensitive libraries and cost ceiling. Build/deploy and valid-token tests can mutate accounting or incur charges; none are authorized by this packet.
5. **Production operator:** require successful staged acceptance and a separate traffic-change approval specifying abort thresholds and rollback target. No production gcloud, DNS, Firebase, Stripe, OSF, registrar or repository operation was run here.
6. **Archive/DOI owner:** separately approve exact license-cleared artifact, metadata and public/embargo state before any upload/registration/publication. No DOI exists by inference from a mock citation or proposed version. No author contact or external model arm is part of this packet.

## Staging procedure — proposed only, NOT executed

- Preserve current complete configuration securely, including revision-to-image digest, traffic weights, CORS origins, service account/IAM, secret reference versions, mounted files/library hashes, Firebase domain/provider settings, and frontend build-time configuration. Record references, not secret contents. Do not copy historical identifiers as current facts.
- Use an actually isolated staging project/service and deny public access until its security policy is reviewed. A no-traffic revision in production is not automatically isolated from production IAM, databases, billing or mounted data.
- Build the final SHA into an immutable digest with explicit scientific mode and approved read-only library inputs. Do not rely on `latest`, assume container staging exposes all routes, or replace the full environment with a blanket env command. Current Dockerfile has `PHYTO_ENV=cloud` but no scientific-mode selection; missing selection is intentionally fail-closed.
- For a non-public mock smoke test only, select `PHYTO_ENV=local`, `PHYTO_DRY_RUN=1`, `PHYTO_SCIENTIFIC_EXECUTION=mock`; never use mock auth on a public service. For approved real-user staging use auth enabled and auth dry-run off, with explicit scientific real mode and license-cleared local reference paths. This does not authorize paid predictors.
- First verify health names only the supported real workflow/tool and `scientific_ready:false`. Check absent/invalid/expired/revoked/wrong-project tokens reject, then approved existing staging identity and cross-user/workspace isolation; do not log bearer tokens. Verify route pricing before any valid-user check.
- Verify browser login/refresh/sign-out and exact origin CORS, immutable frontend build-time Firebase configuration, least privilege, ledger reservations/refunds/idempotency and audit provenance. Backend-only checks do not certify the separately versioned frontend.
- With separately approved non-sensitive scientific test data, exercise malformed-input rejection, unsupported-workflow 422, bounded measured-reference ranking, hash audit, mandatory human checkpoint and citation-free real completion. Approval records a human decision, not independent scientific confirmation. Check explicit unavailable DOI endpoint rather than expecting a placeholder DOI.
- Record observed results, hashes, revision/digest/config references and named sign-off. Any missing gate blocks production promotion. No inference/model download/training is necessary for the current local reference executor; other arms require separate rights and budget approval.

## Rollback — proposed only, NOT executed

Abort on mock auth acceptance, auth bypass, wrong-project acceptance, data isolation failure, valid-user errors, unexpected external/paid call, accounting anomalies, provenance mismatch or unsupported scientific claims. Stop promotion and retain redacted evidence.

An authorized operator must first verify a current known-safe revision/digest and full configuration; November 8 recorded revisions are not certified recovery targets. Only after explicit recovery approval, use a reviewed traffic-only rollback to that exact revision and independently handle the frontend if needed. Avoid publishing executable production commands with stale revision names.

Do **not** set public `PHYTO_DRY_RUN=1` or disable auth to recover. Traffic rollback does not undo Firestore/ledger writes, IAM, Firebase, DNS, secret changes or archive publication. Preserve stable approved secret versions and HMAC continuity; do not delete ledgers, buckets or users. Historical bucket-deletion and DNS-mutation snippets in DEPLOYMENT_GCP are not authorized release steps. If no safe target exists, use an approved maintenance/restricted-access response, not a mock-public fallback. Re-run security, browser/CORS, accounting and narrow scientific acceptance before reopening access.
