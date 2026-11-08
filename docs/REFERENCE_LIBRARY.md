# Reference-library acquisition, import and rights gate

## Current release decision

**No rights-cleared, independently identity-verified measured HSQC library is
bundled.** The initial `424fdda` starter library was based on public fixture
comments rather than record-level source and reuse evidence. It is quarantined;
its new duplicated spectral files and derived usable outputs are removed from
the release tree. A questionable quercetin structure is another reason not to
use those records scientifically. Historical commits retain the attempted import;
removing current files does not erase that history or establish data rights.

Original pre-existing `tests/fixtures/real_npmrd` files remain regression fixtures,
not a newly cleared measured library. Their README licence assertion is not an
independent verification of upstream terms, and says nothing about externally
sending spectra for inference.

## Import pipeline

`scripts/build_reference_library.py` builds a structure CSV/reference JSON pair
with deterministic hashes and quarantine reasons. The final source schema and
validation requirements are defined by the builder and its tests. Schema checks
cannot verify ownership, measured status, permissions, or scientific assignments;
these require recorded curator evidence.

Keep source downloads and restricted owner data outside public source control.
Provide per-record stable accession/URL, explicit licence and attribution,
commercial/noncommercial rights, redistribution and external-inference permission,
measured/predicted status, solvent/acquisition conditions, verified full structure
identity and assignment evidence, and original file hashes. Record transformations;
never repair structural or assignment ambiguities silently.

Validation must check full InChIKey, formula consistency, finite ppm coordinates,
duplicate coordinates/assignments/structures, conflicting metadata, source paths,
and missing rights evidence. Quarantined records cannot become usable references
merely because the runtime accepts a `provenance: experimental` string.

## Acquisition candidates — no blanket permissions asserted

- **NP-MRD**: the existing fixture README reports noncommercial CC-BY-NC terms.
  Obtain current authoritative terms and per-record accessions/downloads; clarify
  allowed redistribution and inference use. An account may be required.
- **NMRShiftDB2**: acquire an authoritative licence statement and the relevant
  records before reuse. Connection probes failed here; an unlinked search summary
  is not licence evidence. Do not infer experimental HSQC cross-peaks merely by
  combining unverified 1D atom assignments.
- **BMRB**: inspect the current dataset-specific reuse terms and appropriate
  small-molecule assigned records. Accessibility is not clearance.
- **Owner data**: publication/redistribution permission and scientific assignment
  confirmation are outstanding author-only inputs. Local possession does not
  authorize public distribution or external inference.

## Acceptance criterion still open

A usable distributable library requires multiple independently identified measured
references with the rights and acquisition evidence above. Synthetic tests,
rounded literature tables, and a single self-match do not satisfy that criterion.
The import/quarantine infrastructure can be reviewed now; scientific-library
completion remains blocked on suitable records and rights confirmation.

Builder v2 requires the existing manifest columns plus `source_accession_url`,
`identity_source_url`, `rights_evidence_url` (HTTPS) and
`redistribution_assertion=cleared`; expected InChIKey, declared formula and
evidence nature are mandatory. The template is non-spectral. These strings
are curator assertions, not verification; direct runtime JSON bypasses the
builder. Legitimate private local use is not prohibited by this distribution gate.
