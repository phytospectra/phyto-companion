"""Structure database access: mock locally, real providers when configured.

Two providers:
- ``MockStructureDB`` — deterministic in-memory fixture library (the mock
  predictor's reference molecules), local plane only. Never a source of
  scientific results.
- ``FileStructureDB`` — a real, user-configurable curated library (CSV:
  name, smiles, provenance, source). This is the Step 6+ provider wiring:
  a deployment points ``PHYTO_STRUCTURE_DB_PATH`` at its own curated
  structure list, and candidate retrieval becomes a real capability in
  both planes. A missing or malformed file is a startup error, not a
  silent degradation (ADR-0081).

The interface matches what the orchestrator's ``sirius.predict_formula`` /
database tools will call: lookup by formula, lookup by InChIKey.
"""

from __future__ import annotations

import csv
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from phyto_shared.models import Candidate, MolecularFormula
from phyto_shared.exceptions import CloudServiceError

from .candidate_ranker import parse_formula_string

__all__ = [
    "StructureDatabase",
    "MockStructureDB",
    "MOCK_LIBRARY",
    "FileStructureDB",
]

MOCK_LIBRARY: list[tuple[str, str]] = [
    # (name, smiles) -- formulas verified via RDKit at import time
    ("benzene", "c1ccccc1"),
    ("phenol", "Oc1ccccc1"),
    ("catechol", "Oc1ccccc1O"),
    ("ethanol", "CCO"),
    ("ethylene glycol", "OCCO"),
    # Identity only: PubChem CIDs 5280343 / 5280863; no shift assignments.
    ("quercetin", "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O"),
    ("kaempferol", "C1=CC(=CC=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O"),
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
        # Labeled fixture: these molecules are not measured reference data.
        provenance="synthetic",
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


class FileStructureDB(StructureDatabase):
    """Real curated structure library loaded from a CSV file.

    Columns: ``name``, ``smiles`` and optionally ``provenance``
    (default ``structure_only``: a row that does not declare a
    provenance must not be silently claimed as experimental) and
    ``source`` (default the file path's name). Every row's formula is
    computed from the SMILES with RDKit; an invalid SMILES is a startup
    error, never a skipped row (ADR-0081: a partially-loaded library
    would quietly miss candidates and look like a negative result).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise CloudServiceError(
                f"structure database file not found: {self.path}",
                context={"path": str(self.path), "capability": "structure_db"},
            )
        self._candidates: list[Candidate] = []
        self._by_ik: dict[str, Candidate] = {}
        with open(self.path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            required = {"name", "smiles"}
            header = set(reader.fieldnames or [])
            missing = required - header
            if missing:
                raise CloudServiceError(
                    f"structure database {self.path} is missing required "
                    f"columns: {', '.join(sorted(missing))}",
                    context={"path": str(self.path),
                             "columns": sorted(header)},
                )
            for lineno, row in enumerate(reader, start=2):
                name = (row.get("name") or "").strip()
                smiles = (row.get("smiles") or "").strip()
                if not name or not smiles:
                    raise CloudServiceError(
                        f"structure database {self.path} line {lineno}: "
                        "empty name or smiles",
                        context={"path": str(self.path), "line": lineno},
                    )
                mol = Chem.MolFromSmiles(smiles)
                if mol is None:
                    raise CloudServiceError(
                        f"structure database {self.path} line {lineno}: "
                        f"invalid SMILES for {name!r}",
                        context={"path": str(self.path), "line": lineno,
                                 "name": name},
                    )
                formula = rdMolDescriptors.CalcMolFormula(mol)
                counts = parse_formula_string(formula)
                candidate = Candidate(
                    id=name.lower().replace(" ", "-"),
                    name=name,
                    smiles=smiles,
                    formula=MolecularFormula(counts=counts),
                    exact_mass=MolecularFormula(counts=counts).exact_mass(),
                    provenance=(row.get("provenance") or "structure_only").strip(),
                    source=(row.get("source") or self.path.name).strip(),
                )
                self._candidates.append(candidate)
                inchikey = Chem.MolToInchiKey(mol)
                if inchikey in self._by_ik:
                    raise CloudServiceError(
                        f"structure database {self.path} line {lineno}: "
                        f"duplicate InChIKey {inchikey} ({name!r} duplicates "
                        f"{self._by_ik[inchikey].name!r})",
                        context={"path": str(self.path), "line": lineno,
                                 "inchikey": inchikey},
                    )
                self._by_ik[inchikey] = candidate
        if not self._candidates:
            raise CloudServiceError(
                f"structure database {self.path} contains no rows",
                context={"path": str(self.path), "capability": "structure_db"},
            )

    def candidates_for_formula(self, counts: dict[str, int]) -> list[Candidate]:
        return [c for c in self._candidates if dict(c.formula.counts) == counts]

    def candidate_by_inchikey(self, inchikey: str) -> Candidate | None:
        return self._by_ik.get(inchikey)

    def known_inchikeys(self) -> set[str]:
        return set(self._by_ik)
