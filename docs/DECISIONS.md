# Design Decision Log (ADR)

This file records every significant design decision for PhytoCompanion. Entries are
append-only: never edit or delete a decided ADR — supersede it with a new one that
references the old ID.

---

## ADR Template (copy for each new entry)

```markdown
### ADR-NNNN: <short, imperative title>
- **Date:** YYYY-MM-DD
- **Status:** proposed | accepted | superseded by ADR-MMMM
- **Deciders:** <who made the call>
- **Context:** <the forces at play; what problem demanded a decision>
- **Decision:** <what we chose, stated precisely>
- **Alternatives considered:** <what else was on the table and why it lost>
- **Consequences:** <what this buys us and what it costs us>
```

---

## ADR Numbering Note (recorded 2026-10-21)

**Status:** informational; not a numbered decision.

### The gap

ADR-0019 through ADR-0023 are not present in this log. Those IDs were reserved
during early planning for candidate decisions (session-level token accounting,
sandbox timeouts, cross-modal weighting, mixture handling, IR module scope).
They were deliberately skipped rather than deleted. The log is append-only and
skipped IDs are **not reused**.

If a future decision genuinely warrants one of these numbers, create the entry
with an explicit "Deferred from original planning" context line so the gap is
not silently filled.

### Verified sequence

Checked programmatically against this file on 2026-10-21:

- Entries present: **0001-0018** and **0024-0040** (35 entries).
- Missing within the range: **0019, 0020, 0021, 0022, 0023** -- these five only.
- No duplicate IDs; entries are in ascending order.
- No references to ADR-0019..0023 anywhere else in the repository, so the gap
  creates no dangling links.

### Correction to the original planning note

The reserved-topic list above is recorded as-is, but only **one** of the five
topics can actually be traced to a later ADR. The other four were either never
formalised or are covered without a dedicated decision entry. Stating the
traceability accurately matters more than preserving a tidy story:

| Reserved topic | Traceable to | Notes |
|---|---|---|
| Session-level token accounting | **ADR-0028**, **ADR-0033** | ADR-0028 moved token metering ahead of the frontend; ADR-0033 set the free-tier budget and route prices. |
| Sandbox timeouts | *none* | No ADR in the log addresses sandbox execution limits. |
| Cross-modal weighting | *none as a decision* | ADR-0010, ADR-0013, ADR-0014 and ADR-0018 touch cross-modal scoring, but none records an explicit weighting decision. |
| Mixture handling | *none* | No ADR mentions mixtures or the Part C benchmark, despite Part C being reported (65% calibrated vs 95% pure). This is an open documentation gap. |
| IR module scope | *none* | No ADR addresses infrared spectroscopy or JCAMP-DX scope, although the README lists IR parsing as a service responsibility. |

Two of these (mixture handling, IR module scope) describe real platform
behaviour with no recorded rationale. They should be picked up as new ADRs
under fresh IDs if and when the decisions are formalised -- not backfilled into
0019-0023, and not treated as already settled by this note.

---

### ADR-0001: Project location inside the existing research directory

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 1 review)
- **Context:** The monorepo needed a home on the development machine (MacBook M2). The
  working directory already contains the group's NMR/MS research materials (SUMMIT
  notebooks, reference PDFs, prior analysis outputs).
- **Decision:** The codebase lives at `phyto-companion/` as a subdirectory of
  `/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/`, clearly separated from the
  existing research files. It can be relocated or extracted to its own repository later
  without code changes (all paths inside the monorepo are relative).
- **Alternatives considered:** A sibling folder under `~/Documents` or a dedicated
  `~/dev` directory — rejected to keep code and the science it serves in one place
  during development.
- **Consequences:** Research artifacts and code share a parent folder (slight clutter);
  in exchange, fixtures and literature stay within easy reach. Git history starts
  cleanly at `phyto-companion/` when the folder is promoted to its own repository.

### ADR-0002: License not yet chosen

- **Date:** 2026-10-21
- **Status:** accepted (decision deferred)
- **Deciders:** Project owner; to be settled with the research group
- **Context:** The platform targets an academic freemium model: an open-source-friendly
  core with hosted advanced tiers. License choice affects dependency compatibility
  (e.g., copyleft vs. permissive), data/DB licensing (COCONUT, NMRShiftDB2, NP-MRD have
  their own terms), and commercial hosting options.
- **Decision:** No license is asserted yet; README states "to be decided." No license
  file exists in the repo until the choice is made. This ADR must be superseded before
  any public release.
- **Alternatives considered:** MIT/Apache-2.0 (permissive, hosting-friendly) vs.
  AGPL-3.0 (strong reciprocal, protects the open core) vs. dual licensing. All remain
  open until the group decides.
- **Consequences:** The repository is "all rights reserved" by default in the meantime;
  nobody outside the group may legally reuse the code yet, which is acceptable
  pre-publication.

### ADR-0003: Solvent reference table includes all commonly observed carbons, beyond the minimum specified

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 2; flagged for owner review
- **Context:** The Step 2 specification gave minimum ¹H/¹³C reference values per
  solvent. Standard reference tables (e.g., Cambridge Isotopes, Fulmer 2010) also list
  additional carbons for two of the specified solvents: CD₃CN has a nitrile carbon at
  118.69 ppm and acetone-d₆ has a carbonyl carbon at 206.26 ppm. The ingestion module
  will flag solvent peaks, impurities, and *missing quaternary carbons* — screening
  quaternary carbons requires knowing where those solvent carbons appear.
- **Decision:** Keep every user-specified value exactly as given, and additionally
  include the CD₃CN nitrile carbon (118.69) and acetone-d₆ carbonyl carbon (206.26) in
  `SOLVENT_REFERENCES`, each documented in the entry's `notes`. All other entries match
  the specification verbatim.
- **Alternatives considered:** Storing only the specified values and putting the extra
  carbons in `notes` text — rejected because machine-readable positions are needed for
  automated flagging.
- **Consequences:** Downstream flagging code can mask every common solvent carbon
  automatically. If the owner prefers the strictly minimal table, deleting the two
  extra tuple entries is a one-line change each.

### ADR-0004: Step 2 API interpretations where the spec allowed latitude

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 2; flagged for owner review
- **Context:** Four points in the Step 2 specification required interpretation.
- **Decision:**
  1. `AuditLog.to_dataframe()` returns `list[dict]` (one dict per record) rather than a
     pandas DataFrame — pandas stays out of the shared library so the local plane stays
     lightweight; cloud services wrap the rows with `pandas.DataFrame(rows)`.
  2. `uncertainty.score_margin` returns `math.inf` when no distinct competitor score
     exists (single-element list) and `0.0` when every competitor ties the target
     (zero separation — must not read as confidence); otherwise it is
     `target − best distinct competitor`.
  3. `Peak2D.c_ppm` carries the second ¹H axis for homonuclear experiments
     (COSY/TOCSY/NOESY), since the field layout was specified identically for all five
     2D experiment types; documented in the model docstring.
  4. `verdict_from_evidence` takes one extra optional keyword, `novelty_flagged=False`,
     so the `NOVELTY_FLAGGED` verdict is reachable from the function; without it the
     verdict could never be produced by the single entry point the spec defines.
- **Alternatives considered:** Asking before implementing each — rejected as
  over-blocking for reversible, documented interpretations; all four are surfaced here
  for review and are one-line changes if disapproved.
- **Consequences:** The spec's function signatures are honored with minimal, documented
  extensions; reviewers should confirm or amend each of the four points.

### ADR-0005: `ranking_interval` ranks higher scores as better (spec formula corrected)

- **Date:** 2026-10-21
- **Status:** superseded by ADR-0007 (the rank arithmetic survives in `rank_higher_is_better`)
- **Deciders:** Surfaced by the Step 2 test suite; flagged for owner review
- **Context:** The Step 2 specification defined ``best rank = 1 + count of strictly
  smaller scores``.  Taken literally with the platform's scoring conventions
  (candidate scores, spectral similarities, coverage — all "higher is better"),
  that formula assigns the top scorer rank N.  The spec also requires
  ``is_unique_top1`` to be true only for a sole rank-1 candidate, which the
  literal formula cannot ever produce for a maximum score in a multi-candidate
  list.
- **Decision:** Implement ``best = 1 + #(scores strictly greater beyond atol)``,
  keeping the worst-rank tie formula unchanged.  The unit tests encode this
  semantics (they caught the inversion when the literal formula was first
  implemented).
- **Alternatives considered:** Treating lower scores as better (distance-like
  scoring) — rejected; every downstream score in the platform (SUMMIT match
  scores, cosine similarities, coverage fractions) is higher-is-better.  If a
  distance-based ranker is ever added, it should negate its scores at the
  boundary rather than change this convention.
