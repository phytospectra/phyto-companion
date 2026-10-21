"""Foundational constants for the PhytoCompanion platform.

This module is the single source of truth for:

1.  Solvent reference positions (residual 1H / 13C signals)
2.  Adduct definitions for singly charged ions
3.  Element monoisotopic masses, natural abundances and RDBE valences
4.  MSI confidence levels (Sumner et al., Metabolomics 2007)
5.  Tool tier levels (deterministic / ML-validated / LLM-generated)
6.  Confidence verdicts and the evidence each one requires
7.  A minimal set of physical constants for future NMR calculations

Downstream modules MUST import from here.  Hardcoding any of these values
elsewhere in the codebase violates the contributing rules in the README.

Standard library only: this module is the foundation everything else builds
on and must stay importable with zero third-party dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .exceptions import InvalidInputError

__all__ = [
    "SolventReference",
    "AdductInfo",
    "ElementInfo",
    "MSIConfidenceLevel",
    "ToolTier",
    "ConfidenceVerdict",
    "SOLVENT_REFERENCES",
    "ADDUCT_MASSES",
    "ELEMENT_MASSES",
    "ELEMENT_VALENCES",
    "MSI_LEVEL_REQUIREMENTS",
    "TOOL_TIER_REQUIREMENTS",
    "CONFIDENCE_VERDICT_REQUIREMENTS",
    "NOVELTY_STATUS_DEFINITIONS",
    "NoveltyStatus",
    "solvent_lookup",
    "ObservationModality",
    "MODALITY_FAMILIES",
    "MODALITY_REQUIREMENTS",
    "NMR_MODALITIES",
    "MS_MODALITIES",
    "modality_families",
    "is_single_family",
    "single_family_gap",
    "parse_observation_modalities",
]


# ---------------------------------------------------------------------------
# 1. Solvent reference positions (ppm, relative to TMS)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolventReference:
    """Residual solvent peak positions.

    ``h_ppm`` / ``c_ppm`` are tuples because some solvents show several
    residual signals (e.g. pyridine-d5).  Solvents with no observable 13C
    residual (D2O) carry an empty ``c_ppm`` tuple.
    """

    h_ppm: tuple[float, ...]
    c_ppm: tuple[float, ...]
    notes: str = ""


# Every value below matches the project specification; the two extra 13C
# entries (CD3CN nitrile, acetone-d6 carbonyl) are documented in ADR-0003.
SOLVENT_REFERENCES: Mapping[str, SolventReference] = MappingProxyType(
    {
        "CDCl3": SolventReference(
            h_ppm=(7.26,),
            c_ppm=(77.16,),
            notes="Residual CHCl3; 13C appears as a triplet around 77.16.",
        ),
        "CD3OD": SolventReference(
            h_ppm=(3.31,),
            c_ppm=(49.00,),
            notes="Residual CD2HOD.",
        ),
        "DMSO-d6": SolventReference(
            h_ppm=(2.50,),
            c_ppm=(39.52,),
            notes="Residual DMSO-d5; 13C is a multiplet near 39.52.",
        ),
        "Acetone-d6": SolventReference(
            h_ppm=(2.05,),
            c_ppm=(29.84, 206.26),
            notes="29.84 = methyl (multiplet); 206.26 = carbonyl carbon, "
            "included for quaternary-carbon screening (ADR-0003).",
        ),
        "C5D5N": SolventReference(
            h_ppm=(8.74, 7.58, 7.22),
            c_ppm=(150.35, 135.91, 123.87),
            notes="Pyridine-d5; three inequivalent positions in both nuclei.",
        ),
        "C6D6": SolventReference(
            h_ppm=(7.16,),
            c_ppm=(128.06,),
            notes="Benzene-d6; residual C6D5H.",
        ),
        "CD3CN": SolventReference(
            h_ppm=(1.94,),
            c_ppm=(1.32, 118.69),
            notes="1.32 = methyl; 118.69 = nitrile carbon, included for "
            "quaternary-carbon screening (ADR-0003).",
        ),
        "D2O": SolventReference(
            h_ppm=(4.79,),
            c_ppm=(),
            notes="HDO has no fixed position: it is pH- and temperature-"
            "dependent.  Never treat 4.79 as an absolute reference; use a "
            "internal standard (e.g. TSP/DSS) where possible.",
        ),
    }
)


def solvent_lookup(name: str) -> SolventReference:
    """Return the solvent reference for ``name``.

    Matching is case-insensitive and ignores hyphens/whitespace, so
    ``"DMSO-d6"``, ``"dmso-d6"`` and ``"DMSOD6"`` all resolve to the same
    entry.  Raises ``KeyError`` listing the known solvents otherwise.
    """
    key = name.strip().lower().replace("-", "").replace(" ", "")
    for stored, ref in SOLVENT_REFERENCES.items():
        if stored.lower().replace("-", "").replace(" ", "") == key:
            return ref
    raise KeyError(
        f"Unknown solvent {name!r}. Known solvents: {sorted(SOLVENT_REFERENCES)}"
    )


# ---------------------------------------------------------------------------
# 2. Adducts (singly charged)
#
# Mass deltas are computed from IUPAC monoisotopic atomic masses and the
# electron rest mass:
#     cation  m(delta) = sum(atoms) - m_e        (one electron removed)
#     anion   m(delta) = sum(atoms) + m_e        (one electron added)
# with m_e = 0.000548579909 Da.  Example:
#     [M+H]+  : 1.00782503223 - 0.00054857991 = 1.00727645  -> +1.007276
#     [M+Cl]- : 34.968852682  + 0.00054857991 = 34.96940126 -> +34.969401
# All values rounded to 6 decimal places.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdductInfo:
    """An adduct species attached to a neutral molecule M.

    ``mass_delta`` is added to the neutral exact mass; the ion m/z is then
    ``(M + mass_delta) / |charge|``.
    """

    name: str
    mass_delta: float
    charge: int  # +1 or -1 for now; the field allows future z=2 species
    polarity: str  # "positive" | "negative"


ADDUCT_MASSES: Mapping[str, AdductInfo] = MappingProxyType(
    {
        "[M+H]+": AdductInfo("[M+H]+", 1.007276, 1, "positive"),
        "[M-H]-": AdductInfo("[M-H]-", -1.007276, -1, "negative"),
        "[M+Na]+": AdductInfo("[M+Na]+", 22.989221, 1, "positive"),
        "[M+K]+": AdductInfo("[M+K]+", 38.963158, 1, "positive"),
        "[M+NH4]+": AdductInfo("[M+NH4]+", 18.033826, 1, "positive"),
        "[M+HCOO]-": AdductInfo("[M+HCOO]-", 44.998203, -1, "negative"),
        "[M+Cl]-": AdductInfo("[M+Cl]-", 34.969401, -1, "negative"),
    }
)


# ---------------------------------------------------------------------------
# 3. Element monoisotopic masses and natural abundances
#
# Monoisotopic mass = mass of the most abundant stable isotope.  Note the
# Se subtlety: its most abundant isotope (80Se) is NOT its lightest one
# (74Se), so for Se-containing molecules the monoisotopic peak is not the
# lowest-mass isotope peak.  Selenium's abundance reflects 80Se.
# Sources: IUPAC 2021 / CIAAW values, rounded as stored.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementInfo:
    symbol: str
    monoisotopic_mass: float  # Da
    natural_abundance: float  # fraction of the monoisotopic isotope, 0..1
    isotope: str  # nuclide carrying that mass, e.g. "12C"


ELEMENT_MASSES: Mapping[str, ElementInfo] = MappingProxyType(
    {
        "H": ElementInfo("H", 1.00782503223, 0.999885, "1H"),
        "B": ElementInfo("B", 11.00930536, 0.8009, "11B"),
        "C": ElementInfo("C", 12.00000000, 0.9893, "12C"),
        "N": ElementInfo("N", 14.00307400443, 0.996360, "14N"),
        "O": ElementInfo("O", 15.99491461957, 0.997570, "16O"),
        "F": ElementInfo("F", 18.99840322773, 1.0, "19F"),
        "Si": ElementInfo("Si", 27.97692653465, 0.92223, "28Si"),
        "P": ElementInfo("P", 30.97376199842, 1.0, "31P"),
        "S": ElementInfo("S", 31.9720711744, 0.9499, "32S"),
        "Se": ElementInfo("Se", 79.9165218, 0.4961, "80Se"),
        "Cl": ElementInfo("Cl", 34.968852682, 0.7576, "35Cl"),
        "Br": ElementInfo("Br", 78.9183376, 0.5069, "79Br"),
        "I": ElementInfo("I", 126.9044719, 1.0, "127I"),
    }
)

# Typical valences used by the ring-double-bond-equivalent (RDBE) formula:
#     RDBE = 1 + 0.5 * sum(n_i * (v_i - 2))
# Heuristic only: P is stored at its trivalent value (phosphines) and will
# under-count pentavalent phosphates; Si and B follow organic conventions.
ELEMENT_VALENCES: Mapping[str, int] = MappingProxyType(
    {
        "H": 1,
        "B": 3,
        "C": 4,
        "N": 3,
        "O": 2,
        "F": 1,
        "Si": 4,
        "P": 3,
        "S": 2,
        "Se": 2,
        "Cl": 1,
        "Br": 1,
        "I": 1,
    }
)


# ---------------------------------------------------------------------------
# 4. MSI confidence levels (Sumner et al., Metabolomics 3:161, 2007)
# ---------------------------------------------------------------------------


class MSIConfidenceLevel(IntEnum):
    """Metabolite Standards Initiative confidence levels.

    Level 1 is the only level at which PhytoCompanion may report a compound
    as *confirmed* -- and only with recorded human-in-the-loop approval.
    """

    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4


MSI_LEVEL_REQUIREMENTS: Mapping[MSIConfidenceLevel, str] = MappingProxyType(
    {
        MSIConfidenceLevel.LEVEL_1: (
            "Identified compound: agreement with an authentic reference "
            "standard analysed under identical conditions in >= 2 orthogonal "
            "properties (e.g. retention time + MS/MS, or NMR consistency). "
            "Requires recorded human sign-off before the platform reports "
            "'confirmed'."
        ),
        MSIConfidenceLevel.LEVEL_2: (
            "Putatively annotated compound: probable identity from spectral "
            "similarity to library/literature data (GNPS library hit, "
            "NMRShiftDB2/COCONUT match, SUMMIT cross-modal match) without an "
            "in-house authentic standard."
        ),
        MSIConfidenceLevel.LEVEL_3: (
            "Putatively characterized compound class: class/subclass "
            "assignment from physicochemical properties and spectral evidence "
            "(building-block fragments, HSQC network neighbourhood), without "
            "justification for a specific structure."
        ),
        MSIConfidenceLevel.LEVEL_4: (
            "Unknown compound: evidence insufficient even for a class-level "
            "assignment."
        ),
    }
)


# ---------------------------------------------------------------------------
# 5. Tool tier levels
# ---------------------------------------------------------------------------


class ToolTier(IntEnum):
    """How much verification a tool's output requires before use.

    Tier 1 tools are exact algorithms; Tier 2 are statistical models with
    quantified confidence; Tier 3 generate plausible content that must be
    independently verified before it may influence any claim.
    """

    TIER_1_DETERMINISTIC = 1
    TIER_2_ML_VALIDATED = 2
    TIER_3_LLM_GENERATED = 3

    @property
    def requires_verification(self) -> bool:
        return self is not ToolTier.TIER_1_DETERMINISTIC

    @property
    def verification_kind(self) -> str:
        return TOOL_TIER_REQUIREMENTS[self].split(":", 1)[1].strip().lower()


TOOL_TIER_REQUIREMENTS: Mapping[ToolTier, str] = MappingProxyType(
    {
        ToolTier.TIER_1_DETERMINISTIC: (
            "Deterministic tools (RDKit, exact-mass arithmetic, statistical "
            "tests): used without additional verification."
        ),
        ToolTier.TIER_2_ML_VALIDATED: (
            "ML-validated tools (ShiftMPNN, SIRIUS): use only with reported "
            "confidence scores and a documented fallback path."
        ),
        ToolTier.TIER_3_LLM_GENERATED: (
            "LLM-generated content (hypotheses, interpretations): mandatory "
            "QA/Critic verification before any downstream use; may never "
            "alter data or scores."
        ),
    }
)


# ---------------------------------------------------------------------------
# 6. Confidence verdicts
# ---------------------------------------------------------------------------


class ConfidenceVerdict(StrEnum):
    """The honest endpoint of an identification decision (confidence axis).

    These are the only words the platform may use when describing how well
    evidence supports a structure.  ``CONFIRMED`` is reserved for MSI Level 1
    evidence with human approval.  Novelty is deliberately NOT one of these
    values: it is a separate, orthogonal axis (``NoveltyStatus``), because a
    compound can be CONFIRMED and novel (known structure, new natural
    source) or CANDIDATE and known (ADR-0006).
    """

    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    AMBIGUOUS = "ambiguous"
    CANDIDATE = "candidate"
    ABSTAIN = "abstain"


CONFIDENCE_VERDICT_REQUIREMENTS: Mapping[ConfidenceVerdict, str] = MappingProxyType(
    {
        ConfidenceVerdict.CONFIRMED: (
            "MSI Level 1 evidence: authentic-standard agreement in >= 2 "
            "orthogonal properties, unique top-1 rank with margin >= "
            "MIN_MARGIN_FOR_CONFIRMED, and recorded human-in-the-loop "
            "approval."
        ),
        ConfidenceVerdict.TENTATIVE: (
            "Single best-supported structure at MSI Level 2/3: coverage >= "
            "MIN_COVERAGE_FOR_TENTATIVE and margin >= "
            "MIN_MARGIN_FOR_TENTATIVE; not verified against a standard."
        ),
        ConfidenceVerdict.AMBIGUOUS: (
            "Two or more candidates statistically tied; evidence "
            "insufficient to distinguish them. Reported as a set -- never "
            "silently resolved to one member."
        ),
        ConfidenceVerdict.CANDIDATE: (
            "Plausible structure from enumeration/search with partial "
            "evidence (coverage >= MIN_COVERAGE_FOR_CANDIDATE), or any "
            "Tier-3 (LLM) origin; supporting and contradicting evidence "
            "must both be listed."
        ),
        ConfidenceVerdict.ABSTAIN: (
            "Evidence insufficient for any structural claim (coverage < "
            "MIN_COVERAGE_FOR_CANDIDATE) or a tool-tier violation occurred; "
            "no claim is made."
        ),
    }
)


# ---------------------------------------------------------------------------
# 6b. Novelty status (orthogonal to confidence; ADR-0006)
# ---------------------------------------------------------------------------

NoveltyStatus = Literal["novel", "known", "uncertain"]
"""Whether a compound is known, possibly novel, or not assessed.

