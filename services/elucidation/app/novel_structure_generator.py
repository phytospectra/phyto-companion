"""Novel structure generation by chemical graph editing (RDKit).

Adapted from the Identify_my_compound workflow: for each enabled edit
type, enumerate single-site edits of the parent structures, keep only
products that (a) sanitize, (b) match the target formula EXACTLY, (c)
are not duplicates (InChIKey) and not already in the database.

Malformed intermediates are dropped silently (sanitization failures are
expected and routine); a completely unreachable target formula raises
InvalidInputError so a misconfigured edit list cannot masquerade as an
empty result.

Edit "sites": atom indices for atom-level edits, bond indices for
c_reduction.  Site semantics are documented per edit in ``EDIT_SITES``.
"""

from __future__ import annotations

from typing import Literal, Optional

from rdkit import Chem

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, MolecularFormula

from .candidate_ranker import parse_formula_string

__all__ = [
    "EditType",
    "NovelCandidate",
    "EDIT_FORMULA_DELTAS",
    "EDIT_PRIORITY",
    "apply_edit_at",
    "generate_novel_candidates",
]

EditType = Literal[
    "hydroxylation",
    "methylation",
    "glycosylation",
    "prenylation",
    "acetylation",
    "deoxygenation",
    "demethylation",
    "c_reduction",
    "oh_shift",
]

# Net formula effect of one edit (element -> count change).  Verified
# against RDKit for every attach-based edit (see tests).
EDIT_FORMULA_DELTAS: dict[str, list[dict[str, int]]] = {
    "hydroxylation": [{"O": 1}],
    "methylation": [{"C": 1, "H": 2}],  # C-methylation and O-methylation agree
    "glycosylation": [
        {"C": 6, "H": 10, "O": 5},  # hexose
        {"C": 6, "H": 10, "O": 4},  # deoxyhexose
    ],
    "prenylation": [{"C": 5, "H": 8}],
    "acetylation": [{"C": 2, "H": 2, "O": 1}],
    "deoxygenation": [{"O": -1}],
    "demethylation": [{"C": -1, "H": -2}],
    "c_reduction": [{"H": 2}],
    "oh_shift": [{}],
}

# Plausibility priority (spec): hydroxylation > methylation > prenylation
# > glycosylation > acetylation; the remaining edits follow.
EDIT_PRIORITY: list[str] = [
    "hydroxylation",
    "methylation",
    "prenylation",
    "glycosylation",
    "acetylation",
    "deoxygenation",
    "demethylation",
    "c_reduction",
    "oh_shift",
]

DEFAULT_ENABLED_EDITS: list[str] = [
    "hydroxylation",
    "methylation",
    "glycosylation",
    "prenylation",
    "acetylation",
]

# Attach-based fragments: [*] marks the attachment atom on the fragment;
# "kind" is which parent atoms can host the edit.
_ATTACH_EDITS: dict[str, list[tuple[str, str]]] = {
    "hydroxylation": [("[*]O", "CH")],
    "methylation": [("[*]C", "CH"), ("[*]C", "OH")],
    "glycosylation": [
        ("[*]C1OC(CO)C(O)C(O)C1O", "OH"),  # hexose (net +C6H10O5)
        ("[*]C1OC(C)C(O)C(O)C1O", "OH"),   # deoxyhexose (net +C6H10O4)
    ],
    "prenylation": [("[*]CC=C(C)C", "CH"), ("[*]CC=C(C)C", "OH")],
    "acetylation": [("[*]C(=O)C", "OH")],
}


class NovelCandidate(Candidate):
    """A generated structure: a Candidate plus its edit provenance.

    Extends Candidate (Step 2) so novel structures can flow directly
    into ``rank_candidates`` and downstream services.
    """

    parent_ik: str
    edit_type: EditType
    edit_site: Optional[int]
    generated_smiles: str


# ---------------------------------------------------------------------------
# Core editing primitives
# ---------------------------------------------------------------------------