- **Consequences:** Consistent rank semantics across the platform; deviation from
  the spec's literal wording is documented here and in the function docstring.

### ADR-0006: Novelty is a separate axis from confidence

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (mandatory refinement before Step 3)
- **Context:** `NOVELTY_FLAGGED` as a `ConfidenceVerdict` value conflated two
  orthogonal concepts.  A compound can be CONFIRMED and novel (a known structure
  found in a new natural source), CANDIDATE and novel (a plausible structure with
  no literature match), or CONFIRMED and non-novel — none of which the single-axis
  verdict could express.  Downstream modules (Steps 4–6) depend on the separation.
- **Decision:** `ConfidenceVerdict` shrinks to the five pure-confidence values
  (CONFIRMED, TENTATIVE, AMBIGUOUS, CANDIDATE, ABSTAIN — the owner instruction
  said "six" but enumerated exactly these five; five is implemented).  A new
  `NoveltyStatus = Literal["novel", "known", "uncertain"]` axis lives in
  `constants.py` with `NOVELTY_STATUS_DEFINITIONS` (the "platform never claims
  novelty" language moved there).  `verdict_from_evidence` now returns a frozen
  `EvidenceAssessment(verdict, novelty)` dataclass: the confidence rules ignore
  `novelty` entirely, and `novelty` is passed through unchanged — determining it
  (library-match analysis) is downstream's job, with `"uncertain"` as the honest
  default.  The four combinations (CONFIRMED+known, CONFIRMED+novel,
  CANDIDATE+novel, ABSTAIN+known) are covered by tests.
- **Alternatives considered:** Keeping `NOVELTY_FLAGGED` alongside the other five
  — rejected as structurally dishonest (it is not a confidence level); a boolean
  `may_be_novel` flag — rejected because "not assessed" must be distinguishable
  from "assessed as not novel".
- **Consequences:** Any structure claiming an identification must now carry both
  fields; the elucidation service (Step 5) computes novelty from library-match
  evidence and feeds it in.  Removal of `NOVELTY_FLAGGED` is a breaking change
  contained within this revision (no downstream callers existed yet).

### ADR-0007: Explicit dual ranking/margin APIs; no single-convention function

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (mandatory refinement before Step 3)
- **Context:** ADR-0005 fixed a single "higher is better" convention, but the
  Step 5 SUMMIT matching uses distance scores where LOWER is better (HSQC
  Modified Hungarian distances, ppm residuals).  One ambiguous entry point
  invites silent misuse by downstream callers with the opposite convention.
- **Decision:** The single-convention functions (`ranking_interval`,
  `is_unique_top1`, `score_margin`) are replaced by explicit pairs:
  `rank_higher_is_better` / `rank_lower_is_better`,
  `is_unique_top1_higher_is_better` / `is_unique_top1_lower_is_better`, and
  `margin_higher_is_better` / `margin_lower_is_better`.  Both members of each
  pair share one audited private core; both are tested, including the mandated
  disambiguation case ([0.9, 0.5, 0.3], index 0 → rank 1 higher-is-better,
  rank 3 lower-is-better) and a duality property (negating all scores must
  swap the two variants' results exactly).  Downstream callers must name their
  convention at every call site.
- **Alternatives considered:** A `higher_is_better: bool` parameter on one
  function — rejected: default arguments get copied call-to-call and the
  convention becomes invisible again; negating distance scores at the boundary
  (ADR-0005's original guidance) — rejected per owner instruction and because it
  pushes bookkeeping onto every caller.
- **Consequences:** Slightly larger API surface; in exchange, score semantics
  are explicit and greppable at every call site.  `CandidateRank.ranking_interval`
  / `.margin` (data fields on the model) remain convention-agnostic; the Step 5
  ranker must use the lower-is-better variants to fill them and state so in its
  provenance parameters.

### ADR-0008: `CandidateRank` carries an explicit `ranking_convention`

- **Date:** 2026-10-21
- **Status:** accepted (owner pre-approved)
- **Deciders:** Project owner
- **Context:** ADR-0007 made ranking conventions explicit at the function level,
  but persisted `CandidateRank` records were still convention-agnostic: a consumer
  reading `score`, `margin`, or `ranking_interval` could not know which comparison
  produced them.  Step 5 needs both conventions (SUMMIT combined score is
  lower-is-better; similarity scores are higher-is-better).
- **Decision:** `CandidateRank` gains a REQUIRED (no default)
  `ranking_convention: Literal["higher_is_better", "lower_is_better"]` field;
  constructing a rank without it raises `ValidationError` (regression-tested).
  The `uncertainty.py` function pairs carry the convention in their names and
  docstrings (the owner's documented-docstring alternative), keeping one source
  of truth for semantics.  The Step 5 ranker must set the field on every rank it
  persists.
- **Alternatives considered:** A default value — rejected: a silent default
  reintroduces exactly the ambiguity ADR-0007 removed.  A matching
  `ranking_convention` parameter on the margin functions — rejected: the function
  names already state their convention unambiguously.
- **Consequences:** Breaking change contained to this revision (all test
  constructions updated).  Downstream services (Steps 4–6) must supply the field
  whenever they build a `CandidateRank`.

### ADR-0009: Checkpoint approval doubles as Tier-3 (LLM) authorization

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 3; flagged for owner review
- **Context:** `enforce_tiering` requires Tier-3 tools to have
  `human_in_the_loop_approved=True`, while the four workflow checkpoints are tracked
  separately in `approved_checkpoints`.  Two independent approval ledgers for what
  is morally the same human decision ("a human is watching this run") would invite
  states where a run pauses at a checkpoint yet later silently loses LLM
  authorization (or keeps it after being rejected).
- **Decision:** `ExecutionContext.with_approval(checkpoint, actor)` grants BOTH the
  specific checkpoint and `human_in_the_loop_approved=True` in one immutable
  context transition (the original context is untouched; the audit trail records
  the approval event with the actor).  Tiering enforcement itself still checks the
  two flags independently — a test constructs a context with a checkpoint approved
  directly (no `with_approval`) and shows the Tier-3 call still fails.  The
  orchestrator API's `/runs/{id}/approve` is the only path that mints approvals.
- **Alternatives considered:** Fully independent ledgers (checkpoint approvals +
  a separate LLM-authorization endpoint) — rejected as double bookkeeping for one
  human act; making `human_in_the_loop_approved` a session-wide flag — rejected
  because contexts are frozen value objects and per-run transitions must be
  auditable.
- **Consequences:** One approval act = one coherent policy state; the engine can
  never be left holding a Tier-3 grant the human never gave.  If a future workflow
  needs LLM use *without* any of the four checkpoints, a dedicated
  `authorize_llm_use` context transition will be added (and ADR'd).

### ADR-0010: Step 4 (networking service) design decisions

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 4; flagged for owner review
- **Context:** The Step 4 specification left several implementation choices open.
- **Decision:**
  1. **Synthetic GNPS fixtures.** The spec preferred real GNPS2 outputs but allows
     synthetic schema-matched fixtures when submission is infeasible.  The local
     plane has no GNPS credentials and must not contact external APIs, so all six
     fixtures under `tests/fixtures/gnps/` are synthetic, labeled as such in file
     headers and a fixture README.  Real exports can replace them later without
     touching the tests.
  2. **Scientific stack dependency additions.** numpy, scipy, pandas, networkx,
     and matplotlib join the networking service (scipy for
     `linear_sum_assignment` and the Pearson t-distribution p-value; networkx per
     the spec; matplotlib for report PNGs, imported lazily).  All are local-plane
     safe (no network, modest memory).
  3. **Fused model extension.** The Step 2 `FusedNetwork` is subclassed with
     `nodes: dict[str, FusedNode]` and `FusedEdge` edges; the parent topology
     validator and stats method are overridden by same name (verified by tests).
     `FusedNode.annotation_source` records the modality an annotation came from
     ("MS2"/"HSQC"/"fused"), which `cross_modal_annotation` requires: propagation
     crosses ONLY edges whose modality differs from the annotation's source.
  4. **Cross-modal confidence penalty** is a named constant
     (`CROSS_MODAL_PENALTY = 0.7`); propagated annotations cannot chain
     (a `cross_modal` donor never donates further).
  5. **Annotation propagation (Stienstra) votes layer-by-layer**: a node's
     annotation is decided by majority vote among ALL already-annotated
     neighbors at the time its BFS layer is processed, not by whichever seed
     the BFS happens to reach first; confidence decays 0.8 per hop via the
     donor chain (`ANNOTATION_CONFIDENCE_DECAY`).
  6. **GNPS client endpoint paths** (`/molecularnetworking/submit` etc.) model
     the GNPS2 API but are marked provisional until verified against the live
     API in Step 8 deployment; the retry/rate-limit/auth plumbing is what the
     tests pin down.
  7. **MockGNPSClient heuristic**: spectra whose precursor masses differ by a
     common amino-acid residue mass (±0.1 Da) are connected; annotations at
     mixed MSI levels land on every third node so annotated, unannotated, and
     mixed-confidence code paths are all exercised deterministically.
- **Alternatives considered:** real GNPS fixture submission (blocked by
  split-plane rules and absent credentials); implementing Hungarian in pure
  Python (rejected: scipy is the reference implementation the paper's metric
  assumes); propagating cross-modal annotations through "both"-modality edges
  (rejected: ambiguous provenance).
- **Consequences:** The fused-network tests demonstrate the platform's core
  claim on synthetic data: fusion + cross-modal propagation annotates more
  nodes than either modality alone.  Fixture realism is the main caveat --
  parser behavior against genuine GNPS exports should be spot-checked when
  credentials exist.

### ADR-0011: Synthetic GNPS fixtures are a known risk; Modified Hungarian decay/derivation documented

- **Date:** 2026-10-21
- **Status:** accepted (owner-mandated clarification of Step 4)
- **Deciders:** Project owner
- **Context:** Two Step 4 items needed explicit risk recording and verification:
  1. All GNPS test fixtures are synthetic (ADR-0010.1); parser behavior against
     genuine exports is unverified.
  2. The Modified Hungarian implementation cites Stienstra et al. eq. 1-2 but no
     hand-computed verification existed, and the 0.8/hop annotation confidence
     decay constant was unjustified.
- **Decision:**
  1. **Fixture risk register.** Synthetic fixtures are a standing risk (R-001):
     the parsers could accept our own schema blind spots.  Mitigations: (a)
     fixtures model the documented GNPS2 attribute/column names exactly;
     (b) a follow-up task before production use: submit one tiny dataset to the
     public GNPS2 API from the cloud plane, download real exports, add them as
     `*_real.*` fixtures, and run the same parser tests against both; (c) any
     parser divergence between synthetic and real fixtures is a release blocker.
  2. **Equation verification.** `test_nmr_networking.py` gains
     `test_modified_hungarian_matches_hand_computation`, a fully hand-derived
     example (cost matrix written out, Hungarian assignment, normalization by
     max(N,M)) that the implementation must reproduce exactly -- the equation
     citation is now backed by arithmetic, not trust.
  3. **Decay justification.** `ANNOTATION_CONFIDENCE_DECAY = 0.8` is an
     auditable instantiation of Stienstra et al.'s qualitative statement that
     propagated annotations carry reduced confidence: it yields 64% of seed
     confidence at 2 hops and 51% at 3 hops -- steep enough that long chains
     cannot masquerade as near-seed evidence, shallow enough that immediate
     structural relatives (the method's main use) stay usable.  It is a
     documented default, not a fitted constant; recalibration with real
     annotated networks is a planned validation task.
- **Alternatives considered:** 0.9/hop (too permissive: 0.73 at 3 hops), 0.5/hop
  (too steep: 2-hop relatives drop below 0.3).
- **Consequences:** The risk is now on the record with a concrete mitigation
  path; the distance metric is pinned by hand arithmetic.

### ADR-0012: Cross-modal annotations never chain

- **Date:** 2026-10-21
- **Status:** accepted (owner-mandated clarification of Step 4)
- **Deciders:** Project owner
- **Context:** `cross_modal_annotation` donates annotations across the modality
  boundary.  Nothing in the algorithm inherently prevented a propagated
  annotation from donating again (chain: MS2 -> HSQC -> another node), each hop
  multiplying a 0.7 penalty while compounding provenance ambiguity.
- **Decision:** A node with `annotation_source == "cross_modal"` is never a
  donor.  Every annotation in the fused network traces to exactly one original
  modality (or a fusion of both); propagated annotations are terminal.  This is
  enforced in code and pinned by `test_no_chained_propagation`.
- **Alternatives considered:** Allowing chains with a hard hop cap -- rejected:
  provenance of a 2-hop annotation is already too murky to audit, and the
  platform's honesty rules prefer under-annotation over un-auditable
  over-annotation.
- **Consequences:** Coverage grows by at most one cross-modal hop per
  originally-annotated node; the evidence trail for every propagated annotation
  is a single, explicitly logged edge.

### ADR-0013: Step 5 (elucidation service) design decisions

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 5; flagged for owner review
- **Context:** The Step 5 specification left several implementation choices open,
  and surfaced one Step 2 model gap.
- **Decision:**
  1. **`CandidateRank.evidence_graph` field added to Step 2.**  Spec 5.3 requires
     attaching an EvidenceGraph to each rank, but the Step 2 model had no such
     field (`extra="forbid"` rejected it).  Added as an optional field; the
     structured per-claim graph is built by the elucidation service's
     `build_evidence_graph`.
  2. **Mock ShiftPredictor reference table: andrographolide substituted with
     caffeine.**  No verified andrographolide SMILES was available offline
     (candidate SMILES computed C17-C18 formulas, not the true C20H30O5);
     shipping an invented structure in a reference table would be worse than
     substituting.  Caffeine (C8H10N4O2, textbook shifts) takes its place; the
     entry list is benzene, ethanol, quercetin, kaempferol, caffeine + phenol and
     ethylene glycol (used by localization tests).  Predictions run on the
     CANONICAL SMILES form so table order and atom symbols always align (a real
     bug caught by the plausibility test: input-order symbols zipped with
     canonical-order shifts).
  3. **Match cost scale.**  1D costs are raw ppm deviations within tolerance,
     else `cap`; 2D (HSQC/multinuclear) costs are the tolerance-normalized
     deviation (0..1 within window) times `cap`, else `cap`.  Identical lists
     score 0, disjoint equal-length lists score exactly `cap`, and every
     MatchResult requires `ranking_convention="lower_is_better"`.
  4. **MS2 scoring at candidate level** is precursor-m/z agreement (adduct-aware
     via Step 2 `Adduct`, 5 ppm tolerance): score 0 or `cap`.  Fragment-level
     MS2 matching arrives with the real SIRIUS/GNPS integration.
  5. **Formula mismatch pins the distance at `cap`** (worst rank, zero coverage)
     with a `formula_mismatch` evidence item, rather than excluding the
     candidate: the rank list stays complete and auditable.
  6. **Verdicts**: `verdict_from_evidence` with MSI Level 2, Tier 2 (ShiftMPNN is
     ML), novelty="known" for database candidates; tied candidates surface as
     AMBIGUOUS via the n_tied rule (pinned by test).
  7. **Site-localization confidence** = 1 / (number of sites within the rank-gap
     threshold); symmetric parents honestly report many competing sites and low
     confidence, and `n_possible_sites == 1` is flagged as trivially
     uninformative.
  8. **Edit coverage**: hydroxylation, methylation (C- and O-), glycosylation
     (hexose + deoxyhexose), prenylation, acetylation, deoxygenation,
     demethylation, and c_reduction are implemented AND tested (formula deltas
     verified against RDKit); `oh_shift` is implemented (donor->lowest-index
     acceptor) but NOT covered by tests -- rare and expensive by design, lowest
     priority for test effort.
  9. **Unreachable target formula raises `InvalidInputError`** (spec) --
     distinguishable from "reachable but every product failed filters".
- **Alternatives considered:** accuracy-weighted MS2 fragment scoring
  (deferred); excluding formula-mismatched candidates (rejected: hides
  evidence); implementing andrographolide from memory (rejected: wrong
  structure in a reference table is worse than a documented substitution).
- **Consequences:** The elucidation pipeline is fully testable offline with
  RDKit-only chemistry; the mock's honesty caveat (lookup values approximate,
  rules crude) is documented in the module docstring and carried in every
  ShiftPrediction's `model_id`.

### ADR-0014: MS2 scoring is reference-spectrum cosine; precursor m/z is a filter

- **Date:** 2026-10-21
- **Status:** accepted (owner-mandated Step 5 fix)
- **Deciders:** Project owner
- **Context:** The Step 5 ranker originally scored MS2 evidence as precursor
  m/z agreement (0 or `cap`). That is not MS2 evidence at all: it duplicates
  the molecular-formula gate and makes the `weight_ms2` term meaningless.
- **Decision:**
  1. `ms2_cosine(reference, observed, fragment_tolerance_da=0.02)` computes the
     GNPS-style spectral cosine on sqrt-transformed intensities of greedily
     m/z-matched pairs (greedy within tolerance, ascending distance).
  2. `rank_candidates(..., reference_ms2: dict[candidate_id, peaks])` scores the
     MS2 modality as the distance `1 - cosine` (`RankingConfig.ms2_distance =
     "one_minus_cosine"`, configurable).
  3. **No reference spectrum => MS2 scoring is DISABLED** for that candidate,
     and an `ms2_scoring_disabled` evidence item records the gap. Absent
     evidence never becomes a free pass.
  4. **Precursor m/z is a FILTER**: candidates whose adduct-adjusted ion mass
     falls outside `precursor_tolerance_ppm` are excluded from the returned
     ranks entirely (they are not ranked last).
- **Alternatives considered:** scoring precursor agreement as a weak term
  (rejected: double-counts the formula gate and flatters every candidate whose
  mass happens to agree); in-silico fragment prediction (deferred to the real
  SIRIUS/CFM-ID integration).
- **Consequences:** MS2 can only help where reference spectra exist -- by
  design. Two Step 5 tests that relied on benzene surviving a phenol precursor
  were corrected (benzene is now correctly filtered out); a new
  `test_precursor_mz_is_a_filter_not_a_score` pins the behaviour.

### ADR-0015: OPLS-DA is not implemented (patented)

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6 specification)
- **Context:** OPLS-DA is widely used in metabolomics but is patented by
  Sartorius (Trygg & Wold) and requires a commercial licence. Shipping it in an
  open platform would create a licensing liability for academic users.
- **Decision:** `services/metabolomics/app/statistics.py` exposes PCA, PLS-DA,
  random forest, and univariate tests, and deliberately exposes **no** OPLS-DA
  function. The module carries `OPLS_DA_NOT_IMPLEMENTED` explaining why. A
  regression test asserts no callable with "opls" in its name exists in the
  public API (the explanatory constant may mention it).
- **Alternatives considered:** implementing OPLS-DA anyway (licensing risk);
  silently omitting it (users would assume absence is an oversight).
- **Consequences:** Users who specifically need OPLS-DA are pointed to PLS-DA
  (which reports R2Y, Q2, and permutation p-values) and told why.

### ADR-0016: Knowledge graph separates PREDICTED_TARGET from REPORTED_TARGET

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6 specification)
- **Context:** A computational target prediction and a literature-reported
  target are categorically different evidence. Flattening both into one
  "target" edge would let predictions silently inherit the authority of
  experimental reports.
- **Decision:** Two distinct edge types (`PREDICTED_TARGET`,
  `REPORTED_TARGET`). `add_compound_target(reported=False)` requires a
  `confidence` and a `method`; `reported=True` requires a `citation` and
  REJECTS a `method` argument (a literature report has no computational
  method). Query results carry `edge_type` per target; compound context counts
  reported and predicted targets separately. Idempotent imports record a
  version hash in `__import_metadata`.
- **Alternatives considered:** a single edge with a `source` property (rejected:
  easy to filter wrong); a boolean `predicted` flag (rejected: invites
  accidental conflation in queries).
- **Consequences:** Every downstream consumer must state which evidence class it
  is reading. Local storage is networkx; the Neo4j implementation at deployment
  must enforce the same two edge types.

### ADR-0017: Mechanism hypotheses are always labelled, evidenced, and validated

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6 specification)
- **Context:** Pharmacology is where the platform is most tempted to overreach.
  An LLM can produce a fluent, plausible mechanism with no support.
- **Decision:** `MechanismHypothesis` is `is_hypothesis=True` by construction
  (the constructor forces it), requires a populated `EvidenceGraph`, and is
  produced only by `mechanism_hypothesis`, which:
  1. builds the evidence graph from the prediction edges (target, method,
     confidence, citation);
  2. generates the narrative via the injected LLM client (Step 3 protocol) or a
     deterministic fallback;
  3. validates the text deterministically: it must name at least one evidenced
     target and must not use overclaiming language ("proven", "established");
  4. optionally runs an external QA/Critic callable;
  5. raises `InsufficientEvidenceError` on any violation -- the hypothesis is
     REJECTED, not softened.
- **Alternatives considered:** downgrading confidence instead of rejecting
  (rejected: a fluent unsupported claim is worse than no claim); trusting the
  LLM to self-censor (rejected).
- **Consequences:** A mechanism can only reach a user with an inspectable
  evidence trail. The tests pin both rejection paths (missing target, overclaim)
  and the honest path.

### ADR-0018: Step 6 design decisions (fusion substitute, mocks, and gaps)

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Generated during Step 6; flagged for owner review
- **Context:** Several Step 6 items had no permissively-licensed or
  locally-available implementation path.
- **Decision:**
  1. **DIABLO is not implemented.** mixOmics is GPL-3; no permissive Python port
     exists. `pls_da_concat` (PLS-DA on per-block first-singular-value scaled,
     concatenated blocks) is the documented substitute, exposed alongside
     `permutation_test_fusion` (the statistical fusion-helps test). The module
     carries `DIABLO_UNAVAILABLE_REASON`; a test asserts no `diablo` callable
     exists.
  2. **Mock feature table with published ground truth.** `MockDetector`'s docstring
     states exactly which feature is elevated, which is a flat internal standard,
     which is noise, and which drifts; every statistical test asserts against
     that documented truth.
  3. **Mock pathway database** uses KEGG C-numbers with a documented
     feature->compound map; enrichment always reports
     `background_size` and the exact test
     (`hypergeometric over-representation, BH FDR`).
  4. **Mock pharmacology knowledge base** (ChEMBL-style ligands, target-pathway
     links, PubMed articles) carries real-format citations so no prediction or
     pathway is citation-less.
  5. **Docking and ML target prediction are unavailable locally** and fail
     closed / return empty with an explicit gap, never a guess.
  6. **ADMET reports `method="unavailable"`** with the mandated limitations text.
  7. **Diterpenoid reference molecule** (`CCC(C)CCC(C)CCC(C)CCC(C)CC=CCO`) was
     added to the mock predictor and structure DB as a formula-verified
     C20H40O positional isomer in the phytol series -- not claimed as phytol
     itself, since isomerism cannot be verified offline.
  8. **Tanimoto->confidence** is a documented linear map
     `clip((t - 0.4)/0.6, 0, 1)`; every prediction states the mapping in
     `confidence_basis`.
- **Alternatives considered:** vendoring GPL code (licensing); fabricating an
  ADMET model (dishonest); skipping the fusion statistical test (loses the
  multi-modal claim's evidence).
- **Consequences:** Every unavailability is explicit and reasoned, and every
  available result states its method, basis, and citation.

### ADR-0024: Public data integration (NP-MRD archive + SUMMIT fixtures)

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (hybrid Mode C + D decision)
- **Context:** The owner's NMR data folder was inventoried read-only
  (`scripts/inventory_dataset.py`, `data/inventory/dataset_inventory.json`):
  15.34 GB logical / **425.5 MB materialized**, and — contrary to the original
  assumption — **zero Bruker experiments**. It is a SUMMIT benchmarking
  workspace (public database mirrors, model checkpoints, benchmark outputs).
  The only plausible home for real spectra, `experimental_spectra.zip`
  (13.72 GB), is the **public NP-MRD archive** and is a Google Drive
  placeholder on disk.
- **Decision:** A hybrid integration:
  1. **Cloud plane (Mode D).** `scripts/sync_public_archive_to_gcs.sh` uploads
     **only** `experimental_spectra.zip` to
     `gs://<bucket>/npmrd/raw/`, refusing to run while the file is a
     placeholder, computing SHA256 before upload, never deleting (`no -d`), and
     writing `data/inventory/public_data_manifest.json` with URL, licence
     (CC-BY-NC), SHA256, size, and upload time. The archive is never committed.
  2. **Local plane (narrow Mode C).** Small public benchmark CSVs and the
     andrographolide HSQC peak list are copied into
     `tests/fixtures/real_npmrd/` (each a few KB, no raw data) and exercised by
     `tests/test_real_npmrd_fixtures.py`.
  3. **No Bruker ingestion changes** based on this dataset: there are no Bruker
     experiments in it. That enhancement waits for genuine acquisitions.
- **Alternatives considered:** Mode A/B (impossible: no complete per-sample
  datasets exist to fixture); Mode C alone (would prove peak-list parsing but
  never exercise the 13.7 GB archive); materializing the archive locally
  (rejected: 13.7 GB on an 8 GB MacBook violates the split-plane rule).
- **Consequences:** The platform's real-data claim rests on public NP-MRD and
  SUMMIT benchmark data — the same inputs the SUMMIT benchmark itself used.
  Owner-specific experimental data is a separate, later integration.

### ADR-0025: SUMMIT reference-implementation artifacts are excluded from the platform

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The data folder mixes public data with the SUMMIT
  proof-of-concept's own artifacts: five SHIFT-MPNN checkpoint files (`.pt`,
  ~15.4 MB each), HOSE/`nmrshiftdb2` tables, benchmark_v2/v3/v4 result
  directories, and database dumps. These are the reference implementation's
  outputs and models — they are not platform code, and vendoring them would
  create licence ambiguity and a large, stale bundle.
- **Decision:** `summit_poc/` and `experimental_spectra.zip` are added to
  `.gitignore` with explanatory comments. Only the small **benchmark CSV
  outputs** and the andrographolide peak list are copied into
  `tests/fixtures/real_npmrd/` (Step 3B), under `tests/` rather than anywhere
  resembling a source tree, so no one mistakes them for platform assets. The
  checkpoints remain available in the owner's workspace for cross-checking
  only.
- **Alternatives considered:** committing the `.pt` checkpoints so the
  elucidation service can use them (rejected: the platform targets a Vertex AI
  ShiftMPNN endpoint; shipping reference weights is a Step 8/deployment
  decision with its own licence review); committing the whole benchmark tree
  (rejected: superseded runs, large and redundant).
- **Consequences:** The repository stays free of third-party binaries. A
  cross-check against the reference implementation remains possible by
  pointing the validation scripts at the workspace path.


### ADR-0026: NP-MRD CC-BY-NC commercial-use prohibition and tier separation

- **Date:** 2026-10-21
- **Status:** accepted (decision deferred on commercial Pro-tier resolution)
- **Deciders:** Project owner
- **Context:** The NP-MRD experimental spectra archive and extracted peak lists
  are licensed under Creative Commons Attribution-NonCommercial (CC-BY-NC).
  This license permits academic, research, and non-commercial redistribution with
  attribution, but strictly prohibits commercial use. Because PhytoCompanion is
  conceived as a freemium platform with both an academic free tier and a commercial
  Pro tier, shipping or serving CC-BY-NC data in paid tiers creates a commercial
  licensing violation.
- **Decision:**
  1. The platform's **FREE academic tier** may utilize and serve NP-MRD data for
     non-commercial scientific research with explicit attribution to NP-MRD.
  2. The **PRO tier** must NOT include or distribute NP-MRD data unless an
     explicit commercial license or data-access agreement is obtained from the
     NP-MRD consortium.
  3. All manifests (`public_data_manifest.json`, `DATA_SOURCES.md`, `validation_report.json`)
     must prominently display the CC-BY-NC commercial prohibition warning.
  4. The commercial Pro-tier data strategy (whether to negotiate a commercial
     NP-MRD license, restrict Pro tier to synthetic/in-house data, or use
     unencumbered public databases) is deferred until pre-deployment commercialization
     review.
- **Consequences:** PhytoCompanion remains legally compliant and respects academic
  consortium licenses. Clear boundaries prevent accidental contamination of commercial
  offerings with non-commercial data.

### ADR-0027: Benchmark scope caveat -- SUMMIT v4 numbers are a reproduction, not independent validation

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The 92.3% top-1 retrieval figure and the 88.9%-vs-44.3% site
  localisation figure were derived from CSV outputs that the SUMMIT v4 benchmark
  produced itself. Presenting them as independent validation would overstate the
  evidence, because the same implementation generated both the predictions and
  the ground-truth comparison. Two further context facts materially affect how
  the numbers should be read: (a) the candidate-pool distribution is highly
  skewed (median 48, maximum 642) and the weakest accuracy bucket is the
  *smallest* pool (<10 candidates, 79.3%); (b) the localisation benchmark has a
  median of only 3 possible edit sites, and 8 of its 36 compounds have exactly
  one possible site, making the task vacuous for 22% of that set and inflating
  the published mean-of-1/n chance baseline to 44.3%.
- **Decision:**
  1. All external documents must describe the 92.3% top-1 figure as a
     **reproduction of SUMMIT v4**, never as independent or held-out validation.
  2. The figure must always be quoted alongside the median candidate pool size
     and the accuracy-by-pool-size breakdown.
  3. The localisation result must always be quoted with the median number of
     possible sites and a disclosure of how many cases had a single possible site.
  4. `scripts/analyze_benchmark_context.py` regenerates
     `data/inventory/benchmark_context_report.md`, which is the authoritative
     context document; it must be re-run if the fixtures change.
- **Consequences:** Claims remain honest and defensible under scrutiny. The
  platform still has no independent, genuinely held-out benchmark of its own,
  which is a known gap to close before any public performance claim.

### ADR-0028: Token metering integrated before the frontend to avoid retrofit

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** PhytoCompanion is being prepared for a 200-user academic pilot
  funded personally, so usage-based tokens are required for sustainability. If
  the frontend were built first, every screen, button and optimistic update
  would need retrofitting with balance display, cost preview, and
  insufficient-funds handling -- and hidden per-call costs would surface to
  users only after the fact. Token awareness is therefore a day-one frontend
  concern, not a later optimisation.
- **Decision:** Integrate the token layer BEFORE Step 7 (frontend).
  1. **Auth middleware performs a SYNCHRONOUS reservation** against the
     Firestore-backed ledger before any metered route executes. A request either
     has tokens held for it or is refused; concurrent requests cannot overrun a
     balance.
  2. **The billing worker is ASYNCHRONOUS.** After a call, a ``UsageEvent`` is
     published to Pub/Sub and consumed off the request path for audit,
     reconciliation and refunds. Billing failure must never fail a user request.
  3. **Free tier:** 50 tokens granted once per new user
     (``FREE_TIER_SIGNUP_TOKENS``; the figure is set by ADR-0033).
  4. **Pro tier:** token top-up purchased via Stripe.
  5. Routes are priced explicitly in ``ROUTE_PRICING``; an undeclared route
     resolves to ``None`` and must not be silently billed at a guessed rate.
- **Consequences:** The frontend can show cost before an action and remaining
  balance after it. The ledger is append-only, so disputes are resolvable.
  Service scaffolding (``services/auth``, ``services/billing``), the pricing
  table and the usage-event models exist now; the Firebase middleware,
  Firestore reservation client, Pub/Sub worker and pricing values land when the
  token system code is supplied. ``services/auth`` and ``services/billing`` are
  deployable placeholders that report ``token_layer: not_implemented``.

### ADR-0029: RAG architecture -- the LLM never answers without retrieved context

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6.5 specification)
- **Context:** A fine-tuned 7B model asked a natural-product question without
  evidence will produce fluent, plausible, unciteable prose. In a research
  setting that is worse than no answer: it is indistinguishable from a real
  finding to anyone who did not run the retrieval. The platform's honesty
  principle ("no identification claims without support") therefore has to be
  enforced structurally, not by prompting.
- **Decision:**
  1. Every LLM endpoint requires non-empty ``context_papers`` and returns
     HTTP 400 (not a schema 422) when they are empty, so the caller is told to
     run a literature search first.
  2. ``grounding_score`` is computed locally and deterministically from the
     returned text -- cited-and-valid claims / total claims. A citation counts
     only if its identifier was among the supplied papers.
  3. A claim carrying no citation is counted separately (``claims_uncited``).
     The QA/Critic rejects the output if the score is below 0.7 **or** any
     claim is uncited **or** any cited identifier is not in the context
     (a fabricated citation).
  4. The orchestrator's QA/Critic recomputes the audit from the text and the
     papers it recorded; the model's self-reported score is never trusted.
  5. The Tier-3 call itself still requires recorded human authorization
     (ADR-0009), so the ``mechanism_hypothesis`` workflow pauses before the
     LLM runs as well as before the knowledge-graph write.
- **Alternatives considered:** Prompt-only grounding instructions (rejected:
  unenforceable); a second LLM as judge with no deterministic checks (rejected:
  an LLM judging an LLM cannot be the persistence gate); cosine similarity
  between answer and abstracts (rejected: rewards paraphrase, not citation).
- **Consequences:** Tier-3 output can be refused outright, and a refusal is a
  normal, auditable outcome rather than an error path. The grounding module
  lives in ``phyto_shared`` so the service and the gate cannot disagree.

### ADR-0030: Literature source priority and deduplication by DOI then PMID

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6.5 specification)
- **Context:** Three indexes return overlapping records with different
  completeness. PubMed carries curated MeSH indexing and abstracts; Semantic
  Scholar adds citation graph and broader coverage; OpenAlex has the widest
  coverage but frequently no PMID. A naive concatenation would show the same
  paper three times and let a weaker record win a ranking slot.
