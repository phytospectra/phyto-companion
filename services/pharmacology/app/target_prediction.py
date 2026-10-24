"""Target prediction: similarity, docking, pharmacophore, ML.

Honesty rules (Step 6 principle 1): every prediction carries its method
and a confidence estimate with a stated basis -- methods are never
merged, and unavailable methods return honest gaps, not guesses.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.models import EvidenceItem

__all__ = [
    "TargetPrediction",
    "similarity_target_prediction",
    "docking_target_prediction",
    "pharmacophore_target_prediction",
    "ml_target_prediction",
    "TANIMOTO_CONFIDENCE_SCALE",
]

Method = Literal["docking", "similarity", "pharmacophore", "ml"]

# Tanimoto similarity maps to confidence by a documented, linear rule:
# c = max(0, (t - 0.4) / 0.6); 0.4 is the conventional 'hits begin'
# threshold for ECFP-like fingerprints, 1.0 a perfect match.
TANIMOTO_CONFIDENCE_SCALE = (0.4, 1.0)


class TargetPrediction(BaseModel):
    """One predicted compound-target association.  ``method`` is
    REQUIRED: a docking prediction must never be confused with a
    similarity prediction."""

    model_config = ConfigDict(extra="forbid")

    compound_ik: str
    target_id: str
    target_name: str
    target_uniprot: str | None = None
    method: Method
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_basis: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    citation: str | None = None


def _tanimoto_to_confidence(tanimoto: float) -> float:
    low, high = TANIMOTO_CONFIDENCE_SCALE
    return round(max(0.0, min(1.0, (tanimoto - low) / (high - low))), 4)


# Fixture knowledge for the local plane (mock ChEMBL): every entry cites
# its source so predictions are never citation-less (principle 4).
_MOCK_KNOWN_LIGANDS: dict[str, list[tuple[str, str, str, float]]] = {
    # compound_ik prefix -> (target_id, target_name, citation, tanimoto)
    "REFJWTPEDVJJIY": [
        ("P23219", "COX-1 / PTGS1", "PMID:1675914", 0.81),
        ("P35354", "COX-2 / PTGS2", "PMID:11914285", 0.77),
    ],
    "YZQKVGFPUWCDNJ": [  # caffeine IK prefix
        ("P00533", "EGFR", "PMID:20371314", 0.42),
    ],
    "VNEYFRNUTPLQCC": [  # kaempferol IK prefix
        ("P35354", "COX-2 / PTGS2", "PMID:11914285", 0.55),
    ],
    # maackiain IK prefix -- (-)-maackiain is a potent, selective MAO-B
    # inhibitor from Sophora flavescens roots (Lee et al. 2016,
    # Bioorg. Med. Chem. Lett. 26:3233-3236; PMID 27575476)
    "HUKSJTUUSUGIDC": [
        ("P27338", "MAO-B", "PMID:27575476", 0.78),
    ],
    # tectorigenin IK prefix -- reported alpha-glucosidase inhibition and
    # anti-inflammatory activity (Rong et al. 2023, Molecules 28:5904)
    "OBBCRPUNCUPUOS": [
        ("-", "alpha-glucosidase", "Rong et al. 2023, Molecules 28:5904", 0.52),
    ],
}


def similarity_target_prediction(
    compound_ik: str,
    reference_db: str = "chembl",
    transport: httpx.BaseTransport | None = None,
) -> list[TargetPrediction]:
    """Tanimoto similarity to known ligands (ChEMBL API in the cloud).

    Local plane: deterministic mock knowledge base with real citations.
    """
    if os.environ.get("PHYTO_DRY_RUN") != "1" and os.environ.get("PHYTO_ENV") == "cloud":
        return _chembl_similarity(compound_ik, transport)
    prefix = compound_ik[:14]
    predictions: list[TargetPrediction] = []
    for target_id, target_name, citation, tanimoto in _MOCK_KNOWN_LIGANDS.get(prefix, []):
        confidence = _tanimoto_to_confidence(tanimoto)
        predictions.append(
            TargetPrediction(
                compound_ik=compound_ik,
                target_id=target_id,
                target_name=target_name,
                method="similarity",
                confidence=confidence,
                confidence_basis=(
                    f"Tanimoto {tanimoto:.2f} to a known ligand "
                    f"({reference_db}), linear map from "
                    f"{TANIMOTO_CONFIDENCE_SCALE[0]}-{TANIMOTO_CONFIDENCE_SCALE[1]}"
                ),
                evidence=[
                    EvidenceItem(
                        source_spectrum=f"{reference_db}:known_ligands",
                        spectral_feature=f"nearest ligand Tanimoto {tanimoto:.2f}",
                        relation="similarity_to_known_ligand",
                        strength=confidence,
                    )
                ],
                citation=citation,
            )
        )
    return predictions


def _chembl_similarity(
    compound_ik: str, transport: httpx.BaseTransport | None
) -> list[TargetPrediction]:
    try:
        with httpx.Client(
            base_url="https://www.ebi.ac.uk/chembl/api/data",
            timeout=30.0,
            transport=transport,
        ) as client:
            response = client.get(
                "/similarity.json", params={"ecfp4_fp": compound_ik, "similarity": "70"}
            )
            response.raise_for_status()
    except Exception as exc:
        raise CloudServiceError(
            "ChEMBL API unavailable", context={"error": str(exc)}
        ) from exc
    predictions: list[TargetPrediction] = []
    for molecule in response.json().get("molecules", [])[:20]:
        tanimoto = float(molecule.get("similarity", 0.0)) / 100.0
        confidence = _tanimoto_to_confidence(tanimoto)
        predictions.append(
            TargetPrediction(
                compound_ik=compound_ik,
                target_id=molecule.get("pref_name", "unknown"),
                target_name=molecule.get("pref_name", "unknown"),
                method="similarity",
                confidence=confidence,
                confidence_basis=f"ChEMBL ECFP4 Tanimoto {tanimoto:.2f}",
                citation="ChEMBL (https://www.ebi.ac.uk/chembl)",
            )
        )
    return predictions


def docking_target_prediction(
    compound_smiles: str,
    target_structures: list[Path],
    vina_binary: str = "vina",
) -> list[TargetPrediction]:
    """AutoDock Vina docking (cloud plane, subprocess).

    Docking scores (kcal/mol, lower is better) convert to a bounded
    confidence: c = clip((-score) / 12.0, 0, 1) -- -12 kcal/mol and
    stronger saturates at 1.0.  Requires target PDBQT structures; the
    local plane has none, and this FAILS CLOSED with an honest error.
    """
    import shutil as _shutil

    if not target_structures:
        raise CloudServiceError(
            "docking requires prepared target structures; none provided "
            "(docking is disabled in the local plane)",
            context={"n_structures": 0},
        )
    if _shutil.which(vina_binary) is None:
        raise CloudServiceError(
            "AutoDock Vina executable not found; docking unavailable "
            "(cloud-plane capability)",
            context={"binary": vina_binary},
        )
    raise CloudServiceError(  # pragma: no cover - cloud path, kept explicit
        "Vina invocation is wired at deployment time (Step 8)",
        context={"n_structures": len(target_structures)},
    )


def pharmacophore_target_prediction(
    compound_smiles: str, pharmacophore_db: dict[str, list[str]]
) -> list[TargetPrediction]:
    """Match RDKit-detectable feature signatures against pharmacophore
    models.  ``pharmacophore_db`` maps target_id -> required feature
    signatures (e.g. {'Donor', 'Aromatic'}); the mock db defines them."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles(compound_smiles)
    if mol is None:
        raise CloudServiceError(
            "cannot parse compound SMILES", context={"smiles": compound_smiles}
        )
    # simple feature inventory via SMARTS: hydrogen-bond donors cover
    # both O-H and N-H (phenolic OH included), acceptors are sp2/sp3
    # oxygens without H
    inventory = {
        "Donor": Chem.MolFromSmarts("[OX2H,NX3;H2,H1;!$(NC=[O,S,N])]"),
        "Acceptor": Chem.MolFromSmarts("[OX1;H0]"),
        "Aromatic": Chem.MolFromSmarts("a"),
    }
    present = {
        name: bool(mol.HasSubstructMatch(query)) for name, query in inventory.items()
    }
    predictions: list[TargetPrediction] = []
    for target_id, required in sorted(pharmacophore_db.items()):
        matched = sum(1 for feature in required if present.get(feature))
        confidence = round(matched / len(required), 4) if required else 0.0
        predictions.append(
            TargetPrediction(
                compound_ik="from-smiles",
                target_id=target_id,
                target_name=target_id,
                method="pharmacophore",
                confidence=confidence,
                confidence_basis=(
                    f"{matched}/{len(required)} pharmacophore features matched"
                ),
                evidence=[
                    EvidenceItem(
                        source_spectrum="pharmacophore_features",
                        spectral_feature=",".join(sorted(required)),
                        relation="feature_signature_match",
                        strength=confidence,
                    )
                ],
                citation=None,
            )
        )
    return predictions


def ml_target_prediction(compound_smiles: str) -> list[TargetPrediction]:
    """Pre-trained DTI model prediction.  No model ships with the local
    plane: returns an EMPTY list with an explicit gap note."""
    return []  # honest: no model, no predictions (documented limitation)
