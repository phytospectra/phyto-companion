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
- **NMRShiftDB2**: its [home page](https://nmrshiftdb.nmr.uni-koeln.de/)
  was accessible and describes open-content licensing and assigned spectra, but
  neither exact terms nor an eligible HSQC accession were verified. Obtain both
  before reuse. Do not infer experimental HSQC cross-peaks merely by combining
  unverified 1D atom assignments.
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

### Import-ready procedure (builder v3)

Use the non-spectral `source/manifest.template.csv` as the header for a private
source directory's `manifest.csv`. Supply the transformed peak CSV and original
source file inside that directory; record the actual original SHA-256, the
transformation, a stable acquisition ID, measurement and assignment evidence
URLs, acquisition conditions, full identity and formula, and the applicable
rights scope and evidence. Dataset-level terms can serve as `rights_evidence_url`
if their applicability to the accession is documented in `rights_scope`.
`measurement_status` must be `measured` and `redistribution_assertion` must be
`cleared`. All evidence URLs must be HTTPS. The builder checks the original
file hash and separately records the transformed table hash; it emits unchanged
runtime structures/reference schemas and records provenance in `MANIFEST.json`.
Run `python scripts/build_reference_library.py --source <private-source> --output
<private-output> --strict`; inspect quarantine reasons before any release.
Never publish original files or outputs without appropriate rights.

All evidence and rights fields are curator assertions, not software verification;
direct runtime JSON bypasses this gate. This gate does not establish the separate
query/reference acquisition independence required for evaluation. Private local
use outside this distribution gate is not prohibited.

### Quercetin investigation

[PubChem CID 5280343](https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/5280343/property/MolecularFormula,InChIKey,CanonicalSMILES/JSON)
identifies quercetin as `REFJWTPEDVJJIY-UHFFFAOYSA-N` (C15H10O7). The historical
starter manifest instead paired the fixture peaks with
`ISJMMWDSNQVANM-UHFFFAOYSA-N`, [PubChem CID 5381322](https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/ISJMMWDSNQVANM-UHFFFAOYSA-N/property/IUPACName,MolecularFormula,InChIKey/JSON),
5,7-dihydroxy-2-(2,4,6-trihydroxyphenyl)chromen-4-one, an isomer with the same
formula. Historical commit `424fdda` propagated the incorrect structure into
`source/manifest.csv`, `structures.csv`, `references.json` record
`quercetin-226ce4d48558`, its generated `MANIFEST.json` and self-match tests.
These library artifacts were removed from the release tree in `02f54cc`; their
history remains. The pre-existing fixture is a rounded hand-curated table with
no exact source accession, not evidence identifying a measured acquisition.
Correcting the structure alone cannot validate or transfer those peaks.

The same incorrect SMILES also appeared in the mock structure database,
shift predictor and orchestrator ranker, and directly affected tests. Their
identity-only mock structures now use PubChem quercetin and kaempferol
(CID 5280863, `IYRMWMYZSQPJKC-UHFFFAOYSA-N`); the former kaempferol lookup
also represented an isomer. Both unverified shift tables were removed rather
than transferred. The mock predictor uses its generic, non-scientific fallback;
the mock ranker distances and provenance are synthetic.