- **Decision:** Priority order **PubMed > Semantic Scholar > OpenAlex**. Merge
  records by DOI first, then PMID, using union-find so a DOI-linked and a
  PMID-linked copy of the same paper collapse into one group. Keep the
  highest-priority record as primary, record the others in ``also_found_in``,
  and fill any missing field (PMID, DOI, abstract, year) from the highest
  priority duplicate. ``source_counts`` reports hits per index BEFORE
  deduplication so coverage remains visible.
- **Alternatives considered:** Trusting each index's own relevance score
  (rejected: not comparable across providers); dropping duplicates silently
  (rejected: loses the corroboration signal); DOI-only matching (rejected:
  older PubMed records often lack a DOI).
- **Consequences:** Every paper has a single, traceable primary source and an
  explicit corroboration list. A paper with no PMID and no DOI is rejected at
  the model level because it cannot be cited or deduplicated.

### ADR-0031: NMR structure search integrated as a first-class Tier-1 service

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6.5 specification)
- **Context:** The platform's elucidation path began from predicted shifts and
  candidate databases. Experimental HSQC peaks could not be searched directly
  against spectral databases, so the most direct structural evidence a
  natural-product chemist has was unused. NP-MRD, BMRB and nmrshiftdb2 each
  publish shift records under different licences and different query shapes.
