"""Reference-library builder + built-library regression tests.

The committed starter library is assembled from hand-curated LITERATURE
tables (rounded values; evidence_nature literature_hand_curated_rounded).
Self-match and isomer-ambiguity checks below are regression coverage of the
builder + executor wiring on real RDKit/SciPy providers. They are NOT
independent measured validation and support no accuracy claim.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.real_executor import RealToolExecutor
from orchestrator.tool_registry import build_default_registry
from phyto_shared.models import Peak2D

REPO = Path(__file__).resolve().parents[1]
LIBRARY = REPO / "data" / "reference_libraries" / "literature_flavonoids_v1"
SOURCE = LIBRARY / "source"
TOOL = "reference_hsqc.rank"


def _peaks_from_table(path: Path) -> list[dict]:
    rows = [
        line for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    import csv as _csv

    return [
        {
            "peak_type": "2d",
            "h_ppm": float(row["dH"]),
            "c_ppm": float(row["dC"]),
            "intensity": 1.0,
            "experiment_type": "HSQC",
        }
        for row in _csv.DictReader(rows)
    ]


@pytest.fixture(scope="module")
def built_library(tmp_path_factory):
    out = tmp_path_factory.mktemp("built") / "literature_flavonoids_v1"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "build_reference_library.py"),
         "--source", str(SOURCE), "--output", str(out)],
        capture_output=True, text=True, check=True,
    )
    return out, result.stdout


@pytest.fixture(scope="module")
def executor(built_library):
    out, _ = built_library
    return RealToolExecutor(
        build_default_registry(),
        out / "structures.csv",
        out / "references.json",
    )


class TestBuilder:
    def test_rebuild_is_byte_identical(self, built_library, tmp_path):
        out, _ = built_library
        rerun = tmp_path / "rerun"
        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_reference_library.py"),
             "--source", str(SOURCE), "--output", str(rerun)],
            capture_output=True, text=True, check=True,
        )
        for name in ("structures.csv", "references.json", "MANIFEST.json"):
            assert (out / name).read_bytes() == (rerun / name).read_bytes(), name

    def test_committed_outputs_match_rebuild(self, built_library):
        out, _ = built_library
        for name in ("structures.csv", "references.json", "MANIFEST.json"):
            assert (out / name).read_bytes() == (LIBRARY / name).read_bytes(), name

    def test_andrographolide_is_quarantined_with_reason(self, built_library):
        _, stdout = built_library
        assert "quarantined  Andrographolide" in stdout
        assert "no verified structure" in stdout
        manifest = json.loads((LIBRARY / "MANIFEST.json").read_text())
        assert manifest["records_imported"] == 3
        assert manifest["records_quarantined"] == 1
        assert manifest["evidence_nature"]["quercetin"] == "literature_hand_curated_rounded"

    def test_strict_flag_fails_on_quarantine(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_reference_library.py"),
             "--source", str(SOURCE), "--output", str(tmp_path / "o"), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1

    def test_damaged_peak_table_is_quarantined_not_repaired(self, tmp_path):
        source = tmp_path / "source"
        shutil.copytree(SOURCE, source)
        table = source / "quercetin_hsqc.csv"
        table.write_text(table.read_text().replace("6.19,98.3", "99.19,98.3"))
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_reference_library.py"),
             "--source", str(source), "--output", str(tmp_path / "o")],
            capture_output=True, text=True, check=True,
        )
        assert "quarantined  Quercetin" in result.stdout
        assert "outside" in result.stdout
        names = {
            row["name"]
            for row in json.loads((tmp_path / "o" / "MANIFEST.json").read_text())["per_record"]
            if row["status"] == "imported"
        }
        assert names == {"Kaempferol", "Luteolin"}

    def test_duplicate_coordinates_are_refused(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        (source / "t.csv").write_text("position,dH,dC\nA,6.19,98.3\nB,6.19,98.3\n")
        (source / "manifest.csv").write_text(
            "name,smiles,solvent,source,license,table,expected_inchikey,declared_formula,evidence_nature\n"
            "Quercetin,O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12,DMSO-d6,x,CC-BY-NC,t.csv,,,\n"
        )
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "build_reference_library.py"),
             "--source", str(source), "--output", str(tmp_path / "o")],
            capture_output=True, text=True, check=True,
        )
        assert "duplicate cross-peak coordinates" in result.stdout


class TestBuiltLibraryRegression:
    """Regression only: literature self-match + designed isomer ambiguity."""

    def test_quercetin_literature_self_match(self, executor):
        peaks = _peaks_from_table(SOURCE / "quercetin_hsqc.csv")
        output = executor.execute(TOOL, {
            "formula": "C15H10O7",
            "peaks": peaks,
            "h_tolerance_ppm": 0.05,
            "c_tolerance_ppm": 0.5,
            "max_results": 10,
        })
        assert output.query_peak_count == len(peaks)
        top = output.hits[0]
        assert top["candidate"]["id"] == "quercetin"
        assert top["matcher_result"]["score"] == pytest.approx(0)
        assert top["matcher_result"]["coverage"] == 1
        assert output.evidence_assessment["disposition"] == "ranked"
        assert output.evidence_assessment["score_gap"] is None  # formula excludes isomers

    def test_kaempferol_luteolin_isomer_pair_ranks_self_match_first(self, executor):
        # Both are C15H10O6. The fixture comments describe 1D assignment
        # overlap, but their HSQC cross-peak tables genuinely differ: only
        # H-6/H-8 overlap within tolerance. The honest outcome is therefore
        # a SEPARATED ranking with the self-match first -- not a near-tie.
        # (Machine-flagged ambiguity on near-identical references is covered
        # by the synthetic near-tie test in tests/test_real_orchestrator.py.)
        peaks = _peaks_from_table(SOURCE / "luteolin_hsqc.csv")
        output = executor.execute(TOOL, {
            "formula": "C15H10O6",
            "peaks": peaks,
            "h_tolerance_ppm": 0.05,
            "c_tolerance_ppm": 0.5,
            "max_results": 10,
        })
        candidates = [hit["candidate"]["id"] for hit in output.hits]
        assert set(candidates) == {"kaempferol", "luteolin"}
        assert candidates[0] == "luteolin"
        assert output.hits[0]["matcher_result"]["score"] == pytest.approx(0)
        assessment = output.evidence_assessment
        assert assessment["disposition"] == "ranked"
        assert assessment["top_hit"]["candidate_id"] == "luteolin"
        assert assessment["runner_up_hit"]["candidate_id"] == "kaempferol"
        assert assessment["score_gap"] is not None and assessment["score_gap"] >= 0.25

    def test_reference_metadata_travels_to_hits(self, executor):
        peaks = _peaks_from_table(SOURCE / "quercetin_hsqc.csv")
        output = executor.execute(TOOL, {
            "formula": "C15H10O7", "peaks": peaks,
            "h_tolerance_ppm": 0.05, "c_tolerance_ppm": 0.5, "max_results": 1,
        })
        hit = output.hits[0]
        assert hit["solvent"] == "DMSO-d6"
        assert "CC-BY-NC" in hit["license"]
        assert "not an authoritative database record" in hit["source"]
        assert hit["reference_provenance"] == "experimental"

    def test_schema_objects_reject_non_hsqc_peaks(self, executor):
        with pytest.raises(Exception):
            executor.execute(TOOL, {
                "formula": "C15H10O7",
                "peaks": [Peak2D(h_ppm=6.19, c_ppm=98.3, intensity=1.0,
                                 experiment_type="HMBC").model_dump()],
            })
