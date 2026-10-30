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

Checked programmatically against this file on 2026-10-21, and re-checked on
2026-10-22 after the growth-and-profit ADRs (0043-0049) were appended:

- Entries present: **0001-0018**, **0024-0040**, and **0043-0049**
  (42 entries).
- Missing within the range: **0019, 0020, 0021, 0022, 0023** only. The later
  batch additionally skips **0041, 0042** (see the section note at ADR-0043).
- No duplicate IDs; entries are in ascending order.
- No references to ADR-0019..0023 anywhere else in the repository, so the gap
  creates no dangling links.

**Re-verified 2026-10-22, with one claim corrected:** a review request asked
this note to be changed to say that ADR-0019 and ADR-0023 now exist and only
0020-0022 are missing. Re-checking the file shows that claim is **wrong**:
ADR-0019 and ADR-0023 were never written. The file jumps from ADR-0018
straight to ADR-0024, and the only occurrences of the strings "ADR-0019" and
"ADR-0023" anywhere in the repository are the two sentences in this note.
Nothing in the growth-and-profit batch added either ID. The gap is therefore
still **0019-0023**, and the verified sequence above is the authoritative
state. Should a future decision genuinely need one of those IDs, the
"Deferred from original planning" convention described above still applies.

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
  4. **Domain layout (updated 2026-10-26):** the web app is the only
     Phytospectra client — the desktop app was removed.
     ``www.phytospectra.com`` is the canonical host for the Next.js web app
     (Cloud Run ``phyto-frontend``); ``phytospectra.com`` and
     ``app.phytospectra.com`` serve the same app (Cloud Run domain mappings,
     one per hostname). Citations, share links and Stripe return URLs default
     to the canonical host via ``PHYTO_APP_BASE_URL``.
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

---

## Growth-and-profit features (ADR-0043 .. ADR-0049)

**Numbering note (recorded 2026-10-21, revised 2026-10-22):** these seven
entries begin at 0043, not 0041. ADR-0041 and ADR-0042 were never written; per
this log's append-only rule they are skipped rather than reused. Code comments
and tests reference 0043--0048 directly, so the gap is intentional and
load-bearing for the cross-references in this batch. ADR-0049 documents the
rate limiter that ADR-0044 introduces, because it is a separate module with its
own enforcement surface.

### ADR-0043: Every completed analysis mints a citable identifier

- **Date:** 2026-10-21
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Citations are the primary acquisition loop for an academic tool:
  every published citation reaches exactly the audience the product is for, at
  zero acquisition cost. But a citation is only useful if a reader can trust and
  verify it, and only if the author can obtain it without friction. Charging
  tokens to read a citation would defeat the loop, and registering a DOI with a
  third party must never be able to fail or block a completed analysis.
- **Decision:**
  1. Every run that reaches ``complete`` mints a ``CitationRecord`` once, at
     completion (also on checkpoint approval that completes a run), and stores
     it with the run.
  2. Two identifier forms: the always-available canonical
     ``phytospectra.run:<run_id>``, and a DOI URL when ``ZENODO_TOKEN`` is
     configured. DOI registration is metadata-only (no data files) and
     idempotent per ``run_id`` (dedup key = a Zenodo search on the run_id
     keyword).
  3. The record embeds its own provenance: model version, cost-model version,
     provenance manifest hash, and audit-chain head. A reader can re-derive the
     manifest hash and verify the chain without trusting the platform.
  4. ``GET /runs/{run_id}/citation`` is free and unpriced. It returns 409 for a
     run that has not completed and 404 for an unknown run.
  5. Registration failures are logged and downgraded to the canonical id; they
     never raise. The registrar is built from the environment only when a token
     is present, so the local plane never makes a network call.
  6. Retrieval emits the ``run_cited`` cohort event at most once per run.
- **Alternatives considered:** A DOI for every run from day one (rejected: needs
  a real Zenodo account and turns an optional third party into a hard dependency
  of completion); a citation generated only on demand (rejected: the chain head
  and manifest hash would then depend on when the user asked, not on the run);
  charging for citation retrieval (rejected: it suppresses the loop the feature
  exists to create).
- **Consequences:** Citation text (BibTeX + plaintext) is rendered at
  mint time and stored, so it is stable even if formatting code changes later.
  The Zenodo path is untested against the live API (only the mock registrar and
  failure paths are), so the first real deposit must be verified manually.
  ``run_cited`` counts entry into the citation ledger, not a downstream
  publication; no data supports claiming the latter. **Known gap:** the
  citation response is meant to surface the run's active share URL, but
  ``GET /runs/{run_id}/citation`` reads ``entry.get("share_url")`` while
  ``POST /runs/{run_id}/share`` stores only ``share_token``. No code path
  writes ``share_url``, so the field always serialises as ``null`` and the
  citation cannot advertise the link until that key is populated at share
  creation.

### ADR-0044: Shareable read-only run URLs (with per-IP rate limiting)

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Evidence is only persuasive when a colleague can look at it. The
  natural distribution unit is a link to one run's evidence panel, opened by
  someone with no account. That link must not require the recipient to trust
  the sender with credentials, and must not become a route to unauthenticated
  read of the whole platform. Because the endpoint is unauthenticated by
  design, it is also the one surface a scraper can hit without an account, so
  it needs its own request ceiling.