- **Decision:**
  1. Add ``services/nmr_search`` with clients for NP-MRD, BMRB
     (``/search/multiple_shift_search``) and nmrshiftdb2 (REST peak search).
  2. Matching is greedy nearest-neighbour assignment on normalised (1H, 13C)
     distances; unmatched query peaks count against ``coverage`` so a record
     matching two of fifteen peaks cannot rank well.
  3. Every hit carries its database and its licence (``LicenseInfo``).
     CC-BY-NC records are dropped when ``include_noncommercial=False``, which
     the commercial tier sets (ADR-0026).
  4. ``mechanism_hypothesis`` runs NMR search, then literature for the top
     candidate, then the grounded LLM hypothesis, then the QA/Critic.
  5. Live providers' request shapes follow their published APIs but have not
     been exercised from this machine; the mock client over the committed
     NP-MRD fixture is what the local plane and tests use.
- **Alternatives considered:** Combining the three databases behind one
  vendor-agnostic index (rejected for now: licence terms differ per record, so
  the licence must travel with the hit); exact-match peak lookup (rejected:
  solvent and referencing shifts make exact matching useless).
- **Consequences:** Experimental peaks now have a direct retrieval path, and
  licence compliance is a property of each hit rather than of the service.
  The platform still makes no identification claim from a database hit alone.

