"""Novel structure generator tests: transformations, formula invariant,
limits, failure modes."""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, MolecularFormula

from elucidation.candidate_ranker import parse_formula_string
from elucidation.novel_structure_generator import (
    DEFAULT_ENABLED_EDITS,
    NovelCandidate,
    apply_edit_at,
    generate_novel_candidates,
)


def _candidate(name: str, smiles: str) -> Candidate:
    counts = parse_formula_string(rdMolDescriptors.CalcMolFormula(
        Chem.MolFromSmiles(smiles)
    ))
    return Candidate(
        id=name.lower(),
        name=name,
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="experimental",
        source="test",
    )


BENZENE = _candidate("benzene", "c1ccccc1")
ETHANOL = _candidate("ethanol", "CCO")
PHENOL = _candidate("phenol", "Oc1ccccc1")
ANISOLE = _candidate("anisole", "COc1ccccc1")
BUTENE = _candidate("2-butene", "CC=CC")
QUERCETIN = _candidate("quercetin", "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12")


def _product_formula_smiles(smiles: str) -> str:
    return rdMolDescriptors.CalcMolFormula(Chem.MolFromSmiles(smiles))


class TestKnownTransformations:
    def test_benzene_hydroxylation_gives_phenol(self):
        products = generate_novel_candidates("C6H6O", [BENZENE], ["hydroxylation"])
        assert "Oc1ccccc1" in [p.generated_smiles for p in products]

    def test_ethanol_methylation_gives_both_products(self):
        products = generate_novel_candidates("C3H8O", [ETHANOL], ["methylation"])
        smiles = {p.generated_smiles for p in products}
        assert "CCCO" in smiles  # 1-propanol (C-methylation)
        assert "CCOC" in smiles  # ethyl methyl ether (O-methylation)

    def test_deoxygenation_phenol_to_benzene(self):
        products = generate_novel_candidates("C6H6", [PHENOL], ["deoxygenation"])
        assert "c1ccccc1" in [p.generated_smiles for p in products]

    def test_demethylation_anisole_to_phenol(self):
        products = generate_novel_candidates("C6H6O", [ANISOLE], ["demethylation"])
        assert "Oc1ccccc1" in [p.generated_smiles for p in products]

    def test_c_reduction_butene_to_butane(self):
        products = generate_novel_candidates("C4H10", [BUTENE], ["c_reduction"])
        assert "CCCC" in [p.generated_smiles for p in products]

    def test_glycosylation_of_ethanol(self):
        products = generate_novel_candidates("C8H16O6", [ETHANOL], ["glycosylation"])
        assert products, "hexose glycoside of ethanol expected"
        assert all(p.edit_type == "glycosylation" for p in products)

    def test_prenylation_and_acetylation(self):
        prenyl = generate_novel_candidates("C7H14O", [ETHANOL], ["prenylation"])
        assert any(p.generated_smiles == "CCOCC=C(C)C" for p in prenyl)
        acetyl = generate_novel_candidates("C4H8O2", [ETHANOL], ["acetylation"])
        assert any(p.generated_smiles == "CCOC(C)=O" for p in acetyl)


class TestInvariants:
    def test_every_product_matches_target_formula_exactly(self):
        products = generate_novel_candidates(
            "C15H10O8", [QUERCETIN], ["hydroxylation", "methylation"]
        )
        assert products, "quercetin has CH and OH sites to edit"
        for product in products:
            assert _product_formula_smiles(product.generated_smiles) == "C15H10O8"
            mol = Chem.MolFromSmiles(product.generated_smiles)
            assert mol is not None  # everything sanitizes

    def test_products_are_novel_candidates_of_candidate(self):
        products = generate_novel_candidates("C6H6O", [BENZENE], ["hydroxylation"])
        assert all(isinstance(p, NovelCandidate) for p in products)
        assert all(isinstance(p, Candidate) for p in products)  # inheritance
        assert products[0].parent_ik == Chem.MolToInchiKey(
            Chem.MolFromSmiles("c1ccccc1")
        )

    def test_known_inchikeys_excluded(self):
        phenol_ik = Chem.MolToInchiKey(Chem.MolFromSmiles("Oc1ccccc1"))
        products = generate_novel_candidates(
            "C6H6O", [BENZENE], ["hydroxylation"], known_inchikeys={phenol_ik}
        )
        assert products == []  # phenol was the only C6H6O product

    def test_max_generated_respected(self):
        products = generate_novel_candidates(
            "C15H10O8", [QUERCETIN], ["hydroxylation", "methylation"],
            max_generated=2,
        )
        assert len(products) <= 2

    def test_priority_ordering_hydroxylation_first(self):
        # kaempferol = quercetin minus one OH: hydroxylation reaches it,
        # and hydroxylation products must come first in the list
        kaempferol = _candidate("kaempferol", "O=c1cc(-c2ccc(O)cc2O)oc2cc(O)cc(O)c12")
        products = generate_novel_candidates(
            "C15H10O7", [kaempferol],
            ["hydroxylation", "methylation", "prenylation", "glycosylation", "acetylation"],
        )
        assert products
        assert products[0].edit_type == "hydroxylation"
        assert "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12" in [
            p.generated_smiles for p in products if p.edit_type == "hydroxylation"
        ]


class TestFailureModes:
    def test_oh_shift_disabled_by_default(self):
        # Step 5 review: oh_shift is rare and expensive; it must never be
        # enabled unless explicitly requested
        assert "oh_shift" not in DEFAULT_ENABLED_EDITS
        products = generate_novel_candidates("C6H6O", [BENZENE])  # defaults
        assert all(p.edit_type != "oh_shift" for p in products)

    def test_unreachable_formula_raises(self):
        with pytest.raises(InvalidInputError) as excinfo:
            generate_novel_candidates("C7H20N3", [BENZENE])
        assert "cannot be produced" in str(excinfo.value)

    def test_unknown_edit_raises(self):
        with pytest.raises(InvalidInputError):
            generate_novel_candidates("C6H6O", [BENZENE], ["chlorination"])

    def test_no_parents_raises(self):
        with pytest.raises(InvalidInputError):
            generate_novel_candidates("C6H6O", [])

    def test_invalid_intermediates_dropped_silently(self):
        # benzene + prenylation at ring positions creates some products;
        # any that fail sanitization must vanish without raising
        products = generate_novel_candidates("C11H14", [BENZENE], ["prenylation"])
        for product in products:
            assert Chem.MolFromSmiles(product.generated_smiles) is not None


class TestApplyEditAt:
    def test_direct_edit_helper(self):
        assert apply_edit_at("c1ccccc1", "hydroxylation", 0) == "Oc1ccccc1"
        assert apply_edit_at("CCO", "hydroxylation", 0) == "OCCO"

    def test_invalid_site_returns_none(self):
        assert apply_edit_at("CCO", "deoxygenation", 0) is None  # atom 0 is C, not O
