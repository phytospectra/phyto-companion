"""Bounded, offline measured-reference HSQC retrieval and distance ranking.

``RealToolExecutor(registry, structure_path, reference_path)`` loads a curated
FileStructureDB CSV and an explicitly supplied UTF-8 JSON array, once at startup.
There is no bundled reference dataset, prediction fallback, or network provider.
Reference records have this schema (unknown fields are rejected)::

    {"reference_id": "accession-1", "candidate_id": "ethyl-alcohol",
     "inchikey": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
     "source": "dataset accession / measurement citation", "solvent": "CDCl3",
     "license": "explicit reuse license", "provenance": "experimental",
     "peaks": [{"h_ppm": 1.2, "c_ppm": 18.0, "intensity": 1.0}]}

``candidate_id`` is FileStructureDB's ``name.lower().replace(' ', '-')``, NOT
an InChIKey or arbitrary display name; ``inchikey`` is the record's structural
identity and MUST resolve to that same candidate in the structure library, so a
mislabelled reference cannot attach another compound's measurements. IDs must
resolve uniquely; reference IDs must also be unique. Several references per candidate are allowed and ranked
individually. Intensity is optional and is NOT used by the chemical-shift matcher.
``experimental`` is a curator assertion, not independent scientific validation.
Synthetic fixtures can exercise the real providers but do not become measured
public references by doing so. Human review is always required downstream.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, MolecularFormula, Peak2D

# Repository/test imports and the existing full-plane container use different
# package layouts. Never change sys.path or mask a missing provider dependency.
try:
    from services.elucidation.app.hungarian_matcher import MatchResult, match_hsqc
    from services.elucidation.app.structure_db import FileStructureDB
except ModuleNotFoundError as exc:
    if exc.name not in {"services", "services.elucidation", "services.elucidation.app"}:
        raise
    from elucidation.hungarian_matcher import MatchResult, match_hsqc
    from elucidation.structure_db import FileStructureDB

if TYPE_CHECKING:
    from .tool_registry import ToolRegistry

__all__ = ["ReferenceHSQCInput", "ReferenceHSQCOutput", "RealToolExecutor"]

_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_STRUCTURES = 1000
_MAX_REFERENCES = 256
_MAX_PEAKS = 256
_MAX_REFERENCE_PEAKS = 8192
_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)([0-9]*)")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _formula_counts(text: str) -> dict[str, int]:
    """Consume the entire neutral elemental formula; no adduct/charge guessing."""
    if not isinstance(text, str) or not 1 <= len(text) <= 100:
        raise ValueError("formula must be a bounded neutral elemental formula")
    text = text.strip()
    counts: dict[str, int] = {}
    position = 0
    for token in _FORMULA_TOKEN.finditer(text):
        if token.start() != position:
            raise ValueError("formula contains unsupported syntax")
        symbol, digits = token.groups()
        if len(digits) > 4:
            raise ValueError("formula atom count exceeds limit")
        count = int(digits) if digits else 1
        if count < 1:
            raise ValueError("formula counts must be positive")
        counts[symbol] = counts.get(symbol, 0) + count
        position = token.end()
    if position != len(text) or not counts or sum(counts.values()) > 10000:
        raise ValueError("invalid or oversized elemental formula")
    # Shared element table rejects unknown symbols.
    return MolecularFormula(counts=counts).counts


class ReferenceHSQCInput(_Contract):
    """Known neutral formula and observed, finite HSQC cross-peaks only."""

    formula: str = Field(min_length=1, max_length=100)
    peaks: list[Peak2D] = Field(min_length=1, max_length=_MAX_PEAKS)
    h_tolerance_ppm: float = Field(default=0.05, ge=0.000001, le=1.0)
    c_tolerance_ppm: float = Field(default=0.5, ge=0.000001, le=20.0)
    max_results: int = Field(default=10, ge=1, le=100, strict=True)

    @field_validator("formula")
    @classmethod
    def _formula(cls, value: str) -> str:
        _formula_counts(value)
        return value.strip()

    @field_validator("peaks")
    @classmethod
    def _hsqc(cls, peaks: list[Peak2D]) -> list[Peak2D]:
        for peak in peaks:
            if peak.experiment_type != "HSQC":
                raise ValueError("only HSQC peaks are supported")
            if not all(math.isfinite(v) for v in (peak.h_ppm, peak.c_ppm, peak.intensity)):
                raise ValueError("HSQC values must be finite")
        return peaks


class _ReferencePeak(_Contract):
    h_ppm: float = Field(ge=-5, le=15, strict=True)
    c_ppm: float = Field(ge=-50, le=300, strict=True)
    intensity: float | None = Field(default=None, ge=0, strict=True)


class _ReferenceRecord(_Contract):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    reference_id: str = Field(min_length=1, max_length=200)
    candidate_id: str = Field(min_length=1, max_length=200)
    inchikey: str = Field(pattern=r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
    source: str = Field(min_length=1, max_length=2000)
    solvent: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=1000)
    provenance: Literal["experimental"]
    peaks: list[_ReferencePeak] = Field(min_length=1, max_length=_MAX_PEAKS)


class ReferenceHSQCHit(_Contract):
    candidate: Candidate
    reference_id: str
    source: str
    solvent: str
    license: str
    reference_provenance: Literal["experimental"]
    matcher_result: MatchResult


class ReferenceHSQCOutput(_Contract):
    """Typed internal contract, serialized through the registry's public schema."""

    hits: list[ReferenceHSQCHit] = Field(default_factory=list, max_length=100)
    query_peak_count: int = Field(ge=1, le=_MAX_PEAKS)
    formula: str
    unavailable_references: list[str] = Field(default_factory=list)
    limitations: list[str]
    provenance: dict[str, Any]