### ADR-0032: API keys live in Google Secret Manager, never in the image or repo

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6.5 specification)
- **Context:** The literature and NMR services need provider credentials
  (``NCBI_API_KEY``, ``SEMANTIC_SCHOLAR_API_KEY``, and any future database
  keys). A key in a Docker image, a ``.env`` file, or a git history is a
  permanent disclosure: rotating it does not un-publish it.
- **Decision:** All provider keys are stored in Google Secret Manager and
  injected into Cloud Run at runtime as environment variables. Images contain
  no keys; ``.gitignore`` excludes the Firebase service-account JSON, ``.env``
  and any ``*key*.json``.
- **Alternatives considered:** A ``.env`` file mounted from a bucket (rejected:
  still a file on disk, and easy to copy off); baking keys into the image
  (rejected: every pull of the image discloses them).
- **Consequences:** Local development needs a real key only when a live
  provider is exercised; tests and dry runs use no key. Rotating a key is a
  Secret Manager version bump with no rebuild.

### ADR-0033: Token budget -- free tier 50, tiered route prices, refunds on failure

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner (Step 6.5 specification)
- **Context:** Literature and NMR calls are cheap but not free, and the LLM
  endpoints cost an order of magnitude more. A single price for "an API call"
  would let a user burn a generation budget on retrieval, and charging for a
  call that failed before doing work would make retries punitive.
