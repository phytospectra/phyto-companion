"""ADMET prediction with honest unavailability.

No black-box confidence scores: either a real model reports its raw
predictions with the model's published performance, or the local plane
says so -- ``method="unavailable"`` with an explanatory note
(honesty guarantee 7).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ADMETResult", "predict_admet", "UNAVAILABLE_NOTE"]

UNAVAILABLE_NOTE = (
    "No ADMET model is deployed in this environment. Returning honest "
    "unavailability rather than a plausible-sounding guess. Wire "
    "ADMET-AI / admetSAR / SwissADME in the cloud plane (Step 8) and "
    "report the model version and its published performance alongside "
    "any prediction."
)


class ADMETResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smiles: str
    method: Literal["unavailable", "admet-ai", "admetsar", "swissadme"]
    predictions: dict[str, str] = Field(default_factory=dict)
    source: str
    model_version: str | None = None
    known_limitations: str = UNAVAILABLE_NOTE
    published_performance: str | None = Field(
        default=None,
        description="Cited validation metrics of the model, when a model "
        "is actually used",
    )


def predict_admet(smiles: str) -> ADMETResult:
    """Honest local result: method='unavailable', no predictions."""
    return ADMETResult(
        smiles=smiles,
        method="unavailable",
        predictions={},
        source="none (no model deployed)",
    )
