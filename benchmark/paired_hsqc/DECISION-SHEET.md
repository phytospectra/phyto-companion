# Paired HSQC evaluation — decision sheet for scientific review

Status: **NOT LOCKED, NOT PREREGISTERED.** This sheet lists every design
choice that requires scientific judgment before the protocol in
`benchmark/paired_hsqc/PROTOCOL.md` may be locked. No held-out outcomes have
been inspected; no threshold below may be changed because of future results.

## Primary design (already fixed in the protocol, open to reviewer veto)

- **Question:** on independent measured HSQC queries with a known neutral
  formula, does arm A (production executor: rectangular Hungarian one-to-one
  assignment over capped normalized pair costs) produce better supported
  candidate-level ranking than arm B (directed query-to-nearest-reference
  averaging without a global one-to-one constraint), including abstention on
  absent/ambiguous truth?
- **Contrast:** A enforces a global one-to-one assignment and normalizes by
  max(query, reference) peak count; B allows one reference peak to explain
  many query peaks and averages per-query costs. Both share formula
  retrieval, tolerances, candidate pool, reference library, ambiguity and
  abstention machinery. The difference is real (assignment + normalization),
  not a nominal duplicate; the protocol states B is not a pure Hungarian
  ablation and requires count-stratified reporting.
- **Primary endpoint:** supported-correct preference on all planned present
  test queries (failures count zero); co-primary safety: supported-wrong
  selections, especially absent/ambiguous.
- **Secondary endpoints:** retrieval/reference recall, raw top-1/top-3,
  coverage, abstention rates, ambiguity flag rates, runtime, per-stratum rows.
- **Inference:** query-weighted cluster bootstrap by compound/acquisition
  group (10,000 draws, seed 20261109); exact McNemar only for one query per
  group. Repeated acquisitions of one compound never inflate n.

## Decisions requiring scientific review (answer each before lock)

1. **Engineering thresholds as decision rules.** H/C tolerances 0.05/0.5 ppm,
   minimum 3 distinct matched coordinates, minimum 50% query coverage,
   near-tie gap 0.25 distance units. These are inherited engineering
   defaults, never calibrated. Does the reviewing scientist accept them as
   frozen rules for this study, or require literature-grounded values?
2. **Acquisition-compatibility strata.** Solvent/field/referencing mismatch
   is prespecified as a stratum. Define the stratum boundaries and which
   mismatches are exclusions before any outcomes are seen.
3. **Ground-truth adjudication.** Absent identities and chemical family
   grouping cannot be established by executable accepted-ID sets. Name the
   adjudicator(s), the adjudication artifact format, and the external
   timestamping receipt that will constitute the lock.
4. **Sample size.** Target 100 independent present groups + 50 absent + ≥20
   ambiguity challenges is a feasibility/precision proposal (≈±10 pp), NOT
   powered superiority (≈235 pairs for the stated 80% power scenario).
   Reviewers must accept descriptive/pilot interpretation for attainable n.
5. **Dev/test split authority.** Which independence groups are development
   versus test, and who may amend the protocol between dev inspection and
   test freeze, must be named; any amendment becomes a new protocol version.
6. **Supervisor.** The harness detects 60 s timeouts only after completion.
   A hard-kill supervisor (60 s/arm/query, 30 min/run) must be built and
   frozen before any final measured run.
7. **Secondary LLM arms C/D.** Optional; require an immutable local model
   pin (proposed Qwen2.5-7B-Instruct GGUF Q4_K_M with file SHA256 and
   llama_cpp version — to be filled before any run), ≤20 cases × 3 repeats,
   temperature 0, $0 provider spend, ≤2 CPU-hours. Any API model needs a
   new approval. C and D receive different inputs (D adds A's computed
   tables); this is stated, not hidden.

## Lock checklist (none performed yet)

- [ ] Scientist/adjudicator names and sign-off on decisions 1–6.
- [ ] External timestamped receipt of protocol + decision sheet digests.
- [ ] Screening log with exclusions and reviewer identities.
- [ ] Key digest binding after keys are final.
- [ ] Evidence manifests covering every query and reference source ID.

Until every item is checked, the protocol may be called review-ready at
most — never locked, preregistered, or executed for scientific claims.
