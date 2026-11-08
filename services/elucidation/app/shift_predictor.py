"""ShiftMPNN shift prediction: Vertex AI in the cloud plane, deterministic
mock locally.

The mock carries a small hard-coded lookup for fixture molecules and a
rule-based fallback for anything else, including quercetin and kaempferol.
Their former mislabeled isomer tables were removed, not reassigned.  Per the Step 5 specification the mock is NOT accurate
chemistry -- it exercises code paths deterministically.

Note: the specification named andrographolide as the fifth reference, but
no verified SMILES was available offline (our candidates computed C17-C18
formulas, not the true C20H30O5); caffeine (textbook 13C/1H values,
verifiable formula) substitutes rather than shipping an invented
structure.  Add andrographolide from a database when networked.

Shift sentinel convention: per-atom lists cover ALL heavy atoms; entries
are (c_shift, h_shift) where a value of -1.0 means "not predicted for
this atom" (non-carbon 13C shifts; 1H shifts of non-H-bearing atoms).
"""

from __future__ import annotations

import os
import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError

__all__ = [
    "ShiftPrediction",
    "ShiftPredictor",
    "VertexAIShiftPredictor",
    "MockShiftPredictor",
    "get_shift_predictor",
]

# Rough per-inference cloud cost (USD) keyed by deployed model id; used
# for the cost tracking the orchestrator reports per analysis run.
SHIFT_MPNN_COST_TABLE: dict[str, float] = {
    "shiftmpnn-v4-l4": 0.0004,
    "shiftmpnn-v4-default": 0.0006,
}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ShiftPrediction(BaseModel):
    """Predicted 1H/13C shifts, one entry per heavy atom."""

    model_config = ConfigDict(extra="forbid")

    smiles: str
    atom_indices: list[int]
    atom_symbols: list[str]
    predicted_c_shifts: list[float]
    predicted_h_shifts: list[float]
    model_id: str
    model_version: str
    inference_latency_ms: float = Field(..., ge=0.0)


class ShiftPredictor(Protocol):
    def predict(self, smiles: str) -> ShiftPrediction: ...


# ---------------------------------------------------------------------------
# Vertex AI implementation (cloud plane)
# ---------------------------------------------------------------------------


