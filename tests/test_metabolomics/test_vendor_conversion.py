"""M1-b vendor-native conversion contract (Thermo .raw -> mzML).

Fail-closed acceptance: no parser configured -> named error, never a
synthetic mzML. Binary resolution, extension gating and checksum
recording are pinned here.
"""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from metabolomics.vendor_conversion import (
    SUPPORTED_VENDOR_EXTENSIONS,
    ThermoRawFileParserConverter,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("THERMO_RAW_FILE_PARSER", raising=False)
    monkeypatch.delenv("MZMINE_HOME", raising=False)


class TestResolveParser:
    def test_missing_everywhere_fails_closed(self):
        with pytest.raises(CloudServiceError) as excinfo:
            ThermoRawFileParserConverter().resolve_parser()
        assert "ThermoRawFileParser" in str(excinfo.value)

    def test_explicit_path_wins(self, tmp_path):
        binary = tmp_path / "ThermoRawFileParser"
        binary.write_bytes(b"#!\n")
        found = ThermoRawFileParserConverter(parser_path=str(binary)).resolve_parser()
        assert found == str(binary)

    def test_env_path_wins(self, tmp_path, monkeypatch):
        binary = tmp_path / "ThermoRawFileParser"
        binary.write_bytes(b"#!\n")
        monkeypatch.setenv("THERMO_RAW_FILE_PARSER", str(binary))
        assert ThermoRawFileParserConverter().resolve_parser() == str(binary)

    def test_bundled_copy_in_mzmine_home(self, tmp_path, monkeypatch):
        bundled = tmp_path / "external_tools" / "thermo_raw_file_parser"
        bundled.mkdir(parents=True)
        binary = bundled / "ThermoRawFileParser"
        binary.write_bytes(b"#!\n")
        monkeypatch.setenv("MZMINE_HOME", str(tmp_path))
        assert ThermoRawFileParserConverter().resolve_parser() == str(binary)


class TestConvertGating:
    def test_missing_input_rejected(self, tmp_path):
        conv = ThermoRawFileParserConverter(parser_path="/bin/true")
        with pytest.raises(InvalidInputError):
            conv.convert(tmp_path / "nope.raw", tmp_path)

    def test_unsupported_extension_rejected(self, tmp_path):
        fake = tmp_path / "sample.d"
        fake.write_bytes(b"x")
        conv = ThermoRawFileParserConverter(parser_path="/bin/true")
        with pytest.raises(InvalidInputError) as excinfo:
            conv.convert(fake, tmp_path)
        assert excinfo.value.context["suffix"] == ".d"

    def test_supported_extensions_recorded(self):
        assert SUPPORTED_VENDOR_EXTENSIONS == (".raw",)

    def test_parser_failure_surfaces_log(self, tmp_path, monkeypatch):
        fake = tmp_path / "sample.raw"
        fake.write_bytes(b"x" * 64)
        script = tmp_path / "failing-parser"
        script.write_text("#!/bin/sh\nexit 3\n")
        script.chmod(0o755)
        conv = ThermoRawFileParserConverter(parser_path=str(script))
        with pytest.raises(CloudServiceError) as excinfo:
            conv.convert(fake, tmp_path)
        assert excinfo.value.context["returncode"] == 3

    def test_success_records_checksums_and_version(self, tmp_path, monkeypatch):
        raw = tmp_path / "sample.raw"
        raw.write_bytes(b"RAWBYTES")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        script = tmp_path / "ok-parser"
        # Ignore all args; write the mzML the contract expects.
        script.write_text(
            f"#!/bin/sh\nprintf 'fake mzml' > {out_dir / 'sample.mzML'}\necho '4.3.1'\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("THERMO_RAW_FILE_PARSER", str(script))
        conv = ThermoRawFileParserConverter()
        result = conv.convert(raw, out_dir)
        assert result.mzml_path.name == "sample.mzML"
        import hashlib

        assert result.raw_sha256 == hashlib.sha256(b"RAWBYTES").hexdigest()
        assert result.mzml_sha256 == hashlib.sha256(b"fake mzml").hexdigest()
        assert result.parser_version == "4.3.1"
