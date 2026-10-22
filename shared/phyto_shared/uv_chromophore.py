"""UV chromophore classification for reversed-phase HPLC-PDA (ADR-0062).

Rule set follows the classic phytochemistry literature:

* Mabry, Markham & Thomas (1970), *The Systematic Identification of
  Flavonoids*, Springer -- Band I (300-550 nm) / Band II (240-300 nm)
  positions and their diagnostic shifts.
* Harborne (1984), *Phytochemical Methods*, 2nd ed., Chapman & Hall --
  chromophore classes and their characteristic maxima.

The classifier reads a PDA spectrum as ``[(wavelength_nm, absorbance)]``
and returns a :class:`UVClass`.  It is deliberately a *screening* tool:

* Band I is the highest local maximum ABOVE 300 nm; Band II the highest
  local maximum in 240-300 nm.  ``band_ratio`` is Band I / Band II
  intensity.
* Visible absorption above 450 nm is the anthocyanin (flavylium) signal.
* Several resolved bands (>= 3 maxima) alongside a 240-265 nm Band II
  point to a xanthone; a Band I 305-345 with a 265-295 Band II to a
  coumarin.
* No resolved band above 240 nm is "terpene-like": the molecule has no
  useful UV chromophore.

Limits -- read before trusting a class
--------------------------------------
* Designed for reversed-phase HPLC-PDA in methanol/acetonitrile-water
  gradients.  Solvent systems shift lambda_max by roughly 5-15 nm
  (Harborne 1984); the rule windows are widened accordingly, but a class
  assigned within 5 nm of a window edge is genuinely ambiguous.
* Flavone and flavonol differ only by the 3-OH: their Band I windows
  overlap near 345-355 nm.  Without an authentic standard or a shift
  reagent (AlCl3/H3BO3, Mabry 1970) the classifier reports the nearer
  class and says so in ``diagnosis_notes``.
* Coumarin and phenolic-acid windows overlap near 325/290 nm; the
  classifier prefers the better-fitting window and flags the ambiguity.
* The SMILES path (:func:`predict_class_from_smiles`) is structural, not
  spectral: it can say a molecule *has* a flavonol skeleton even when a
  weak or overlapped spectrum would classify as unclassified.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "UVClassLabel",
    "UVClass",
    "CLASS_LABELS",
    "classify_uv",
    "predict_class_from_smiles",
]

UVClassLabel = Literal[
    "flavone",
    "flavonol",
    "flavanone",
    "isoflavone",
    "chalcone",
    "anthocyanin",
    "coumarin",
    "phenolic_acid",
    "alkaloid_isoquinoline",
    "alkaloid_indole",
    "xanthone",
    "terpene_like",
    "unclassified",
]

CLASS_LABELS: tuple[str, ...] = (
    "flavone",
    "flavonol",
    "flavanone",
    "isoflavone",
    "chalcone",
    "anthocyanin",
    "coumarin",
    "phenolic_acid",
    "alkaloid_isoquinoline",
    "alkaloid_indole",
    "xanthone",
    "terpene_like",
    "unclassified",
)


class UVClass(BaseModel):
    """The classifier's verdict on one PDA spectrum.

    ``band_i_nm``/``band_ii_nm`` are ``None`` when no resolved maximum
    exists in that window; ``band_ratio`` is ``None`` unless both exist.
    ``confidence`` is 0.0-1.0 and is what the UV reranker thresholds on
    (default 0.6).
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    primary_class: UVClassLabel
    confidence: float = Field(..., ge=0.0, le=1.0)
    band_i_nm: float | None = Field(default=None, ge=0.0)
    band_ii_nm: float | None = Field(default=None, ge=0.0)
    band_ratio: float | None = Field(default=None, ge=0.0)
    diagnosis_notes: str = ""

    @model_validator(mode="after")
    def _ratio_consistent(self) -> "UVClass":
        if self.band_ratio is not None and (
            self.band_i_nm is None or self.band_ii_nm is None
        ):
            raise ValueError(
                "band_ratio requires both band_i_nm and band_ii_nm"
            )
        return self


