# GNPS test fixtures

**All files in this directory are SYNTHETIC**, generated locally on
2026-10-21 to match the GNPS2 output schemas. No GNPS task was submitted:
the local development plane must not contact external APIs (split-plane
rule), and no GNPS credentials exist in this environment.

Per the Step 4 specification: "If this is not feasible in the local
environment, generate synthetic fixtures that match the GNPS2 output
schema exactly, and note in the fixture file header that they are
synthetic." Real GNPS2 exports should replace these before production
use (drop them in, keeping the same schemas, and the parser tests will
run against them unchanged).

| File | Schema modeled on |
|---|---|
| `classical_mn.graphml` | GNPS2 classical MN GraphML (node keys `label`, `precursor mass`; edge keys `cosine score`, `matched peaks`) |
| `fbmn.graphml` | GNPS2 FBMN GraphML (adds `feature_id` from the MZmine/feature table) |
| `iimn.graphml` | GNPS2 IIMN GraphML (adds `ionidentity` node attribute, Schmid et al. 2021) |
| `library_annotations.tsv` | GNPS2 library-search TSV (`#Scan#`, `LibraryName`, `MQScore`, `SharedPeaks`, ...) |
| `clusters.tsv` | Cluster-info TSV (cluster id, member scans, consensus spectrum) |
| `sample.mgf` | MGF input whose precursor masses produce a known network under the mock's residue-delta heuristic |
