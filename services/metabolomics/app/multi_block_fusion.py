"""Multi-block fusion of NMR, LC-MS, and IR blocks from the same samples.

DIABLO (mixOmics) is NOT implemented: the reference implementation is R
under GPL-3 and there is no permissively-licensed Python port; loading it
via rpy2 would make the service GPL-dependent.  Per the Step 6
specification, PLS-DA on concatenated scaled blocks is the documented
substitute (ADR-0018).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import InvalidInputError

__all__ = [
    "MFAResult",
    "ConsensusPCAResult",
    "FusionTestResult",
    "DIABLO_UNAVAILABLE_REASON",
    "mfa",
    "consensus_pca",
    "pls_da_concat",
    "permutation_test_fusion",
]

DIABLO_UNAVAILABLE_REASON = (
    "DIABLO requires the GPL-licensed mixOmics R package (via rpy2); no "
    "permissively-licensed Python implementation exists. PLS-DA on "
    "concatenated scaled blocks (pls_da_concat) is the substitute."
)


class MFAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explained_variance_ratio: list[float]
    scores: list[list[float]]
    block_names: list[str]
    block_scalings: list[float]  # first singular value per block


class ConsensusPCAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explained_variance_ratio: list[float]
    scores: list[list[float]]
    block_saliences: list[list[float]]  # block x component contributions


class FusionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fused_q2: float
    best_single_block: str
    best_single_q2: float
    permutation_p_value: float
    n_permutations: int
    fusion_better: bool


def _validate_blocks(blocks: dict[str, np.ndarray]) -> tuple[list[str], list[np.ndarray], int]:
    if not blocks:
        raise InvalidInputError("no blocks given", context={})
    names = sorted(blocks)
    arrays = [np.asarray(blocks[name], dtype=float) for name in names]
    n_samples = arrays[0].shape[0]
    for name, array in zip(names, arrays):
        if array.ndim != 2 or array.shape[0] != n_samples:
            raise InvalidInputError(
                "all blocks must be 2-D with the same number of samples",
                context={"block": name, "shape": list(array.shape)},
            )
    return names, arrays, n_samples


def _scale_blocks(arrays: list[np.ndarray]) -> tuple[list[np.ndarray], list[float]]:
    """Scale each block by its first singular value (MFA convention)."""
    scaled = []
    scalings = []
    for array in arrays:
        first_sv = float(np.linalg.svd(array, compute_uv=False)[0])
        if first_sv <= 0:
            first_sv = 1.0
        scaled.append(array / first_sv)
        scalings.append(first_sv)
    return scaled, scalings


def mfa(blocks: dict[str, np.ndarray], n_components: int = 3) -> MFAResult:
    """Multiple Factor Analysis: per-block first-SV scaling, concatenation,
    PCA."""
    from sklearn.decomposition import PCA

    names, arrays, _ = _validate_blocks(blocks)
    scaled, scalings = _scale_blocks(arrays)
    combined = np.hstack(scaled)
    n_components = max(1, min(n_components, min(combined.shape)))
    model = PCA(n_components=n_components)
    scores = model.fit_transform(combined)
    return MFAResult(
        explained_variance_ratio=[float(v) for v in model.explained_variance_ratio_],
        scores=[[float(v) for v in row] for row in scores],
        block_names=names,
        block_scalings=scalings,
    )


def consensus_pca(blocks: dict[str, np.ndarray], n_components: int = 3) -> ConsensusPCAResult:
    """Consensus PCA on scaled concatenated blocks with block saliences:
    the fraction of each component's loading norm contributed per block."""
    from sklearn.decomposition import PCA

    names, arrays, _ = _validate_blocks(blocks)
    scaled, _ = _scale_blocks(arrays)
    combined = np.hstack(scaled)
    n_components = max(1, min(n_components, min(combined.shape)))
    model = PCA(n_components=n_components)
    scores = model.fit_transform(combined)
    loadings = model.components_  # components x features
    saliences: list[list[float]] = []
    start = 0
    slices: list[slice] = []
    for array in scaled:
        slices.append(slice(start, start + array.shape[1]))
        start += array.shape[1]
    for component in range(n_components):
        total = float(np.sum(loadings[component] ** 2)) or 1.0
        row = [
            round(float(np.sum(loadings[component, s] ** 2) / total), 4)
            for s in slices
        ]
        saliences.append(row)
    return ConsensusPCAResult(
        explained_variance_ratio=[float(v) for v in model.explained_variance_ratio_],
        scores=[[float(v) for v in row] for row in scores],
        block_saliences=saliences,
    )