class VertexAIShiftPredictor:
    """Calls the ShiftMPNN endpoint on Vertex AI.

    All Google SDK imports are deferred: importing this module on the
    local plane costs nothing, and calling it without the SDK fails
    closed with ``CloudServiceError``.
    """

    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 0.25

    def __init__(
        self,
        endpoint_id: str,
        project: str,
        location: str = "us-central1",
        model_id: str = "shiftmpnn-v4-l4",
        model_version: str = "4.0.0",
    ) -> None:
        self.endpoint_id = endpoint_id
        self.project = project
        self.location = location
        self.model_id = model_id
        self.model_version = model_version

    def estimate_cost_usd(self) -> float:
        return SHIFT_MPNN_COST_TABLE.get(self.model_id, 0.001)

    def predict(self, smiles: str) -> ShiftPrediction:
        try:
            from google.cloud import aiplatform  # deferred: cloud plane only
        except ImportError as exc:
            raise CloudServiceError(
                "google-cloud-aiplatform is not installed; the local plane "
                "must use MockShiftPredictor (split-plane rule)",
                context={"endpoint_id": self.endpoint_id},
            ) from exc

        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                aiplatform.init(project=self.project, location=self.location)
                endpoint = aiplatform.Endpoint(self.endpoint_id)
                response = endpoint.predict(instances=[{"smiles": smiles}])
                payload = response.predictions[0]
                return ShiftPrediction(
                    smiles=smiles,
                    atom_indices=list(payload["atom_indices"]),
                    atom_symbols=list(payload["atom_symbols"]),
                    predicted_c_shifts=[float(x) for x in payload["c_shifts"]],
                    predicted_h_shifts=[float(x) for x in payload["h_shifts"]],
                    model_id=self.model_id,
                    model_version=self.model_version,
                    inference_latency_ms=round(
                        (time.perf_counter() - started) * 1000.0, 3
                    ),
                )
            except Exception as exc:  # SDK raises many error types
                last_error = exc
                if attempt < self.MAX_ATTEMPTS:
                    time.sleep(self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise CloudServiceError(
            f"ShiftMPNN endpoint failed after {self.MAX_ATTEMPTS} attempts",
            context={"endpoint_id": self.endpoint_id, "error": str(last_error)},
        )


# ---------------------------------------------------------------------------
# Deterministic mock (local plane)
# ---------------------------------------------------------------------------

# (c_shift, h_shift) per heavy atom, ordered by RDKit atom order of the
# canonical SMILES key.  -1.0 = not predicted for that atom.
_REFERENCE_TABLE: dict[str, list[tuple[float, float]]] = {
    # benzene: c1ccccc1 -- 6 aromatic CH
    "c1ccccc1": [(128.5, 7.26)] * 6,
    # ethanol: CCO -- [C, C, O]
    "CCO": [(18.4, 1.23), (57.7, 3.70), (-1.0, 2.60)],
    # caffeine: Cn1c(=O)c2c(ncn2C)n(C)c1=O -- [C,N,C,N,C,C,C,O,N,C,C,O,N,C]
    "Cn1c(=O)c2c(ncn2C)n(C)c1=O": [
        (28.0, 3.30), (-1.0, -1.0), (148.0, -1.0), (-1.0, -1.0),
        (106.0, -1.0), (141.0, 7.50), (155.0, -1.0), (-1.0, -1.0),
        (-1.0, -1.0), (33.0, 3.55), (28.0, 3.30), (-1.0, -1.0),
        (-1.0, -1.0), (30.0, 3.85),
    ],
    # The former quercetin and kaempferol tables used different isomers and
    # have no verified atom assignments. Both identities use the mock rules.
    # phenol: Oc1ccccc1 -- [O, C, C, C, C, C, C]
    "Oc1ccccc1": [
        (-1.0, 5.30), (155.0, -1.0), (116.0, 6.85), (130.0, 7.20),
        (121.0, 6.80), (130.0, 7.20), (116.0, 6.85),
    ],
    # ethylene glycol: OCCO -- [O, C, C, O]
    "OCCO": [(-1.0, 4.80), (62.0, 3.70), (62.0, 3.70), (-1.0, 4.80)],
    # acyclic diterpenoid, C20H40O (formula-verified; a positional isomer
    # in the phytol series -- isomerism not verifiable offline, ADR-0018)
    "CCC(C)CCC(C)CCC(C)CCC(C)CC=CCO": [
        (14.0, 0.88), (30.0, 1.25), (37.5, 1.55), (14.0, 0.88),
        (30.0, 1.25), (30.0, 1.25), (37.5, 1.55), (14.0, 0.88),
        (30.0, 1.25), (30.0, 1.25), (37.5, 1.55), (14.0, 0.88),
        (30.0, 1.25), (30.0, 1.25), (37.5, 1.55), (14.0, 0.88),
        (30.0, 1.25), (125.0, 5.40), (125.0, 5.40), (60.0, 4.05),
        (14.0, 0.88),
    ],
}


class MockShiftPredictor:
    """Deterministic, offline ShiftMPNN stand-in.

    Lookup molecules get their tabulated shifts; everything else falls
    back to element/hybridization rules (aromatic C ~128, sp3 C ~30,
    C-O ~68, carbonyl ~175 ppm; H: aromatic ~7, C-H sp3 ~1.2, O-H ~4.5).
    The rules are deliberately crude -- code-path coverage, not chemistry.
    """

    model_id = "shiftmpnn-mock"
    model_version = "0.1.0"

    def __init__(self) -> None:
        from rdkit import Chem

        self._chem = Chem
        # canonical-SMILES -> shift list (canonicalized at registration)
        self._table: dict[str, list[tuple[float, float]]] = {}
        for smiles, shifts in _REFERENCE_TABLE.items():
            self.register(smiles, shifts)

    def register(self, smiles: str, shifts: list[tuple[float, float]]) -> None:
        """Register (or replace) a lookup molecule (used by tests)."""
        mol = self._chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"cannot parse SMILES {smiles!r}")
        n_atoms = mol.GetNumAtoms()
        if len(shifts) != n_atoms:
            raise ValueError(
                f"shift table for {smiles!r} has {len(shifts)} entries "
                f"but the molecule has {n_atoms} heavy atoms"
            )
        self._table[self._chem.MolToSmiles(mol)] = list(shifts)

    def predict(self, smiles: str) -> ShiftPrediction:
        started = time.perf_counter()
        mol = self._chem.MolFromSmiles(smiles)
        if mol is None:
            raise CloudServiceError(
                "MockShiftPredictor cannot parse SMILES", context={"smiles": smiles}
            )
        # Predict on the CANONICAL form so atom symbols and the shift
        # table are always aligned, regardless of the input SMILES form.
        canonical = self._chem.MolToSmiles(mol)
        canonical_mol = self._chem.MolFromSmiles(canonical)
        if canonical_mol is None:  # pragma: no cover - canonical reparse
            raise CloudServiceError(
                "canonical SMILES failed to reparse", context={"smiles": smiles}
            )
        if canonical in self._table:
            pairs = self._table[canonical]
        else:
            pairs = [self._rule_shift(atom) for atom in canonical_mol.GetAtoms()]

        symbols = [atom.GetSymbol() for atom in canonical_mol.GetAtoms()]
        return ShiftPrediction(
            smiles=smiles,
            atom_indices=list(range(len(symbols))),
            atom_symbols=symbols,
            predicted_c_shifts=[c for c, _ in pairs],
            predicted_h_shifts=[h for _, h in pairs],
            model_id=self.model_id,
            model_version=self.model_version,
            inference_latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )

    @staticmethod
    def _rule_shift(atom) -> tuple[float, float]:
        """Rule-based fallback: plausible, deterministic, not accurate."""
        from rdkit import Chem

        if atom.GetSymbol() != "C":
            has_h = atom.GetTotalNumHs() > 0
            return (-1.0, 4.5 if has_h else -1.0)
        has_h = atom.GetTotalNumHs() > 0
        if atom.GetIsAromatic():
            return (128.0, 7.0) if has_h else (135.0, -1.0)
        has_o = any(n.GetSymbol() == "O" for n in atom.GetNeighbors())
        has_double = any(
            bond.GetBondType() == Chem.BondType.DOUBLE for bond in atom.GetBonds()
        )
        if has_o and has_double:
            return (175.0, -1.0)  # carbonyl region
        if has_o:
            return (68.0, 3.8) if has_h else (72.0, -1.0)
        return (30.0, 1.2) if has_h else (40.0, -1.0)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_shift_predictor(**kwargs) -> ShiftPredictor:
    """Resolve the predictor for the current plane (ADR-0078).

    Dry-run (the local plane's default) returns the deterministic mock.
    Real mode -- ``PHYTO_ENV=cloud`` with ``PHYTO_DRY_RUN`` unset or ``0`` --
    NEVER falls back to the mock: a missing Vertex configuration or the
    absence of the Google SDK raises, so a misconfigured deployment cannot
    answer with mock science.  Explicitly setting ``PHYTO_DRY_RUN=1`` on the
    cloud plane is the documented bring-up mode and is reported by
    ``/health`` as ``local-mock``.
    """
    env = os.environ.get("PHYTO_ENV", "local")
    explicit = os.environ.get("PHYTO_DRY_RUN")
    dry_run = (env != "cloud") if explicit is None else explicit == "1"
    if dry_run:
        return MockShiftPredictor()

    required = ("endpoint_id", "project")
    missing = [key for key in required if not kwargs.get(key)]
    if missing:
        raise CloudServiceError(
            "shift prediction is in real mode but is missing required "
            f"configuration: {', '.join(missing)}. Refusing to fall back to "
            "the mock predictor; set VERTEX_ENDPOINT_ID and "
            "GOOGLE_CLOUD_PROJECT, or run the local plane with "
            "PHYTO_DRY_RUN=1.",
            context={"env": env, "missing": missing},
        )
    try:
        import google.cloud.aiplatform  # noqa: F401
    except ImportError as exc:
        raise CloudServiceError(
            "shift prediction is in real mode but google-cloud-aiplatform "
            "is not installed; refusing to fall back to the mock predictor",
            context={"env": env},
        ) from exc
    return VertexAIShiftPredictor(**kwargs)