- **Decision:**
  1. Share tokens are HMAC-SHA256 signed with ``SHARE_HMAC_KEY``, a separate,
     lower-privilege credential from the Firebase token. The payload is
     canonical JSON ``{run_id, scope, iat, exp, jti}`` and the signature covers
     the payload byte-for-byte, so any edit invalidates the token.
  2. ``scope`` is ``"read_only"`` by construction and is re-checked on verify;
     a token whose scope is anything else is rejected. A token grants READ on
     exactly one ``run_id`` -- it cannot approve a checkpoint, mutate a run, or
     read a different run.
  3. Expiry defaults to 30 days and is clamped to ``[1, 365]``; the ceiling
     means a leaked token cannot grant read access forever. Revocation is by
     token id (``jti``), and a revocation entry expires no later than the token
     it kills, so the revocation list cannot grow without bound.
  4. ``ShareTokenManager.from_env`` uses a clearly-labelled development key
     only when ``PHYTO_DRY_RUN=1``, and refuses to mint without
     ``SHARE_HMAC_KEY`` otherwise.
  5. The public surface is two GET endpoints: ``GET /share/{token}`` (the
     read-only view) and ``GET /share/{token}/artifacts/{name}`` (one task
     artifact). Neither exists as a mutating verb, and the orchestrator's
     approve route is on the authenticated surface only.
  6. The shared view carries a call to action back into the product
     (``Open in Phytospectra`` -> ``/analysis/new``) with ``sample_id`` and
     ``workflow`` prefilled, so a reader can start their own analysis from the
     evidence they just read. It never exposes the owner's identity or raw
     uploads.
  7. ``GET /share/*`` is rate-limited per client IP with a true sliding window,
     default 60 requests per minute (``SHARE_RATE_LIMIT_PER_MIN`` overrides),
     returning 429 when exceeded. 60/min is far above human reading pace and
     far below scraping throughput.
  8. Share reads are unpriced (0 tokens) in ``ROUTE_PRICING``; charging for
     them would suppress the distribution loop.
- **Alternatives considered:** Requiring an account to view a shared run
  (rejected: kills the zero-friction distribution loop the feature exists for);
  a random opaque database key instead of a signed token (rejected: verification
  would require a database read on every view, and the key alone would carry no
  expiry); JWT with the Firebase key (rejected: a share token must not share the
  blast radius of the identity credential); unlimited token lifetime (rejected:
  a leaked link would be permanent).
- **Consequences:** Revocation is only real when a registry is wired; a manager
  without one can mint and verify but never revoke. **Known gap:** the
  orchestrator constructs its manager with ``ShareTokenManager.from_env()`` and
  passes no registry, so ``DELETE /runs/{run_id}/share`` reports
  ``revoked: 0`` and a revoked token still verifies. ``InMemoryShareRegistry``
  and ``FirestoreShareRegistry`` exist and are correct in isolation but are
  never instantiated in production wiring (grep confirms they appear only under
  ``tests/``). Wiring the registry is therefore a prerequisite for advertising
  revocation at all; until then item 3's revocation guarantee is unimplemented
  at the service boundary. The rate limiter is process-local, so it is correct
  for the single-instance local plane only; a multi-instance deployment must
  move the counter to Redis. **TODO:** no Redis-backed limiter exists yet, and
  the Firestore share registry is not exercised by the local suite (only the
  in-memory one is). The share-token scope check prevents privilege reuse, but
  it does not by itself stop a holder from forwarding the link; expiry plus
  explicit revocation is the mitigation.

### ADR-0045: The guided first story and the monthly allowance

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Activation is the metric that matters, and a new user holding 50
  free tokens can burn them on dead ends before ever reaching the value moment
  (the evidence panel). Two forces have to be reconciled: the sequence should
  guide a new user through one complete, honest analysis, and the tokens remain
  the user's, so nothing may be forced. Separately, the free allowance must be
  a cap on spend that is independent of the token balance -- otherwise a user
  with a topped-up balance could keep consuming free-tier compute indefinitely.
- **Decision:**
  1. ``first_story_steps`` defines an eight-step sequence: upload a sample,
     run elucidation, view the evidence panel, search reference NMR data,
     search the literature, request an LLM explanation, build a molecular
     network, then get the citation and a share link.
  2. Every step is opt-in. The sequence exists so the UI can prompt toward the
     value moment; skipping a step leaves those tokens with the user.
  3. Step costs are DERIVED from ``ROUTE_PRICING`` at call time, never
     hardcoded, so a price change cannot silently make the documented sequence
     wrong. Reads that are free by design (evidence, citation) are listed at 0.
  4. The invariant that is actually asserted is "the full story fits inside the
     free monthly allowance with buffer to retry", not a fixed token total.
     With the current price table the sequence costs **41 tokens** of the
     50-token allowance, leaving a **9-token** retry buffer. (The original
     planning figure of 37 tokens / 13 buffer predates the frozen base table
     and no longer matches it; the table wins, because changing prices is out
     of scope for this decision.)
  5. ``GET /onboarding/first-story`` is unpriced (0 tokens): it is guidance,
     and metering it would discourage the very flow it exists to start.
  6. The monthly allowance is enforced by the auth middleware as a cap on
     tokens SPENT per calendar month, independent of the balance. A request
     that would exceed it is refused with 402 even when the balance could cover
     it, because free-tier compute is the dominant cost term.
  7. Allowance reset semantics: ``used_this_month`` resets at 00:00 UTC on the
     first of the next month; the allowance is re-granted by topping the
     balance UP TO the allowance (``balance = max(balance, allowance)``), so
     unspent allowance never accumulates and purchased tokens are never
     revoked. A plan change grants the new plan's allowance immediately, and a
     later downgrade never takes granted tokens back.
- **Alternatives considered:** A single "run everything" button that spends the
  tokens in one call (rejected: hides what each step costs and removes the
  user's ability to stop); hardcoding the 37-token story (rejected: it would
  drift from ``ROUTE_PRICING`` and go stale silently); letting the balance
  override the monthly cap (rejected: reopens the free-tier cost leak);
  accumulating unspent allowance (rejected: turns the free tier into a savings
  account and undermines the monthly margin model).
- **Consequences:** The advertised "37 tokens" is now 41; any UI copy quoting 37
  must be corrected, and a test pins the invariant against the live pricing
  table so a future price change that prices the story out of the free tier
  fails the build. Entitlements exist in an in-memory mock and a Firestore
  client; the Firestore path is not exercised locally. The allowance is tracked
  per calendar month with no proration, so a mid-month upgrade gets the full
  new allowance rather than a pro-rated amount -- acceptable while plans are
  monthly, and a decision to revisit if annual plans are introduced.

### ADR-0046: Distilled model routing for the free tier, and a read-through query cache

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Free-tier cost is the dominant term in the margin equation, and
  two of the most frequent free-tier calls -- LLM explanations and repeat
  literature/NMR lookups -- are also the ones where cheapness matters least to
  the user's experience if quality is preserved. Distillation risks lowering
  output quality exactly at the value moment, and caching risks serving stale
  or mismatched results.
- **Decision:**
  1. ``MODEL_TIERS`` routes the ``free`` plan to a distilled model id
     (``PHYTO_DISTILLED_MODEL``, default ``qwen2.5-1.5b-instruct-distilled``)
     and every paid plan to the full model id (``PHYTO_FULL_MODEL``, default
     ``qwen2.5-7b-instruct-qlora``). ``select_model_tier`` derives the tier
     from ``PLAN_FEATURES[...].distilled_llm`` so plans and routing cannot
     disagree.
  2. Tier routing applies to the deployment-default provider path only. An
     explicitly named provider is honored exactly as named, because silently
     substituting it would violate the fail-closed provider rule.
  3. The QA grounding gate applies IDENTICALLY to both tiers: an output below
     ``GROUNDING_MIN_SCORE`` or carrying any uncited claim is rejected with
     503, whichever model produced it. Distillation must not lower quality
     below the honesty threshold.
  4. If the distilled model is unavailable (``PHYTO_DISTILLED_DISABLED=1``),
     free users fall back to the full model and the fallback is logged as a
     COST ANOMALY, because it quietly reopens the cost leak the tier exists to
     close.
  5. Literature and NMR-search results go through a read-through cache
     (``CACHE_TTL_SECONDS`` = 24h). The key is the SHA256 of the operation plus
     the normalized query: lowercased, trimmed, whitespace-collapsed, with
     filter keys sorted. Semantically different queries (different filters,
     different peaks) do not share an entry.
  6. A cache hit is free: the endpoint sets ``X-Cache: HIT``, and the auth
     middleware refunds the reservation because it sees that header. A miss is
     charged normally and writes the cache. The cached value is the upstream
     response, so quality is intended to be unchanged. **Known gap:** a miss and
     a hit validate differently. The miss path constructs ``LiteratureResponse``
     around already-built ``PaperHit`` objects, and pydantic does not revalidate
     nested instances, so a response can be served and cached with an
     out-of-range ``relevance_score`` (raw BM25 sums exceed the field's
     ``le=1.0`` bound in ``services/literature/app/router.py``). The hit path
     parses the cached dict and *does* validate, so the same payload that
     returned 200 on the miss raises ``ValidationError`` on the hit. Until the
     score is bounded or normalized at the source, the cache is not
     payload-equivalent across hit and miss.
- **Alternatives considered:** Serving free users the full model and absorbing
  the cost (rejected: it is the dominant margin term); routing free users to a
  stronger-but-slower model instead of a smaller one (rejected: latency at the
  value moment is its own churn driver); a shorter cache TTL (rejected: 24h
  absorbs same-day repeat traffic while ensuring a retracted paper stops being
  served within a day); caching on the raw query string (rejected: trivial
  whitespace and casing differences would split the cache and erase the
  savings).
- **Consequences:** The local plane cannot honestly simulate a worse distilled
  model, so ``DistilledLLMClient`` subclasses the mock and shares its grounded
  renderer; the contract under test is routing and the gate, not the distilled
  weights. **TODO:** no real distilled weights are wired into the local suite,
  and the gate's behaviour on a genuinely weaker model must be validated before
  the tier is relied on for quality claims. The Firestore cache is production
  only and untested locally. Because a cache hit refunds the reservation, a
  cache stampede can still make N concurrent identical misses pay N times;
  acceptable at current volume, revisit if it shows up in the cost data.

### ADR-0047: Plan tiers in code, prices in Stripe, and pooled workspaces

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The product needs a free tier that seeds adoption and
  institutional tiers that actually pay. If prices live in code, code and
  Stripe will eventually disagree about what a user bought; if features live
  only in Stripe, the middleware and the LLM router cannot gate on them
  without a network call. Classrooms additionally need shared budgets and a
  teaching-relevant measure of activation, because per-student billing would
  kill the teaching loop.
- **Decision:**
  1. ``PLAN_FEATURES`` is the single source of truth for FEATURES per plan:
     ``free`` (50 tokens, 1 seat, distilled LLM, no API access), ``pro`` (2,000
     tokens, 1 seat, full LLM, API access), ``lab`` (10,000 tokens, 10 seats,
     priority, full LLM, API access), ``department`` (100,000 tokens, 200
     seats, priority, full LLM, API access, SSO, audit export), and
     ``enterprise`` (negotiated, marked ``custom``; concrete limits remain TBD
     until a contract defines them).
  2. PRICES DO NOT LIVE IN CODE. Prices live in Stripe. ``GET /billing/plans``
     returns features only, so the pricing page renders from one source and
     prices are fetched from Stripe. The mock MRR table exists for test/dry-run
     only and is explicitly not the product's prices.
  3. Plan order is ``free < pro < lab < department < enterprise`` and gates are
     expressed as "at or above" (API keys require ``pro``; SSO and audit export
     require ``department``). An unknown plan raises rather than silently
     downgrading to free.
  4. Workspace caps come from ``WORKSPACE_CAPS`` so the pricing page, the API
     and the middleware cannot disagree: ``classroom`` 30 seats / 5,000 pool
     tokens per month, ``lab`` 10 / 10,000, ``department`` 200 / 100,000.
  5. A workspace has a shared pool. A member's request draws from the pool
     BEFORE their personal balance, and a pool draw bypasses the personal
     monthly allowance because the pool is the workspace's budget, not the
     member's. If the pool cannot cover the request, the member falls back to
     their personal balance.
  6. Only a lab-or-above plan may create a workspace (classrooms are taught by
     paying instructors, while the student seat is free -- that is the teaching
     loop). Members are added by email and resolve to a placeholder uid until
     the invitee signs in.
  7. Activation is a completed metered ``POST /analysis`` (``ACTIVATING_OPERATION``),
     never a sign-in or an invitation. The instructor dashboard reports
     per-member tokens used, activation, 30-day retention and the seat count.
  8. Billing: subscribe returns a Stripe Checkout URL; the webhook is idempotent
     per ``session.id`` (a redelivery returns the stored outcome instead of
     re-granting), and it emits ``plan_changed`` with the MRR read from the
     Stripe session.
- **Alternatives considered:** Prices as constants in ``plans.py`` (rejected:
  they would drift from Stripe, which is the actual system of record); feature
  flags fetched from Stripe per request (rejected: adds a network dependency to
  every gated request); per-student billing for classrooms (rejected: it prices
  out the teaching loop that seeds institutional adoption); individual quotas
  for workspace members (rejected: the institution buys a budget, not N
  individual budgets); counting a sign-in as activation (rejected: it would
  flatter the dashboard while measuring nothing).
- **Consequences:** ``enterprise`` exists so entitlement ranking works, but its
  every concrete limit is TBD; calling ``plan_features("enterprise")`` returns
  zeros and must not be used to grant real capacity. Workspace pool draws and
  seat checks are transactional in the Firestore store but only lock-guarded
  in memory, so the local plane is not a concurrency proof. **Known gaps:** the
  design above is not yet fully reachable. ``ROUTE_PRICING`` keys workspace
  creation as ``("POST", "/workspaces/")`` while ``cost_for_route`` strips the
  slash from the request path only, so a bare ``POST /workspaces`` matches no
  price and the middleware answers 503 instead of serving the request; and the
  in-memory invite path calls ``secrets.sha256`` (no such function) instead of
  ``hashlib.sha256``, so adding a member raises rather than returning a
  membership. Both are wiring defects, not design changes. **TODO:** the
  Firestore workspace store is not exercised by the local suite. Pool tokens
  are granted by the Stripe webhook in production; in dry-run the checkout
  session is a mock and the test wires the grant separately. A downgrade does
  not claw back seats already issued, so an oversubscribed workspace after a
  downgrade needs an explicit reconciliation step.

### ADR-0048: Cohort analytics measure profitability, not just growth

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Growth is only good if it pays for itself. Signup counts alone
  cannot distinguish healthy growth from buying users at a loss, and a separate
  analytics pipeline would drift from -- and could contradict -- the audit
  trail about what actually happened.
- **Decision:**
  1. Five metrics are computed per monthly cohort (UTC ``YYYY-MM``):
     activation, D30 retention, conversion, gross margin, and contribution per
     user. Activation requires a ``run_completed`` event intersected with the
     cohort's signups -- a signup alone never counts. D30 requires activity in
     ``[signup+25d, signup+35d]``. Conversion counts users whose latest plan is
     not ``free``, over activated users. Gross margin is
     ``(revenue - infra) / revenue`` and is ``None`` when revenue is 0
     (undefined, not silently 0 or -inf). Contribution per user is
     ``(revenue - infra) / signups``.
  2. Events are appended to the existing hash-chained audit log as records of
     ``kind == "analytics_event"``, so they inherit tamper evidence and cannot
     disagree with the audit trail. Workflow events share some names (the
     engine logs its own ``run_started``) and are filtered out by ``kind``.
  3. The event vocabulary is a contract: ``user_signup``, ``run_started``,
     ``run_completed``, ``run_shared``, ``run_cited``, ``plan_changed``,
     ``workspace_member_activated``. An unknown event name raises, because a
     typo would silently vanish from every cohort.
  4. Two guardrails fire: negative contribution per user always alerts, and
     rising signups with flat-or-declining contribution per user alerts. Growth
     is only pursued while contribution per user is positive and rising.
  5. The job is ``run_cohort_job(records, store)``: compute, persist per-month
     rows, evaluate guardrails, persist alerts. The daily trigger is the
     deployment's choice; ``POST /admin/cohorts/recompute`` is the HTTP/test
     entry point and ``GET /admin/cohorts`` reads the table. Both are
     admin-only.
  6. In production the job is fed the union of the per-service audit chains
     (or their mirrors), since runs, signups and plan changes are raised in
     different services. The record schema is identical, which is what keeps
     this a plumbing task rather than a port.
- **Alternatives considered:** A separate analytics database (rejected: it would
  duplicate the audit trail and could disagree with it); counting sign-in as
  activation (rejected: it measures nothing about value); revenue per cohort
  only, without contribution per user (rejected: a growing cohort can hide
  worsening unit economics); erroring on zero revenue for gross margin
  (rejected: an undefined margin is more honest than a fabricated one);
  tracking only aggregate MRR (rejected: it cannot show whether a cohort pays
  for its own compute).
- **Consequences:** The cohort job is only as complete as the records it is
  given; the local plane feeds it one service's chain, and the cross-service
  union is a deployment concern that is not yet wired. Revenue is recognised
  when ``plan_changed`` is emitted (webhook time), not at invoice settlement,
  so it is a good directional margin signal rather than an accounting figure.
  Contribution per user is cohort-cumulative in spirit but computed from the
  events present in the supplied records, so feeding a partial history will
  understate revenue and overstate losses -- the guardrail should be read as a
  tripwire, not a P&L. **TODO:** Firestore cohort persistence and the
  cross-service union are not exercised locally.

### ADR-0049: Rate limiting is enforced per IP on unauthenticated share reads

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Share URLs are unauthenticated by design (ADR-0044), which makes
  ``GET /share/*`` the one surface a scraper can hit without an account.
  Separately, the platform fans out to external indexes whose owners impose
  their own request ceilings; exceeding those gets the deployment's egress IP
  throttled or blocked, which degrades every user at once. The limits need to
  be enforced where the traffic actually originates, with an interface that
  does not change when the backend moves from process memory to Redis.
- **Decision:**
  1. ``SlidingWindowRateLimiter`` implements a true sliding window over the
     last ``window_seconds`` using a monotonic clock and a per-key deque,
     guarded by a lock. ``allow(key)`` returns False once the key has
     ``max_requests`` hits inside the window.
  2. The limit is **60 requests per minute per client IP**
     (``DEFAULT_SHARE_RATE_LIMIT``), overridable with
     ``SHARE_RATE_LIMIT_PER_MIN``. 60/min is far above any human reading pace
     and far below scraping throughput.
  3. It is applied to both unauthenticated share endpoints --
     ``GET /share/{token}`` and ``GET /share/{token}/artifacts/{name}`` -- and
     refuses with **429** and a retry hint when exceeded. The key is the client
     IP, because these requests carry no user identity.
  4. The limiter is process-local and therefore correct for the single-instance
     local plane only. The ``allow(key)`` interface is deliberately the whole
     contract so a multi-instance deployment can move the counter to Redis
     without touching callers (noted in ADR-0044).
  5. External-provider calls are throttled separately, at the client, to stay
     inside each provider's published guidance: PubMed 10 req/s with an API key
     and 3 req/s without, Semantic Scholar 1 req/s, OpenAlex 10 req/s (all
     minimum-interval gates), and GNPS 5 req/s (async sliding window).
- **Alternatives considered:** A fixed-window counter (rejected: it allows a
  double burst across the window boundary); a token bucket (rejected: bursts
  are exactly what gets an IP throttled, and NCBI's guidance is a steady rate);
  per-user limits on share reads (rejected: there is no user identity on an
  unauthenticated request); limiting by share token instead of IP (rejected: it
  is trivially bypassed by minting or rotating tokens, and one scraper can
  rotate); a reverse proxy or API-gateway limit only (rejected: it would not be
  testable in the local plane, and the endpoint's semantics belong with the
  endpoint).
- **Consequences:** A shared NAT (a university or a lab behind one egress IP)
  shares the 60/min budget across everyone behind it, so a large classroom on
  one IP could hit the ceiling during a synchronized exercise; the limit is
  configurable, and raising it for known institutional IPs is the intended
  remedy rather than removing the limit. The in-memory counter resets on
  process restart and is invisible to sibling instances, so it is not a
  distributed guarantee. **TODO:** no Redis-backed implementation exists yet;
  the class is the seam where one would be added. **Difference from the
  original brief:** this module does **not** rate-limit the LLM endpoint --
  LLM cost is controlled by the token allowance and the distilled tier
  (ADR-0045, ADR-0046), not by request rate. The external-API limits described
  in item 5 live in the literature and networking clients, not in this module.

---

### ADR-0050: Single-modality analyses are first-class, and say what they cannot establish

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The platform's stated principle is that a user with only NMR data
  or only MS data must get a complete, honest analysis -- not a degraded one.
  The code did not honour that. The ranker happened to tolerate missing
  modalities, but it returned no record of which modalities it had actually
  scored, so an NMR-only ranking was wire-indistinguishable from a fused one.
  Worse, the planner hard-coded `sirius.predict_formula` (an MS/MS tool) into
  both `single_compound` and `novelty_scan` and hard-coded an HSQC matching step
  into `single_compound`, so an NMR-only user's run would fail partway through
  or, for a workflow that merely tolerated the missing step, complete while
  silently omitting the mass constraint its verdicts assumed. A reviewer reading
  `verdict: tentative` had no way to see that no MS evidence existed.
- **Decision:**
  1. Observation modalities get a shared vocabulary in
     `phyto_shared.constants`: `ObservationModality` (`h_1d`, `c_1d`, `hsqc`,
     `ms1`, `ms2`), a family mapping (NMR/MS), and helpers `modality_families`,
     `is_single_family`, `single_family_gap`, and
     `parse_observation_modalities` (case-insensitive, and it REJECTS unknown
     names rather than dropping them -- a typo must not silently shrink the
     evidence set a run is planned against).
  2. Every `CandidateRank` carries an optional `CandidateEvidenceProfile`
     listing the modalities that actually produced a score (`evaluated`), those
     supplied but unusable (`unavailable`), the families covered, and a
     human-readable `gap`. Families are computed from CONTRIBUTING evidence, not
     from what the user sent: an MS2 spectrum supplied without a reference
     spectrum does not upgrade an NMR-only result to cross-modal.
  3. The single-family caveat is appended to the rank's `EvidenceGraph.gaps` as
     well as the profile. The graph is what a downstream reader (and the LLM
     explainer) consumes; a caveat that lives only in a side field is a caveat
     nobody reads.
  4. MS-only ranking is supported explicitly: MS2 plus a reference spectrum
     scores, and `precursor_mz` acts as the mass filter (ADR-0014). MS1 alone,
     or MS2 without a reference, raises `InvalidInputError` with a message
     naming the missing evidence. It never returns a rank with no support.
  5. Tasks declare `requires_modalities`; `PlannerAgent.plan` accepts the
     observed set and prunes steps that cannot run, recording each in
     `TaskGraph.pruned` with a reason. Dependents are rewired onto the nearest
     surviving ancestor. `depends_on` (ordering) is separated from `consumes`
     (real dataflow) and `consumes_any` (a join, e.g. the knowledge-graph gate
     needs whichever network was built), so pruning removes only steps that are
     genuinely starved.
  6. `AnalysisRequest.modalities` is optional and empty means "undeclared":
     planning is then unconstrained and behaviour is exactly as before. This is
     deliberate -- callers that have not declared their evidence must not be
     silently narrowed.
  7. A workflow with no runnable step for the supplied evidence is refused with
     422, not executed as an empty success. Separately, each workflow declares
     `WORKFLOW_MODALITY_REQUIREMENTS`: the modalities of which at least ONE must
     be present. This catches what per-task pruning cannot. Pruning keeps a
     connected path, but a path can reach no defensible conclusion -- a
     `novelty_scan` on MS1-only data survives as a graph and would then propose
     structures from an analogue's predicted shifts with nothing observed to
     test them against. `ms1` appears in no requirement set, because a precursor
     m/z filters candidates and never identifies one (ADR-0014).
     `compare_extracts` declares the empty set: it is feature statistics and is
     genuinely instrument-agnostic, so a precursor mass alone is enough.
- **Alternatives considered:** Requiring both modalities (rejected: it excludes
  exactly the users the principle names); inferring modalities from
  `parameters` (rejected: implicit and impossible to validate, and a missing
  parameter cannot be distinguished from an unparsed one); failing the run when
  a required step is missing (rejected: a user with good NMR data and no MS
  should still get an NMR analysis, with the gap stated); letting the ranking
  average over only available modalities without reporting them (rejected:
  that is the silent-weakening the platform forbids -- the score is honest, the
  absence of a whole instrument is not); treating a missing modality as a
  zero-scoring modality (rejected: it would penalise a candidate for evidence
  the user never had, and would make the combined score depend on data
  collection rather than structure).
- **Consequences:** Verdicts are unchanged for unchanged evidence -- this ADR
  reports coverage, it does not re-weight it. `MIN_COVERAGE_FOR_TENTATIVE` still
  governs the label, so a well-covered NMR-only match remains TENTATIVE (MSI
  Level 2), which is correct: NMR-only cannot reach Level 1 without a standard.
  The `unavailable` list is populated only for score-capable modalities;
  `ms1` is a filter, so it appears in the families covered but never in
  `evaluated`. Tasks that consume a pruned task's output are pruned too, which
  means a `novelty_scan` on HSQC-only data keeps the whole reachable chain
  (the analogue's shifts are predicted from the library structure, not from a
  SIRIUS formula) -- this is correct but worth knowing: the formula gate is
  then absent, and `evidence_gap` says so. **TODO:** COCONUT/NPAtlas formula
  enumeration from an NMR-derived formula is not implemented; an NMR-only user
  supplies the formula themselves, and the ranker's `require_formula_match`
  still fails candidates whose formula differs.

---

### ADR-0051: Commit discipline for multi-chat work

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** Two chat sessions edited this repository concurrently on
  2026-10-21. Commits e9feb29 and 7148e54 were created from one session while
  the other was adding growth-feature code to the same working tree, so their
  token-system messages describe only part of what they contain. The code is
  committed and pushes cleanly, but the history no longer tells a reader what
  each commit did. The ID here is 0051, not the requested 0049: ADR-0049 was
  already allocated to the share rate limiter, and this log allocates IDs by
  checking the existing sequence rather than assuming the next number.
- **Decision:**
  1. Every chat session commits its completed work before ending.
  2. Commit messages note the chat name, so concurrent work is attributable.
  3. The ADR sequence is checked before allocating a new ID (`grep -oE
     "ADR-[0-9]{4}" docs/DECISIONS.md | sort -u`), so a second session cannot
     silently reuse a number.
  4. No file-based lock is used for same-user multi-chat work; commit
     discipline is the control. A lock would not span sessions that start
     before it is taken, and it would add state to clean up.
- **Alternatives considered:** Rewriting e9feb29/7148e54 to split the mixed
  changes (rejected: it requires a force-push, breaks reproducibility of the
  current tree, and every existing reference to those SHAs stops resolving);
  a lockfile/lease (rejected: same-user sessions do not reliably observe one,
  and a stale lock blocks all work); leaving the history unexplained
  (rejected: the next reader would reasonably mistrust the messages).
- **Consequences:** Mixed commits stay in history and are documented as such in
  `docs/WORKING_PRACTICES.md`. Attribution now depends on authors remembering
  to name the chat, which is exactly what the rule asks of them. This ADR does
  not change any code or runtime behaviour.

---

### ADR-0052: Taxonomy-aware reranking of structural candidates

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** A ranked candidate list from spectral matching alone ignores
  the strongest prior a natural-products sample carries: the organism it came
  from. Compounds reported from the same species (or genus/family) in LOTUS,
  KNApSAcK and NPASS are far more likely a priori than database look-alikes
  with equal spectral distance, and the extraction solvent modulates which
  lipophilicity range the extract plausibly contains. The reranking must be
  additive: `candidate_ranker`'s scoring logic is validated and must not be
  touched, and a taxon prior is evidence to be weighed, never a filter.
- **Decision:** A post-ranking reranker
  (`services/elucidation/app/taxonomy_reranker.py`,
  `rerank_with_taxon_prior`) consumes the ranked list and adjusts each
  candidate's score by a bonus keyed to the strongest taxonomic level at
  which its InChIKey is reported from the sample's taxon: species +0.30,
  genus +0.15, family +0.05, no match 0.00. When the user supplied an
  extraction solvent and a logP is computable (RDKit Crippen), the bonus is
  multiplied by a smooth solvent-compatibility curve in [0, 1]
  (`shared/phyto_shared/solvent_prior.py`) — a soft multiplier with no hard
  exclusion at any finite logP. Scores keep their `ranking_convention`
  (bonus subtracts from lower-is-better distances, adds to similarities);
  the sort is stable so equal adjusted scores preserve input order. Each
  matched candidate carries a `taxon_prior` EvidenceItem (source = the
  database that supplied the prior, strength = applied bonus), a
  `taxon_prior_match` level field, and full `taxon_prior_provenance`
  (`ReportedCompound` records: database, organism, level, reference).
  Candidates with a confirmed PubMed (compound AND species) hit additionally
  get `reported_in_species_paper` evidence, which also flows to the LLM as
  context for mechanism hypotheses. When the user supplied no species or
  family, the reranker does not run: the orchestrator logs "taxon prior
  skipped: no metadata" and proceeds with the original ranking. Unknown
  solvents are a warning (filter skipped), never an error.
- **Alternatives considered:** re-weighting inside `candidate_ranker`
  (rejected: the task and the platform's modularity forbid modifying
  validated scoring); a hard exclusion of compounds never reported from the
  taxon (rejected: it would erase genuinely novel metabolites, which are the
  platform's headline use case); a fixed additive bonus independent of
  solvent (rejected: a hexane extract reporting a logP-1 glycoside is
  chemically implausible and the bonus should say so); boosting by number of
  database reports rather than taxonomic depth (rejected: report counts
  measure database coverage, not evidence strength); making the prior a
  filter on candidate generation (rejected: post-ranking is auditable and
  reversible — the evidence item states the bonus, so a reader can undo it).
- **Consequences:** A species-level report can lift a candidate past a
  slightly better spectral match — intended: two candidates within ~0.3
  distance units are spectrally indistinguishable given ShiftMPNN's error,
  and the taxon prior is exactly the independent evidence that separates
  them. `margin`, `ranking_interval` and `tied_candidates` are left
  untouched by the reranker and still describe the spectral-only ranking;
  the `taxon_prior` evidence item is the honest record of the adjustment.
  The prior is only as good as its databases (see ADR-0054): absence of a
  report is weak evidence of absence, which is why "no match" scores 0.00
  rather than negative.

---

### ADR-0053: Taxonomy resolution source — GBIF primary, ITIS fallback

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The taxon prior (ADR-0052) keys on a canonical name plus
  lineage (species -> genus -> family), so every run needs one resolver that
  returns stable taxon keys and a full hierarchy. The resolver must also fail
  honestly: a fabricated lineage would silently poison every downstream
  prior level.
- **Decision:** GBIF (`/v1/species/match`) is the primary resolver: it is a
  documented, stable, key-returning fuzzy matcher that answers with the full
  kingdom..species lineage in one request (verified live on 2026-10-22:
  exact match, confidence 99, for the pilot species). ITIS
  (`searchByScientificName` + `getFullHierarchyFromTSN`) is the fallback for
  names GBIF does not know; its "Division" rank is normalised to phylum.
  `resolve_species` raises `InvalidInputError` when NEITHER provider
  resolves the name (an empty prior must be built from an honest "no
  reports", not a guessed lineage) and `CloudServiceError` when both were
  unreachable (a network failure is not a taxonomy verdict). Lineage walks
  (`get_lineage`) use the GBIF full-record rank fields in one request.
  Live transports are injectable so resolution logic is offline-testable.
- **Alternatives considered:** ITIS primary (rejected: two requests per
  resolve and no fuzzy matching, so every typo fails); GBIF alone
  (rejected: single point of failure, and GBIF's backbone rejects some
  orthographic variants ITIS accepts); Wikidata/GBIF backbone lookups by
  free text (rejected: no rank guarantees); failing the whole workflow on
  an unresolvable name (rejected for genus/family-only runs, where nothing
  needs resolving; kept as an error when a user-supplied species name
  resolves nowhere, because that input is wrong, not merely unrecorded).
- **Consequences:** Two providers must stay behind one `TaxonomyClient`
  protocol; `MockTaxonomyClient` implements it with the real GBIF usageKeys
  so local-plane runs and tests see production-shaped data. Rate limits are
  a cloud-plane concern (GBIF is generous; ITIS has none documented);
  caching resolutions per run is future work.

---

### ADR-0054: Taxon prior databases — LOTUS, KNApSAcK, NPASS, with per-candidate provenance

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** "Compounds reported from this taxon" is scattered across
  three complementary natural-product databases with very different API
  stability: all three were probed live on 2026-10-22 — GBIF-style clean
  REST none of them has. LOTUS's documented API host
  (`api.lotus.naturalproducts.net`) refused connections; KNApSAcK's site
  redirects and exposes no stable JSON query API (its core IS a downloadable
  species-metabolite table); NPASS's web endpoints (`bcs.cmus.ac.kr:8313`)
  were unreachable. The pilot must still run offline, deterministically, and
  without network in the local plane.
- **Decision:** All three are integrated behind one `TaxonPriorClient`
  protocol (`query_taxon(name, level) -> list[ReportedCompound]`). For the
  pilot each client serves a small curated snapshot under
  `services/taxon_prior/app/data/` (compound, SMILES, organism, taxon level,
  reference; InChIKey computed from SMILES at load): this is the documented
  pilot approach for KNApSAcK (its species-metabolite core is a static
  download by nature) and for LOTUS/NPASS, whose live paths remain coded
  (LOTUS API organism query; NPASS web endpoints) and degrade to the
  snapshot on any transport error rather than failing the prior. Reports
  deduplicate by InChIKey with species-level provenance preferred over
  genus over family, and every reranked candidate carries its full
  `ReportedCompound` provenance list (which database, which organism, which
  level, which reference) — the bonus is never an unexplained number.
- **Alternatives considered:** live-API-only clients (rejected: all three
  endpoints failed the 2026-10-22 probe; the feature would be dead on
  arrival and untestable offline); one database only (rejected: coverage of
  the three overlaps only partially — e.g. the pilot's cardamonin genus
  report exists only in LOTUS while the species report exists in KNApSAcK);
  full database exports in git (rejected for the pilot: hundreds of MB;
  snapshots are replaced by a data refresh, not a code change); dedup by
  compound name (rejected: the same metabolite carries different names per
  database; InChIKey is the only stable join key).
- **Consequences:** Pilot priors are only as complete as the curated
  snapshots — a compound missing from a snapshot reads as "no report", so
  production must swap in full exports before the bonus weights mean
  anything quantitatively; the code path does not change. Snapshot
  freshness becomes an operational concern (recorded in
  `docs/DATA_SOURCES.md`). Live LOTUS/NPASS response shapes were written
  from their documentation and are unverifiable until the hosts answer;
  they are defensively parsed and never crash the prior.
  - **Production data addendum (sizes and sources).** The three pilot snapshot files carry 17 organism–compound entries total (`knapsack_snapshot.json` 42 lines, `lotus_snapshot.json` 74 lines, `npass_snapshot.json` 26 lines). Before the first pilot user, replace each with the full dump: LOTUS (~750,000 organism–compound pairs, Zenodo CC0), KNApSAcK (~50,000 species-metabolite pairs, downloadable core table), NPASS (~35,000 compounds with species source, static export). The live-API code paths remain in place and are used when the dumps are absent; the snapshots are the offline and CI fallback, not the primary source.

### ADR-0055: Minimum viable isolation record

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (MVP scoping); implemented as one increment across `shared/phyto_shared`, `services/isolation`, `services/elucidation`, `services/orchestrator`.
- **Context:** Every phytochemist fractionates an extract, spots TLC plates, and weighs the isolated compound — but the platform treated structure elucidation as if the sample fell from the sky. The bench observations that constrain a candidate list (which fractions a compound elutes in, which TLC reagents fire, how much material was recovered) were nowhere in the data model, so the rerankers could not use them. The temptation is to model the full isolation graph (sub-fractionation as first-class edges, TLC plate images, cross-lab comparison) before anyone has used the simple version; that is how a platform ships a graph nobody populates.
- **Decision:** Ship the minimum viable isolation record (ADR-0055):
  * `phyto_shared.isolation` defines `Fraction`, `TLCResponse`, `TLCObservation`, `IsolationCompound`, `IsolationRecord` — fractionation, TLC, and yield as structured Pydantic models, validated at the boundary (Rf 0–1, purity a fraction, fraction references checked).
  * Reagent responses are TAGGED, not free text: a closed `TLCReagent` vocabulary (Naturstoff, Dragendorff, vanillin_H2SO4, Liebermann_Burchard, FeCl3, UV254/365, ninhydrin, anisaldehyde) with response kinds `positive_strong / positive_weak / negative / absorbing / fluorescent`. Colour is recorded as optional free text but only reagents whose hint is colour-dependent (vanillin) read it, and only taught colours (yellow; pink/red) carry weight. An untaught colour is an explicit no-signal, never a guess.
  * Fraction polarity is a DECLARED tier (`very_nonpolar … very_polar`), not a solvent name: solvents vary across labs, tiers do not. The solvent string is kept for provenance.
  * `phyto_shared.isolation_prior` turns the record into heuristic priors: `polarity_from_fractions` (tier estimate + confidence falling with the elution spread), `class_from_tlc` (ranked class list from reagent responses), `abundance_from_yield` (% w/w). Every function is documented as a prior, not a measurement.
  * The isolation reranker (`elucidation.isolation_reranker`) adjusts candidate scores: polarity match +0.15 (confidence-scaled) / contradiction −0.25; TLC class in the predicted set +0.20 / no overlap with a non-empty list −0.30; flash-UV λmax inside the chromophore band +0.15 / outside −0.30. One `EvidenceItem` per contributing signal and a separate `isolation_bonus` field so every adjustment is visible and undoable. Bonus/penalty never force a verdict — the spectral ranker already excluded what needed excluding. The layer runs after the UV/RT signals in the spectral ranking and BEFORE the taxon prior reranker: bench isolation of THIS sample is more specific than what the species is generally reported to contain.
  * The identify workflow plans an optional `load-isolation-record` task when the user supplies an `extract_id` with a record; without one the layer is skipped with the visible note "isolation layer skipped: no record". Structure prediction (logP tiers, glycoside SMARTS, chromophore bands) is documented heuristic calibration, and unparseable SMILES predict nothing rather than contradicting.
  * The isolation service (`services/isolation`) persists records per user — Firestore in the cloud plane, in-process locally — with per-user access control enforced through the existing auth middleware and re-checked in every store read.
  * DEFERRED until the MVP is validated by real users: the full isolation graph (sub-fractionation as a first-class edge structure), TLC plate image upload, and cross-lab comparison.
- **Alternatives considered:** (a) Free-text bench notes parsed by an LLM — rejected: the same observation ("Naturstoff positive, yellow spot") becomes structured evidence only if it enters the system structured; parsing is a lossy layer that can silently disagree. (b) Solvent names as the polarity key — rejected: "EtOAc fraction" means different things in different labs and in normal- vs reversed-phase practice; the tier is the portable fact. (c) Hard filters (e.g. a Dragendorff-positive compound excludes non-alkaloid candidates) — rejected: TLC reagents are suggestive, not specific (Dragendorff fires on some lactones; FeCl3 on any decent phenol), and a hard filter would let a weak bench observation veto a strong spectral match. (d) Building the full isolation graph now — deferred per the MVP framing above.
- **Consequences:** Bench observations become ranking priors with full evidence provenance; the frontend evidence panel can render the isolation chain alongside UV/RT/taxon evidence. The MVP vocabulary (reagents, response kinds, tiers) is closed, so supporting a new reagent or response kind is a deliberate model change. Unmeasured honesty: the priors are calibrated on common natural products and a handful of literature windows — a candidate whose class has no chromophore band is not UV-cross-checked, and fraction tiers are the user's declaration, not a measurement. The full isolation graph remains future work; the record's `isolation_chain` field (free list of parent fraction ids) preserves enough provenance for that migration without committing to an edge schema now.

### ADR-0061: Home-library reference standards — Level-1 matching and RT offset calibration

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented as one increment across `shared/phyto_shared`, `services/calibration`, `services/orchestrator`, `services/elucidation`.
- **Context:** A phytochemist's first question about an unknown peak is not "what does the NMR say?" but "is this one of MY standards?". Every lab runs authentic reference standards under its own LC method, and the platform had no place to put them: the identify workflow ranked candidates on spectra alone, and an observed RT or PDA spectrum was dropped because nothing could use it. The MSI Level-1 convention (identification confirmed by an authentic standard on orthogonal properties) was unreachable, and the systematic gap between the retention model's predicted RT and the user's own method had no calibration handle.
- **Decision:** Add a per-user, per-LC-method home library of reference standards:
  * `shared/phyto_shared.chromatography` defines `LCMethod` (column phase, gradient, temperature, flow — no default method exists on purpose), `ReferenceStandard` (name, SMILES, observed `rt_min`, optional UV spectrum and MS2), `uv_cosine` and `ms2_cosine` (GNPS-style similarity, shared contract with the elucidation ranker).
  * `services/calibration` stores standards keyed `(user, method_id)` behind a `HomeLibraryStore` protocol (in-process locally, Firestore in the cloud plane at `users/{uid}/methods/{method_id}/standards/{name}`). **Method isolation is structural**: every lookup is keyed by (user, method), so standards from one method can never match peaks run under another.
  * **Level-1 matching** (`POST /calibrate/match`): an observed peak matches a stored standard when EVERY supplied property agrees within tight tolerances — RT ±0.1 min, UV cosine > 0.95, MS2 cosine > 0.85 (at least one property must be checkable; a UV-less standard can still match on RT + MS2). A full match is stamped on the candidate as an MSI Level-1 identification with `strength 1.0`.
  * **RT offset calibration**: the difference between a standard's observed RT and the retention model's predicted RT is computed per (user, method) and applied to every other candidate's predicted RT in the same method — the delta absorbs the systematic method-vs-model gap instead of poisoning every comparison.
  * In the layer chain the standard matched BY PROPERTIES names a structure; the rank it applies to is found by InChIKey join on the candidate SMILES, so a Level-1 stamp can never attach to a different structure than the one the standard certifies.
- **Alternatives considered:** (a) Free-text "my library" spreadsheet upload parsed at query time — rejected: the match is only as trustworthy as the parse; standards enter structured or not at all. (b) Global (cross-user, cross-method) standard library — rejected: RTs are method-specific by nature and a shared library would invite cross-method matching, the exact failure the structural keying prevents. (c) Matching on name similarity — rejected: the same compound carries different names per vendor; the join keys are structure (InChIKey) and orthogonal properties, never strings.
- **Threshold basis (narrow by design):** the Level-1 gates are ASSERTED, NOT DATA-DERIVED: RT ±0.1 min, UV cosine > 0.95, MS2 cosine > 0.85.  These form an intentionally NARROW box -- Level 1 is a claim that an authentic standard reproduces the peak on every orthogonal channel, so loose tolerances would cheapen the strongest evidence tier the platform issues.  On real plant extracts (complex matrices, partial co-elution, noisy MS2) Level-1 matches will therefore be RARE; that is the honest behaviour, not a defect.  The chain summary reports `top_candidate_msi_level` (1 with a standard match, otherwise 2 -- the ranker's putative-annotation baseline) so "no Level-1" can never be read as "no match": the Level-2/3 attribution path is always surfaced in the report.
- **Consequences:** Users gain the strongest possible evidence tier once they have run standards, and the platform can finally consume observed RT/UV/MS2 for the peak. The library's coverage is the user's own bench work, so the layer is skipped — visibly, "no reference standards" — for users who have none. Level-1 stamps are only as honest as the stored standards' metadata; a standard entered with a wrong SMILES certifies the wrong structure, which the InChIKey join makes visible but cannot prevent.

### ADR-0062: UV chromophore classification from PDA spectra

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented across `shared/phyto_shared`, `services/elucidation`, `services/orchestrator`.
- **Context:** HPLC-PDA runs produce an absorbance spectrum per peak, and sixty years of phytochemistry know what a flavonol's Band I/Band II look like (Mabry 1970; Harborne 1984) — but the platform discarded the PDA spectrum because the ranking pipeline was NMR/MS-only. The UV layer is the cheapest orthogonal evidence there is: the PDA detector is already on the instrument, and a flavonoid-class candidate contradicted by a terpene-like spectrum is wrong in a way the NMR score cannot see.
- **Decision:** Add a rule-based UV classifier and a post-ranking reranker:
  * `phyto_shared.uv_chromophore.classify_uv` reads `[(wavelength_nm, absorbance)]` and returns one of 13 classes (flavone, flavonol, flavanone, isoflavone, chalcone, anthocyanin, coumarin, phenolic acid, isoquinoline and indole alkaloid, xanthone, terpene-like, unclassified) with a confidence in [0,1] and diagnosis notes. Band I = highest local maximum above 300 nm, Band II = highest in 240–300 nm; visible absorption above 450 nm is the anthocyanin signal; the rule windows follow Mabry/Harborne widened for solvent shifts (5–15 nm). `predict_class_from_smiles` is the structural counterpart for candidates.
  * `elucidation.uv_reranker.rerank_with_uv` adjusts scores ADDITIVELY (never touching `candidate_ranker`): observed class in the candidate's predicted classes +0.20, directional incompatibility −0.30, undeterminable 0.00. Bonuses respect each rank's `ranking_convention` (ADR-0008) — a bonus ADDS to higher-is-better and SUBTRACTS from lower-is-better.
  * A low-confidence observed classification (below 0.6) is a no-op: a shaky class must not move scores. A small documented equivalence whitelist (flavone/flavonol share the chromen-4-one core; phenolic_acid shares the hydroxycinnamate chromophore) prevents adjacent classes from reading as contradictions; the classifier itself flags flavone-vs-flavonol and coumarin-vs-phenolic-acid ambiguity in its notes rather than guessing.
  * The layer is skipped — visibly — when no PDA spectrum was supplied.
- **Alternatives considered:** (a) Learned classifier (CNN on PDA spectra) — rejected for the pilot: no curated public training set was available, and the rule set is auditable line by line, which a learned model is not. (b) Hard class filter (a flavonoid spectrum excludes non-flavonoid candidates) — rejected: the classifier is a screening tool with documented window overlaps (flavone/flavonol near 345–355 nm); a hard filter would compound its edge-case errors into false negatives. (c) Feeding raw spectra into similarity scoring against predicted spectra — rejected: predicting absorbance spectra from SMILES is unreliable; class-level comparison is the robust granularity.
- **Threshold basis (provisional):** all numeric gates in this decision are ASSERTED, NOT DATA-DERIVED -- the repo holds no annotated known-pure/known-class PDA corpus to calibrate them against.  In use: confidence floor 0.6 (a classification below it moves nothing), match bonus +0.20, directional contradiction penalty −0.30, UV λmax comparison tolerance 15 nm (derived from the Harborne solvent-shift statement of 5-15 nm, i.e. literature-informed, not fitted).  They are screening thresholds, chosen conservatively (shaky classifications no-op, contradictions capped at −0.30 so a single UV disagreement cannot bury a strong spectral match) and should be recalibrated when a labelled PDA corpus is available.  Provisional until then.
- **Consequences:** Every PDA-bearing run gains a visible chromophore evidence item per candidate. The classifier is calibrated on the classic flavonoid/coumarin literature; alkaloid and terpene classes are coarse (terpene-like really means "no useful UV chromophore"). Solvent and pH shift λmax by 5–15 nm and the 15 nm tolerance in every UV comparison is the honest reflection of that. The confidence gate means weak or overlapped spectra silently contribute nothing — reported as a skip, never as a match.

### ADR-0063: Retention-time prediction — method-scoped, soft by design

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented across `shared/phyto_shared`, `services/retention`, `services/elucidation`, `services/orchestrator`.
- **Context:** Observed RT was unusable without a prediction to compare it against. RT prediction is HIGHLY method-dependent: a model trained on one column/gradient/temperature pair must not be presented as an RT oracle for another method. And a 2025 literature check (Malm & Kruve) found RT prediction underperforms RT projection in some regimes, meaning a hard RT gate would compound a known weakness into false negatives. Public natural-product-specific RT training data surveyed 2026-10-22 was too sparse to fine-tune on without overfitting.
- **Decision:** Add a retention service and a soft RT reranker:
  * `services/retention` serves `predict_rt(smiles, method)` where every `RetentionPrediction` carries the `method_id` it is valid for plus an honest interval. Production model lineage is documented: METLIN SMRT (~80k compounds, open) as base training data; NP fine-tuning deferred. The pilot ships (a) a hand-curated 20-compound lookup on the reference gradient (30-min 5→95% C18) and (b) a logP descriptor baseline with a deliberately wide interval; a user method that differs materially from the reference gradient gets the wide baseline rather than silently transferred lookup values.
  * `elucidation.rt_reranker.rerank_with_rt` adjusts scores ADDITIVELY: |predicted − offset-calibrated − observed| > rt_tolerance → −0.25; within tolerance/2 → +0.15; else 0.00. **RT never excludes a candidate** — scores move, and every movement is attached as evidence naming the prediction that caused it.
  * The home-library RT offset (ADR-0061), when available, is applied to every candidate's predicted RT before comparison.
  * The layer is skipped — visibly — without `lc_method` or an observed RT.
- **Alternatives considered:** (a) Hard RT filter — rejected per the Malm & Kruve finding: a wrong prediction would silently delete the true candidate, the worst failure mode a ranking aid can have. (b) Fine-tuned GNN now — rejected: the 2026-10-22 survey found public NP RT data too sparse; shipping it would overfit; the model id/version plumbing is in place for when it lands. (c) One global method ("just use 30-min C18") — rejected: predictions outside their training method are fiction; the method_id scoping makes transfer explicit instead of implicit.
- **Consequences:** Observed RT finally moves scores, visibly, with its prediction attached. The pilot's RT numbers are literature-typical, not laboratory-grade — the ±0.6 min lookup interval and the wide baseline interval say so. RT predictions remain valid ONLY for the method they were made under, and the service refuses to pretend otherwise. When a fine-tuned model replaces the lookup, the version string changes and the fallback stays.

### ADR-0064: Retention-index normalization via co-injected anchor series

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented across `shared/phyto_shared`, `services/elucidation`, `services/orchestrator`.
- **Context:** RT is method-bound (ADR-0063), but phytochemists compare runs across labs, columns and months. The established fix (Aalizadeh et al. 2022) is a retention INDEX: co-inject a homologous series (e.g. the cocamide diethanolamide homologues), let each homologue's RT define an axis point indexed by chain length, and interpolate every peak's RI = 100·n + 100·(RT − RT_n)/(RT_n+1 − RT_n) between bracketing homologues. RI is portable across methods in a way RT can never be. The feature is opt-in by nature: it requires the user to have co-injected anchors.
- **Decision:** Add RI math and an RI reranking path:
  * `phyto_shared.chromatography` defines `AnchorStandard` (chain length + observed RT) and `AnchorCalibration` (validated monotonic), plus `rt_to_ri` / `ri_to_rt` piecewise-linear interpolation and `build_anchor_calibration`. Fewer than two anchors raises `InvalidInputError` — a one-anchor "RI" is not an axis.
  * `CandidateRank` gains `ri_used`: when anchors were supplied the RT reranker compares retention INDICES instead of RTs, ignoring the RT offset (it is absorbed by the RI axis), and the evidence names the RI basis.
  * In the layer chain the RI path replaces the RT path whenever `anchor_series` has ≥ 2 members; with fewer anchors the chain falls back to method-specific RT (ADR-0063) rather than guessing an axis.
- **Alternatives considered:** (a) Universal RT scaling factors from column literature — rejected: less accurate than an in-run axis and silently method-dependent, the exact problem RI solves. (b) Always-on synthetic anchors (simulated homologues) — rejected: a fabricated axis is worse than none; the anchors must be REAL injections. (c) Krz-Marquart or cubic-spline interpolation — deferred: piecewise-linear between bracketing homologues is the Aalizadeh convention, auditable, and adequate for the anchor densities real runs produce.
- **Consequences:** Cross-method and cross-lab RT comparison becomes possible when the user invests in anchors, and the RI number is reproducible from the stored anchors alone. The layer stays honestly opt-in: without anchors the chain reports "RI skipped" and falls back to RT, so no user is forced to buy a standard series to use the platform. RI windows in curated data (e.g. pharmacopoeial markers) can now be quoted against a portable axis instead of one lab's gradient.

### ADR-0065: Peak purity — co-elution detection forces AMBIGUOUS below threshold

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented across `shared/phyto_shared`, `services/orchestrator`.
- **Context:** A peak that looks clean in the chromatogram can still be a co-elution, and an NMR identification made on a contaminated peak silently describes a mixture. Two cheap orthogonal signals expose it: UV spectral drift across the peak (a pure peak has the same chromophore at apex and edges; a mixture drifts) and mass-channel misalignment (a shoulder carrying a different m/z than the apex is a different compound).
- **Decision:** Add `phyto_shared.peak_purity.peak_purity` and enforce its consequence:
  * The score is 0–1: UV agreement across apex/left/right shoulders weighted 0.7, apex-vs-shoulder m/z alignment weighted 0.3 (aligned 1.0 / drifting 0.6 / misaligned 0.2; missing channels 0.8 — unconfirmed, not failed). The MS tolerance is max(0.05 Da, 50 ppm).
  * Below `PURITY_AMBIGUITY_THRESHOLD` (0.5) the layer chain FORCES the verdict to AMBIGUOUS on every rank, appends the caveat "Peak appears to be a mixture; identification may refer to the dominant component" to the evidence graph gaps, and stamps a `peak_purity_override` evidence item — the override is an action with provenance, not a hidden penalty.
  * The score is stamped on every rank (`purity_score`) regardless of threshold, so a clean pass is also visible.
- **Alternatives considered:** (a) Score-based soft penalty only — rejected: a contaminated peak is not a slightly worse identification, it is a different claim ("dominant component of a mixture"); the verdict must say so. (b) Full purity algorithms (spectral deconvolution, apex-vs-apex MS/MS matching) — deferred: the two cheap signals catch the common case and never mislead; deconvolution can come later behind the same score field. (c) Treating missing MS channels as failure — rejected: an NMR+UV run with no MS shoulder still yields meaningful UV drift evidence; unconfirmed (0.8) is the honest middle.
- **Threshold basis (provisional):** the weights and the threshold are ASSERTED, NOT DATA-DERIVED -- the repo holds no known-pure / known-co-eluting standard corpus to compute a score distribution against (checked 2026-10-22: `tests/fixtures/` and `data/` contain no purity ground truth).  In use: UV agreement weight 0.7, MS alignment weight 0.3, alignment tiers 1.0/0.6/0.2 aligned/drifting/misaligned, missing channels 0.8, MS tolerance max(0.05 Da, 50 ppm), and the forced-AMBIGUOUS threshold **0.5**.  The 0.5 choice: it sits clearly BELOW the worst fixture a well-aligned shoulder produces (the MS-only misalignment fixture in `tests/test_peak_purity.py` scores 0.585 = 0.7×0.75 + 0.3×0.2, which must NOT force AMBIGUOUS because the UV channel is silent) and clearly ABOVE a genuine two-signal co-elution (drifted UV + misaligned m/z ≈ 0.2-0.4).  It is a coarse single constant by design and should be recalibrated on real labelled runs; provisional until then.
- **Consequences:** Identifications made on mixed peaks can no longer parade as confident — the AMBIGUOUS verdict with the caveat travels into the report. The threshold (0.5) is a constant, deliberately coarse; users see the score itself and can judge 0.55 for themselves. Purity is computed upstream (whoever holds the chromatographic data) and enters the chain as a number — the chain enforces the consequence, it does not own the raw peak shapes.

### ADR-0066: Pharmacopoeial marker prior — species authenticity as expectations, not proof

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Platform owner (analytical-chemistry scope); implemented across `services/taxon_prior`, `services/orchestrator`, `data/`.
- **Context:** Pharmacopoeias (IP, USP, Ph. Eur., ChP) define quality-control markers for medicinal plants: named compounds with expected RT windows and UV maxima under reference conditions. For a user who declares "this sample is Curcuma longa" and has NO reference standards in the home library (ADR-0061), those monograph entries are the only available chromatographic expectation — a free prior that says what the literature says this species's QC markers look like. The temptation to reverse the logic — marker hit "proves" the species — is exactly backwards and had to be designed out.
- **Decision:** Add a curated marker file and a marker-evaluation layer:
  * `data/pharmacopoeial_markers.json`: 26 species, each with named markers carrying SMILES, expected RT range (min, against the documented reference gradient), expected UV maxima (primary/secondary, nm), and a per-entry `reference` citing the pharmacopoeial source. Invalid entries are dropped at load with a logged reason, never propagated; a missing file degrades to "no markers available" rather than failing.
  * `taxon_prior.markers` evaluates each marker against the observed peak: RT inside the window ± 0.5 min grace, UV maximum within 15 nm of any expected maximum. Checks with no input stay `None` — an unchecked expectation is never reported as met.
  * The layer chain attaches the marker check as EVIDENCE to every rank (the check is about the PEAK/species authenticity, not about one candidate): supporting markers +0.30 strength per evidence item, a small +0.05 score bonus when any marker supports, and each item carries its pharmacopoeial reference so the report cites the monograph per marker.
  * **Honest scope (the design-out):** markers are EXPECTATIONS, not observations. A marker hit supports the species-level identity claim weakly (+0.05). Neither ever flips a candidate verdict on its own.
  * **Miss-penalty invariant (enforced in code):** the miss penalty for an UNFLAGGED marker is 0.0 -- strictly smaller in magnitude than the +0.05 support bonus (``MARKER_MISS_PENALTY_UNFLAGGED = 0.0 < MARKER_SUPPORT_BONUS = 0.05`` in ``taxon_prior.markers``; the layer chain asserts the flagged penalty 0.10 exceeds the bonus).  Only a marker explicitly flagged ``expected_in_matrix`` (the monograph's passing criterion for the analysed matrix) may take the larger -0.10 penalty when absent.
  * **Chemotype-variation caveat:** a GENUINE sample can lack a monograph marker, for reasons the platform cannot see from the chromatogram alone: (i) seasonal and ontogenetic variation -- marker accumulation is growth-stage- and harvest-dependent (chemotypes within one species differ in their dominant marker); (ii) extraction selectivity -- the user's solvent and protocol may not recover the marker the monograph's assay extracts; (iii) co-elution -- the marker may be present but unresolved from the matrix under the user's method, so the observed peak is a blend.  Therefore a marker miss is NEVER treated as proof of absence: unflagged misses carry zero penalty and are surfaced as evidence with strength 0.0, and only the ``expected_in_matrix`` flag -- a deliberate curation act recorded per entry -- licenses the larger penalty.
- **Alternatives considered:** (a) Scraping pharmacopoeial PDFs at runtime — rejected: licensing (monographs are copyrighted) and parsing fragility; the curated file carries explicit per-entry references instead. (b) Marker hit as a hard species-identity certificate — rejected: an RT window met is consistent with many species; the prior supports, never proves. (c) Folding markers into the taxon prior databases (ADR-0054) — rejected: different epistemics — taxon priors say what a species is REPORTED to contain, markers say what a pharmacopoeia EXPECTS to find under QC conditions; conflating them would corrupt both provenance chains.
- **Consequences:** Even standard-less users get a visible literature-anchored chromatographic check when their species has a monograph. The marker file is the maintenance burden: entries are reference-gradient-relative (documented in `REFERENCE_GRADIENT`, ADR-0063), so users on very different methods get grace-band-limited comparisons and the report's RT wording stays honest. Species without monographs fall through with a visible "no curated markers" skip. The licence status of each cited monograph must be re-verified when the file is refreshed (see DATA_SOURCES.md).

### ADR-0056: Public-by-default data tiering with proprietary escalation

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The platform will launch globally and accept contributions from
  users in many jurisdictions. A future proprietary data asset requires day-one
  provenance labelling, a ToS that grants the platform commercial rights, and
  ABS-clear collection for any data that ever enters the proprietary tier.
  Publishing openly does not extinguish ABS obligations; the obligation
  attaches to the physical sample, not the data licence. Separately, NP-MRD
  data is licensed CC-BY-NC by its depositors and cannot be used in any
  commercial context.
- **Decision:** Three data tiers — public (CC0), proprietary (licensed), and
  training_only (internal use, unpublished). Every spectrum and isolation
  record is labelled with its tier and ABS status; every provenance manifest
  carries both; the provenance validator rejects proprietary-tier analyses
  that use non-commercial sources; isolation records default to training_only
  and can only be promoted by curators. The revenue model is software,
  services, and institutional licenses — not data sales in the near term. The
  proprietary tier is a preserved option, not the primary business.
- **Alternatives considered:** Publish everything under CC0 with no commercial
  path (rejected: forfeits optionality that costs nothing to preserve); treat
  all data as proprietary from day one (rejected: contradicts the global
  academic launch and the trust-building that public registries depend on);
  rely on "publish then license later" (rejected: not legally coherent; a CC0
  publication cannot be re-licensed).
- **Consequences:** Every contribution carries a small labelling burden (tier
  + ABS status + source dataset) at upload. Most data will default to
  training_only for the first year; that is acceptable. Public-tier is the
  trustworthy open registry that builds reputation. Proprietary tier is
  dormant until an ABS agreement and a substantial corpus exist. The ToS must
  be finalized with a lawyer before the first external user; the
  DATA_GOVERNANCE.md checklist is the brief.

### ADR-0068: NMR preprocessing provenance — every parameter recorded as evidence, implemented in-house

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner (metabolomics layers pass, Phase 1 approval)
- **Context:** The metabolomics service had statistics (PCA, PLS-DA, univariate) but no NMR spectral preprocessing: apodisation, zero-filling, FFT, phase correction, chemical-shift referencing, baseline correction, alignment and PQN normalisation were assumed to have happened elsewhere, invisibly. The 2025 NMR-reproducibility discussion (cf. the Metabolomics Standards Initiative precedent) is unambiguous: a bucketed peak matrix without its preprocessing parameters is not reproducible — two labs reporting "the same" matrix may have applied different line broadening, phase and baseline treatment and produced different chemistry. Meanwhile `nmrmetaproc` is not installed in the project venv (verified in Phase 1 reconnaissance) and the no-install-without-approval constraint applies; `nmrglue` is likewise absent. R 4.6.1 with `metabom8` IS on PATH but is GPL-licensed and the platform is Apache-2.0 (the same contamination logic as ADR-0018 for mixOmics/DIABLO).
- **Decision:** Implement the standard preprocessing pipeline in-house on numpy + scipy, as a new `services/nmr_preproc` service, and record EVERY parameter of every run in a `PreprocessingRecord` (shared/phyto_shared/metabolomics.py) attached as named evidence (`nmr_preprocessing_applied`):
  * exponential apodisation with explicit line broadening LB (Hz);
  * zero-filling to an explicit factor;
  * FFT (magnitude or absorption mode recorded);
  * zero- and first-order phase correction (auto method; phases applied are stored in the record);
  * chemical-shift referencing against TSP/DSS with the applied shift stored;
  * asymmetric least squares baseline correction (Eilers-style asLS, λ and p stored);
  * whole-spectrum cross-correlation alignment against a reference spectrum;
  * PQN normalisation (Dieterle 2006, same implementation family as the metabolomics service's `normalize`);
  * bucketing to an explicit bin width with excised regions recorded.
  The 2025 reporting-guideline minimum fields (apodisation function and LB, zero-fill factor, phase method, referencing standard, baseline method and parameters, alignment method, normalisation method, bucket size, excised regions, solvent, field strength, pulse program) are exactly the fields the `PreprocessingRecord` carries and the ADR-0072 checklist audits. A non-FID input (a feature table, or a Bruker directory without usable `fid`/`pdata/1/1r`) emits `nmr_preprocessing_skipped` with a specific human-readable reason and passes through unchanged — never a silent fallback.
- **Alternatives considered:** (a) Wrap `nmrmetaproc` — rejected: not installed, and installation requires explicit owner approval (Phase 1 finding); (b) shell out to R `metabom8` preprocessing — rejected: GPL dependency in the request path of an Apache-2.0 service, plus a subprocess boundary that hides parameters the ADR exists to expose; (c) record parameters in prose documentation — rejected: documentation rots and is not queryable per-run; evidence items are.
- **Consequences:** The in-house pipeline is defensible only because each algorithmic step is documented here and tested against data with known ground truth (the Curcuma longa storage fixture: known peak positions from its peak-assignment table, plus a synthetic-FID test with known positions/integrals). Auto-phasing and correlation-based alignment are the weakest links (documented as such in the service docstring); they are stored parameters, so a reviewer can detect and override them. The service joins the tool registry as `nmr_preproc.preprocess` with the same skip-visibility contract as every other layer.

### ADR-0069: LC-MS preprocessing adapter policy — adapter only, never a reimplementation

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner (metabolomics layers pass, Phase 1 approval)
- **Context:** Feature detection for LC-MS belongs to mature external tools (XCMS, MZmine 3, MS-DIAL); reimplementing their peak picking would be both an enormous engineering burden and scientifically inferior. But each tool exports a different CSV schema, and silently guessing column meanings across schemas is how m/z and RT columns get swapped. The platform already accepts an MZmine-batch path in the metabolomics service's `MZmineDetector`; what is missing is a normalising front door for already-exported feature tables from all three tools.
- **Decision:** New `services/lcms_preproc` service is an ADAPTER only: it ingests exported feature tables from XCMS, MZmine 3 and MS-DIAL, accepts wide (features as rows, samples as columns) or long (sample/feature/intensity rows) format, and normalises them to the internal layout (feature_id, mz, rt, per-sample intensities) with per-sample metadata. Recognition is by header signature; a file recognisable as none of the three exports yields `lcms_preprocessing_skipped` with the reason AND the list of expected schemas — a named rejection, not a parse error. A file recognised but missing a required column is rejected with the exact missing-column reason. Normalisation on ingest is pass-through by default; TIC and sum are opt-in and recorded in the `PreprocessingRecord` (ADR-0068 model) together with the tool name/version and the tolerances found in the file header (recorded as found, never invented). No peak picking, no RT/mz re-alignment, no gap filling — ever.
- **Alternatives considered:** (a) Reimplement minimal centroiding/peak picking — rejected: violates the adapter scope and would produce worse features than the tools users already trust; (b) accept only MZmine exports (extend the existing `MZmineDetector` parse) — rejected: XCMS and MS-DIAL are equally standard in the field; the adapter is three small parsers, not three peak pickers; (c) require a fixed house upload schema and make users convert — rejected: moves the guessing from code to users.
- **Consequences:** The adapter's correctness burden is header-signature coverage; each of the three schemas gets a fixture test with an exact-column export and a missing-column rejection test. Tool-version strings are taken from the file when present (else `None` — the checklist in ADR-0072 will flag the absence rather than fabricate "unknown version").

### ADR-0070: O-PLS regression for continuous Y and VIP as first-class evidence

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner (metabolomics layers pass, Phase 1 approval)
- **Context:** The existing supervised model is PLS-DA (categorical Y) — unusable for the field's standard regression questions (storage time, dose, concentration), the Curcuma fixture being exactly such a design (storage_day 0–28 as a continuous covariate). `pyopls` is not installed (Phase 1 finding) and installation requires approval, so wrapping is unavailable. ADR-0015 keeps OPLS-DA absent: the discriminant analysis offered by Sartorius's commercial software is not shipped. The distinction that matters: this pass adds O-PLS REGRESSION on a continuous covariate, implemented from the published NIPALS description (Trygg & Wold 2002), for internal academic research use, at the owner's explicit direction; the discriminant-analysis refusal stands unchanged, and PLS-DA remains the substitute of record for categorical Y (with `pls_da_concat` for multi-block fusion, ADR-0018). The patent-risk reading and this carve-out are recorded here deliberately, not buried.
- **Decision:** Add `opls_regression.py` to the metabolomics service: NIPALS O-PLS (Trygg & Wold 2002) — one predictive component via PLS1 on X and centred continuous y, then systematic Y-orthogonal components extracted from the residual and deflated, with predictive/orthogonal scores and loadings returned separately. Validation matches the Curcuma reference pipeline (`metabom8` opls defaults): Q2 by balanced Monte Carlo cross-validation (k=15 repetitions, 2/3 train split, deterministic seed), permutation test with n=100 default (p = (1 + exceedances)/(1 + n)); a model whose permutation p exceeds threshold or whose Q2 < 0 emits `model_validation_failed` evidence with the numbers, not a quietly passed model. Too few samples or a constant covariate emits `statistical_model_skipped` with the reason. Continuous Y never silently falls back to PLS-DA. VIP becomes a first-class output (`vip.py`): the existing PLS VIP formula (statistics.vip_scores, sum-of-squares weighting) generalised to the O-PLS predictive model; threshold 1.0 by default (configurable), `passes_threshold` per feature, paired with univariate FDR/p where a test was supplied and an explicit `pairing_note` when not; an empty selection emits `vip_selection_empty` with the reason. The VIP>1 convention's known limitation (score scales with model dimensionality and is not a p-value) is stated in the module docstring.
- **Alternatives considered:** (a) Wrap `pyopls` — rejected: not installed (Phase 1), approval required to change that; (b) discretise continuous Y and use PLS-DA — rejected: destroys the regression design (the Curcuma half-life question becomes unanswerable), and the binning choice would be an undocumented degree of freedom; (c) shell out to R `metabom8::opls` — rejected: GPL subprocess in the request path (ADR-0018 logic); acceptable only as a test-time cross-check, which is how the Curcuma reference script remains useful.
- **Consequences:** The platform gains the regression complement to PLS-DA with full parameter provenance; the existing test pinning OPLS-DA's absence (`test_opls_da_not_in_public_api`) is deliberately narrowed — by this ADR — to assert that no OPLS-DA (categorical/discriminant) callable exists while permitting `opls_regression` for continuous Y; the assertion change is cited to this ADR in the test. In-house NIPALS correctness is pinned by Curcuma-fixture tests (Q2 > 0, permutation p < 0.05, predictive-score correlation with storage_day > 0.9), not by trust.

### ADR-0071: STOCSY as an evidence layer — correlations are hypotheses, never assignments

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner (metabolomics layers pass, Phase 1 approval)
- **Context:** STOCSY (statistical total correlation spectroscopy) is the standard NMR metabolomics tool for discovering structural correlations: a driver peak's intensity vector across samples is correlated against every other bucket, revealing signals from the same molecule (positive r) or coupled degradation behaviour (negative r). The temptation it must be designed against: treating a strong correlation as a chemical assignment. It is not — a correlation is a statistical association that GENERATES the hypothesis "these signals may share a molecule or pathway", which chemical confirmation (spiking, isolation, 2D NMR) must then test.
- **Decision:** Add `stocsy.py` to the metabolomics service, emitting `stocsy_correlation` evidence: 1D driver-peak STOCSY (driver bucket's cross-sample vector vs every bucket) and 2D all-pairs STOCSY (every qualifying bucket pair). Statistics: Pearson r per bucket, t-statistic with n−2 df, two-tailed p, Benjamini-Hochberg FDR (same `false_discovery_control` as the rest of the service). Reporting threshold |r| ≥ 0.7 AND FDR ≤ 0.05 (both configurable); correlations below threshold are not emitted as evidence. Minimum three spectra; fewer emits `stocsy_skipped` with the reason. Every emitted evidence item's text carries the standing warning that correlations are hypotheses requiring chemical confirmation, not assignments.
- **Alternatives considered:** (a) Emit all correlations with flags — rejected: thousands of sub-threshold pairs would bury the signal and dilute the evidence graph; thresholds are the field convention; (b) mutual-information or rank correlation — rejected: the standard is Pearson with these thresholds; changing the statistic changes the thresholds' meaning; (c) omit the warning boilerplate once documented — rejected: each evidence item must be self-describing because items are read individually.
- **Consequences:** The Curcuma fixture gives the ground-truth test: the oxidation-product driver at ~7.55 ppm must anti-correlate with the curcuminoid buckets (~3.85, ~5.95, ~6.75 ppm) at |r| ≥ 0.7 and FDR ≤ 0.05, and two synthetic spectra with independent signals must produce no false correlations. The all-pairs mode is O(buckets²) — bounded by the bucket width (default 0.04 ppm ⇒ ~300 buckets over 12 ppm) and documented as such.

### ADR-0072: Analytical reporting checklist as evidence, with visible gaps

- **Date:** 2026-10-22
- **Status:** accepted
- **Deciders:** Project owner (metabolomics layers pass, Phase 1 approval)
- **Context:** The 2025 NMR-reproducibility discussion and the MSI precedent define what a metabolomics report must state (acquisition, preprocessing, statistics). Recording those fields in documentation is not enough: a run's report is generated per analysis, so completeness must travel with the run. A chain that produced 7/7 evidence items at 60% reporting completeness is scientifically different from one at 100%, and the platform must be able to tell them apart — silently defaulting a missing field ("unknown spectrometer") is fabrication by omission.
- **Decision:** The metabolomics layer chain ends in a `reporting_checklist` evidence item built from a `ReportingChecklist` model (shared/phyto_shared/metabolomics.py). Required fields per modality: NMR — field strength, pulse program, apodisation function and LB, zero-fill factor, phase method, referencing standard, baseline method and parameters, alignment method, normalisation method and reference region, bucket width, excised regions, solvent; LC-MS — tool and version, RT tolerance, m/z tolerance, normalisation method; statistics — model software, cross-validation scheme, permutation count and p, VIP threshold, STOCSY driver and thresholds, FDR method. Every required field is either populated (from the `PreprocessingRecord` and model evidence of earlier layers) or listed in `missing_fields` — a missing field produces a visible caveat, never a default value. `completeness = provided/required` is recorded as part of the evidence payload so downstream consumers can gate on it.
- **Alternatives considered:** (a) Documentation-only checklist — rejected: does not travel with runs and cannot be asserted in tests; (b) refuse to run when incomplete — rejected: pilot data is routinely incomplete; the honest behaviour is to run and disclose, matching the platform-wide single-modality honesty stance (ADR-0050); (c) store completeness as a top-level run score only — rejected: score without the missing-field list invites misreading; the list IS the actionable part.
- **Consequences:** Every chain run ends with an auditable completeness statement; the Curcuma fixture (full parameters available) must score 100%, and a deliberately incomplete record must yield a non-empty `missing_fields` with a named caveat (tested). New required fields added by future guidelines extend the model additively and re-score old runs honestly (they will show less than 100% — that is the point).

### ADR-0073: In-house spectral deconvolution and qNMR region integration as Tier-1 tools

- **Date:** 2026-10-26
- **Status:** accepted
- **Deciders:** Project owner (qNMR capabilities pass, extracts focus)
- **Context:** Extract mixtures present overlapped multiplets that the existing stack can only work around statistically (STOCSY, bucketing, HSQC networking); there is no production 1D peak picker, no line-shape fitting, and the `qnmr` branch of `metabolomics/app/normalization.py` is a documented scalar placeholder "pending the real qNMR service". Commercial qNMR tooling (e.g. Bruker ACP 2) resolves overlap mathematically — peak/shape fitting and region integration with quantified per-component areas. The platform's extracts workflow needs the same capability, under the house constraints: numpy/scipy only, every parameter recorded, failures named rather than absorbed.
- **Decision:** Add two modules to the nmr_preproc service. `deconvolution.py` (Tier 1): a prominence-gated 1D peak picker seeded by a MAD noise estimate (`scipy.signal.find_peaks`), line-shape models (pseudo-Voigt default; Lorentzian/Gaussian selectable), and per-region seeded least-squares fitting (`scipy.optimize.curve_fit`, the existing house fitting precedent) of components on a local polynomial baseline (degree 0/1). Every region yields a `RegionFit` with explicit status — `fitted` (with analytic per-component areas: height × width closed form), `failed` (non-convergence, recorded with the reason, never raised), or `skipped` (no seeds above the SNR threshold). `quantify.py` (Tier 1): trapezoid region integration with an edge-anchored linear baseline option, fit-based integration (sum of analytic component areas, subset-selectable for one component of an overlapped cluster), and classical internal-standard qNMR (`n_a = (I_a/N_a)/(I_std/N_std) · n_std`) where every analyte result carries its substituted equation as audit text; non-physical inputs (non-positive integrals or proton counts, unusable standard amounts) raise named `SkipQuantification`. Both are exposed as nmr_preproc endpoints and registered as Tier-1 tools (`nmr_preproc.deconvolve`, `nmr_preproc.quantify`) whose mock executors run the real implementations; the publication bundle gains per-region deconvolution overlay figures and `tables/deconvolution_<id>.csv` / `tables/quantification.csv`.
- **Alternatives considered:** (a) wrap a commercial or GPL fitting engine — rejected: license posture (ADR-0018) and the offline local plane; (b) add `lmfit` — rejected: new dependency for capability scipy already provides, against the in-house ADR-0068 stance; (c) keep working around overlap statistically (STOCSY/bucketing only) — rejected: statistically dependent signals cannot yield per-component integrals, which absolute quantification requires; (d) raise fit failures as exceptions — rejected: one bad region in a batch must not discard the good fits; failure is data with a reason.
- **Consequences:** Extract spectra gain mathematical resolution of overlapped signals and calibrated quantification behind the same fail-loud contract as the rest of the NMR path. Known limitations recorded in the module docstrings: prominence seeding misses peaks buried entirely under stronger neighbours (manual seeds via `extra_seeds_ppm`), one shared local baseline per region, multiplet fine structure fitted as free components rather than J-coupled groups, and no error propagation from the covariance matrix yet (areas are point estimates). The `qnmr` normalization placeholder comment now points here; the elucidation ranker's input is deliberately NOT switched to deconvolved peak lists in this pass — that bridge is deferred until fit quality is validated on real extracts (follow-up decision pending).

### ADR-0074: Custom equations via a whitelisted AST evaluator — no eval, ever

- **Date:** 2026-10-26
- **Status:** accepted
- **Deciders:** Project owner (qNMR capabilities pass, extracts focus)
- **Context:** Derived quantities (purity from an internal standard, marker-to-total ratios, specification verdicts like "purity ≥ 95%") currently require code changes; the fixed thresholds in `qc_monitor.py` cannot express lab-specific formulas. The obvious implementation — `eval()` on a user string — is forbidden by the platform's own sandbox rules (`orchestrator/app/sandbox.py` explicitly lists `eval`/`exec`/`compile` as forbidden calls), and arbitrary-code execution has no place in an auditable evidence pipeline.
- **Decision:** Add `equations.py` to the nmr_preproc service (Tier 1): user expressions are parsed with `ast.parse` and evaluated by RECURSIVELY WALKING the tree against a strict whitelist — numeric constants, declared variable names, arithmetic `+ - * / // % **`, unary `+ - not`, comparisons `< <= > >= == !=`, `and`/`or` (so an expression can BE a specification check), and calls only to `abs min max round sqrt exp log log10 pow`. Every other construct (attribute access, subscripts, lambdas, comprehensions, f-strings, undeclared names, non-whitelisted calls, division by zero, domain errors) raises `EquationError` naming the offending construct. `evaluate_equation` returns the value plus a substituted-values audit string; `check_specs` evaluates boolean spec expressions to pass/fail results with warning/severe levels following the `qc_monitor.QCAlert` convention. Ready-made equations (`analyte_nmol`, `wt_percent`, `marker_ratio`) ship as `BUILTIN_EQUATIONS` over the same evaluator. Exposed as `POST /nmr-preproc/equations/evaluate` and the Tier-1 tool `nmr_preproc.evaluate_equation`.
- **Alternatives considered:** (a) `eval()` with a scrubbed namespace — rejected: forbidden by the sandbox rule and unsafe by construction (dunder reach-through, comprehensions side effects); (b) `sympy` symbolic parsing — rejected: new dependency for arithmetic the whitelist covers; (c) a fixed library of named derived quantities only — rejected: labs' spec formulas vary per pharmacopoeia and per project; a whitelist evaluator is the smallest honest generalisation; (d) allow conditionals (`if`/else expressions) — rejected: comparisons + and/or already express specification checks, and ternaries invite branch-heavy logic that belongs in code review, not in a data field.
- **Consequences:** Analysts get derived numbers and pass/fail verdicts without code changes, with every evaluation carrying its substituted values for audit. The whitelist is deliberately narrow: string operations, loops, and function definitions are out of scope by design — an equation is an arithmetic formula over numbers, not a program. Extending the function whitelist (e.g. `tanh`, `atan2`) is a one-line change but must keep functions total-or-named-error: partial functions (e.g. `log` of a negative) already surface as `EquationError` with the numeric cause, never NaN.

### ADR-0075: Read Bruker files correctly, enforce cross-artefact consistency, use MSI levels as the standard defines them

- **Date:** 2026-10-26
- **Status:** accepted
- **Deciders:** Project owner (case-study review pass)
- **Context:** An external review of the "NMR file for prediction" case study found five defects in priority order: (1) `2rr` processed 2D files were read as flat row-major arrays although TopSpin writes them in XDIM block/submatrix storage, and the loader's orientation check (element-wise correlation of the matrix with its transpose) is meaningless for non-square spectra and silently transposed correctly-read data; (2) FID reading ignored `DTYPA` — every 1H `fid` in the export declares `DTYPA=2` (float64 re/im pairs) and was misread as int32, producing garbage magnitudes and a false "FID shows no decay — corrupt export" verdict on healthy acquisitions (decay ratio ~1.0 read vs ~100 actual); (3) nothing verified that the numbers in the ACS text, the assignment tables and the JSON exports agree, so hand-edited or stale artefacts could ship; (4) the 13C assignment path had no lipid-region exclusion, so fatty-acyl/glycerol contaminant carbons could be assigned to the analyte; (5) the home-library dereplication stamped a curated *literature* shift-table match as "MSI Level 1 pending human sign-off", but the standard (Sumner et al., Metabolomics 3:161, 2007) reserves Level 1 for an authentic reference standard analysed under identical conditions.
- **Decision:** (a) `twod.load_2d` undoes XDIM submatrix storage exactly as nmrglue's `read_pdata`/`reorder_submatrix` does (nmrglue stays a non-dependency per ADR-0068), reads the processed dtype from `DTYPP`/`BYTORDP`, and orients homonuclear spectra with a physical partner-symmetry test (every cross-peak at (F2=a, F1=b) must have a partner at (F2=b, F1=a), checked in a +/-1-point neighbourhood because the axes can have different ppm spacings). (b) `pipeline._load_fid` derives the dtype from `DTYPA`/`BYTORDA` (2 = float64, else int32, nmrglue convention) and records `fid_data_type` in the provenance metadata; the processed `1r` path honours `DTYPP`. (c) New `nmr_preproc/consistency.py` checks, per sample, that text shifts/labels match the assignment table, the header matches metadata, picked peaks are backed by assignments (warning), and deconvolution centres lie inside their fitted windows; `render_report_docx` and `export_bundle` REFUSE the artefact on any error (voice rule R7), and every bundle carries `tables/consistency_report.txt` plus a docx section. (d) MSI levels are restated verbatim from the standard (`MSI_LEVEL_NAMES`: identified compound / putatively annotated compound / putatively characterized compound class / unknown compound); `home_library.dereplicate` now stamps Level 2 (putatively annotated) for table matches, Level 3 below 40% coverage and Level 4 for none — Level 1 is the calibration service's authentic-standard evidence only; `CandidateRank` gains an explicit `msi_level` field (ranker baseline 2, calibration layer sets 1 with `level_1_match`), and the orchestrator summary reports the attributed level instead of a blanket 1-or-2.
- **Alternatives considered:** (a) adopt nmrglue as a dependency for `read_pdata` — rejected for the service plane (ADR-0068 keeps the preprocessing numpy/scipy only), though the in-house undo implements the same layout; (b) warn instead of refuse on consistency errors — rejected: a report whose text and tables disagree is unpublishable, and half-enforced rules rot; (c) keep "Level 1 pending human sign-off" with a footnote — rejected: the standard's levels are community vocabulary, not house labels; a platform that redefines them in private cannot be compared with the literature it cites.
- **Consequences:** The case study's recorded finding "raw FIDs are corrupt" is corrected — the files were healthy float64 FIDs misread as int32; the named-skip note now fires only for genuinely corrupt data. XDIM-blocked 2D spectra load with the correct F1/F2 mapping (verified on the M6-4 COSY: partner-symmetry score 0.0 -> 1.0). Bundles that drift between text and tables are refused at export. MSI wording across reports, verdicts and the orchestrator summary matches Sumner 2007, so a reviewer can map every platform claim onto the standard's levels.

### ADR-0076: Lipid-region exclusion as a recorded curation step in the 13C assignment path

- **Date:** 2026-10-26
- **Status:** accepted
- **Deciders:** Project owner (case-study review pass)
- **Context:** Review fix #4: the M6-4 13C spectrum carries lipid/acyl contaminant carbons, and nothing in the assignment path excluded them — the case study's own curation recorded `[10, 40]` as an excluded region but the platform had no shared definition, so a fatty-acyl envelope (terminal CH3 ~14, chain CH2 ~22-34) could reach the candidate matcher and consume the matching budget or manufacture false assignments.
- **Decision:** New `shared/phyto_shared/lipid_regions.py` defines the lipid windows ONCE with reasons (`LIPID_REGIONS_13C` catalog: aliphatic 10-40, glycerol 55-75, olefinic 125-135, triglycerol ester C=O 172-174.5). The DEFAULT mask is only the aliphatic `[10, 40]` window — the one window that cannot overlap the platform's target chemistries (pterocarpan/flavonoid oxygenated aliphatics sit at 60-90, aromatics at 93-160, carbonyls at 165-185 ppm); the glycerol/olefinic/ester windows overlap real analyte carbons and are opt-in. `curate_13c_assignment` splits a peak list into kept/excluded with the reason per dropped peak, and `candidate_ranker.lipid_curation_13c` applies the mask to `c_1d` and the HSQC carbon dimension before any matching, attaching an `EvidenceItem` (`relation="lipid_region_excluded"`) to every rank so the exclusion is auditable. `RankingConfig.exclude_lipid_regions_13c` (empty tuple = off) lets a caller tighten or disable the mask.
- **Alternatives considered:** (a) exclude all four catalog windows by default — rejected: verified against the case study itself, that mask deletes genuine pterocarpan C-6 (66.8), C-11a (78.6), aromatic CH (130-133) and C-4a-type carbons (167-176); a filter that eats the right answer is worse than no filter; (b) put the mask in the preprocessing bucketing (`excised_regions`) — rejected: bucketing is for statistical preprocessing, not assignment evidence, and the exclusion must also reach HSQC; (c) leave curation to the analyst — rejected: the reviewer's finding is precisely that manual-only curation is where the lipid peaks leaked through.
- **Consequences:** 13C (and HSQC-carbon) evidence entering the ranker is lipid-masked by default with every dropped peak recorded; verified on the case-study list — the five lipid chain carbons (14.1/22.7/29.7/31.9/34.0) drop, all ten analyte carbons survive. Samples with genuine aliphatic natural products (prenyl chains resonate 17-35 ppm) must pass `exclude_lipid_regions_13c=()` or a tightened window; that trade-off is stated in the module docstring.

### ADR-0077: Web app only — the desktop app is removed and www is the canonical host

- **Date:** 2026-10-26
- **Status:** accepted
- **Deciders:** Project owner
- **Context:** The repo shipped a Tkinter desktop companion (`desktop/phytospectra_desktop`, PyInstaller build in `scripts/build_desktop.sh`, release workflow `.github/workflows/desktop-release.yml`) whose download page was served from GCS buckets at `phytospectra.com` / `www.phytospectra.com` (GCS website hosting resolves the bucket from the request Host header). The owner judged the desktop app not good-looking and redundant: everything it did (sign-in, upload, billing, report bundles) already exists in the web app at `app.phytospectra.com` (Cloud Run `phyto-frontend`), and the web app needs no installation.
- **Decision:** Delete the desktop app and all of its distribution: `desktop/`, `scripts/build_desktop.sh`, `scripts/publish_downloads.py`, `.github/workflows/desktop-release.yml`, and `infrastructure/download_page/`. The web app becomes the only Phytospectra client, and **`www.phytospectra.com` is the canonical public host** — Cloud Run domain mappings now exist for `phytospectra.com` (apex, Google-issued A records `216.239.32/34/36/38.21`), `www.phytospectra.com` (CNAME `ghs.googlehosted.com.`) and `app.phytospectra.com` (already live), all serving `phyto-frontend`. `PHYTO_APP_BASE_URL` defaults (citations `shared/phyto_shared/citation.py`, share links `shared/phyto_shared/share.py`, Stripe return URLs `services/auth/app/billing_routes.py`, share-view URLs in the orchestrator) change from `https://app.phytospectra.com` to `https://www.phytospectra.com`. The GCS download buckets (`phytospectra.com`, `www.phytospectra.com`, `phytospectra-downloads`) are obsolete and slotted for deletion once DNS no longer points at them.
- **Alternatives considered:** (a) keep the desktop app as a thin wrapper — rejected by the owner: two clients to style, build and secure for one product; (b) keep the download page on the apex and the web app on `app.` only — rejected: a landing page that merely duplicates the app's sign-in adds a second deploy target without adding information; (c) canonical host stays `app.phytospectra.com` — rejected: the owner wants the bare and www domains to BE the app, so citation and share URLs should read the primary brand host.
- **Consequences:** One client to maintain and style; installer CI, the downloads page and the domain-named-bucket GCS hosting scheme disappear. Existing desktop users (if any) lose the signed binary — the web app covers the same workflow. `app.phytospectra.com` keeps working (same Cloud Run service) so old links and the deployed orchestrator's env var remain valid; `www.phytospectra.com` needs DNS updated in Cloudflare (run `infrastructure/cloudflare_dns_setup.sh`) and SSL is provisioned automatically by the domain mappings (~15-60 min after DNS).
- **Amendment (2026-10-26, same day):** the owner extended the same treatment to `phytospectra.org`, superseding the planned 301 redirect. All six hostnames (`{,@,www,app}` × both zones) serve the same web app; the DNS script now manages both zones in one pass (`ZONE_NAMES`, default `phytospectra.com phytospectra.org`). The .org Cloud Run domain mappings are blocked on Google-side domain verification (`gcloud domains verify phytospectra.org` → Search Console, a browser step the owner must complete); the .org DNS records may be written before the mappings exist — harmless, but cert issuance starts only once a mapping is in place.

### ADR-0078: Runtime mode selection is explicit and fail-closed — no silent mock in real mode

- **Date:** 2026-10-31
- **Status:** accepted
- **Deciders:** Project owner (enterprise implementation brief, Phase 1A)
- **Context:** The elucidation service's `_Runtime` constructed `MockStructureDB()` unconditionally and called `get_shift_predictor()` with no arguments; the factory silently returned the mock whenever the Vertex endpoint/project arguments were missing. Switching `PHYTO_ENV=cloud` therefore did NOT activate real prediction — a deployed service would answer scientific requests with mock shift values and the 9-molecule fixture library while looking identical to a real one. This is exactly the "no silent mock fallback in production" violation the enterprise brief (2026-10-31) flags as finding 1. The same silent-degradation shape existed in the factory itself: missing SDK or missing arguments fell back to `MockShiftPredictor` without any signal.
- **Decision:** New `elucidation/runtime_config.py` defines `ElucidationConfig` (mirroring `auth.config.AuthConfig`'s plane semantics): `PHYTO_ENV=local` (default) is dry-run; `PHYTO_ENV=cloud` is real mode unless `PHYTO_DRY_RUN=1` is set explicitly (the documented bring-up mode). `ElucidationConfig.validate()` refuses startup in real mode when `VERTEX_ENDPOINT_ID` or `GOOGLE_CLOUD_PROJECT` is missing (`ElucidationConfigError`), naming the missing variables. `_Runtime` is built from the validated config and constructs `VertexAIShiftPredictor` directly — it must not delegate to the env-reading factory, which would re-derive the plane from `os.environ` and silently disagree with an explicitly-passed config (found by test). The structure database is honest about its limits instead of mock-or-nothing: only the fixture library exists today, so in real mode the slot stays empty and access raises `CloudServiceError` (surfaced as HTTP 503 by a new exception handler) rather than serving the fixture library as a database. `get_shift_predictor` itself is now fail-closed in real mode (missing args or missing SDK raise; no mock fallback). `/health` reports `predictor_mode` (`local-mock` / `cloud-real`) and `structure_db_available` so a deployment's mode is observable from the outside.
- **Alternatives considered:** (a) keep silent mock fallback but log a warning — rejected: a log line does not stop mock science from shipping; the brief's rule is loud failure, not loud lying; (b) require real cloud dependencies even on the local plane — rejected: the split-plane rule (local has no credentials, tests drive endpoints directly) is what makes the 1500-test offline suite possible; (c) a `StructureDatabaseUnavailable` null object that returns `[]` — rejected: an empty candidate list looks like a legitimate ranking result ("no candidates match") and would quietly produce abstentions that read as scientific conclusions; a 503 cannot be mistaken for an analysis.
- **Consequences:** A cloud deployment missing Vertex configuration now fails at startup with an actionable message instead of serving mock predictions. `/health` distinguishes the bring-up mode (`PHYTO_ENV=cloud,PHYTO_DRY_RUN=1` → `predictor_mode: local-mock`) from real operation, so the deploy-docs' temporary state stays auditable. Candidate retrieval in real mode is explicitly unavailable (503) until the Step 6+ provider (COCONUT/NPAtlas/ChemSpider) is implemented — the next backlog item, not a regression. Local-plane tests are unaffected: `tests/conftest.py` pins `PHYTO_ENV=local`, and the full elucidation suite passes unchanged.

### ADR-0079: §13 HMBC C-4a correlations were mis-pinned at 66.5 ppm — reconciled at 157.2 ppm with measured baselines

- **Date:** 2026-10-31
- **Status:** accepted
- **Deciders:** Project owner (enterprise implementation brief, Phase 1B — coordinate audit, no expectation values changed to make tests pass: every new number is a measurement on the fixture HMBC under the §4 reference implementation)
- **Context:** The enterprise brief's finding 4: the §13 regression suite pinned `H-11a→C-4a` at (5.47, 66.5) with exactly the frozen baseline of `H-11a→C-6` (18.3) — the same physical cross-peak listed twice under different labels — and filed `H-6eq→C-4a` as a "divergence" because at 66.5 it measures only 3.7 while §13 claims ≈27 verified. Measurement on the real M6-4-2/4 HMBC fixture shows the 13C axis has exactly one signal at 66.5 (C-6; §13's own 13C list has 157.2, 132.1, 109.8, 104.7, 103.7, 101.3, 93.8, 78.5, 66.5, 40.2 — no second 66.x signal for a separate C-4a), while the oxygenated aromatic quaternary C-4a must sit at 157.2. Scanning the H-11a and H-6eq columns finds strong cross-peaks at 157.2: H-6eq→C(157.2) S/N 32.2 (6.8× its ±4 ppm control) — reconciling §13's "verified ≈27" — and H-11a→C(157.2) S/N 13.9 (2.3× control, verified). Both are strong cross-peaks that the earlier pins had mis-filed at the wrong carbon.
- **Decision:** `_HMBC_STRONG` now pins `H-11a→C-4a` at (5.47, 157.2) with its own frozen baseline (13.9, control 6.0) and adds `H-6eq→C-4a` at (4.22, 157.2) with baseline 32.2 (control 4.8); the duplicated 66.5 row is removed. `H-6eq→C-4a` is removed from `_HMBC_DISCREPANT`. The remaining divergences (H-7→C-10a, H-7→C-9, H-1→C-3/4a, OCH2O→C-8, OCH2O→C-9, H-1→C-11a, H-11a→C-11b, H-2→C-11b, H-6a→C-10a) are unchanged — they still measure 3–6 despite §13's claims, and stay pinned as presence bands pending recovery of §13's original S/N computation. Full suite passes (18/18 §13 regression tests).
- **Alternatives considered:** (a) leave the duplicated pin and the mis-filed divergence and just document it — rejected: the register must reflect the measured data, and a test asserting a cross-peak at a carbon that does not exist is a latent scientific error, not just a cosmetic one; (b) change §13's expected values to the measured ones globally — rejected: §13's numbers are the published reference case and stay the source of truth for what the platform must reproduce; only the coordinate binding was wrong, not §13's S/N claims (27 ≈ 32.2 and 10 ≈ 13.9 are within §13's ±30% tolerance).
- **Consequences:** Two §13 "divergences" are resolved as verification wins (the cross-peaks exist and are verified at the correct carbon). The presence-band set shrinks by one. Any reader regression still fails: a broken deblock drops 157.2 to ~1 like the others. Future label-coordinate additions to the regression tables must be checked against the measured 13C list (one signal per coordinate) before pinning.

### ADR-0080: LLM judge verdicts carry the judge's model identity

- **Date:** 2026-10-31
- **Status:** accepted
- **Deciders:** Project owner (enterprise implementation brief, Phase 1C)
- **Context:** The brief's finding 5: the research survey claimed "only the QA judge calls an LLM", but a code inventory shows provider-backed completion paths in three places (orchestrator QA critic judge, `services/llm` `/complete` with Vertex tier-1 and OpenRouter tier-3 clients, orchestrator `llm_client.py` for Tier-3 tools). The `services/llm` responses already record `model_id` + `model_version` and bake them into the provenance manifest — but the QA critic's `llm_judge` check recorded only `passed` + a text excerpt: a verdict could not be attributed to the model that produced it, which blocks the Phase 4 model-comparison work (4C) and any later fine-tune replacement from being audited. The brief requires recording model/database identity in every relevant result.
- **Decision:** The `LLMClient` protocol gains a `model_identity` property (a short non-secret slug: `mock-llm-client:deterministic` for the mock, `vertex-endpoint:<id>` for the Vertex client). `CheckResult` gains an optional `model_identity` field (`None` for rule-based checks, serialized into `to_dict()` only when set); `_llm_judge_check` stamps it from the client on every judge verdict. Clients that don't expose the property fall back to `"unknown-judge"` via `getattr` — recorded honestly rather than guessed. The identity is an endpoint id or model slug, never a key or credential.
- **Alternatives considered:** (a) require all clients to raise if `model_identity` is missing — rejected: breaks the existing SpyLLM test double for no safety gain, and the honest `unknown-judge` string keeps the gap visible; (b) put the identity only in the orchestrator's audit log — rejected: the verdict and its attribution must travel together (the CheckResult is what gets persisted and displayed).
- **Consequences:** Every `llm_judge` verdict now names its judge. The `services/llm` paths were already compliant (verified: `clients.py` carries `model_id`/`model_version` into `LLMCompletion` and the manifest; `openrouter_client.py` records the served model id from the response body). Phase 4C comparison runs can group verdicts by judge. Future work (backlog P1C remainder): assert the same tagging on any new provider-backed endpoint as it is added.