def _attach_fragment(mol: Chem.Mol, anchor_idx: int, fragment_smiles: str):
    """Attach a dummy-marked fragment at ``anchor_idx`` (replacing one H).

    Returns a sanitized product Mol or None (invalid intermediate).
    H counts are read from the ORIGINAL sanitized mol before combining --
    CombineMols leaves stale property caches.
    """
    anchor_h = mol.GetAtomWithIdx(anchor_idx).GetTotalNumHs()
    frag = Chem.MolFromSmiles(fragment_smiles)
    if frag is None:
        return None
    dummy = next(a.GetIdx() for a in frag.GetAtoms() if a.GetAtomicNum() == 0)
    neighbor = next(iter(frag.GetAtomWithIdx(dummy).GetNeighbors())).GetIdx()
    rw = Chem.RWMol(Chem.CombineMols(mol, frag))
    offset = mol.GetNumAtoms()
    anchor = rw.GetAtomWithIdx(anchor_idx)
    anchor.SetNoImplicit(True)
    anchor.SetNumExplicitHs(max(0, anchor_h - 1))
    rw.AddBond(anchor_idx, offset + neighbor, Chem.BondType.SINGLE)
    rw.RemoveAtom(offset + dummy)
    product = rw.GetMol()
    try:
        Chem.SanitizeMol(product)
        return product
    except Exception:
        return None  # expected: many intermediates are chemically invalid


def _hydroxyl_groups(mol: Chem.Mol) -> list[int]:
    """Indices of OH oxygens (oxygen bearing at least one H)."""
    return [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetSymbol() == "O" and atom.GetTotalNumHs() >= 1
    ]


def _ch_sites(mol: Chem.Mol) -> list[int]:
    """Indices of carbon atoms bearing at least one H (C-edit sites)."""
    return [
        atom.GetIdx()
        for atom in mol.GetAtoms()
        if atom.GetSymbol() == "C" and atom.GetTotalNumHs() >= 1
    ]


def _methoxy_methyls(mol: Chem.Mol) -> list[int]:
    """Indices of methoxy methyl carbons (O-CH3) for demethylation."""
    sites = []
    for atom in mol.GetAtoms():
        if atom.GetSymbol() != "C" or atom.GetDegree() != 1:
            continue
        if atom.GetTotalNumHs() != 3:
            continue
        neighbor = atom.GetNeighbors()[0]
        if neighbor.GetSymbol() == "O":
            sites.append(atom.GetIdx())
    return sites


def _hydroxylated_carbons(mol: Chem.Mol) -> list[tuple[int, int]]:
    """(carbon_idx, oxygen_idx) pairs where the O is an OH on that carbon."""
    pairs = []
    for oxygen_idx in _hydroxyl_groups(mol):
        for neighbor in mol.GetAtomWithIdx(oxygen_idx).GetNeighbors():
            if neighbor.GetSymbol() == "C":
                pairs.append((neighbor.GetIdx(), oxygen_idx))
    return pairs