def _read_bounded(path: str | Path) -> bytes:
    with Path(path).open("rb") as handle:
        data = handle.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise ValueError("library exceeds size limit")
    return data


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _module_hash(function: Any) -> str:
    # Hash the actual imported implementation, not a guessed checkout path.
    import inspect

    path = inspect.getsourcefile(function)
    if path is None:
        raise ValueError("provider source unavailable")
    return _sha256(_read_bounded(path))


def _versions() -> dict[str, str]:
    result = {}
    for package in ("numpy", "scipy", "rdkit", "pydantic", "phyto-companion-shared"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            if package == "phyto-companion-shared":
                # Tests expose shared/ directly without installed dist metadata.
                from phyto_shared import __version__

                result[package] = __version__
            else:
                result[package] = "unavailable"
    return result


class RealToolExecutor:
    """Zero-API-cost local adapter; unsupported tools fail before provider calls.

    Startup failures deliberately contain neither configured paths nor library
    contents. Query budget violations are errors, never silent candidate pruning.
    The registry must register ``reference_hsqc.rank`` before execute/preflight.
    """

    mode = "real"
    supported_tools = frozenset({"reference_hsqc.rank"})
    supported_workflows = frozenset({"reference_hsqc_identify"})

    def __init__(
        self, registry: ToolRegistry, structure_path: str | Path, reference_path: str | Path
    ) -> None:
        self.registry = registry
        try:
            structure_bytes = _read_bounded(structure_path)
            reference_bytes = _read_bounded(reference_path)
            rows = list(csv.DictReader(io.StringIO(structure_bytes.decode("utf-8"))))
            if not 1 <= len(rows) <= _MAX_STRUCTURES:
                raise ValueError("structure library row limit")
            ids = [(row.get("name") or "").strip().lower().replace(" ", "-") for row in rows]
            if any(not item or len(item) > 200 for item in ids) or len(set(ids)) != len(ids):
                raise ValueError("ambiguous candidate identifiers")
            # Bound SMILES parsing work before invoking RDKit in FileStructureDB.
            if any(not row.get("smiles") or len(row["smiles"]) > 4096 for row in rows):
                raise ValueError("invalid or oversized structure")
            # RDKit parse diagnostics otherwise echo invalid private SMILES to stderr.
            from rdkit import rdBase

            with rdBase.BlockLogs():
                self._db = FileStructureDB(structure_path)
            # FileStructureDB owns parsing. Detect edits between snapshot and load.
            if _read_bounded(structure_path) != structure_bytes:
                raise ValueError("structure library changed during startup")
            candidates = [self._db.candidate_by_inchikey(key) for key in self._db.known_inchikeys()]
            by_id = {candidate.id: candidate for candidate in candidates if candidate is not None}
            if set(by_id) != set(ids):
                raise ValueError("structure identifier mismatch")
            # FileStructureDB defaults source to a filename. Do not return paths.
            for row, candidate_id in zip(rows, ids):
                if not (row.get("source") or "").strip():
                    by_id[candidate_id].source = "configured structure library"
            raw = json.loads(
                reference_bytes.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            if not isinstance(raw, list) or len(raw) > _MAX_REFERENCES:
                raise ValueError("reference library must be a bounded array")
            records = [_ReferenceRecord.model_validate(record) for record in raw]
            if len({record.reference_id for record in records}) != len(records):
                raise ValueError("duplicate reference identifiers")
            if sum(len(record.peaks) for record in records) > _MAX_REFERENCE_PEAKS:
                raise ValueError("reference peak budget exceeded")
            for record in records:
                candidate = self._db.candidate_by_inchikey(record.inchikey)
                if candidate is None or candidate.id != record.candidate_id:
                    raise ValueError("reference identity does not match structure")
            if any(record.candidate_id not in by_id for record in records):
                raise ValueError("unresolved reference candidate")
            self._references: dict[str, list[_ReferenceRecord]] = {}
            for record in records:
                self._references.setdefault(record.candidate_id, []).append(record)
            for references in self._references.values():
                references.sort(key=lambda reference: reference.reference_id)
            self._structure_count = len(rows)
            self._reference_count = len(records)
            self._provenance = {
                "mode": "real",
                "provider": "FileStructureDB + match_hsqc (local measured-reference JSON)",
                "algorithm": "scipy.optimize.linear_sum_assignment via match_hsqc",
                "algorithm_sha256": _module_hash(match_hsqc),
                "structure_provider_sha256": _module_hash(FileStructureDB),
                "adapter_sha256": _sha256(_read_bounded(__file__)),
                "library_checksums": {
                    "structure_sha256": _sha256(structure_bytes),
                    "reference_sha256": _sha256(reference_bytes),
                },
                "versions": _versions(),
                "ranking_convention": "lower_is_better",
                "matcher_cap": 4.0,
                "multiplicity_mode": "none",
                "requires_network": False,
                "api_cost_usd": 0.0,
                "scientific_ready": False,
                "scientific_validation": "not_validated",
                "human_review_required": True,
                "reference_provenance": "curator_asserted_experimental",
            }
        except Exception:
            raise InvalidInputError("Invalid local reference-HSQC configuration") from None

    def preflight(self, tool_names: Iterable[str]) -> None:
        # Inspect the whole plan before consulting the registry or running a tool.
        names = tuple(tool_names)
        if any(name not in self.supported_tools for name in names):
            raise InvalidInputError("Tool is not supported by the real local executor")
        for name in names:
            self.registry.get_tool(name)

    def provenance(self, tool_name: str) -> dict[str, Any]:
        if tool_name not in self.supported_tools:
            raise InvalidInputError("Tool is not supported by the real local executor")
        # Return an independent JSON-compatible copy; callers cannot alter hashes.
        return {"tool": tool_name, **json.loads(json.dumps(self._provenance))}

    def health(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "configuration_valid": True,
            "dependencies_available": True,
            "versions": dict(self._provenance["versions"]),
            "structure_count": self._structure_count,
            "reference_count": self._reference_count,
            "supported_tools": sorted(self.supported_tools),
            "supported_workflows": sorted(self.supported_workflows),
            "requires_network": False,
            "scientific_ready": False,
            "scientific_validation": "not_validated",
        }

    def execute(self, tool_name: str, inputs: dict[str, Any]) -> BaseModel:
        self.preflight((tool_name,))
        spec = self.registry.get_tool(tool_name)
        try:
            registered = spec.input_schema.model_validate(inputs)
            query = ReferenceHSQCInput.model_validate(registered.model_dump())
        except (ValidationError, ValueError, InvalidInputError):
            raise InvalidInputError("Invalid reference-HSQC input") from None
        counts = _formula_counts(query.formula)
        candidates = sorted(self._db.candidates_for_formula(counts), key=lambda item: item.id)
        observed = [(peak.h_ppm, peak.c_ppm) for peak in query.peaks]
        hits = []
        unavailable = []
        for candidate in candidates:
            references = self._references.get(candidate.id, [])
            if not references:
                unavailable.append(candidate.id)
            for reference in references:
                result = match_hsqc(
                    [(peak.h_ppm, peak.c_ppm) for peak in reference.peaks],
                    observed,
                    tol_h=query.h_tolerance_ppm,
                    tol_c=query.c_tolerance_ppm,
                    cap=4.0,
                    multiplicity_mode="none",
                )
                hits.append(
                    ReferenceHSQCHit(
                        candidate=candidate,
                        reference_id=reference.reference_id,
                        source=reference.source,
                        solvent=reference.solvent,
                        license=reference.license,
                        reference_provenance=reference.provenance,
                        matcher_result=result,
                    )
                )
        hits.sort(key=lambda hit: (hit.matcher_result.score, hit.candidate.id, hit.reference_id))
        provenance = self.provenance(tool_name)
        provenance.update(
            {
                "candidate_count": len(candidates),
                "matched_reference_count": len(hits),
                "results_truncated": len(hits) > query.max_results,
                "incomplete": not hits or bool(unavailable) or len(hits) > query.max_results,
                "h_tolerance_ppm": query.h_tolerance_ppm,
                "c_tolerance_ppm": query.c_tolerance_ppm,
            }
        )
        limitations = [
            "Not scientifically validated; human review is required, not an identification verdict.",
            "Only the configured local structure and curator-declared measured-reference libraries were searched.",
            "Missing reference evidence is unknown, not a negative match; empty hits are incomplete evidence.",
            "Distances are not probabilities or calibrated confidence; lower is better.",
            "Solvent, temperature, pH, field strength, stereochemistry and mixtures are not corrected or resolved.",
            "Intensity, multiplicity and solvent flags are not used; all supplied HSQC coordinates are matched.",
            "Inherited matcher uses rectangular assignments: excess peaks are not explicitly penalized; inspect counts and unmatched indices.",
        ]
        output = ReferenceHSQCOutput(
            hits=hits[: query.max_results],
            query_peak_count=len(query.peaks),
            formula=query.formula,
            unavailable_references=unavailable,
            limitations=limitations,
            provenance=provenance,
        )
        return spec.output_schema.model_validate(output.model_dump())
