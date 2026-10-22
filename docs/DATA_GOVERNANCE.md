# Data Governance — Technical and Legal-Requirements Document

**ADR-0056 · 2026-10-22 · Status: implemented (engineering), pending (legal)**

This is a TECHNICAL AND LEGAL-REQUIREMENTS document. It is **not** the Terms of
Service and contains no legal language. A lawyer drafts the ToS from Section 5
(the checklist); everything else explains what the platform enforces in code and
why, so the lawyer can see the machine-enforced reality the ToS must describe.

Companion documents: `docs/DATA_SOURCES.md` (per-source licences — the source of
truth for what each corpus may be used for), `docs/DECISIONS.md` ADR-0056 (the
strategic decision), `shared/phyto_shared/data_governance.py` (the data model).

---

## Section 1: Strategy summary

**Public-by-default with a proprietary escalation path.**

- Most contributed data is published openly (CC0, the `public` tier) or held
  internally for model training (`training_only` tier). The open registry is the
  trust-building asset the academic community sees.
- Revenue comes from **software, services, and institutional licenses** — not
  from selling the data corpus in the near term.
- The **proprietary** tier is preserved as a FUTURE OPTION, not the primary
  business. Preserving it costs almost nothing at day one (a few labels per
  contribution) and forfeiting it would be irreversible: once data is published
  CC0 it can never be re-licensed.
- The engineering consequence: every contribution is labelled with its data
  tier, ABS status, and source dataset at upload, and defaults to the most
  restrictive setting (`training_only` / `unknown`). Promotion to a promotable
  tier is always an explicit, curator-gated act. Nothing becomes public or
  licensable by accident.

## Section 2: Tier definitions

Every spectrum contribution, isolation compound, and provenance manifest
carries one of:

| Tier | Meaning | What a user can do with it | What a user cannot do |
|---|---|---|---|
| `public` | Published under CC0. No further license needed. | Use, redistribute, and commercialize the data freely; no attribution required (though attribution is practice). | Nothing is restricted — which is exactly why promotion into this tier is gated (irreversible). |
| `proprietary` | Held in a commercial corpus; accessible only to licensees under a separate agreement. | Access under the licensee agreement's terms only. | Redistribute, resell, or use outside the agreement. Not accessible to non-licensees at all. |
| `training_only` | Used to train/validate platform models. Never published, never licensed. | Contribute it, withdraw it (Section 5), and rely on its not appearing in any corpus. | See the data itself in any published form; it is internal to the platform. |

Escalation path: `training_only` → `public` or `proprietary`, on explicit
curator action only, and only when the ABS status permits (Section 3).
Promotion to `proprietary` additionally requires an executed benefit-sharing
agreement for the underlying collection. Code: `is_tier_compatible_with_abs`,
the isolation validator (Part 5 of ADR-0056), and the provenance validator.

## Section 3: ABS status

Access and benefit-sharing (ABS) obligations arise from the source country's
law (Nagoya Protocol and national implementations). **The obligation attaches
to the physical sample, not the data licence**: publishing the data openly does
not extinguish the duty to the source country. The platform must comply with
the **source country's law**, not the platform's own jurisdiction.

Every isolation record carries:

- `collection_country` (ISO 3166 alpha-2) — determines whose ABS law applies;
- `collection_permit` — the permit or PIC/MAT agreement reference;
- `abs_status` — one of:

| Status | Meaning | Downstream effect |
|---|---|---|
| `clear` | ABS-compliant collection with executed PIC (prior informed consent) and MAT (mutually agreed terms). | Eligible for `public` and `proprietary`. |
| `pending` | Collection underway or the agreement is in negotiation. | May NOT be published or licensed; `training_only` only. |
| `exempt` | Commercially supplied authenticated material; OR a country with no ABS restriction on the species; OR published-literature reproduction. | Eligible for `public`; NOT sufficient for `proprietary` (an exemption covers non-commercial distribution, not a licensed corpus). |
| `unknown` | Collection provenance not established. | Treated as `pending` for every downstream decision. |

### Countries in scope with ABS requirements for plant genetic resources

At minimum, the platform must treat collections from these countries as
ABS-governed (national law implements access and benefit-sharing; several
require a nationally executed agreement before any commercial use):

- **Thailand** — Biodiversity Act framework; national ABS permits required.
- **India** — Biodiversity Act 2002 (National Biodiversity Authority approval
  for access and benefit-sharing).
- **Brazil** — Law 13.123/2015 (SISGen registration required before access).
- **South Africa** — National Environmental Management: Biodiversity Act (permits
  and benefit-sharing agreements).
