"""Structure database access: mock locally, COCONUT/NPAtlas/ChemSpider in
the cloud plane (Step 6+ wiring).

The mock ships a small fixture library (the mock predictor's reference
molecules) indexed by formula and InChIKey.  The interface matches what
the orchestrator's ``sirius.predict_formula`` / database tools will call:
lookup by formula, lookup by InChIKey.
"""

from __future__ import annotations

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from phyto_shared.models import Candidate, MolecularFormula

from .candidate_ranker import parse_formula_string

__all__ = ["StructureDatabase", "MockStructureDB", "MOCK_LIBRARY"]

MOCK_LIBRARY: list[tuple[str, str]] = [
    # (name, smiles) -- formulas verified via RDKit at import time
    ("benzene", "c1ccccc1"),
    ("phenol", "Oc1ccccc1"),
    ("catechol", "Oc1ccccc1O"),
    ("ethanol", "CCO"),
    ("ethylene glycol", "OCCO"),
    ("quercetin", "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12"),
    ("kaempferol", "O=c1cc(-c2ccc(O)cc2O)oc2cc(O)cc(O)c12"),
    ("caffeine", "Cn1c(=O)c2c(ncn2C)n(C)c1=O"),
    # acyclic diterpenoid C20H40O (formula-verified positional isomer,
    # phytol series; ADR-0018)
    ("acyclic diterpenoid", "CCC(C)CCC(C)CCC(C)CCC(C)CC=CCO"),
]


def _candidate_from(name: str, smiles: str) -> Candidate:
    mol = Chem.MolFromSmiles(smiles)
    formula = rdMolDescriptors.CalcMolFormula(mol)
    counts = parse_formula_string(formula)
    return Candidate(
        id=name.lower().replace(" ", "-"),
        name=name,
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="experimental",
        source="mock structure library",
    )


class StructureDatabase:
    """Interface: lookup by formula and by InChIKey."""

    def candidates_for_formula(self, counts: dict[str, int]) -> list[Candidate]:
        raise NotImplementedError

    def candidate_by_inchikey(self, inchikey: str) -> Candidate | None:
        raise NotImplementedError

    def known_inchikeys(self) -> set[str]:
        raise NotImplementedError


class MockStructureDB(StructureDatabase):
    """Deterministic in-memory library for the local plane."""

    def __init__(self, library: list[tuple[str, str]] | None = None) -> None:
        self._candidates = [
            _candidate_from(name, smiles)
            for name, smiles in (library or MOCK_LIBRARY)
        ]
        self._by_ik = {
            Chem.MolToInchiKey(Chem.MolFromSmiles(c.smiles)): c
            for c in self._candidates
        }

    def candidates_for_formula(self, counts: dict[str, int]) -> list[Candidate]:
        return [c for c in self._candidates if dict(c.formula.counts) == counts]

    def candidate_by_inchikey(self, inchikey: str) -> Candidate | None:
        return self._by_ik.get(inchikey)

    def known_inchikeys(self) -> set[str]:
        return set(self._by_ik)