def pls_da_concat(
    blocks: dict[str, np.ndarray],
    labels: np.ndarray,
    n_components: int = 2,
) -> dict:
    """PLS-DA substitute for DIABLO on concatenated scaled blocks.

    Returns per-block Q2 (each block alone) and the fused Q2.
    """
    from sklearn.cross_decomposition import PLSRegression
    from sklearn.model_selection import cross_val_predict
    from sklearn.preprocessing import LabelEncoder

    def q2_of(matrix: np.ndarray) -> float:
        encoded = LabelEncoder().fit_transform(labels).astype(float)
        components = max(1, min(n_components, min(matrix.shape) - 1))
        pls = PLSRegression(n_components=components)
        try:
            predicted = cross_val_predict(pls, matrix, encoded, cv=3)
            return float(
                1.0
                - np.sum((encoded - predicted) ** 2)
                / np.sum((encoded - encoded.mean()) ** 2)
            )
        except Exception:
            return float("nan")

    names, arrays, _ = _validate_blocks(blocks)
    scaled, _ = _scale_blocks(arrays)
    single = {name: q2_of(array) for name, array in zip(names, scaled)}
    fused = q2_of(np.hstack(scaled))
    return {"fused_q2": fused, "single_block_q2": single}


def permutation_test_fusion(
    blocks: dict[str, np.ndarray],
    labels: np.ndarray,
    n_permutations: int = 100,
) -> FusionTestResult:
    """The statistical 'fusion helps' test: is the fused model's Q2 better
    than the best single block, beyond label-shuffling chance?"""
    from sklearn.cross_decomposition import PLSRegression
    from sklearn.model_selection import cross_val_predict
    from sklearn.preprocessing import LabelEncoder

    names, arrays, _ = _validate_blocks(blocks)
    if len(np.unique(labels)) != 2:
        raise InvalidInputError(
            "permutation_test_fusion expects two classes",
            context={"classes": list(np.unique(labels))},
        )
    scaled, _ = _scale_blocks(arrays)
    combined = np.hstack(scaled)

    def q2_of(matrix: np.ndarray, y: np.ndarray) -> float:
        pls = PLSRegression(n_components=min(2, min(matrix.shape) - 1))
        try:
            predicted = cross_val_predict(pls, matrix, y, cv=3)
            return float(
                1.0 - np.sum((y - predicted) ** 2) / np.sum((y - y.mean()) ** 2)
            )
        except Exception:
            return float("nan")

    encoded = LabelEncoder().fit_transform(labels).astype(float)
    fused_q2 = q2_of(combined, encoded)
    single = {name: q2_of(array, encoded) for name, array in zip(names, scaled)}
    best_name = max(single, key=single.get)
    best_q2 = single[best_name]

    rng = np.random.default_rng(42)
    exceed = 0
    for _ in range(n_permutations):
        shuffled = rng.permutation(encoded)
        if q2_of(combined, shuffled) >= fused_q2:
            exceed += 1
    p_value = (1 + exceed) / (1 + n_permutations)
    return FusionTestResult(
        fused_q2=round(fused_q2, 4) if np.isfinite(fused_q2) else -1.0,
        best_single_block=best_name,
        best_single_q2=round(best_q2, 4) if np.isfinite(best_q2) else -1.0,
        permutation_p_value=round(p_value, 4),
        n_permutations=n_permutations,
        fusion_better=bool(fused_q2 > best_q2),
    )
