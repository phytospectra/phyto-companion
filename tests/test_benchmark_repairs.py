"""Run the offline regression harness without rebuilding tracked artifacts."""

from pathlib import Path
import shutil
import subprocess
import sys


def test_benchmark_repair_harness(tmp_path):
    root = Path(__file__).resolve().parents[1]
    pilot = Path("benchmark/blinded_pilot")
    fixtures = Path("tests/fixtures/real_npmrd")
    for relative in (pilot, fixtures):
        shutil.copytree(root / relative, tmp_path / relative,
                        ignore=shutil.ignore_patterns("__pycache__"))
    result = subprocess.run(
        [sys.executable, str(tmp_path / pilot / "test_repairs.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL REPAIR CHECKS PASS" in result.stdout
