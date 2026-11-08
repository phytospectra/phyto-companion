"""Bounded offline acceptance tests for reference-HSQC orchestration.

Temporary CSV/JSON libraries below are SYNTHETIC algorithm fixtures, not
scientific validation. All shifts used here are invented, not independent measurement or identification
accuracy evidence. No downloads, paid providers, Firebase, or Firestore are used.
Accounting settlement/idempotency remains covered in tests/test_auth/.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import inspect
import json
import socket
from contextlib import ExitStack
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth.config import AuthConfig
from auth.firebase_auth import MockFirebaseAuthClient
from auth.install import TokenLayer, install_auth
from auth.pubsub_publisher import LoggingPublisher
from auth.token_balance import MockTokenBalanceClient
from auth.workspaces import InMemoryWorkspaceStore
from orchestrator import real_executor
from orchestrator.audit_logger import ChainedAuditLogger
from orchestrator.execution_config import ScientificExecutionConfig, build_tool_executor
from orchestrator.planner_agent import PlannerAgent, Task, TaskGraph, WorkflowEngine
from orchestrator.real_executor import RealToolExecutor
from orchestrator.tool_registry import MockToolExecutor, build_default_registry
from orchestrator.tool_tiering import ExecutionContext
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Peak2D
from phyto_shared.provenance import sha256_dict

TOOL = "reference_hsqc.rank"
WORKFLOW = "reference_hsqc_identify"
AUTH = {"Authorization": "Bearer mock:alice"}
PUBLIC_FIXTURES = Path(__file__).parent / "fixtures" / "real_npmrd"


def _inchikey(smiles):
    """Actual RDKit identity — never a hand-typed key."""
    from rdkit import Chem

    return Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))


def _peak(h=1.2, c=18.0, **changes):
    return Peak2D(h_ppm=h, c_ppm=c, intensity=1.0, experiment_type="HSQC").model_dump(mode="json") | changes


def _parameters(**changes):
    return {
        "formula": "C2H6O",
        "peaks": [_peak(1.2, 18.0), _peak(3.6, 58.0)],
        "h_tolerance_ppm": 0.05,
        "c_tolerance_ppm": 0.5,
        "max_results": 10,
    } | changes


def _request(**changes):
    return {
        "sample_id": "synthetic-algorithm-test-not-validation",
        "workflow": WORKFLOW,
        "modalities": ["hsqc"],
        "dry_run": False,
        "parameters": _parameters(),
    } | changes


def _write_libraries(directory, rows, references):
    structures = directory / "structures.csv"
    reference_path = directory / "references.json"
    with structures.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "smiles", "provenance", "source"])
        writer.writeheader()
        writer.writerows(rows)
    reference_path.write_text(json.dumps(references), encoding="utf-8")
    return structures, reference_path


@pytest.fixture(autouse=True)
def offline_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "0")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "api-audit.jsonl"))
    monkeypatch.delenv("PHYTO_STRUCTURE_DB_PATH", raising=False)
    monkeypatch.delenv("PHYTO_HSQC_REFERENCE_LIBRARY", raising=False)
    monkeypatch.delenv("FIREBASE_CREDENTIALS_PATH", raising=False)

    def forbidden_connection(*args, **kwargs):
        pytest.fail("An offline acceptance test attempted an IP network connection")

    original_connect = socket.socket.connect

    def local_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            forbidden_connection()
        return original_connect(sock, address)

    monkeypatch.setattr(socket, "create_connection", forbidden_connection)
    monkeypatch.setattr(socket.socket, "connect", local_only)
    from orchestrator import main

    main.get_runtime.cache_clear()
    yield
    main.get_runtime.cache_clear()


@pytest.fixture
def synthetic_libraries(tmp_path):
    """Invented shifts over RDKit structures; NEVER measured-reference validation.

    The loader requires the literal 'experimental'. Keeping that schema literal
    does not change the explicitly SYNTHETIC origin of these numeric fixtures.
    """
    rows = [
        {"name": "Ethyl Alcohol", "smiles": "CCO", "provenance": "synthetic",
         "source": "SYNTHETIC algorithm-test structure; not validation"},
        {"name": "Dimethyl Ether", "smiles": "COC", "provenance": "synthetic",
         "source": "SYNTHETIC algorithm-test isomer; not validation"},
    ]
    identities = {row["name"].lower().replace(" ", "-"): _inchikey(row["smiles"]) for row in rows}

    def reference(identifier, candidate, peaks):
        return {
            "reference_id": identifier, "candidate_id": candidate,
            "inchikey": identities[candidate],
            "source": "SYNTHETIC invented HSQC shifts; not measured or scientific validation",
            "solvent": "SYNTHETIC/no measured solvent",
            "license": "Test-generated numeric data only",
            "provenance": "experimental",  # required curator assertion, not evidence
            "peaks": [{"h_ppm": h, "c_ppm": c} for h, c in peaks],
        }

    references = [
        reference("synthetic-z", "ethyl-alcohol", [(1.2, 18.0), (3.6, 58.0)]),
        reference("synthetic-a", "ethyl-alcohol", [(1.2, 18.0), (3.6, 58.0)]),
        reference("synthetic-ether", "dimethyl-ether", [(2.9, 45.0), (3.0, 46.0)]),
    ]
    return _write_libraries(tmp_path, rows, references)


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def real(registry, synthetic_libraries, monkeypatch):
    def no_mock(*args, **kwargs):
        pytest.fail("Real scientific execution must never fall back to MockToolExecutor")

    monkeypatch.setattr(MockToolExecutor, "execute", no_mock)
    return RealToolExecutor(registry, *synthetic_libraries)


@pytest.fixture
def api(monkeypatch, synthetic_libraries):
    """Inject ALL auth collaborators before the factory, even for dry_run=False.

    Replacing middleware kwargs after create_app is too late: install_auth can
    already have constructed Firestore clients by then.
    """
    from orchestrator import main

    def unexpected_auth_build(*args, **kwargs):
        pytest.fail("The app tried to construct live authentication collaborators")

    monkeypatch.setattr("auth.install.build_token_layer", unexpected_auth_build)
    stack = ExitStack()

    def create(*, mode="real", auth_dry_run=True, auth_enabled=True):
        monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", mode)
        monkeypatch.setenv("PHYTO_STRUCTURE_DB_PATH", str(synthetic_libraries[0]))
        monkeypatch.setenv("PHYTO_HSQC_REFERENCE_LIBRARY", str(synthetic_libraries[1]))
        main.get_runtime.cache_clear()
        config = AuthConfig("offline-test", None, auth_dry_run, 50, auth_enabled)
        layer = TokenLayer(
            MockFirebaseAuthClient(), MockTokenBalanceClient({"alice": 1000}),
            LoggingPublisher(), entitlements=None, workspaces=InMemoryWorkspaceStore(),
        )
        monkeypatch.setattr(
            main, "install_auth", lambda application, cfg: install_auth(application, cfg, layer=layer)
        )
        return stack.enter_context(TestClient(main.create_app(config)))

    yield create
    stack.close()
    main.get_runtime.cache_clear()


class TestExecutionConfiguration:
    @pytest.mark.parametrize("environment", ["local", "cloud"])
    @pytest.mark.parametrize("mode", [None, "", "invalid", "REAL"])
    def test_absent_or_invalid_mode_fails_closed(self, environment, mode):
        env = {"PHYTO_ENV": environment, "PHYTO_DRY_RUN": "1"}
        if mode is not None:
            env["PHYTO_SCIENTIFIC_EXECUTION"] = mode
        with pytest.raises(InvalidInputError, match="explicitly"):
            ScientificExecutionConfig.from_env(env)

    @pytest.mark.parametrize("environment", ["local", "cloud"])
    def test_explicit_offline_mock_factory(self, registry, environment):
        cfg = ScientificExecutionConfig.from_env({
            "PHYTO_ENV": environment, "PHYTO_SCIENTIFIC_EXECUTION": "mock", "PHYTO_DRY_RUN": "1",
        })
        assert cfg.mode == "mock" and cfg.environment == environment
        assert cfg.structure_library is cfg.reference_library is None
        assert isinstance(build_tool_executor(registry, cfg), MockToolExecutor)

    @pytest.mark.parametrize("dry_run", [None, "0", "true"])
    def test_cloud_mock_requires_explicit_offline_auth_flag(self, dry_run):
        env = {"PHYTO_ENV": "cloud", "PHYTO_SCIENTIFIC_EXECUTION": "mock"}
        if dry_run is not None:
            env["PHYTO_DRY_RUN"] = dry_run
        with pytest.raises(InvalidInputError, match="PHYTO_DRY_RUN=1"):
            ScientificExecutionConfig.from_env(env)

    @pytest.mark.parametrize("environment", ["local", "cloud"])
    @pytest.mark.parametrize("dry_run", ["0", "1"])
    def test_real_factory_is_independent_of_auth_mode(
        self, registry, synthetic_libraries, environment, dry_run,
    ):
        structures, references = synthetic_libraries
        if environment == "cloud" and dry_run == "1":
            # Offline mock authentication must never front real science on a
            # cloud deployment (mock tokens would reach real execution).
            with pytest.raises(InvalidInputError, match="PHYTO_DRY_RUN=1"):
                ScientificExecutionConfig.from_env({
                    "PHYTO_ENV": environment, "PHYTO_SCIENTIFIC_EXECUTION": "real",
                    "PHYTO_DRY_RUN": dry_run, "PHYTO_STRUCTURE_DB_PATH": str(structures),
                    "PHYTO_HSQC_REFERENCE_LIBRARY": str(references),
                })
            return
        cfg = ScientificExecutionConfig.from_env({
            "PHYTO_ENV": environment, "PHYTO_SCIENTIFIC_EXECUTION": "real",
            "PHYTO_DRY_RUN": dry_run, "PHYTO_STRUCTURE_DB_PATH": str(structures),
            "PHYTO_HSQC_REFERENCE_LIBRARY": str(references),
        })
        assert cfg.structure_library == structures
        assert cfg.reference_library == references
        executor = build_tool_executor(registry, cfg)
        assert isinstance(executor, RealToolExecutor)
        assert executor.mode == "real"

    @pytest.mark.parametrize("variable", ["PHYTO_STRUCTURE_DB_PATH", "PHYTO_HSQC_REFERENCE_LIBRARY"])
    @pytest.mark.parametrize("bad_path", ["absent", "missing", "directory"])
    def test_real_paths_required_and_redacted(self, synthetic_libraries, tmp_path, variable, bad_path):
        env = {"PHYTO_SCIENTIFIC_EXECUTION": "real",
               "PHYTO_STRUCTURE_DB_PATH": str(synthetic_libraries[0]),
               "PHYTO_HSQC_REFERENCE_LIBRARY": str(synthetic_libraries[1])}
        if bad_path == "absent":
            del env[variable]
        else:
            env[variable] = str(tmp_path if bad_path == "directory" else tmp_path / "private-missing")
        with pytest.raises(InvalidInputError) as error:
            ScientificExecutionConfig.from_env(env)
        assert variable in str(error.value)
        assert str(tmp_path) not in str(error.value)


class TestLocalProviders:
    def test_synthetic_csv_json_runs_real_rdkit_and_hungarian(self, real):
        # Reverse observations to require optimal assignment, not index pairing.
        result = real.execute(TOOL, _parameters(peaks=list(reversed(_parameters()["peaks"]))))
        assert [hit["reference_id"] for hit in result.hits] == [
            "synthetic-a", "synthetic-z", "synthetic-ether",
        ]
        first = result.hits[0]
        assert first["candidate"]["id"] == "ethyl-alcohol"
        assert first["candidate"]["formula"]["counts"] == {"C": 2, "H": 6, "O": 1}
        assert first["candidate"]["exact_mass"] == pytest.approx(46.041864812)
        assert first["candidate"]["provenance"] == "synthetic"
        matcher = first["matcher_result"]
        assert matcher["score"] == pytest.approx(0)
        assert matcher["coverage"] == 1
        assert matcher["ranking_convention"] == "lower_is_better"
        assert {(a["predicted_index"], a["observed_index"]) for a in matcher["assignments"]} == {
            (0, 1), (1, 0),
        }
        assert result.hits[-1]["matcher_result"]["score"] == pytest.approx(4)
        assert result.provenance["scientific_validation"] == "not_validated"
        assert result.provenance["human_review_required"] is True
        assert all("SYNTHETIC" in hit["source"] for hit in result.hits)

    def test_synthetic_tolerances_and_result_limit_are_used(self, real):
        peaks = [_peak(1.22, 18.2), _peak(3.62, 58.2)]
        loose = real.execute(TOOL, _parameters(peaks=peaks, max_results=1))
        tight = real.execute(TOOL, _parameters(
            peaks=peaks, h_tolerance_ppm=0.001, c_tolerance_ppm=0.01,
        ))
        assert len(loose.hits) == 1
        assert loose.hits[0]["matcher_result"]["score"] < tight.hits[-1]["matcher_result"]["score"]
        assert loose.provenance["matched_reference_count"] == 3
        assert loose.provenance["results_truncated"] is True
        assert loose.provenance["incomplete"] is True
        assert tight.provenance["h_tolerance_ppm"] == 0.001
        assert tight.provenance["c_tolerance_ppm"] == 0.01

    def test_no_formula_candidates_is_incomplete_not_identification(self, real):
        output = real.execute(TOOL, _parameters(formula="C99H100"))
        assert output.hits == []
        assert output.provenance["candidate_count"] == 0
        assert output.provenance["incomplete"] is True
        assert any("not an identification verdict" in text for text in output.limitations)
        assessment = output.evidence_assessment
        assert assessment["disposition"] == "no_hits"
        assert assessment["top_hit"] is None and assessment["near_tie"] is False
        assert "not a negative identification" in " ".join(assessment["reasons"])

    class TestEvidenceAssessment:
        """Engineering-default ranking-support signals; never identifications."""

        def test_rules_are_surfaced_in_provenance_and_labelled(self, real):
            output = real.execute(TOOL, _parameters())
            rules = output.provenance["assessment_rules"]
            assert rules["near_tie_score_gap"] > 0
            assert rules["min_query_coverage"] > 0
            assert rules["min_distinct_query_peaks"] > 0
            assert "not scientifically evaluated" in rules["rule_source"]
            assert rules["rule_source"] == output.evidence_assessment["rule_source"]

        def test_few_distinct_peaks_is_insufficient_evidence(self, real):
            # Both fixture peaks match the top reference exactly, but two
            # distinct cross-peaks cannot support ranking.
            output = real.execute(TOOL, _parameters())
            assessment = output.evidence_assessment
            assert assessment["disposition"] == "insufficient_evidence"
            assert assessment["distinct_query_peaks"] == 2
            assert assessment["near_tie"] is False
            assert any("transparency only" in text for text in assessment["reasons"])

        def test_low_query_coverage_is_insufficient_evidence(self, real):
            # Four extra unmatched query peaks drag coverage to 2/6 < 50%.
            peaks = [_peak(1.2, 18.0), _peak(3.6, 58.0)] + [
                _peak(5.0 + 0.1 * i, 120.0 + i) for i in range(4)
            ]
            output = real.execute(TOOL, _parameters(peaks=peaks))
            assessment = output.evidence_assessment
            assert assessment["disposition"] == "insufficient_evidence"
            top = assessment["top_hit"]
            assert top["matched_peak_count"] == 2 and top["query_coverage"] == pytest.approx(1 / 3)

        def test_match_count_agrees_with_matcher_and_matched_distinctness(self, real):
            peaks = [_peak(1.2, 18.0), _peak(3.6, 58.0), _peak(9.0, 100.0)]
            output = real.execute(TOOL, _parameters(peaks=peaks))
            top = output.evidence_assessment["top_hit"]
            matcher = output.hits[0]["matcher_result"]
            assert top["matched_peak_count"] == sum(a["cost"] < 4.0 for a in matcher["assignments"])
            assert top["query_coverage"] == pytest.approx(matcher["coverage"], abs=1e-6)
            assert top["matched_peak_count"] == 2
            assert output.evidence_assessment["disposition"] == "insufficient_evidence"

        def test_near_tie_between_candidates_is_ambiguous(self, registry, tmp_path):
            # Two formula-compatible candidates whose references sit at the
            # same distance from the query: no ranking can separate them.
            rows = [
                {"name": "Ethyl Alcohol", "smiles": "CCO", "provenance": "synthetic",
                 "source": "SYNTHETIC algorithm-test structure; not validation"},
                {"name": "Dimethyl Ether", "smiles": "COC", "provenance": "synthetic",
                 "source": "SYNTHETIC algorithm-test isomer; not validation"},
            ]
            identities = {row["name"].lower().replace(" ", "-"): _inchikey(row["smiles"]) for row in rows}
            shared = [(1.5, 30.0), (2.5, 40.0), (3.5, 50.0)]
            references = [
                {"reference_id": f"synthetic-{name}", "candidate_id": cid,
                 "inchikey": identities[cid],
                 "source": "SYNTHETIC invented HSQC shifts; not measured",
                 "solvent": "SYNTHETIC/no measured solvent",
                 "license": "Test-generated numeric data only",
                 "provenance": "experimental", "peaks": [{"h_ppm": h, "c_ppm": c} for h, c in shared]}
                for name, cid in (("alcohol", "ethyl-alcohol"), ("ether", "dimethyl-ether"))
            ]
            duplicate = dict(references[1], reference_id="synthetic-ether-second")
            references.append(duplicate)
            structures, reference_path = _write_libraries(tmp_path, rows, references)
            executor = RealToolExecutor(registry, structures, reference_path)
            output = executor.execute(TOOL, _parameters(
                formula="C2H6O",
                peaks=[_peak(h, c) for h, c in reversed(shared)],
            ))
            assessment = output.evidence_assessment
            assert assessment["disposition"] == "ambiguous"
            assert assessment["near_tie"] is True and assessment["score_gap"] == pytest.approx(0)
            assert assessment["top_hit"]["candidate_id"] != assessment["runner_up_hit"]["candidate_id"]
            assert any("does not support a unique preference" in text for text in assessment["reasons"])
            limited = executor.execute(TOOL, _parameters(
                peaks=[_peak(h, c) for h, c in shared], max_results=1,
            ))
            assert len(limited.hits) == 1
            assert limited.evidence_assessment["disposition"] == "ambiguous"
            assert limited.evidence_assessment["runner_up_hit"] is not None
            limited_two = executor.execute(TOOL, _parameters(
                peaks=[_peak(h, c) for h, c in shared], max_results=2,
            ))
            assert {hit["candidate"]["id"] for hit in limited_two.hits} == {"dimethyl-ether"}
            assert limited_two.evidence_assessment["disposition"] == "ambiguous"

        def test_separated_rich_query_is_ranked_not_identification(self, registry, tmp_path):
            rows = [
                {"name": "Ethyl Alcohol", "smiles": "CCO", "provenance": "synthetic",
                 "source": "SYNTHETIC algorithm-test structure; not validation"},
                {"name": "Dimethyl Ether", "smiles": "COC", "provenance": "synthetic",
                 "source": "SYNTHETIC algorithm-test isomer; not validation"},
            ]
            identities = {row["name"].lower().replace(" ", "-"): _inchikey(row["smiles"]) for row in rows}
            references = [
                {"reference_id": "synthetic-alcohol", "candidate_id": "ethyl-alcohol",
                 "inchikey": identities["ethyl-alcohol"],
                 "source": "SYNTHETIC invented HSQC shifts; not measured",
                 "solvent": "SYNTHETIC/no measured solvent",
                 "license": "Test-generated numeric data only",
                 "provenance": "experimental",
                 "peaks": [{"h_ppm": h, "c_ppm": c} for h, c in
                           [(1.5, 30.0), (2.5, 40.0), (3.5, 50.0)]]},
                {"reference_id": "synthetic-ether", "candidate_id": "dimethyl-ether",
                 "inchikey": identities["dimethyl-ether"],
                 "source": "SYNTHETIC invented HSQC shifts; not measured",
                 "solvent": "SYNTHETIC/no measured solvent",
                 "license": "Test-generated numeric data only",
                 "provenance": "experimental",
                 "peaks": [{"h_ppm": h, "c_ppm": c} for h, c in
                           [(2.9, 45.0), (3.0, 46.0), (3.1, 47.0)]]},
            ]
            structures, reference_path = _write_libraries(tmp_path, rows, references)
            executor = RealToolExecutor(registry, structures, reference_path)
            output = executor.execute(TOOL, _parameters(
                formula="C2H6O",
                peaks=[_peak(1.5, 30.0), _peak(2.5, 40.0), _peak(3.5, 50.0)],
            ))
            assessment = output.evidence_assessment
            assert assessment["disposition"] == "ranked"
            assert assessment["top_hit"]["candidate_id"] == "ethyl-alcohol"
            assert assessment["runner_up_hit"]["candidate_id"] == "dimethyl-ether"
            assert assessment["score_gap"] == pytest.approx(4.0)
            assert assessment["near_tie"] is False
            assert assessment["solvent_compatibility_checked"] is False
            assert any("not an identification" in text for text in assessment["reasons"])

        def test_all_candidates_without_references_reports_no_hits(self, registry, synthetic_libraries):
            structures, references = synthetic_libraries
            references.write_text("[]")
            executor = RealToolExecutor(registry, structures, references)
            output = executor.execute(TOOL, _parameters())
            assert output.hits == []
            assessment = output.evidence_assessment
            assert assessment["disposition"] == "no_hits"
            assert any("no measured reference" in text for text in assessment["reasons"])


    def test_missing_reference_is_unknown_not_a_fabricated_hit(self, registry, synthetic_libraries):
        structures, references = synthetic_libraries
        records = json.loads(references.read_text())
        references.write_text(json.dumps(records[:2]))
        output = RealToolExecutor(registry, structures, references).execute(TOOL, _parameters())
        assert output.unavailable_references == ["dimethyl-ether"]
        assert {hit["candidate"]["id"] for hit in output.hits} == {"ethyl-alcohol"}
        assert output.provenance["incomplete"] is True
        rich = RealToolExecutor(registry, structures, references).execute(TOOL, _parameters(
            peaks=[_peak(1.2, 18.0), _peak(3.6, 58.0), _peak(5.0, 100.0)]
        ))
        assert rich.evidence_assessment["disposition"] == "insufficient_evidence"
        assert any("lack references" in reason for reason in rich.evidence_assessment["reasons"])

    def test_library_and_provider_hashes_versions_and_audit(self, real, registry, synthetic_libraries, tmp_path):
        output = real.execute(TOOL, _parameters())
        provenance = output.provenance
        assert provenance["library_checksums"] == {
            "structure_sha256": hashlib.sha256(synthetic_libraries[0].read_bytes()).hexdigest(),
            "reference_sha256": hashlib.sha256(synthetic_libraries[1].read_bytes()).hexdigest(),
        }
        for key, provider in [("algorithm_sha256", real_executor.match_hsqc),
                              ("structure_provider_sha256", real_executor.FileStructureDB)]:
            assert provenance[key] == hashlib.sha256(Path(inspect.getsourcefile(provider)).read_bytes()).hexdigest()
        assert provenance["adapter_sha256"] == hashlib.sha256(Path(real_executor.__file__).read_bytes()).hexdigest()
        for package in ("numpy", "scipy", "rdkit", "pydantic"):
            assert provenance["versions"][package] == version(package)
        assert provenance["versions"]["phyto-companion-shared"]
        assert provenance["api_cost_usd"] == 0 and provenance["requires_network"] is False
        clone = real.provenance(TOOL)
        clone["library_checksums"].clear()
        assert real.provenance(TOOL)["library_checksums"] == provenance["library_checksums"]
        audit = ChainedAuditLogger(tmp_path / "provider-audit.jsonl")
        graph = PlannerAgent(registry).plan(
            run_id="provenance", workflow=WORKFLOW, dry_run=False, parameters=_parameters(),
        )
        result = WorkflowEngine(registry, real, audit).run(graph, ExecutionContext(run_id="provenance", dry_run=False))
        assert result.status == "awaiting_review"
        call, = [record for record in audit.records() if record["kind"] == "tool_call"]
        task = graph.task_by_id("rank-reference-hsqc")
        assert call["input_hash"] == sha256_dict(task.inputs)
        assert call["output_hash"] == sha256_dict(task.output)
        assert audit.verify_chain() is True


@pytest.mark.parametrize("changes", [
    {"formula": ""}, {"formula": "C2H6O+"}, {"formula": "[M+H]+"}, {"formula": "C0H6O"},
    {"formula": "Xx2"}, {"formula": "C99999"}, {"formula": "C2H6O trailing"},
    {"peaks": []}, {"peaks": [_peak(experiment_type="HMBC")]},
    {"peaks": [_peak(h_ppm=16)]}, {"peaks": [_peak(intensity=-1)]},
    {"peaks": [_peak(intensity=float("nan"))]}, {"peaks": [_peak(c_ppm=float("inf"))]},
    {"peaks": [{"h_ppm": 1.2}]}, {"peaks": [_peak()] * 257},
    {"h_tolerance_ppm": 0}, {"c_tolerance_ppm": 0}, {"h_tolerance_ppm": 2},
    {"max_results": 0}, {"max_results": 101}, {"unexpected": "no"},
])
def test_invalid_real_formula_or_hsqc_never_calls_matcher(real, monkeypatch, changes):
    matcher = Mock(side_effect=AssertionError("invalid input reached matcher"))
    monkeypatch.setattr(real_executor, "match_hsqc", matcher)
    with pytest.raises(InvalidInputError):
        real.execute(TOOL, _parameters(**changes))
    matcher.assert_not_called()


@pytest.mark.parametrize("damage", [
    "invalid-json", "object-root", "duplicate-json-key", "nonfinite-json", "unknown-candidate",
    "duplicate-reference", "unknown-field", "missing-license", "predicted-reference", "empty-peaks",
    "invalid-smiles", "duplicate-candidate", "duplicate-structure", "missing-file",
    "mismatched-identity",
])
def test_malformed_libraries_fail_without_mock_fallback(
    registry, synthetic_libraries, monkeypatch, capsys, damage,
):
    structures, references = synthetic_libraries
    records = json.loads(references.read_text())
    if damage == "invalid-json":
        references.write_text("{private secret malformed")
    elif damage == "object-root":
        references.write_text("{}")
    elif damage == "duplicate-json-key":
        references.write_text('[{"reference_id":"private secret","reference_id":"duplicate"}]')
    elif damage == "nonfinite-json":
        references.write_text('[{"peaks": [NaN]}]')
    elif damage == "invalid-smiles":
        structures.write_text("name,smiles\nprivate secret,not_a_smiles\n")
    elif damage == "duplicate-candidate":
        structures.write_text("name,smiles\nSame Name,CCO\nsame-name,COC\n")
    elif damage == "duplicate-structure":
        structures.write_text("name,smiles\nFirst,CCO\nSecond,CCO\n")
    elif damage == "missing-file":
        references.unlink()
    else:
        if damage == "unknown-candidate":
            records[0]["candidate_id"] = "private-secret-missing"
        elif damage == "mismatched-identity":
            # Valid key, wrong compound: measurements relabelled onto another structure.
            records[0]["inchikey"] = _inchikey("COC")
        elif damage == "duplicate-reference":
            records.append(copy.deepcopy(records[0]))
        elif damage == "unknown-field":
            records[0]["private"] = "secret"
        elif damage == "missing-license":
            del records[0]["license"]
        elif damage == "predicted-reference":
            records[0]["provenance"] = "predicted"
        elif damage == "empty-peaks":
            records[0]["peaks"] = []
        references.write_text(json.dumps(records))
    fallback = Mock(side_effect=AssertionError("mock fallback constructed"))
    monkeypatch.setattr("orchestrator.execution_config.MockToolExecutor", fallback)
    cfg = ScientificExecutionConfig("real", "local", structures, references)
    with pytest.raises(InvalidInputError) as error:
        build_tool_executor(registry, cfg)
    fallback.assert_not_called()
    assert "private" not in str(error.value)
    assert str(structures.parent) not in str(error.value)
    assert "secret" not in capsys.readouterr().err


@pytest.mark.parametrize("malformed", [
    {"score": -1}, {"coverage": 2}, {"ranking_convention": "higher_is_better"},
    {"n_observed": -1}, {"unexpected": "bad"},
])
def test_malformed_provider_output_rejected_by_result_schema(real, monkeypatch, malformed):
    valid = real_executor.match_hsqc([(1.2, 18.0)], [(1.2, 18.0)]).model_dump()
    monkeypatch.setattr(real_executor, "match_hsqc", lambda *args, **kwargs: valid | malformed)
    with pytest.raises(ValidationError):
        real.execute(TOOL, _parameters())


@pytest.mark.parametrize("failure", [RuntimeError("provider failed"), TimeoutError("local provider timed out")])
def test_failure_or_timeout_is_explicit_and_never_mocked(real, registry, monkeypatch, tmp_path, failure):
    matcher = Mock(side_effect=failure)
    monkeypatch.setattr(real_executor, "match_hsqc", matcher)
    graph = PlannerAgent(registry).plan(
        run_id="failure", workflow=WORKFLOW, dry_run=False, parameters=_parameters(),
    )
    audit = ChainedAuditLogger(tmp_path / "failure-audit.jsonl")
    result = WorkflowEngine(registry, real, audit).run(graph, ExecutionContext(run_id="failure", dry_run=False))
    assert result.status == "failed" and result.failed_task_id == "rank-reference-hsqc"
    failed = graph.task_by_id(result.failed_task_id)
    assert failed.output is None and type(failure).__name__ in failed.error
    assert matcher.call_count == 1  # no retry, no prediction, no mock fallback
    assert not any(record["kind"] == "tool_call" for record in audit.records())
    assert any(record.get("event") == "task_failed" for record in audit.records())


def test_engine_unsupported_preflight_has_no_calls_or_audit(real, registry, tmp_path, monkeypatch):
    execute = Mock(side_effect=AssertionError("preflight must precede ALL provider calls"))
    monkeypatch.setattr(real, "execute", execute)
    graph = TaskGraph(run_id="unsupported", workflow=WORKFLOW, dry_run=False, tasks=[
        Task(id="supported-first", description="must not run", tool=TOOL, inputs=_parameters()),
        Task(id="unsupported-later", description="unsupported", tool="rdkit.formula", inputs={"smiles": "CCO"}),
    ])
    audit = ChainedAuditLogger(tmp_path / "preflight-audit.jsonl")
    with pytest.raises(InvalidInputError, match="not supported"):
        WorkflowEngine(registry, real, audit).run(graph, ExecutionContext(run_id="unsupported", dry_run=False))
    execute.assert_not_called()
    assert audit.records() == []
    assert all(task.status == "pending" for task in graph.tasks)


class TestRealAnalysisAPI:
    @pytest.mark.parametrize("mode", ["mock", "real"])
    @pytest.mark.parametrize("auth_dry_run", [False, True])
    @pytest.mark.parametrize("auth_enabled", [False, True])
    def test_health_offline_science_and_auth_modes_are_independent(self, api, mode, auth_dry_run, auth_enabled):
        client = api(mode=mode, auth_dry_run=auth_dry_run, auth_enabled=auth_enabled)
        response = client.get("/health")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["environment"] == "local"
        assert body["dry_run"] is auth_dry_run
        assert body["scientific_execution"] == mode
        assert body["tool_executor"] == ("RealToolExecutor" if mode == "real" else "MockToolExecutor")
        assert body["scientific_ready"] is False and body["scientific_validation"] == "not_validated"
        readiness = body["execution_readiness"]
        assert readiness["mode"] == mode and readiness["requires_network"] is False
        if mode == "real":
            assert readiness["configuration_valid"] is True
            assert readiness["supported_tools"] == [TOOL]
            assert readiness["supported_workflows"] == [WORKFLOW]
        # Auth-independence of the scientific mode: with valid credentials
        # the same request is gated only by the executor's capability. Mock
        # mode has no offline implementation of this workflow and must
        # refuse it (422) rather than fabricate science; real mode runs it.
        status = client.post("/analysis", headers=AUTH, json=_request()).status_code
        assert status == (422 if mode == "mock" else 200)

    def test_analysis_returns_ranking_then_review_resumes_without_reexecuting(self, api, monkeypatch):
        from orchestrator import main

        client = api(auth_dry_run=False)
        runtime = main.get_runtime()
        execute = Mock(wraps=runtime.executor.execute)
        monkeypatch.setattr(runtime.executor, "execute", execute)
        monkeypatch.setattr(MockToolExecutor, "execute", Mock(side_effect=AssertionError("no mock science")))
        # Real review completion must not mint anything through a mock DOI
        # registrar: real mode configures none at all.
        assert runtime.doi_registrar is None
        parameters = _parameters(max_results=1, h_tolerance_ppm=0.04, c_tolerance_ppm=0.4)
        response = client.post("/analysis", headers=AUTH, json=_request(parameters=parameters))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "awaiting_review"
        summary = body["summary"]
        assert summary["scientific_execution"] == "real"
        assert summary["scientific_validation"] == "not_validated"
        assert summary["awaiting_task_id"] == "review-reference-hsqc"
        artifact, = summary["scientific_results"]
        assert artifact["tool"] == TOOL and artifact["task_id"] == "rank-reference-hsqc"
        assert len(artifact["output"]["hits"]) == 1
        assert artifact["output"]["provenance"]["h_tolerance_ppm"] == 0.04
        assert artifact["output"]["provenance"]["c_tolerance_ppm"] == 0.4
        execute.assert_called_once_with(TOOL, parameters)
        run_id = body["run_id"]
        # Isolate checkpoint semantics from the unpriced-route gate while
        # retaining authentication and run ownership enforcement.
        runtime_before = runtime.runs[run_id]["graph"].task_by_id("rank-reference-hsqc").output
        with monkeypatch.context() as patch:
            from types import SimpleNamespace
            import auth.middleware as auth_middleware

            patch.setattr(auth_middleware, "cost_for_route", lambda method, path: SimpleNamespace(tokens=0))
            wrong = client.post(f"/runs/{run_id}/approve", headers=AUTH, json={
                "checkpoint": "before_mechanism_hypothesis", "actor": "reviewer",
            })
            assert wrong.status_code == 409
            approved = client.post(f"/runs/{run_id}/approve", headers=AUTH, json={
                "checkpoint": "before_confirmed_claim", "actor": "reviewer",
            })
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "complete"
            assert approved.json()["citation"] is None
            repeated = client.post(f"/runs/{run_id}/approve", headers=AUTH, json={
                "checkpoint": "before_confirmed_claim", "actor": "reviewer",
            })
            assert repeated.status_code == 409
        assert execute.call_count == 1
        assert runtime.runs[run_id]["graph"].task_by_id("rank-reference-hsqc").output == runtime_before
        assert any(record.get("event") == "checkpoint_approved" for record in runtime.audit.records())
        assert runtime.audit.verify_chain() is True

    @pytest.mark.parametrize("workflow", ["single_compound", "identify", "novelty_scan", "compare_extracts"])
    def test_unsupported_real_workflow_422_before_provider(self, api, monkeypatch, workflow):
        from orchestrator import main

        client = api()
        executor = main.get_runtime().executor
        calls = Mock(side_effect=AssertionError("unsupported request reached provider"))
        monkeypatch.setattr(executor, "execute", calls)
        response = client.post("/analysis", headers=AUTH, json=_request(workflow=workflow))
        assert response.status_code == 422, response.text
        calls.assert_not_called()
        assert main.get_runtime().runs == {}
        assert main.get_runtime().audit.records() == []

    @pytest.mark.parametrize("changes", [
        {"parameters": {}}, {"parameters": _parameters(peaks=[])},
        {"parameters": _parameters(h_tolerance_ppm=0)},
        {"parameters": _parameters(unexpected=True)}, {"modalities": ["ms2"]},
        {"modalities": ["nonsense"]}, {"dry_run": True},
    ])
    def test_invalid_request_rejected_before_provider(self, api, monkeypatch, changes):
        from orchestrator import main

        client = api()
        calls = Mock(side_effect=AssertionError("invalid request reached provider"))
        monkeypatch.setattr(main.get_runtime().executor, "execute", calls)
        response = client.post("/analysis", headers=AUTH, json=_request(**changes))
        assert response.status_code == 422, response.text
        calls.assert_not_called()

    def test_malformed_json_is_422(self, api):
        response = api().post("/analysis", headers=AUTH | {"Content-Type": "application/json"}, content="{")
        assert response.status_code == 422


class TestResearchWorkspaceContract:
    def test_capabilities_are_authenticated_free_and_truthful(self, api):
        client = api()
        assert client.get('/capabilities').status_code == 401
        response = client.get('/capabilities', headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data['schema_version'] == 1
        assert data['supported_workflows'] == [WORKFLOW]
        assert data['scientific_execution'] == 'real'
        assert data['production_ready'] is False
        assert data['scientific_validation'] == 'not_validated'
        assert data['run_storage'] == 'process_local'
        assert data['analysis_tokens'] == 20

    def test_mock_backend_does_not_advertise_real_workflow(self, api):
        data = api(mode='mock').get('/capabilities', headers=AUTH).json()
        assert data['scientific_execution'] == 'mock'
        assert WORKFLOW not in data['supported_workflows']

    def test_reload_preserves_evidence_and_review_uses_verified_actor(self, api):
        from orchestrator import main
        client = api()
        created = client.post('/analysis', json=_request(), headers=AUTH)
        assert created.status_code == 200, created.text
        body = created.json()
        run_id = body['run_id']
        refreshed = client.get(f'/runs/{run_id}', headers=AUTH)
        assert refreshed.status_code == 200
        assert refreshed.json()['scientific_results'] == body['summary']['scientific_results']
        assert refreshed.json()['scientific_execution'] == 'real'
        assert client.get(f'/runs/{run_id}', headers={'Authorization': 'Bearer mock:bob'}).status_code == 404
        approval = client.post(f'/runs/{run_id}/approve', headers=AUTH, json={
            'checkpoint': 'before_confirmed_claim', 'actor': 'impersonated-scientist',
        })
        assert approval.status_code == 200, approval.text
        assert main.get_runtime().runs[run_id]['context'].actor == 'alice'
        after = client.get(f'/runs/{run_id}', headers=AUTH).json()
        assert after['scientific_results'] == refreshed.json()['scientific_results']
        assert after['scientific_validation'] == 'not_validated'

    def test_private_responses_cannot_be_cached(self, api):
        client = api()
        for path, headers in [('/capabilities', AUTH), ('/runs/missing', AUTH), ('/capabilities', {})]:
            response = client.get(path, headers=headers)
            assert response.headers['cache-control'] == 'no-store'
            assert response.headers['x-content-type-options'] == 'nosniff'

    def test_parallel_reviews_resume_only_once(self, api, monkeypatch):
        import time
        from concurrent.futures import ThreadPoolExecutor
        from orchestrator import main
        client = api()
        created = client.post('/analysis', json=_request(), headers=AUTH)
        run_id = created.json()['run_id']
        engine = main.get_runtime().engine
        original = engine.run
        calls = []

        def slow_resume(*args, **kwargs):
            calls.append(1)
            time.sleep(0.05)
            return original(*args, **kwargs)

        monkeypatch.setattr(engine, 'run', slow_resume)
        def approve(_):
            return client.post(f'/runs/{run_id}/approve', headers=AUTH, json={
                'checkpoint': 'before_confirmed_claim', 'actor': 'reviewer',
            }).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(approve, range(2)))
        assert sorted(statuses) == [200, 409]
        assert len(calls) == 1
