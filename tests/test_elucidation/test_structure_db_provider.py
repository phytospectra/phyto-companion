"""FileStructureDB provider tests (ADR-0081, Phase 2 Step 6+ wiring).

A real curated structure library must be a loadable, configurable
capability: pointing PHYTO_STRUCTURE_DB_PATH at a valid CSV makes
candidate retrieval real in both planes; a missing file, missing
columns, invalid SMILES, or duplicate InChIKey is a startup error --
never a partially-loaded library, which would quietly miss candidates
and read as a negative result.
"""

from __future__ import annotations

import pytest
from rdkit import Chem

from elucidation.structure_db import FileStructureDB, MockStructureDB
from elucidation.runtime_config import ElucidationConfig
from elucidation.main import _Runtime
from phyto_shared.exceptions import CloudServiceError


def _write_csv(path, rows, header="name,smiles,provenance,source"):
    lines = [header] + rows
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestFileStructureDB:
    def test_loads_rows_and_indexes_by_formula_and_inchikey(self, tmp_path):
        csv = tmp_path / "library.csv"
        _write_csv(csv, [
            "maackiain,Oc1ccc2c(c1)OC[C@@H]1[C@H]2Oc2cc3OCOc3cc21",
            "caffeine,Cn1c(=O)c2c(ncn2C)n(C)c1=O",
        ])
        db = FileStructureDB(csv)
        assert db.candidates_for_formula({"C": 16, "H": 12, "O": 5})
        assert len(db.known_inchikeys()) == 2
        ik = Chem.MolToInchiKey(Chem.MolFromSmiles(
            "Cn1c(=O)c2c(ncn2C)n(C)c1=O"))
        assert db.candidate_by_inchikey(ik).name == "caffeine"

    def test_default_provenance_and_source(self, tmp_path):
        csv = tmp_path / "lib.csv"
        _write_csv(csv, ["phenol,Oc1ccccc1"])
        db = FileStructureDB(csv)
        cand = db.candidates_for_formula({"C": 6, "H": 6, "O": 1})[0]
        assert cand.provenance == "experimental"
        assert cand.source == "lib.csv"

    def test_missing_file_is_startup_error(self, tmp_path):
        with pytest.raises(CloudServiceError, match="not found"):
            FileStructureDB(tmp_path / "nope.csv")

    def test_missing_columns_is_startup_error(self, tmp_path):
        csv = tmp_path / "bad.csv"
        _write_csv(csv, ["x,1"], header="a,b")
        with pytest.raises(CloudServiceError, match="missing required columns"):
            FileStructureDB(csv)

    def test_invalid_smiles_is_startup_error_not_skipped(self, tmp_path):
        csv = tmp_path / "bad-smiles.csv"
        _write_csv(csv, ["good,Oc1ccccc1", "bad,not-a-smiles"])
        with pytest.raises(CloudServiceError, match="invalid SMILES"):
            FileStructureDB(csv)
        # and the whole load fails: no partially-initialized instance

    def test_empty_file_is_startup_error(self, tmp_path):
        csv = tmp_path / "empty.csv"
        _write_csv(csv, [])
        with pytest.raises(CloudServiceError, match="no rows"):
            FileStructureDB(csv)

    def test_duplicate_inchikey_is_startup_error(self, tmp_path):
        csv = tmp_path / "dup.csv"
        _write_csv(csv, ["phenol,Oc1ccccc1", "phenol-again,Oc1ccccc1"])
        with pytest.raises(CloudServiceError, match="duplicate InChIKey"):
            FileStructureDB(csv)


class TestRuntimeWiring:
    def _config(self, dry_run, path=None):
        return ElucidationConfig(
            env="local",
            dry_run=dry_run,
            vertex_endpoint_id=None if dry_run else "ep-1",
            gcp_project=None if dry_run else "proj-1",
            vertex_location="us-central1",
            structure_db_path=str(path) if path else None,
        )

    def test_local_plane_with_file_db_serves_the_real_library(self, tmp_path):
        csv = tmp_path / "lib.csv"
        _write_csv(csv, ["phenol,Oc1ccccc1"])
        runtime = _Runtime(self._config(True, csv))
        assert isinstance(runtime.db, FileStructureDB)
        assert runtime.db.candidates_for_formula({"C": 6, "H": 6, "O": 1})

    def test_real_mode_with_file_db_has_retrieval(self, tmp_path):
        csv = tmp_path / "lib.csv"
        _write_csv(csv, ["phenol,Oc1ccccc1"])
        runtime = _Runtime(self._config(False, csv))
        assert isinstance(runtime.db, FileStructureDB)
        # real mode: predictor still Vertex, but retrieval is now real too
        assert runtime.config.real_prediction

    def test_real_mode_bad_db_path_fails_at_startup(self, tmp_path):
        with pytest.raises(CloudServiceError, match="not found"):
            _Runtime(self._config(False, tmp_path / "nope.csv"))

    def test_local_plane_without_path_still_uses_mock(self):
        runtime = _Runtime(self._config(True))
        assert isinstance(runtime.db, MockStructureDB)

    def test_real_mode_without_path_still_503(self):
        runtime = _Runtime(self._config(False))
        with pytest.raises(CloudServiceError, match="explicitly unavailable"):
            runtime.db