def apply_edit_at(smiles: str, edit_type: str, site: int) -> str | None:
    """Apply one edit at a given site; returns canonical SMILES or None.

    Site semantics: atom index for all atom-level edits (the removed
    oxygen for deoxygenation, the removed methyl carbon for
    demethylation, the donor carbon for oh_shift -- the acceptor is
    chosen greedily as the lowest-index CH without OH); bond index for
    c_reduction.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        return _apply_edit_mol(mol, edit_type, site)
    except Exception:
        return None


def _apply_edit_mol(mol: Chem.Mol, edit_type: str, site: int) -> str | None:
    if edit_type in _ATTACH_EDITS:
        kind = _ATTACH_EDITS[edit_type][0][1]
        if edit_type == "methylation":
            # try C-attachment first at carbons, O-attachment at oxygens
            atom = mol.GetAtomWithIdx(site) if site < mol.GetNumAtoms() else None
            if atom is not None and atom.GetSymbol() == "O":
                kind = "OH"
            else:
                kind = "CH"
        product = _attach_fragment(mol, site, _fragment_for(edit_type, kind))
    elif edit_type == "deoxygenation":
        product = _remove_atom_at(mol, site, expected_symbol="O")
    elif edit_type == "demethylation":
        product = _remove_atom_at(mol, site, expected_symbol="C")
    elif edit_type == "c_reduction":
        product = _reduce_bond(mol, site)
    elif edit_type == "oh_shift":
        product = _shift_oh(mol, site)
    else:  # pragma: no cover - guarded by caller
        return None
    if product is None:
        return None
    return Chem.MolToSmiles(product)


def _fragment_for(edit_type: str, kind: str) -> str:
    for fragment, fragment_kind in _ATTACH_EDITS[edit_type]:
        if fragment_kind == kind:
            return fragment
    return _ATTACH_EDITS[edit_type][0][0]


def _remove_atom_at(mol: Chem.Mol, index: int, expected_symbol: str):
    if index >= mol.GetNumAtoms():
        return None
    if mol.GetAtomWithIdx(index).GetSymbol() != expected_symbol:
        return None
    rw = Chem.RWMol(mol)
    rw.RemoveAtom(index)
    product = rw.GetMol()
    try:
        Chem.SanitizeMol(product)
        return product
    except Exception:
        return None


def _reduce_bond(mol: Chem.Mol, bond_index: int):
    if bond_index >= mol.GetNumBonds():
        return None
    bond = mol.GetBondWithIdx(bond_index)
    if bond.GetBondType() != Chem.BondType.DOUBLE or bond.GetIsAromatic():
        return None
    rw = Chem.RWMol(mol)
    rw.GetBondWithIdx(bond_index).SetBondType(Chem.BondType.SINGLE)
    product = rw.GetMol()
    try:
        Chem.SanitizeMol(product)  # implicit H counts rebalance to +2H
        return product
    except Exception:
        return None


def _shift_oh(mol: Chem.Mol, donor_oxygen_idx: int):
    """Move the OH at ``donor_oxygen_idx`` to the lowest-index CH lacking
    OH (a stand-in enumeration for this rare, expensive edit)."""
    if donor_oxygen_idx >= mol.GetNumAtoms():
        return None
    oxygen = mol.GetAtomWithIdx(donor_oxygen_idx)
    if oxygen.GetSymbol() != "O" or oxygen.GetTotalNumHs() < 1:
        return None
    donor_carbon = next(
        (n.GetIdx() for n in oxygen.GetNeighbors() if n.GetSymbol() == "C"), None
    )
    if donor_carbon is None:
        return None
    oh_carbons = {c for c, _ in _hydroxylated_carbons(mol)}
    acceptors = [
        idx for idx in _ch_sites(mol) if idx not in oh_carbons and idx != donor_carbon
    ]
    if not acceptors:
        return None
    acceptor = acceptors[0]
    removed = _remove_atom_at(mol, donor_oxygen_idx, expected_symbol="O")
    if removed is None:
        return None
    product = _attach_fragment(removed, acceptor, "[*]O")
    return product


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def _sites_for(mol: Chem.Mol, edit_type: str) -> list[int]:
    if edit_type == "deoxygenation":
        return _hydroxyl_groups(mol)
    if edit_type == "demethylation":
        return _methoxy_methyls(mol)
    if edit_type == "c_reduction":
        return [
            bond.GetIdx()
            for bond in mol.GetBonds()
            if bond.GetBondType() == Chem.BondType.DOUBLE
            and not bond.GetIsAromatic()
        ]
    if edit_type == "oh_shift":
        return _hydroxyl_groups(mol)
    # attach-based edits
    kinds = {kind for _, kind in _ATTACH_EDITS[edit_type]}
    if kinds == {"CH"}:
        return _ch_sites(mol)
    if kinds == {"OH"}:
        return _hydroxyl_groups(mol)
    return sorted(_ch_sites(mol) + _hydroxyl_groups(mol))


def _formula_counts(mol: Chem.Mol) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol()
        counts[symbol] = counts.get(symbol, 0) + 1
    n_h = sum(atom.GetTotalNumHs() for atom in mol.GetAtoms())
    counts["H"] = counts.get("H", 0) + n_h
    return counts


def _target_reachable(
    parent_counts: dict[str, int], target_counts: dict[str, int], edits: list[str]
) -> bool:
    for edit in edits:
        for delta in EDIT_FORMULA_DELTAS[edit]:
            merged = dict(parent_counts)
            for element, change in delta.items():
                merged[element] = merged.get(element, 0) + change
                if merged[element] <= 0:
                    merged.pop(element)
            if merged == target_counts:
                return True
    return False


def generate_novel_candidates(
    target_formula: str,
    parent_candidates: list[Candidate],
    enabled_edits: list[str] | None = None,
    max_generated: int = 5000,
    known_inchikeys: set[str] | None = None,
) -> list[NovelCandidate]:
    """Enumerate single-site edits of the parents matching the target formula.

    Raises InvalidInputError when no enabled edit's formula delta can map
    ANY parent to the target -- a misconfigured edit list should fail
    loudly, not return an empty list indistinguishable from 'tried and
    found nothing'.
    """
    enabled = enabled_edits or DEFAULT_ENABLED_EDITS
    unknown = [edit for edit in enabled if edit not in EDIT_FORMULA_DELTAS]
    if unknown:
        raise InvalidInputError(
            "unknown edit types", context={"unknown": unknown, "known": list(EDIT_FORMULA_DELTAS)}
        )
    if not parent_candidates:
        raise InvalidInputError("no parent candidates provided", context={})

    target_counts = parse_formula_string(target_formula)
    known = known_inchikeys or set()

    reachable = False
    parents: list[tuple[Candidate, Chem.Mol, str]] = []
    for parent in parent_candidates:
        mol = Chem.MolFromSmiles(parent.smiles)
        if mol is None:
            continue  # unparseable parent: skip, do not crash generation
        parent_ik = Chem.MolToInchiKey(mol)
        if parent_ik:
            known.add(parent_ik)  # parents themselves are never novel
        if _target_reachable(_formula_counts(mol), target_counts, enabled):
            reachable = True
        parents.append((parent, mol, parent_ik or ""))
    if not reachable:
        raise InvalidInputError(
            "target formula cannot be produced by any enabled edit from "
            "any parent",
            context={
                "target_formula": target_formula,
                "enabled_edits": enabled,
                "n_parents": len(parents),
            },
        )

    ordered_edits = [edit for edit in EDIT_PRIORITY if edit in enabled]
    generated: list[NovelCandidate] = []
    seen_inchikeys: set[str] = set()
    for edit in ordered_edits:
        if len(generated) >= max_generated:
            break
        for parent, mol, parent_ik in parents:
            for site in _sites_for(mol, edit):
                if len(generated) >= max_generated:
                    break
                product_smiles = _apply_edit_mol(mol, edit, site)
                if product_smiles is None:
                    continue  # silently dropped invalid intermediate
                product = Chem.MolFromSmiles(product_smiles)
                if product is None:
                    continue
                if _formula_counts(product) != target_counts:
                    continue  # invariant: formula must match EXACTLY
                inchikey = Chem.MolToInchiKey(product)
                if inchikey in seen_inchikeys or inchikey in known:
                    continue
                seen_inchikeys.add(inchikey)
                counts = _formula_counts(product)
                formula_model = MolecularFormula(counts=counts)
                generated.append(
                    NovelCandidate(
                        id=f"novel-{len(generated) + 1}",
                        name=f"{parent.name} + {edit}",
                        smiles=product_smiles,
                        formula=formula_model,
                        exact_mass=formula_model.exact_mass(),
                        provenance="predicted",
                        source="chemical graph editing",
                        parent_ik=parent_ik,
                        edit_type=edit,
                        edit_site=site,
                        generated_smiles=product_smiles,
                    )
                )
    return generated