- **Decision:**
  1. New accounts receive 50 tokens.
  2. Prices: literature search 2, literature for one compound/target 5, NMR
     peak search 3, reference shifts 2, explain 10, hypothesize 25, recommend
     experiments 15, summarize literature 20.
  3. The named constants live in ``shared/phyto_shared/llm_pricing.py`` and
     ``ROUTE_PRICING`` imports them, so a route price and its operation price
     cannot drift apart.
  4. Metering is reserve-then-settle. A request that fails before producing a
     result is refunded in full; a partial result is charged the reserved
     amount.
  5. The orchestrator's tool registry records estimated costs, not charges:
     only the HTTP service layer touches tokens.
- **Alternatives considered:** Per-request flat fee (rejected: wildly
  mispriced across tiers); metering in the orchestrator (rejected: the same
  tool can be called through the API and out of band, so the charge belongs at
  the network boundary).
- **Consequences:** The free tier buys roughly twenty literature searches or
  two hypotheses. Price changes are a one-line edit per operation and are
  covered by a test asserting the constants match the route table.

### ADR-0034: Hybrid LLM provider strategy -- Vertex AI primary, OpenRouter fallback

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Generation is the platform's most expensive and most
  policy-sensitive operation. Vertex AI gives data sovereignty, a region
  guarantee, and the fine-tuned Qwen 2.5 7B + QLoRA endpoint the platform was
  built around, but it needs a deployed endpoint and a GCP project before a
  single token can be generated. OpenRouter exposes a wide range of
  open-source models behind one OpenAI-compatible API with a free tier, which
  is the right tool for development, evaluation, and comparing a fine-tune
  against a base model -- but it is a third-party gateway, so research data
  sent through it is outside our sovereignty boundary.
- **Decision:**
  1. **Vertex AI is the primary provider in production** and the only one
     permitted for the fine-tuned model. It is the default when the service
     runs in the cloud plane.
  2. **OpenRouter is the flexible fallback and experimentation layer**, not a
     production primary. It is used for local development, prompt iteration,
     model comparison, and as an availability fallback.
  3. The provider is **selectable per request** through an optional
     ``provider`` field (``vertex`` | ``openrouter`` | ``local``). Omitting it
     uses the deployment default: Vertex in the cloud plane, the offline mock
     locally. ``local`` exists so the dry-run plane and tests never reach a
     network.
  4. A factory, ``get_llm_client(provider)``, is the single place that maps a
     name to a client. Both clients implement the same ``LLMClient`` protocol
     and share one grounding instruction, so switching providers cannot change
     the citation contract or bypass the grounding gate.
  5. The resolved provider is recorded in the provenance manifest for every
     response, and OpenRouter records the model that actually served the
     request rather than the requested slug.
  6. **API keys for both providers are managed in Google Secret Manager** in
     production (ADR-0032) and injected as environment variables
     (``OPENROUTER_API_KEY``; Vertex uses the service account). No key is
     committed, baked into an image, or logged.
- **Alternatives considered:** OpenRouter-only (rejected: sends every request
  through a third-party gateway and gives up the fine-tuned endpoint);
  Vertex-only (rejected: cannot develop or evaluate models without a deployed
  endpoint and GCP credentials); a second vendor SDK (rejected: OpenRouter is
  OpenAI-compatible, so ``httpx`` is enough and stays mockable).
- **Consequences:** Development and model comparison no longer require GCP.
  Because provider choice is per request, the QA/Critic and grounding rules
  apply identically to both -- an OpenRouter response is not trusted more or
  less than a Vertex one. Requests carrying sensitive data must stay on
  Vertex; that is a policy obligation, not something the code enforces.

### ADR-0035: Public product name is Phytospectra; internal name stays phyto-companion

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The platform needs a public identity that is separate from the
  internal infrastructure name. The GCP project ID (``phyto-companion-prod``)
  and the repository slug are effectively permanent, so they cannot be renamed
  to match whatever the product is called publicly. A search also showed that
  the public name had to be clear of live marks in the software and scientific
  services classes before it was adopted. (This is a distinct decision from
  ADR-0034, which covers the LLM provider strategy; the ID is new because the
  log is append-only and IDs are never reused.)