# class -> (Band I window, Band II window, Band I/II ratio window)
_PROFILES: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "flavonol": ((345.0, 390.0), (255.0, 290.0), (1.2, 2.6)),
    "flavone": ((305.0, 352.0), (240.0, 285.0), (1.05, 2.2)),
    "isoflavone": ((295.0, 330.0), (240.0, 275.0), (0.45, 1.0)),
    "flavanone": ((295.0, 335.0), (265.0, 300.0), (0.15, 0.95)),
    "chalcone": ((335.0, 395.0), (230.0, 270.0), (1.5, 3.5)),
    "coumarin": ((300.0, 350.0), (265.0, 292.0), (0.7, 1.8)),
    "phenolic_acid": ((305.0, 338.0), (285.0, 310.0), (0.6, 1.6)),
    "alkaloid_indole": ((275.0, 310.0), (250.0, 280.0), (0.4, 1.8)),
    "alkaloid_isoquinoline": ((295.0, 355.0), (250.0, 290.0), (0.5, 1.8)),
    "xanthone": ((295.0, 345.0), (235.0, 265.0), (1.0, 3.0)),
}

# Deterministic tie-break: the more specific / more common classes first.
# Anthocyanin is deliberately absent: the visible-absorption branch in
# classify_uv decides it before profile scoring, and it has no Band I/II
# profile -- keeping it here would KeyError the scorer on any spectrum
# where a profile loop reaches it.
_PRIORITY: tuple[str, ...] = (
    "chalcone",
    "flavonol",
    "flavone",
    "isoflavone",
    "flavanone",
    "xanthone",
    "coumarin",
    "phenolic_acid",
    "alkaloid_indole",
    "alkaloid_isoquinoline",
)

#: Below this maximum local prominence there is no meaningful chromophore.
_MIN_PROMINENCE_FRACTION = 0.10
#: Visible absorption must reach this fraction of the global maximum.
_VISIBLE_FRACTION = 0.20


def _smooth(ys: list[float]) -> list[float]:
    """Three-point moving average; PDA noise otherwise invents maxima."""
    if len(ys) < 3:
        return list(ys)
    out = [ys[0]]
    for i in range(1, len(ys) - 1):
        out.append((ys[i - 1] + ys[i] + ys[i + 1]) / 3.0)
    out.append(ys[-1])
    return out


def _local_maxima(xs: list[float], ys: list[float]) -> list[tuple[float, float]]:
    """Local maxima, plateau-safe (a flat top reports its first point)."""
    maxima: list[tuple[float, float]] = []
    for i, value in enumerate(ys):
        left = ys[i - 1] if i > 0 else float("-inf")
        right = ys[i + 1] if i < len(ys) - 1 else float("-inf")
        if value >= left and value > right:
            maxima.append((xs[i], value))
    return maxima


def _in_window(value: float, window: tuple[float, float]) -> bool:
    return window[0] <= value <= window[1]


def _margin_hit(value: float, window: tuple[float, float], margin: float) -> bool:
    return window[0] - margin <= value <= window[1] + margin