Deliberately separate from ``ConfidenceVerdict``: confidence says how well
the evidence supports the structure; novelty says whether that structure
has literature precedent.  All combinations of the two axes are
legitimate and must be reported independently.
"""

NOVELTY_STATUS_DEFINITIONS: Mapping[str, str] = MappingProxyType(
    {
        "novel": (
            "No adequate match in the referenced libraries or literature "
            "after a documented search. The platform never claims novelty; "
            "the flag exists for human evaluation."
        ),
        "known": (
            "Matched to a known compound (library entry or literature "
            "precedent), independent of the confidence of that match."
        ),
        "uncertain": (
            "Novelty not assessed: libraries were not searched or the "
            "search was inconclusive."
        ),
    }
)


# ---------------------------------------------------------------------------
# 7. Physical constants (minimal, for future NMR calculations)
# ---------------------------------------------------------------------------

# SI 2019 defining constants (exact by definition).
BOLTZMANN_CONSTANT_J_PER_K = 1.380649e-23
PLANCK_CONSTANT_J_S = 6.62607015e-34
AVOGADRO_NUMBER = 6.02214076e23
SPEED_OF_LIGHT_M_PER_S = 299_792_458.0

# Electron rest mass in daltons (CODATA), used in adduct-mass arithmetic.
ELECTRON_MASS_DA = 0.000548579909

# Proton gyromagnetic ratio (CODATA 2018) and the equivalent Larmor
# frequency per tesla -- the basis of any future resonance-frequency math.
PROTON_GYROMAGNETIC_RATIO_RAD_S_PER_T = 2.6752218744e8
PROTON_LARMOR_FREQUENCY_MHZ_PER_T = 42.57747892


# ---------------------------------------------------------------------------
# 8. Observation modalities (NMR-only / MS-only / combined)
# ---------------------------------------------------------------------------
#
# Every user is allowed to arrive with one modality.  The platform must then
# say what it did and what it could NOT check, rather than quietly running a
# fused workflow on half the evidence.  These names are the shared vocabulary
# for that: the elucidation ranker reports which ones it evaluated, and the
# orchestrator planner uses them to decide which tasks are even possible.


class ObservationModality(StrEnum):
    """One kind of observation a user can supply for a sample."""

    H_1D = "h_1d"
    C_1D = "c_1d"
    HSQC = "hsqc"
    MS1 = "ms1"
    MS2 = "ms2"


MODALITY_FAMILIES: Mapping[ObservationModality, Literal["NMR", "MS"]] = MappingProxyType(
    {
        ObservationModality.H_1D: "NMR",
        ObservationModality.C_1D: "NMR",
        ObservationModality.HSQC: "NMR",
        ObservationModality.MS1: "MS",
        ObservationModality.MS2: "MS",
    }
)
"""Which instrument family each modality belongs to."""

NMR_MODALITIES: frozenset[ObservationModality] = frozenset(
    {ObservationModality.H_1D, ObservationModality.C_1D, ObservationModality.HSQC}
)

MS_MODALITIES: frozenset[ObservationModality] = frozenset(
    {ObservationModality.MS1, ObservationModality.MS2}
)

MODALITY_REQUIREMENTS: Mapping[ObservationModality, str] = MappingProxyType(
    {
        ObservationModality.H_1D: (
            "Observed 1H chemical shifts (ppm). Supports ranking against "
            "predicted shifts; too few resolved signals to stand alone."
        ),
        ObservationModality.C_1D: (
            "Observed 13C chemical shifts (ppm). Tight tolerance is used when "
            "an HSQC or multiplicity-edited list establishes CH/CH2/CH3, "
            "otherwise a loose full-range tolerance applies."
        ),
        ObservationModality.HSQC: (
            "Observed 1H-13C correlation pairs. The strongest structural "
            "evidence available without a standard; drives the matching "
            "distance and the NMR network."
        ),
        ObservationModality.MS1: (
            "Precursor m/z and adduct. Used as a FILTER on candidate mass "
            "(ADR-0014), never as a structural score."
        ),
        ObservationModality.MS2: (
            "Observed fragment peaks. Scores only against a REFERENCE "
            "spectrum; with no reference it is recorded as a gap, not "
            "silently treated as agreement."
        ),
    }
)


def modality_families(
    modalities: frozenset[ObservationModality] | set[ObservationModality],
) -> frozenset[str]:
    """Instrument families ('NMR', 'MS') represented by these modalities."""
    return frozenset(MODALITY_FAMILIES[m] for m in modalities)


def is_single_family(
    modalities: frozenset[ObservationModality] | set[ObservationModality],
) -> bool:
    """True when every supplied modality comes from ONE instrument family.

    A single-family analysis is legitimate and supported; it is simply less
    constrained than a cross-modal one, and callers must say so rather than
    presenting the result as fused.
    """
    return len(modality_families(modalities)) <= 1


def single_family_gap(
    modalities: frozenset[ObservationModality] | set[ObservationModality],
) -> str | None:
    """The honest evidence gap for a single-family analysis, else None.

    Returns ``None`` for an empty set (nothing was supplied, so there is no
    "missing other family" to report) and for genuinely cross-modal input.
    """
    families = modality_families(modalities)
    if len(families) != 1:
        return None
    family = next(iter(families))
    if family == "NMR":
        return (
            "NMR-only evidence: no mass spectrometry supplied, so the "
            "molecular formula and precursor mass were not independently "
            "confirmed, and no MS2 library hit can support or contradict "
            "this assignment"
        )
    return (
        "MS-only evidence: no NMR supplied, so isomeric structures sharing "
        "this mass and formula cannot be distinguished; connectivities "
        "are unconstrained"
    )


def parse_observation_modalities(
    names: Any,
) -> frozenset[ObservationModality]:
    """Normalize a caller-supplied modality list into a validated set.

    Accepts ``None``/empty (meaning "unspecified"), a single string, or an
    iterable of strings.  Names are matched case-insensitively against
    ``ObservationModality``.  Unknown names raise ``InvalidInputError``:
    a typo like ``"hmbc"`` must not silently drop a modality the user
    believes was considered.
    """
    if names is None:
        return frozenset()
    if isinstance(names, str):
        names = [names]
    modalities: set[ObservationModality] = set()
    for name in names:
        if not isinstance(name, str):
            raise InvalidInputError(
                "modality names must be strings", context={"value": repr(name)}
            )
        try:
            modalities.add(ObservationModality(name.strip().lower()))
        except ValueError:
            raise InvalidInputError(
                f"unknown observation modality '{name}'; allowed: "
                f"{', '.join(sorted(m.value for m in ObservationModality))}",
                context={
                    "modality": name,
                    "allowed": sorted(m.value for m in ObservationModality),
                },
            ) from None
    return frozenset(modalities)