- **Decision:**
  1. **Public name: Phytospectra.** All user-facing material -- frontend,
     documentation, papers, and grant applications -- uses "Phytospectra".
  2. **Domains: phytospectra.com and phytospectra.org**, both registered
     2026-10-21 at Cloudflare on a one-year auto-renewing term. DNS is managed
     in Cloudflare; no records have been configured yet.
  3. **Internal names are unchanged:** the GCP project ID
     (``phyto-companion-prod``) and the repository name remain
     ``phyto-companion`` for infrastructure stability. Python package names
     (``phyto_shared``) and service paths are likewise untouched.
  4. **Planned subdomain layout:** ``phytospectra.com`` serves the landing page
     (Firebase Hosting); ``app.phytospectra.com`` serves the React frontend
     (Firebase Hosting); ``api.phytospectra.com`` serves the FastAPI backend
     (Cloud Run domain mapping); ``phytospectra.org`` is a separate academic
     project site (Firebase Hosting); ``docs.phytospectra.com`` hosts
     documentation (Firebase Hosting).
  5. **Trademark clearance:** PHYTOSPECTRA (Ser. 75079589) and PHYTOSPECTRUM
     (Ser. 85392530) are both dead in Class 5 (supplements), and no live marks
     were found in Class 9 (software) or Class 42 (scientific services).
     Filing is deferred until after the pilot, targeting Classes 9 and 42 via
     USPTO, WIPO Madrid, and Thai DIP.
- **Alternatives considered:** Renaming the GCP project and repository
  (rejected: the project ID is permanent and the repository slug is already
  referenced by CI, Terraform state paths, and the data bucket names);
  keeping a single name for both public and internal use (rejected: the
  internal name could not be cleared or changed, and it is a weak public
  brand).
- **Consequences:** User-facing copy says "Phytospectra" while code, bucket
  names, and infrastructure identifiers still say "phyto-companion". That
  mismatch is deliberate and must be expected in docs and search results. DNS
  records and Firebase sites are not yet created, so nothing resolves today.

### ADR-0036: Benchmark scope caveat -- 92.3% is a reproduction and the localisation baseline is inflated by small candidate sets

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Supersedes:** ADR-0027 (same subject, narrower evidence). ADR-0027 is left
  in place unedited per the append-only rule; read this entry as the current
  statement of the caveat.
- **Context:** Two headline numbers are used to describe the platform's
  structure-elucidation performance: **92.3% top-1** structure retrieval over
  207 compounds, and **88.9% vs 44.3% chance** on site localisation over 36
  compounds. Both were generated by the SUMMIT v4 benchmark, and the platform's
  CSV fixtures are verbatim copies of that benchmark's own output directory.
  Presenting either as independent validation overstates the evidence. A
  quantitative audit (`data/inventory/benchmark_context_report.md`, regenerated
  by `scripts/analyze_benchmark_context.py`) established the following facts,
  none of which were visible in the headline numbers:
  1. **Candidate-pool size varies widely**: median 48, IQR 16-122, range 5-642,
     mean 85.1. The distribution is strongly right-skewed.
  2. **Accuracy varies by pool size**: <10 candidates 79.3% (n=29), 10-50
     93.4% (n=76), 50-200 95.2% (n=84), >200 94.4% (n=18). The 92.3% is *not*
     propped up by easy small pools -- the smallest pool is the weakest -- but
     nor is it a uniform capability: the spread is 15.9 points and the weakest
     bucket is small enough that a few compounds move it several points.
  3. **All 16 failures are near-misses at rank 2 or 3, and all are
     constitutional isomers** (16/16 share the molecular formula; 0/16 are pure
     stereoisomers; 15/16 keep the ring count). Seven are glycosides
     (504-1545 Da) and nine are non-glycosidic, densely oxygenated scaffolds
     (338-716 Da). The failure mode is regioisomer discrimination, not wrong-
     scaffold retrieval.
  4. **The localisation baseline is weak**: median only 3 possible sites, and
     **15 of 36 compounds (42%) have at most two possible sites**, 8 of them
     exactly one, where the answer is forced. The published 44.3% is the mean of
     1/n_sites and single-site cases pull it up to 100%. On the 21 compounds
     with more than two sites, accuracy is **81.0%**, not 88.9%.
- **Decision:**
  1. Every external document, paper, grant, and slide describing the 92.3%
     figure must label it a **reproduction of SUMMIT v4**, never "independent
     validation", "held-out test", or "generalisation".
  2. The 92.3% must be quoted with the **median candidate pool (48)** and the
     accuracy-by-pool-size breakdown, or with an explicit pointer to
     `data/inventory/benchmark_context_report.md`.
  3. The **88.9% localisation figure must not be quoted on its own**. Quote
     **81.0% on the 21 compounds with more than two possible sites**, and state
     that 15/36 compounds had at most two.
  4. Claims about failure modes must say **"constitutional-isomer (regioisomer)
     discrimination"**, and must not claim that stereoisomer discrimination or
     high molecular weight alone explains the misses.
  5. `scripts/analyze_benchmark_context.py` is the generator of record for these
     figures and must be re-run whenever the fixtures change.
- **Alternatives considered:** Reporting 92.3% and 88.9% bare and relying on
  readers to ask about baselines (rejected: the numbers are already circulating
  in slide decks and grant text); quietly dropping the numbers until an
  independent benchmark exists (rejected: they are still the only quantitative
  evidence the platform has); keeping ADR-0027 as the only caveat (rejected: it
  predates the pool-size and <=2-site analyses and understates the
  localisation problem).
- **Consequences:** External claims become accurate but weaker, and this
  document is the proof of that weakening. The platform still has **no
  independent, genuinely held-out benchmark**; closing that gap is a
  prerequisite for any generalisation claim. Until then, the honest summary is:
  the method reproduces SUMMIT v4 closely on SUMMIT v4's own data, with a
  characteristic regioisomer failure mode, and performs 81% top-1 on the
  non-trivial subset of the localisation task.

### ADR-0037: Small-pool failures are a molecule-size effect, not rare-formula prediction error

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The `<10`-candidate bucket is the weakest in the Part A
  benchmark (79.3% top-1, 6 failures of 29), which is counterintuitive: a
  smaller decoy pool should be easier to rank. Two explanations were on the
  table: **(a)** genuine GNN prediction errors on rare-formula scaffolds, or
  **(b)** honest ambiguity, where the correct answer is a near-tie with the
  top-1 pick and the confidence machinery should downgrade the claim. The
  answer matters externally: (b) would be a positive finding about the
  platform's honesty machinery, while (a) would be a limitation to disclose.
  Analysis in `data/inventory/benchmark_context_report.md` section 3
  (generated by `scripts/analyze_benchmark_context.py`) examined all six
  failures using the per-candidate scores in `partA_failures.csv` and RDKit
  structural comparison. No ranker re-run was needed or possible: the fixtures
  contain only scores for the 16 failures, not full decoy pools, and the
  available scores answered the question directly.
- **Findings:**
  1. **All six failures are constitutional isomers of their top-1 pick** (same
     formula, same ring count). Five of six also pass a Tanimoto >= 0.7
     close-isomer test; the exception is D-fructose 2,6-bisphosphate
     (Tanimoto 0.56), still a constitutional isomer but structurally distant.
  2. **Only 2 of 6 have a margin below `MIN_MARGIN_FOR_CONFIRMED` (0.10).**
     CERNUOSIDE A (+0.055) and CERNUOSIDE B (+0.013) are genuine near-ties.
     Both would have been reported below `confirmed`; CERNUOSIDE B also falls
     below `MIN_MARGIN_FOR_TENTATIVE` (0.02) and would be `candidate`.
  3. **4 of 6 are genuine prediction errors** with margins of +0.13 to +0.29,
     well above the confirmed threshold: three heavily oxygenated flavonoids
     and D-fructose 2,6-bisphosphate. These would have been reported
     confidently and wrongly.
  4. **The rare-formula mechanism does not hold.** Formula multiplicity is
     effectively identical between the small-pool and larger-pool compounds
     (mean 1.21 vs 1.19 occurrences across the 207-compound set), so thin
     training coverage of unusual formulas is not what separates this bucket.
  5. **The bucket effect is a molecule-size composition effect.** Small pools
     contain larger molecules (median 38 vs 28 heavy atoms) and a much higher
     glycoside share (55% vs 29%). Stratified by heavy-atom tertile, the
     large-molecule stratum fails at 15.6-25.0% at *every* pool size, while the
     small-molecule stratum fails at 0.0-12.5% (its single failure being
     D-fructose 2,6-bisphosphate). Pool size is standing in for molecule size.
  6. **The `AMBIGUOUS` verdict requires an exact tie** (`n_tied > 1`, tolerance
     1e-8). A small-but-nonzero margin therefore yields `tentative` or
     `candidate`, never `ambiguous`. This is correct behaviour but means the
     near-tie cases are downgraded by the margin thresholds, not by the tie
     rule.
  7. **Measurement caveat:** the margin used is the top-1 pick's advantage over
     the *correct* answer, which is what the fixtures record. For the rank-3
     failures an intermediate candidate's score is absent, so the top-1 pick's
     margin over its immediate runner-up may be smaller. The 2-of-6 ambiguity
     count is therefore a lower bound and the 4-of-6 error count an upper bound.
