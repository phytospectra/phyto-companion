"""Vendor-native LC-MS conversion (.raw -> mzML) via ThermoRawFileParser.

M1-b acceptance surface (ADR-0086/ADR-0087): vendor-native files are NOT
read by the feature-detection batch; they must first be converted to
mzML by a pinned, license-documented converter. This module wraps
ThermoRawFileParser (Apache-2.0, using Thermo's RawFileReader), which
MZmine ships under ``external_tools/thermo_raw_file_parser`` so the
dependency is reproducible from the pinned MZMINE_HOME alone.

Fail-closed contract: when no parser binary is configured or locatable,
conversion raises ``CloudServiceError`` with the exact configuration
gap -- it never silently substitutes a synthetic file.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from phyto_shared.exceptions import CloudServiceError, InvalidInputError

__all__ = [
    "SUPPORTED_VENDOR_EXTENSIONS",
    "ThermoRawFileParserConverter",
    "ConversionResult",
]

#: Vendor extensions this converter accepts. Thermo ``.raw`` only: this
#: is deliberately NOT a claim about other vendors (Bruker ``.d``/
#: ``.baf``, Waters ``.raw`` directory format, SCIEX ``.wiff`` stay
#: separate acceptance items, ADR-0086).
SUPPORTED_VENDOR_EXTENSIONS: tuple[str, ...] = (".raw",)


class ConversionResult:
    """Measured outcome of one conversion (checksums, parser version)."""

    __slots__ = ("mzml_path", "raw_sha256", "mzml_sha256", "parser_version", "parser_path")

    def __init__(
        self,
        mzml_path: Path,
        raw_sha256: str,
        mzml_sha256: str,
        parser_version: str,
        parser_path: str,
    ) -> None:
        self.mzml_path = mzml_path
        self.raw_sha256 = raw_sha256
        self.mzml_sha256 = mzml_sha256
        self.parser_version = parser_version
        self.parser_path = parser_path


class ThermoRawFileParserConverter:
    """Converts Thermo .raw files to indexed mzML (real plane).

    Binary resolution order (first match wins):
    1. explicit ``parser_path`` argument / ``THERMO_RAW_FILE_PARSER`` env;
    2. ``MZMINE_HOME``-relative bundled copies -- either the plain
       ``external_tools/thermo_raw_file_parser/ThermoRawFileParser``
       layout or inside the macOS .app bundle
       (``mzmine.app/Contents/external_tools/...``);
    3. ``ThermoRawFileParser`` on PATH.
    """

    #: the bundled macOS entry point name inside the MZmine .app
    BUNDLED_RELATIVE = Path("external_tools/thermo_raw_file_parser") / "ThermoRawFileParser"
    #: macOS .app bundle variant (portable academia build)
    BUNDLED_APP_RELATIVE = (
        Path("mzmine.app") / "Contents" / "external_tools" / "thermo_raw_file_parser" / "ThermoRawFileParser"
    )

    def __init__(self, parser_path: str | None = None) -> None:
        self.parser_path = parser_path or os.environ.get("THERMO_RAW_FILE_PARSER") or ""

    def resolve_parser(self) -> str:
        if self.parser_path and Path(self.parser_path).is_file():
            return self.parser_path
        mzmine_home = os.environ.get("MZMINE_HOME", "")
        if mzmine_home:
            for candidate in (
                Path(mzmine_home) / self.BUNDLED_RELATIVE,
                Path(mzmine_home) / self.BUNDLED_APP_RELATIVE,
            ):
                if candidate.is_file():
                    return str(candidate)
        found = shutil.which("ThermoRawFileParser")
        if found:
            return found
        raise CloudServiceError(
            "Thermo .raw conversion requires ThermoRawFileParser (Apache-"
            "2.0). Set THERMO_RAW_FILE_PARSER to the binary, or point "
            "MZMINE_HOME at an MZmine installation that bundles it under "
            "external_tools/thermo_raw_file_parser. Refusing to convert "
            "without a real parser.",
            context={"tried": [self.parser_path, "MZMINE_HOME bundle", "PATH"]},
        )

    def parser_version(self) -> str:
        parser = self.resolve_parser()
        proc = subprocess.run(
            [parser, "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return (proc.stdout or proc.stderr).strip()

    def convert(self, raw_path: Path, output_dir: Path) -> ConversionResult:
        if not raw_path.is_file():
            raise InvalidInputError(
                "vendor input file does not exist",
                context={"path": str(raw_path)},
            )
        if raw_path.suffix.lower() not in SUPPORTED_VENDOR_EXTENSIONS:
            raise InvalidInputError(
                "unsupported vendor extension for this converter",
                context={
                    "suffix": raw_path.suffix,
                    "supported": list(SUPPORTED_VENDOR_EXTENSIONS),
                },
            )
        parser = self.resolve_parser()
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        proc = subprocess.run(
            [
                parser,
                "-i",
                str(raw_path),
                "-o",
                str(output_dir),
                "-f",
                "2",
            ],  # 2 = indexed mzML
            capture_output=True,
            text=True,
            timeout=3600,
        )
        expected = output_dir / (raw_path.stem + ".mzML")
        if proc.returncode != 0 or not expected.is_file():
            raise CloudServiceError(
                "ThermoRawFileParser conversion failed",
                context={
                    "returncode": proc.returncode,
                    "log_tail": "\n".join(
                        ((proc.stdout or "") + (proc.stderr or "")).splitlines()[-40:]
                    ),
                },
            )
        mzml_sha256 = hashlib.sha256(expected.read_bytes()).hexdigest()
        return ConversionResult(
            mzml_path=expected,
            raw_sha256=raw_sha256,
            mzml_sha256=mzml_sha256,
            parser_version=self.parser_version(),
            parser_path=parser,
        )
