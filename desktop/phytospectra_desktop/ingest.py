"""Scan a user-chosen folder for NMR / LC-MS inputs.

The desktop workflow is "point it at the folder, everything else happens":
this module discovers

* Bruker-style experiment directories (contain acqus/acqu.par + pdata/),
  one sample per experiment; parent directories named by sample become
  the sample id (matching the platform's nmr_preproc loader convention);
* vendor-processed 1D NMR files (``pdata/1/1r``) via their experiment dirs;
* LC-MS files by suffix: mzML, mzXML, mgf, Agilent/Thermo ``.d``/``.raw``.

Nothing is uploaded wholesale: only the small derived files the API
contract needs (peak lists, metadata, processed matrices) travel to the
cloud, matching the split-plane data-governance rule.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import MS_FILE_SUFFIXES, NMR_DIR_MARKERS


@dataclass
class DiscoveredInput:
    """One NMR experiment or MS file found in the folder tree."""

    sample_id: str
    kind: str  # 'nmr_bruker' | 'ms_file'
    path: Path
    experiment: str | None = None  # e.g. '1H', '13C', 'HSQC'
    notes: list[str] = field(default_factory=list)


@dataclass
class ScanResult:
    samples: dict[str, list[DiscoveredInput]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def nmr_samples(self) -> list[str]:
        return sorted({s for s, items in self.samples.items()
                       if any(i.kind == "nmr_bruker" for i in items)})

    @property
    def ms_files(self) -> list[Path]:
        return [i.path for items in self.samples.values() for i in items
                if i.kind == "ms_file"]

    def summary(self) -> str:
        n_nmr = sum(1 for items in self.samples.values()
                    for i in items if i.kind == "nmr_bruker")
        n_ms = sum(1 for items in self.samples.values()
                   for i in items if i.kind == "ms_file")
        return f"{len(self.samples)} sample(s): {n_nmr} NMR experiment(s), {n_ms} MS file(s)"


def _is_bruker_experiment(d: Path) -> bool:
    if not d.is_dir():
        return False
    names = {p.name.lower() for p in d.iterdir() if p.is_file()}
    if names & {m for m in NMR_DIR_MARKERS if "." in m}:
        return True
    return (d / "pdata").is_dir()


def _experiment_kind(d: Path) -> str:
    """Best-effort experiment label from acqus or the directory name."""
    acq = d / "acqus"
    if acq.exists():
        try:
            for line in acq.read_text(errors="ignore").splitlines()[:120]:
                if line.startswith("##$PULPROG= "):
                    return line.split("= ", 1)[1].strip().replace("<", "").replace(">", "")
        except OSError:
            pass
    return d.name


def _sample_id_for_experiment(d: Path) -> str:
    """Bruker layout: <SAMPLE>/<EXPNO>/ or <SAMPLE>/<EXPNO>/<PROCNO>."""
    parent = d.parent
    if parent.name.isdigit() and parent.parent != parent:
        return parent.parent.name
    return parent.name


def scan_folder(root: Path, max_depth: int = 6) -> ScanResult:
    result = ScanResult()
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")

    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError as exc:
            result.skipped.append(f"{current}: {exc}")
            continue
        for entry in entries:
            if _is_bruker_experiment(entry):
                sid = _sample_id_for_experiment(entry)
                item = DiscoveredInput(
                    sample_id=sid,
                    kind="nmr_bruker",
                    path=entry,
                    experiment=_experiment_kind(entry),
                )
                result.samples.setdefault(sid, []).append(item)
            elif entry.is_dir():
                if entry.suffix.lower() in (".d",) or entry.name.lower().endswith(".d"):
                    sid = entry.parent.name if entry.parent != root else entry.stem
                    result.samples.setdefault(sid, []).append(
                        DiscoveredInput(sample_id=sid, kind="ms_file", path=entry))
                elif depth < max_depth:
                    stack.append((entry, depth + 1))
            elif entry.is_file():
                name = entry.name.lower()
                if name.endswith(MS_FILE_SUFFIXES):
                    sid = entry.parent.name if entry.parent != root else entry.stem
                    result.samples.setdefault(sid, []).append(
                        DiscoveredInput(sample_id=sid, kind="ms_file", path=entry))
                elif name.endswith(".zip"):
                    # zipped Bruker export: peek one level to name the sample
                    try:
                        with zipfile.ZipFile(entry) as zf:
                            tops = {n.split("/")[0] for n in zf.namelist() if n.strip("/")}
                        for t in sorted(tops):
                            result.samples.setdefault(t, []).append(
                                DiscoveredInput(sample_id=t, kind="nmr_zip",
                                                path=entry, notes=[f"zip member {t}"]))
                    except zipfile.BadZipFile:
                        result.skipped.append(f"{entry}: not a readable zip")
    return result