def _score_profile(
    band_i: float, band_ii: float, ratio: float,
    profile: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> tuple[int, list[str]]:
    i_window, ii_window, ratio_window = profile
    score = 0
    notes: list[str] = []
    if _in_window(band_i, i_window):
        score += 2
    elif _margin_hit(band_i, i_window, 6.0):
        score += 1
        notes.append("Band I near window edge")
    if _in_window(band_ii, ii_window):
        score += 2
    elif _margin_hit(band_ii, ii_window, 6.0):
        score += 1
        notes.append("Band II near window edge")
    if _in_window(ratio, ratio_window):
        score += 1
    return score, notes


def classify_uv(spectrum: list[tuple[float, float]]) -> UVClass:
    """Classify one PDA spectrum into a phytochemical chromophore class.

    See the module docstring for the rule set and its limits.  A flat or
    featureless spectrum returns ``terpene_like``; a spectrum with bands
    that fit no class returns ``unclassified`` with a low confidence.
    """
    points = sorted((float(w), float(a)) for w, a in spectrum if a >= 0.0)
    if not points:
        return UVClass(
            primary_class="unclassified",
            confidence=0.0,
            diagnosis_notes="empty spectrum",
        )
    xs = [p[0] for p in points]
    ys = _smooth([p[1] for p in points])
    global_max = max(ys)
    if global_max <= 0.0:
        return UVClass(
            primary_class="terpene_like",
            confidence=0.7,
            diagnosis_notes="no absorption above baseline: no useful chromophore",
        )

    # An endpoint maximum is an artefact of the acquisition window, not a
    # resolved band: on a monotonic ramp (or a flat trace) only the last
    # point qualifies as a local maximum, and counting it would turn a
    # featureless spectrum into a fake "one resolved band" case instead
    # of the terpene_like verdict the docstring promises.
    maxima = [
        (x, y)
        for x, y in _local_maxima(xs, ys)
        if y >= _MIN_PROMINENCE_FRACTION * global_max
        and xs[0] < x < xs[-1]
    ]

    band_i_candidates = [(x, y) for x, y in maxima if x > 300.0]
    band_ii_candidates = [(x, y) for x, y in maxima if 240.0 <= x <= 300.0]
    band_i = max(band_i_candidates, key=lambda p: p[1]) if band_i_candidates else None
    band_ii = max(band_ii_candidates, key=lambda p: p[1]) if band_ii_candidates else None
    band_i_nm = band_i[0] if band_i else None
    band_ii_nm = band_ii[0] if band_ii else None

    visible = [(x, y) for x, y in maxima if x >= 450.0]
    visible_peak = max(visible, key=lambda p: p[1]) if visible else None
    visible_present = bool(
        visible_peak and visible_peak[1] >= _VISIBLE_FRACTION * global_max
    )
    n_resolved_bands = len(maxima)

    # --- anthocyanin: visible flavylium absorption is decisive -------------
    if visible_present and visible_peak is not None:
        if 450.0 <= visible_peak[0] <= 570.0:
            notes = (
                f"visible absorption at {visible_peak[0]:.0f} nm "
                f"(Band I, {visible_peak[1]:.2f}) with Band II "
                f"{band_ii_nm:.0f} nm" if band_ii_nm else
                f"visible absorption at {visible_peak[0]:.0f} nm"
            )
            return UVClass(
                primary_class="anthocyanin",
                confidence=0.9 if band_ii_nm else 0.75,
                band_i_nm=round(visible_peak[0], 1),
                band_ii_nm=round(band_ii_nm, 1) if band_ii_nm else None,
                band_ratio=(
                    round(visible_peak[1] / band_ii[1], 3)
                    if band_ii and band_ii[1] > 0 else None
                ),
                diagnosis_notes=(
                    f"{notes}; flavylium cation (anthocyanin) requires an "
                    "acidic mobile phase to stay coloured"
                ),
            )

    # --- no resolved chromophore ------------------------------------------
    if band_i is None and band_ii is None:
        return UVClass(
            primary_class="terpene_like",
            confidence=0.7,
            diagnosis_notes=(
                "no resolved maximum above 240 nm within 10% of the global "
                "maximum: terpene-like or otherwise chromophore-poor"
            ),
        )

    # --- score every profile that needs both bands ------------------------
    if band_i is None or band_ii is None or band_ii[1] <= 0.0:
        return UVClass(
            primary_class="unclassified",
            confidence=0.3,
            band_i_nm=round(band_i[0], 1) if band_i else None,
            band_ii_nm=round(band_ii[0], 1) if band_ii else None,
            diagnosis_notes=(
                "only one resolved band; the class rule set needs Band I and "
                "Band II.  Acquire a wider PDA range or a stronger injection."
            ),
        )

    ratio = band_i[1] / band_ii[1]
    best_class = "unclassified"
    best_score = 0
    best_notes: list[str] = []
    for class_name in _PRIORITY:
        profile = _PROFILES[class_name]
        if class_name == "xanthone" and n_resolved_bands < 3:
            continue
        score, notes = _score_profile(band_i_nm, band_ii_nm, ratio, profile)
        if score > best_score:
            best_class, best_score, best_notes = class_name, score, notes

    # Flavone/flavonol boundary: Band I 345-355 fits both windows.
    if (
        345.0 <= band_i_nm <= 355.0
        and best_class in {"flavone", "flavonol"}
    ):
        best_notes.append(
            "Band I 345-355 nm is the flavone/flavonol boundary; without a "
            "standard or AlCl3 shift reagent this assignment is ambiguous"
        )
    if best_class in {"coumarin", "phenolic_acid"} and 285.0 <= band_ii_nm <= 295.0:
        best_notes.append(
            "Band II 285-295 nm overlaps the coumarin / phenolic-acid windows"
        )

    if best_score < 3:
        return UVClass(
            primary_class="unclassified",
            confidence=0.3,
            band_i_nm=round(band_i_nm, 1),
            band_ii_nm=round(band_ii_nm, 1),
            band_ratio=round(ratio, 3),
            diagnosis_notes=(
                f"Bands {band_i_nm:.0f}/{band_ii_nm:.0f} nm (ratio "
                f"{ratio:.2f}) fit no class window; "
                + ("; ".join(best_notes) if best_notes else "check solvent and gradient")
            ),
        )

    confidence = min(0.95, max(0.35, best_score / 5.0))
    if best_notes:
        confidence = min(confidence, 0.7)
    diagnosis = (
        f"Band I {band_i_nm:.0f} nm, Band II {band_ii_nm:.0f} nm, ratio "
        f"{ratio:.2f} -> {best_class} (score {best_score}/5); "
        "reversed-phase MeOH/MeCN-water PDA (solvent shifts 5-15 nm)"
    )
    if best_notes:
        diagnosis += "; " + "; ".join(dict.fromkeys(best_notes))

    return UVClass(
        primary_class=best_class,  # type: ignore[arg-type]
        confidence=round(confidence, 3),
        band_i_nm=round(band_i_nm, 1),
        band_ii_nm=round(band_ii_nm, 1),
        band_ratio=round(ratio, 3),
        diagnosis_notes=diagnosis,
    )


# ---------------------------------------------------------------------------
# Structural class prediction from SMILES
# ---------------------------------------------------------------------------

# SMARTS were verified against RDKit's aromatic perception (RDKit kekulizes
# chromen-4-ones with a lowercase pyranone ring).  Several patterns in the
# task spec needed this adjustment; see tests/test_uv_chromophore.py.
_SMARTS: dict[str, tuple[str, ...]] = {
    "flavonol": ("O=c1c([OX2H])c(-[#6])oc2ccccc12",),
    "flavone": ("O=c1cc(-[#6])oc2ccccc12",),
    "isoflavone": ("O=c1c(-[#6])coc2ccccc12",),
    "flavanone": ("O=C1CC(-[#6])Oc2ccccc12",),
    "chalcone": ("[#6]C(=O)C=C[#6]",),
    "coumarin": ("O=c1ccc2ccccc2o1",),
    "anthocyanin": ("c1cc[n+]c2ccccc12",),
    "xanthone": ("O=c1c2ccccc2oc2ccccc12",),
    "phenolic_acid": (
        "[OX2H]C(=O)C=Cc1ccccc1",   # hydroxycinnamic acid
        "[OX2H]C(=O)c1ccccc1",      # hydroxybenzoic acid
    ),
    "alkaloid_isoquinoline": (
        "c1ccc2c(c1)cc[nH]2",       # isoquinoline (neutral)
        "c1ccc2c(c1)cc[n+]2",       # isoquinoline / berberine quaternary
        "c1cnc2ccccc2c1",           # benzyl-isoquinoline ring fusion
                                    # (papaverine-style: N in the pyridine
                                    # ring, fused, substitution-tolerant)
        "C1=NCCc2ccccc21",          # 3,4-dihydroisoquinoline
    ),
    "alkaloid_indole": (
        "c1ccc2[nH]ccc2c1",         # indole
        "c1ccc2[nH]c(C)cc2c1",
    ),
}


def _compiled_smarts() -> dict[str, list]:
    from rdkit import Chem

    cache = getattr(_compiled_smarts, "_cache", None)
    if cache is None:
        cache = {
            name: [p for p in (Chem.MolFromSmarts(s) for s in patterns) if p]
            for name, patterns in _SMARTS.items()
        }
        _compiled_smarts._cache = cache  # type: ignore[attr-defined]
    return cache


def predict_class_from_smiles(smiles: str) -> list[str]:
    """Structural chromophore classes implied by a SMILES string.

    Returns every matched class (a flavonol also matches the flavonoid
    core), ordered most-specific first.  A molecule with no aromatic
    atom and no conjugated carbonyl is reported as ``terpene_like``.
    An unparseable SMILES returns ``[]`` -- never a guessed class.
    """
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return []
    matched = [
        name
        for name, patterns in _compiled_smarts().items()
        if any(mol.HasSubstructMatch(pattern) for pattern in patterns)
    ]
    if not matched:
        aromatic = any(atom.GetIsAromatic() for atom in mol.GetAtoms())
        conjugated_carbonyl = Chem.MolFromSmarts("[#6]=[#6]-[#6]=[O,N]")
        if not aromatic and not mol.HasSubstructMatch(conjugated_carbonyl):
            return ["terpene_like"]
    order = {name: i for i, name in enumerate(_SMARTS)}
    return sorted(matched, key=lambda name: order.get(name, 99))