- **Decision:**
  1. The `<10` bucket deficit is recorded as a **molecule-size and
     glycosylation composition effect**, not as an intrinsic small-pool penalty
     and not as a rare-formula data gap.
  2. External communication may state the **positive** part of the finding:
     two of the six small-pool failures are correctly downgraded by the
     confidence machinery. It must also state the **negative** part: four are
     genuine errors on large, densely oxygenated constitutional isomers.
  3. The methods paragraph in the benchmark context report carries both halves;
     the report must not present the bucket as entirely a success (honesty
     machinery working) or entirely a limitation (rare scaffolds).
  4. `tests/test_benchmark_context.py` pins the six margins, the 2/4 split, the
     isomer verdicts, and the refuted rare-formula test, so a fixture change
     cannot quietly invalidate this conclusion.
- **Alternatives considered:** Reporting (b) as the dominant explanation
  (rejected: it only covers 2 of 6 cases); reporting (a) as "rare-formula
  compounds are harder" (rejected: formula rarity is the same across buckets,
  so the stated mechanism is wrong even though errors are real); re-running the
  ranker to recover full pools (rejected: the fixtures do not contain decoy
  structures for the 13 small-pool successes, so a re-run would not be
  comparable, and the stored scores already answer the question).
- **Consequences:** The platform's small-pool weakness is explained and
  correctly scoped. It remains a real limitation on large glycosylated
  structures, which is now stated in the quotable methods paragraph rather
  than hidden behind the aggregate 92.3%. Because the analysis depends on
  SUMMIT v4's stored scores, it inherits ADR-0036's reproduction caveat.

---

### Amendment to ADR-0034 (2026-10-21)

The originally proposed default model `qwen/qwen3-4b:free` has been
retired from OpenRouter's free tier. The current default is
`google/gemma-4-31b-it:free`, chosen for instruction-tuning, 31B
scale, and context length sufficient for RAG. OpenRouter's free
tier rotates frequently, so the default should be re-verified
periodically via:

  curl -s https://openrouter.ai/api/v1/models | python3 -c \
    'import sys,json; [print(m["id"]) for m in json.load(sys.stdin)["data"] if ":free" in m["id"]]'

The OPENROUTER_MODEL environment variable remains available for
production to pin a specific model without a code change.

### ADR-0038: Token system integration -- synchronous reservation plus asynchronous audit

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The platform requires usage-based metering for the academic pilot to
  be sustainable. It must land before the frontend build so every screen is
  token-aware from day one, rather than retrofitted with balance display, cost
  preview and insufficient-funds handling later (ADR-0028). This entry records
  the concrete integration that fulfils ADR-0028's contract.
- **Decision:**
  1. **Synchronous reservation before endpoint execution.** ``AuthMiddleware``
     resolves the route price from ``ROUTE_PRICING`` and reserves tokens against
     the ledger *before* the handler runs. Insufficient balance returns 402 with
     ``balance``, ``required`` and ``topup_url`` before any work is done.
  2. **Asynchronous audit.** Every reserve/confirm/refund publishes a
     ``UsageEvent`` to Pub/Sub; the billing worker writes it to an append-only
     Firestore ledger. A publisher failure is logged and never fails the user's
     request.
  3. **Failures refund automatically.** Any non-2xx/3xx outcome, including a
     raised exception, returns the reservation in full.
  4. **Idempotency-Key header supported** for client retries; a repeated key
     returns the original reservation without a second charge.
  5. **Free tier default is 50 tokens** (``FREE_TIER_SIGNUP_TOKENS``). Pro-tier
     top-up via Stripe is deferred to the frontend phase.
  6. **An unpriced route returns 503**, not 200. Serving an undeclared route free
     would give away paid work; folding it into the client-error range would
     misattribute a deployment mistake to the caller.
  7. **Models are immutable** (``frozen=True``, ``extra="forbid"``) with
     timezone-aware timestamps, so a ledger row cannot be mutated in place and a
     typo at the billing boundary fails loudly.
- **Alternatives considered:** Pure asynchronous deduction (rejected: allows
  overspending, since a user can fire concurrent requests faster than the
  worker settles); subscription-only billing (rejected: usage varies too widely
  across users); pay-per-request without a balance (rejected: requires a payment
  per request, incompatible with academic batch workflows); pricing an unknown
  route at zero or at a guessed default (rejected: either gives away work or
  bills at an unintended rate).
- **Consequences:** Users must hold a balance before heavy operations, so the
  frontend needs a "Buy tokens" flow. Every service now depends on
  ``auth`` and ``phyto_shared.usage_events``. The reservation schemas in
  ``shared/phyto_shared/usage_events.py`` were replaced wholesale by this
  integration: the earlier scaffolding shape (``request_id``/``method``/``path``,
  ``granted``/``plan``) is superseded by ``idempotency_key``/``operation`` and
  ``status``/``balance_before``. Because the log is append-only, ADR-0028 stands
  unedited; this entry is the record that its scaffolding was replaced.
  **Known gap:** ``ROUTE_PRICING`` covers 17 of the 45 implemented routes.
  Networking has no priced route at all, so with enforcement on its eight
  ``/networks/*`` endpoints return 503. The middleware logs every unpriced route
  at startup rather than inventing prices; the table is owned by ADR-0033 and
  extending it is a pricing decision, not an integration one.

### ADR-0039: Services install AuthMiddleware via create_app factory

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Middleware must be applied consistently across the services, but
  tests need to substitute deterministic mocks, and the local plane has no
  Firebase credentials.
- **Decision:**
  1. Every service exposes ``create_app(config)`` as a factory; module-level
     ``app = create_app()`` remains for the container entrypoint. Routes live on
     an ``APIRouter`` included by the factory.
  2. Middleware is installed inside the factory, before routes are added, so no
     endpoint can be registered outside the metering boundary. CORS is added
     after auth, putting it outermost so 401/402/503 still carry CORS headers.
  3. ``install_auth`` is the single installation site, so middleware order and
     the health contract cannot drift between services.
  4. ``AuthConfig.from_env()`` enables enforcement when ``PHYTO_ENV=cloud`` and
     disables it locally (where ``dry_run`` is also the default). ``PHYTO_AUTH_ENABLED``
     overrides both. This keeps the local suite credential-free without weakening
     production.
  5. Every service's ``/health`` returns ``dry_run`` alongside its existing
     diagnostic fields, so operators can verify the mode.
- **Alternatives considered:** Installing the middleware in each service
  individually (rejected: order and configuration would drift); module-level
  ``app`` with no factory (rejected: tests could not inject mocks); enforcing
  auth unconditionally (rejected: the local plane has no credentials and 700+
  existing tests call handlers directly).
- **Consequences:** Services can no longer register routes on a module-level
  ``app`` object; that pattern is gone. Three services outside the original
  scope (``literature``, ``llm``, ``nmr_search``) were also converted, because
  they own priced routes and leaving them unauthenticated would have made the
  metering boundary inconsistent. Consumer Dockerfiles now stage
  ``services/auth/app`` as ``/srv/auth`` and set ``PYTHONPATH=/srv``, because
  installing it as ``app`` would collide with each service's own ``app``
  package.

### ADR-0040: Mock NMR fixtures are representative, not verified

**Date:** 2026-10-21
**Status:** accepted
**Context:** MockNMRSearchClient needs reference shift data for
kaempferol, quercetin, and luteolin to exercise multi-candidate
ranking. Real NP-MRD records could not be verified from the local
environment.
**Decision:** The three fixtures contain representative published
DMSO-d6 assignments compiled from standard flavonoid NMR tables,
not transcriptions of specific database records. Each fixture
header states this explicitly.
**Consequences:** The fixtures are adequate for exercising code
paths in tests and demo mode. They are NOT adequate as reference
data for real analyses. If the platform later ingests real
reference data for these compounds, the fixtures must be replaced
with verified records and the code paths re-tested. The mock
client's header comment must not be removed or "cleaned up" to
imply the values are verified.
**Note:** Requested as ADR-0038, which had already been allocated
to the token-system integration decision by the time this entry
was appended; recorded under the next available ID (0040) to keep
IDs unique. ADR-0039 was likewise already taken.