- **Peru** — Law 27811 (indigenous peoples' collective knowledge regime) and
  access rules for genetic resources.
- **Malaysia** — Access to Biological Resources framework (Sabah and Sarawak
  have their own ordinances).
- **Indonesia** — Law 5/1994 (CBD ratification) and national access regulations.
- **Vietnam** — Biodiversity Law 2008 (access and benefit-sharing provisions).

This list is the minimum scope, not the complete one: the operative question is
always the law of `collection_country` for the species in question, and the
curator must verify current national requirements per collection.

## Section 4: NP-MRD and CC-BY-NC handling

- NP-MRD data is licensed **CC-BY-NC** by its original depositors
  (`docs/DATA_SOURCES.md` §1, ADR-0026).
- It is used **only in the free academic tier** (`public` tier for free
  academic use, with attribution).
- It is **never** in commercial analytics, **never** in the proprietary data
  tier, and **never** in a model that is sold.
- Enforcement: `ProvenanceBuilder.build()` rejects any proprietary-tier
  analysis whose `source_datasets` include `np_mrd` (or any other
  non-commercial source) and raises `ProvenanceError`; the isolation
  validator rejects promotable tiers with `pending`/`unknown` ABS status; the
  source whitelist lives in `phyto_shared.data_governance.NON_COMMERCIAL_SOURCES`
  and the CI completeness test asserts every source string in the code has a
  licence row in `DATA_SOURCES.md`.

## Section 5: ToS requirements checklist for a lawyer

The ToS must contain, at minimum, these clauses. (Requirements, not legal
language — the lawyer drafts the actual text.)

- [ ] **License grant.** The user grants Phytospectra a perpetual, worldwide,
      royalty-free, sublicensable license to use, aggregate, de-identify, and
      route contributed data into any of the three data tiers. The user retains
      ownership of their raw data.
- [ ] **Benefit sharing.** The user receives (a) attribution in aggregated
      corpora and (b) free Pro-tier access for as long as their contribution
      remains in the corpus. State the specific benefit clearly and precisely;
      ambiguous benefit-sharing is unenforceable.
- [ ] **MSI confidence warranty.** Phytospectra warrants the MSI confidence
      level of each annotation (metabolomics standards initiative evidence
      level), NOT absolute correctness. Evidence chains are attached to every
      claim.
- [ ] **ABS warranty from user.** The user warrants that any plant material
      they upload was collected under applicable law and, where required, with
      prior informed consent and mutually agreed terms. The user indemnifies
      Phytospectra for ABS claims arising from their contributions.
- [ ] **Embargo.** Users may contribute data under a publication embargo of at
      most 24 months, during which the data is held in the `training_only`
      tier and not published.
- [ ] **Withdrawal.** Users may request withdrawal of their contribution
      within 30 days. After 30 days, the data remains in the corpus and the
      user attribution is retained.
- [ ] **Data minimization.** No personally identifiable information is
      collected or retained. Institutional affiliation is retained; individual
      researcher names are optional and removable on request.

## Section 6: Engineering enforcement points

Where each policy is enforced in code:

| Policy | Enforcement point |
|---|---|
| Proprietary-tier analyses never use non-commercial sources | `shared/phyto_shared/provenance.py` → `ProvenanceBuilder._enforce_tier_licences` (raises `ProvenanceError`, logs offending source + tier) |
| Public/proprietary tiers require ABS-clear provenance | `services/isolation/app/governance.py` → `validate_compound_tier` (422 with the standard rejection messages) wired into all isolation write endpoints |
| Source-dataset whitelist / licence map | `shared/phyto_shared/data_governance.py` → `NON_COMMERCIAL_SOURCES`, `SOURCE_DATASET_IDS`, `is_source_allowed_in_tier`; CI completeness test (`tests/test_data_governance/test_data_sources_completeness.py`) fails when a source in code lacks a `DATA_SOURCES.md` row |
| Curator-only promotion out of `training_only` | `services/auth/app/roles.py` (server-side role store, `can_promote`) enforced in the isolation service endpoints — the role never comes from the request payload |
| Audit log of all tier changes | `services/isolation/app/governance.py` → `audit_tier_change` writing to the hash-chained `ChainedAuditLogger` (`PHYTO_ISOLATION_AUDIT_LOG`) — accepted and rejected attempts both recorded |
| Nothing public by accident | Defaults everywhere: `DataTier = training_only`, `ABSStatus = unknown` (`data_governance.py`), applied in `Spectrum`, `SpectrumUploadRequest`, `IsolationRecord`, `IsolationCompound`, `ProvenanceManifest` |

## Section 7: Open questions for the lawyer

- Is the standard "sublicensable" grant sufficient for downstream data
  licensing, or does each sublicense require separate consent from the
  contributing user?
- What is the minimum viable benefit-sharing clause that survives a challenge
  under Thai law?
- For jurisdictions where the plant was collected by a third party (e.g., a
  botanical garden), what provenance documentation does the platform require
  before accepting a contribution into a promotable tier?
- Does the 30-day withdrawal window interact with any statutory cooling-off or
  consumer-protection right in the launch jurisdictions?
- Is the ABS warranty + indemnity structure sufficient protection for the
  platform, or is contributor due diligence (verifying permits for declared
  ABS countries) required before `public` promotion?

## Section 8: ADR cross-reference

The strategic decision (three tiers, public-by-default, proprietary escalation,
revenue from software not data) is recorded as **ADR-0056** in
`docs/DECISIONS.md`. This document implements the data model and validators
that decision requires; the ToS drafted from Section 5 completes it.