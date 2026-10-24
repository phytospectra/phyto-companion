"""Rule-based 1H NMR prediction engine + Lorentzian spectral simulation.

Two-tier design matching the platform's tool tiering (ADR-0071):

  1. Feature extraction (RDKit): every C-H atom is described by
     hybridization, aromaticity, proton count and neighbour corrections --
     the same local-electronic-environment idea as increment/group-
     additivity methods (Ho & Coombes lineage).
  2. Prediction core: a base-shift table per environment plus inductive
     corrections from electronegative neighbours.  This is the
     deterministic Tier-1 core: crude but auditable, and the documented
     extension point -- swap ``predict_shifts`` internals for a trained
     model (RandomForest/GNN over atom-centered Morgan fingerprints)
     without changing the interface or the simulation layer.
  3. Spectral simulation (numpy): first-order multiplet expansion via
     Pascal's triangle (J ~7 Hz) rendered as Lorentzian lineshapes.

Provenance: every returned prediction carries ``model_id`` so overlay
figures and reports can state exactly which predictor produced them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = ["PredictedPeak", "NMRPredictionEngine"]


@dataclass
class PredictedPeak:
    atom_id: str
    chemical_shift: float
    integration: int
    multiplicity: str
    vicinal_h_count: int


@dataclass
class NMRPredictionEngine:
    """Increment-based 1H predictor with Lorentzian spectrum simulation."""

    model_id: str = "increment-rules-0.1.0"

    base_shifts: dict[str, float] = field(default_factory=lambda: {
        "C_SP3_PRIMARY": 0.9,     # methyl -CH3
        "C_SP3_SECONDARY": 1.3,   # methylene -CH2-
        "C_SP3_TERTIARY": 1.5,    # methine -CH-
        "C_SP2_AROMATIC": 7.2,    # aromatic CH
        "C_SP2_OLEFINIC": 5.5,    # alkene CH
    })

    # ------------------------------------------------------------------
    # feature extraction
    # ------------------------------------------------------------------

    def _extract_atom_features(self, atom):
        """Local-environment features for one carbon; None when it has no H."""
        from rdkit import Chem

        if atom.GetSymbol() != "C":
            return None
        # includeNeighbors=True: after Chem.AddHs the protons are explicit
        # atoms and must be counted as neighbours, not implicit H
        num_hs = atom.GetTotalNumHs(includeNeighbors=True)
        if num_hs == 0:
            return None
        if atom.GetIsAromatic():
            env_key = "C_SP2_AROMATIC"
        elif atom.GetHybridization() == Chem.HybridizationType.SP2:
            env_key = "C_SP2_OLEFINIC"
        elif atom.GetHybridization() == Chem.HybridizationType.SP3:
            heavy_neighbours = len(atom.GetBonds()) - num_hs
            if heavy_neighbours <= 1:
                env_key = "C_SP3_PRIMARY"
            elif heavy_neighbours == 2:
                env_key = "C_SP3_SECONDARY"
            else:
                env_key = "C_SP3_TERTIARY"
        else:
            env_key = "C_SP3_PRIMARY"
        return {"env_key": env_key, "num_protons": num_hs, "atom_idx": atom.GetIdx()}

    # ------------------------------------------------------------------
    # prediction core
    # ------------------------------------------------------------------

    def predict_shifts(self, smiles: str) -> list[PredictedPeak]:
        """Predict 1H shifts/multiplicities for every protonated carbon."""
        from rdkit import Chem

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"Invalid SMILES string provided: {smiles!r}")
        mol = Chem.AddHs(mol)

        predictions: list[PredictedPeak] = []
        for atom in mol.GetAtoms():
            features = self._extract_atom_features(atom)
            if features is None:
                continue
            base_shift = self.base_shifts.get(features["env_key"], 1.0)

            # inductive correction from electronegative neighbours,
            # damped by substitution count
            neighbor_correction = 0.0
            for neighbor in atom.GetNeighbors():
                if neighbor.GetSymbol() in ("O", "N", "F", "Cl"):
                    neighbor_correction += 2.2 / max(len(atom.GetBonds()), 1)

            shift = base_shift + neighbor_correction

            # first-order multiplicity from vicinal protons (N+1 rule)
            vicinal_protons = 0
            for neighbor in atom.GetNeighbors():
                if neighbor.GetSymbol() == "C":
                    vicinal_protons += neighbor.GetTotalNumHs(includeNeighbors=True)
            multiplicity_map = {1: "s", 2: "d", 3: "t", 4: "q"}
            mult = multiplicity_map.get(vicinal_protons + 1, "m")

            predictions.append(PredictedPeak(
                atom_id=f"H_C{features['atom_idx']}",
                chemical_shift=round(shift, 3),
                integration=features["num_protons"],
                multiplicity=mult,
                vicinal_h_count=vicinal_protons,
            ))

        # first-order equivalence collapse: atoms whose environment yields
        # the identical (shift, multiplicity) are homotopic at this theory
        # level (e.g. benzene's six CH) -- report one entry with summed
        # integration instead of six 1H rows
        merged: dict[tuple[float, str, int], PredictedPeak] = {}
        for p in predictions:
            key = (p.chemical_shift, p.multiplicity, p.vicinal_h_count)
            if key in merged:
                merged[key].integration += p.integration
                merged[key].atom_id += f"/{p.atom_id}"
            else:
                merged[key] = p
        return [merged[k] for k in sorted(merged, reverse=True)]

    # ------------------------------------------------------------------
    # spectral simulation
    # ------------------------------------------------------------------

    @staticmethod
    def simulate_spectrum(
        predictions: list[PredictedPeak],
        ppm_start: float = 12.0,
        ppm_end: float = -1.0,
        points: int = 8000,
        line_width: float = 0.008,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Continuous Lorentzian lineshape with Pascal-triangle multiplets."""
        x_ppm = np.linspace(ppm_start, ppm_end, points)
        y_intensity = np.zeros(points)

        for peak in predictions:
            center = peak.chemical_shift
            vicinal = peak.vicinal_h_count
            j_spacing_ppm = 0.0175  # ~7 Hz at 400 MHz

            if vicinal == 0:
                sub_peaks = [(center, 1.0)]
            else:
                n = vicinal + 1
                coeffs = [math.comb(vicinal, i) for i in range(n)]
                total = sum(coeffs)
                sub_peaks = [
                    (center + (i - (n - 1) / 2) * j_spacing_ppm, coeffs[i] / total)
                    for i in range(n)
                ]

            for sub_center, weight in sub_peaks:
                amplitude = (peak.integration * weight) * (line_width / (2 * np.pi))
                y_intensity += amplitude / (
                    (x_ppm - sub_center) ** 2 + (line_width / 2) ** 2
                )

        if np.max(y_intensity) > 0:
            y_intensity = y_intensity / np.max(y_intensity)
        return x_ppm, y_intensity
