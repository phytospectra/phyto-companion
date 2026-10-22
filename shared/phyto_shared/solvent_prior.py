"""Extraction-solvent compatibility from Crippen logP (ADR-0052).

Heuristic limitations (read before trusting a compatibility number)
------------------------------------------------------------------
* logP is a crude single-number proxy for extraction behaviour.  It knows
  nothing about pH-dependent ionisation (an alkaloid extracts very
  differently as a free base vs a salt), about specific interactions
  (chelation, H-bond driven partitioning), or about co-extracted
  matrix effects.  Two compounds with the same logP can behave very
  differently in the same solvent system.
* Crippen logP is a fragment-additive PREDICTION with typical errors of
  ~1 log unit; for unusual scaffolds it can be worse.  The value fed to
  ``solvent_compatibility`` is therefore itself uncertain, and the
  sigmoid width (``SMOOTHNESS``) is deliberately of the same order as
  that error so the curve does not pretend to more precision than the
  input has.
* The solvent profiles encode "what chemists typically recover from this
  solvent", not exhaustive extraction theory: methanol/water extracts
  of herbal material do carry some highly lipophilic terpenoids, which
  is exactly why the compatibility curve asymptotes to a small floor
  instead of reaching zero -- and why the downstream reranker uses it
  as a SOFT multiplier on a prior bonus, never as a hard exclusion
  (ADR-0052).
* "Solvent" here means the primary extraction solvent the user reports.
  Mixed solvent systems ("MeOH:H2O 80:20") are not modelled; pass the
  dominant component or none at all.
"""

from __future__ import annotations

import math

from .exceptions import InvalidInputError

__all__ = [
    "KNOWN_SOLVENTS",
    "SOLVENT_PROFILES",
    "SMOOTHNESS",
    "estimate_logp",
    "solvent_compatibility",
]


#: Sigmoid width in logP units.  ~1 log unit: the same order as Crippen's
#: own prediction error, so the transition band matches the input's honest
#: uncertainty rather than implying a sharp boundary.
SMOOTHNESS: float = 1.0

#: Shape spec per solvent: "below c" favours logP < c, "above c" favours
#: logP > c, "window lo hi" favours lo < logP < hi.  Profiles summarise
#: common natural-products extraction practice (ADR-0052).
SOLVENT_PROFILES: dict[str, tuple[str, ...]] = {
    "water": ("below", "2"),
    "methanol": ("below", "4"),
    "ethanol": ("below", "4.5"),
    "ethyl acetate": ("window", "1", "6"),
    "hexane": ("above", "3"),
    "dichloromethane": ("window", "1", "7"),
}

#: Normalisation of user input: lowercase, dashes/underscores to spaces.
_SOLVENT_ALIASES: dict[str, str] = {
    "meoh": "methanol",
    "etoh": "ethanol",
    "h2o": "water",
    "etoac": "ethyl acetate",
    "acetic ether": "ethyl acetate",
    "dcm": "dichloromethane",
    "methylene chloride": "dichloromethane",
    "ch2cl2": "dichloromethane",
}

KNOWN_SOLVENTS: frozenset[str] = frozenset(SOLVENT_PROFILES)


def normalise_solvent(name: str) -> str:
    """Canonical solvent name, or raises ``InvalidInputError``.

    Unknown solvents are the CALLER's warning, not this module's error:
    the module refuses to guess a profile for a solvent it was not
    taught, and the caller (schema validation, reranker) decides whether
    to warn and skip the filter or reject the request.
    """
    key = name.strip().lower().replace("-", " ").replace("_", " ")
    if key in _SOLVENT_ALIASES:
        key = _SOLVENT_ALIASES[key]
    if key not in SOLVENT_PROFILES:
        raise InvalidInputError(
            "unknown extraction solvent",
            context={"solvent": name, "known": sorted(KNOWN_SOLVENTS)},
        )
    return key


def estimate_logp(smiles: str) -> float:
    """Crippen predicted octanol/water logP for one structure.

    Raises ``InvalidInputError`` for an unparseable SMILES: a logP for a
    molecule that does not exist would silently poison every downstream
    compatibility number.
    """
    from rdkit import Chem
    from rdkit.Chem import Crippen

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise InvalidInputError("cannot parse SMILES for logP", context={"smiles": smiles})
    return float(Crippen.MolLogP(mol))


def _logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _falls_below(logp: float, cutoff: float) -> float:
    """1.0 well below ``cutoff`` -> 0.0 well above it (smooth)."""
    return _logistic((cutoff - logp) / SMOOTHNESS)


def _rises_above(logp: float, cutoff: float) -> float:
    """0.0 well below ``cutoff`` -> 1.0 well above it (smooth)."""
    return _logistic((logp - cutoff) / SMOOTHNESS)


def solvent_compatibility(logp: float, solvent: str) -> float:
    """Soft compatibility in [0, 1] between a logP and an extraction solvent.

    1.0 means "this solvent plausibly extracted a compound of this
    lipophilicity"; the value falls smoothly (a logistic of width
    ``SMOOTHNESS`` log units) outside the solvent's favoured window.
    The curve is deliberately never a hard zero at any finite logP: see
    the module docstring for why the floor is soft.
    """
    profile = SOLVENT_PROFILES[normalise_solvent(solvent)]
    kind = profile[0]
    if kind == "below":
        return _falls_below(logp, float(profile[1]))
    if kind == "above":
        return _rises_above(logp, float(profile[1]))
    lo, hi = float(profile[1]), float(profile[2])
    return _rises_above(logp, lo) * _falls_below(logp, hi)
