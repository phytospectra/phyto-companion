"""Invented coordinates ONLY: software contracts, not chemically measured spectra."""
from pathlib import Path

from .harness import LABEL, bootstrap, digest, write


def make_fixture(bundle, keys_path):
    bootstrap()
    from rdkit import Chem
    bundle = Path(bundle)
    bundle.mkdir(parents=True, exist_ok=False)
    base = [{"h_ppm": float(i), "c_ppm": float(20*i)} for i in (1, 2, 3, 4)]
    refs = []
    for cid, smiles, peaks in (
        ("alpha", "CCO", base),
        ("beta", "COC", [{"h_ppm": p["h_ppm"] + .4, "c_ppm": p["c_ppm"] + 4} for p in base]),
    ):
        refs.append({"reference_id": "SYNTHETIC-" + cid, "candidate_id": cid,
                     "inchikey": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles)),
                     "source": "SYNTHETIC-reference-" + cid, "solvent": "SYNTHETIC/no acquisition",
                     "license": LABEL, "provenance": "experimental", "peaks": peaks})
    # Runtime requires 'experimental'; this literal does NOT establish measurement.
    (bundle / "structures.csv").write_text(
        "name,smiles,provenance,source\nalpha,CCO,synthetic,SYNTHETIC\n"
        "beta,COC,synthetic,SYNTHETIC\ngamma,CCC,synthetic,SYNTHETIC\n", encoding="utf-8")
    queries = []
    keys = []
    examples = [
        ("present", "C2H6O", base, "present", ["alpha"], "compound-alpha"),
        ("present-repeat", "C2H6O", base[:3], "present", ["alpha"], "compound-alpha"),
        ("missing-reference", "C3H8", base, "present", ["gamma"], "compound-gamma"),
        ("retrieval-miss", "CH4", base, "present", ["alpha"], "compound-alpha"),
        ("absent", "CH4", base, "absent", [], "absent-1"),
        ("absent-overlap", "C2H6O", base, "absent", [], "absent-2"),
        ("ambiguous-contract", "C2H6O", base, "ambiguous", ["alpha", "beta"], "family-ab"),
        ("sparse", "C2H6O", base[:1], "present", ["alpha"], "compound-alpha"),
    ]
    for cid, formula, peaks, status, accepted, group in examples:
        queries.append({"case_id": cid, "formula": formula, "peaks": peaks, "partition": "test"})
        keys.append({"case_id": cid, "truth_status": status, "acceptable_candidate_ids": accepted,
                     "independence_group": group, "partition": "test",
                     "query_source_id": "SYNTHETIC-query-" + cid,
                     "reference_source_ids": [r["source"] for r in refs] if formula == "C2H6O" else [],
                     "condition_mismatch": False,
                     "provenance_review": {"rights_cleared": False, "independently_acquired": False,
                                           "identity_verified": False}})
    evidence = {"classification": "synthetic_contract", "reviewer": LABEL,
                "adjudication_id": "SYNTHETIC-no-expert", "screening_log_sha256": "0" * 64,
                "external_timestamp_receipt": "SYNTHETIC-not-timestamped", "records": []}
    write(bundle / "manifest.json", {"classification": "synthetic_contract",
                                     "review_artifact_sha256": digest(evidence)})
    write(bundle / "references.json", refs)
    write(bundle / "queries.json", queries)
    write(keys_path, {"schema": 1, "evidence": evidence, "cases": keys})
