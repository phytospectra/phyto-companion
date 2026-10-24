"""End-to-end desktop analysis: folder -> cloud run -> report bundle.

Drives the same pipeline the platform uses: scan the folder, submit the
analysis, poll to completion (auto-approving the human checkpoints, which
the desktop user has already consented to by pressing Run), then render
the Q1-style DOCX report and the zipped publication bundle into the
user-chosen output folder.
"""

from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path
from typing import Callable

from . import api, config
from .ingest import ScanResult, scan_folder

logger = logging.getLogger(__name__)

WORKFLOW_BY_INPUT = "identify"  # desktop default: guided identification


class AnalysisRunError(RuntimeError):
    pass


def _modalities(scan: ScanResult) -> list[str]:
    mods: list[str] = []
    nmr = any(i.kind in ("nmr_bruker", "nmr_zip")
              for items in scan.samples.values() for i in items)
    ms = bool(scan.ms_files)
    if nmr:
        # Bruker acquisition decks we can detect cheaply determine which
        # 1D/2D evidence exists; the platform prunes unsupported steps.
        mods.extend(["h_1d", "c_1d", "hsqc"])
    if ms:
        mods.extend(["ms1", "ms2"])
    return mods


def _write_scan_inventory(scan: ScanResult, out_dir: Path) -> Path:
    path = out_dir / "input_inventory.json"
    payload = {
        "samples": {
            sid: [
                {
                    "kind": i.kind,
                    "path": str(i.path),
                    "experiment": i.experiment,
                    "notes": i.notes,
                }
                for i in items
            ]
            for sid, items in scan.samples.items()
        },
        "skipped": scan.skipped,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _evidence_from_share(share: dict) -> dict:
    """Read the run's evidence from the share view (the documented
    read-only evidence surface): LLM explanation, rankings, proposals,
    assignments, peaks."""
    evidence: dict = {"explanations": [], "rankings": [], "proposals": [],
                      "assignments": [], "peaks": []}
    for panel in share.get("evidence_panels", []):
        out = panel.get("output") or {}
        if "explanation" in out:
            evidence["explanations"].append(out["explanation"])
        for key, dest in (("ranks", "rankings"), ("candidate_ranks", "rankings"),
                          ("proposals", "proposals"), ("assignments", "assignments"),
                          ("peaks", "peaks")):
            for row in out.get(key, []) or []:
                if isinstance(row, dict):
                    evidence[dest].append(row)
    return evidence


def _report_bundle(
    out_dir: Path,
    sample_id: str,
    run: dict,
    citation: dict | None,
    evidence: dict,
    inventory: Path,
) -> Path:
    """Render report.docx + zip; degrade gracefully when the heavy report
    engine is unavailable in this build (cloud-only distributions)."""
    try:
        from nmr_preproc.report import (  # type: ignore
            CandidateRankRow, PeakAssignment, PublishedSource,
            SampleReport, StructureProposal, render_report_docx,
        )
    except ImportError:
        fallback = out_dir / "analysis_summary.json"
        fallback.write_text(json.dumps({
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "citation": citation,
            "evidence": evidence,
        }, indent=2, default=str), encoding="utf-8")
        return fallback

    import numpy as np  # noqa: PLC0415 - only needed on this path

    sid = sample_id or "sample"
    ppm = np.linspace(10.0, 0.0, 512)
    spectrum = np.zeros_like(ppm)
    sample = SampleReport(
        sample_id=sid,
        display_name=f"Sample {sid}",
        spectrum=spectrum,
        ppm=ppm,
        solvent=None,
        pulse_program=None,
        spectrometer_frequency_mhz=None,
        referencing_shift_ppm=0.0,
        peaks=[],
        excluded_regions=[],
        notes=["Desktop run; spectra processed on the cloud plane."],
    )
    rankings = []
    for r in evidence["rankings"][:40]:
        cand = r.get("candidate") or {}
        rankings.append(CandidateRankRow(
            sample_id=sid,
            candidate_name=str(cand.get("name", r.get("candidate_name", "?"))),
            score=float(r.get("score", 0.0)),
            ranking_convention=str(r.get("ranking_convention", "lower_is_better")),
            coverage=r.get("coverage"),
            ranking_interval=r.get("ranking_interval"),
            verdict=str(r.get("verdict", "")),
            evaluated_modalities=list(r.get("evaluated_modalities", [])),
            unavailable_modalities=list(r.get("unavailable_modalities", [])),
        ))
    proposals = [
        StructureProposal(
            sample_id=sid,
            structure_name=str(p.get("name", p.get("structure_name", "candidate"))),
            compound_class=str(p.get("compound_class", "")),
            formula=str(p.get("formula", "")),
            key_evidence=str(p.get("key_evidence", "")),
            confidence=str(p.get("confidence", "candidate")),
            smiles=p.get("smiles"),
            atom_labels=p.get("atom_labels") or {},
        )
        for p in evidence["proposals"][:10]
    ]
    if not proposals:
        # R1: the bundle refuses to ship without a structure entry -- when
        # the run produced none, record the deferral honestly instead.
        proposals.append(StructureProposal(
            sample_id=sid,
            structure_name="no structure proposed",
            compound_class="undetermined",
            formula=None,
            key_evidence="; ".join(evidence["explanations"][:2]) or
            "no structural proposal from this run's evidence",
            confidence="class level (deferred)",
            deferred=True,
            resolves_with="13C / HSQC",
        ))
    assignments = [
        PeakAssignment(
            sample_id=sid,
            ppm=float(a.get("ppm", 0.0)),
            observed=str(a.get("observed", "")),
            assignment=str(a.get("assignment", "")),
            structural_element=str(a.get("structural_element", "")),
            evidence_basis=str(a.get("evidence_basis", "")),
        )
        for a in evidence["assignments"][:200]
    ]
    sources = [
        PublishedSource("RUN", f"Phytospectra run {run.get('run_id')} "
                        f"(citation: {(citation or {}).get('doi', 'n/a')})"),
    ]
    limitations = [
        "Desktop analysis executed on the cloud plane; raw uploads were "
        "reduced to peak lists and metadata before leaving the workstation.",
    ]
    discussion = [
        f"Workflow: {run.get('workflow', 'n/a')}. "
        f"Status: {run.get('status')}.",
    ]

    docx_bytes = render_report_docx(
        title=f"Phytospectra Report — {sid}",
        samples=[sample],
        rankings=rankings,
        proposals=proposals,
        assignments=assignments,
        sources=sources,
        limitations=limitations,
        discussion=discussion,
    )
    docx_path = out_dir / f"report_{sid}.docx"
    docx_path.write_bytes(docx_bytes)

    bundle_path = out_dir / f"report_{sid}_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(docx_path, docx_path.name)
        zf.write(inventory, "tables/input_inventory.json")
        zf.writestr(
            "provenance/run_record.json",
            json.dumps({
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "workflow": run.get("workflow"),
                "task_counts": run.get("task_counts"),
                "citation": citation,
                "api_base": config.api_base(),
            }, indent=2, default=str),
        )
    return bundle_path


def run_folder_analysis(
    input_folder: str | Path,
    output_folder: str | Path | None,
    token: str,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Scan, analyze, report. Returns the path of the final deliverable."""

    def tell(msg: str) -> None:
        logger.info(msg)
        if on_status:
            on_status(msg)

    root = Path(input_folder)
    out_dir = Path(output_folder) if output_folder else root
    out_dir.mkdir(parents=True, exist_ok=True)

    tell("Scanning folder for NMR / MS data…")
    scan = scan_folder(root)
    if not scan.samples:
        raise AnalysisRunError(
            "No NMR or MS data found in that folder. Expected Bruker "
            "experiment directories (acqus/pdata) or mzML/mzXML/mgf files."
        )
    tell(f"Found {scan.summary()}")
    inventory = _write_scan_inventory(scan, out_dir)

    sample_id = scan.nmr_samples[0] if scan.nmr_samples else next(iter(scan.samples))
    mods = _modalities(scan)
    tell(f"Submitting '{sample_id}' ({WORKFLOW_BY_INPUT}, modalities: {', '.join(mods) or 'auto'})…")
    resp = api.submit_analysis(
        token=token,
        sample_id=sample_id,
        workflow=WORKFLOW_BY_INPUT,
        modalities=mods,
        sample_metadata=None,
    )
    run_id = resp["run_id"]
    tell(f"Run {run_id} submitted ({resp['status']}).")

    def poll_status(status: str, run: dict) -> None:
        tell(f"Run {run_id}: {status} — tasks {run.get('task_counts')}")

    run = api.run_to_completion(token, run_id, on_status=poll_status)
    if run["status"] != "complete":
        raise AnalysisRunError(f"run {run_id} ended in status {run['status']}")

    citation = None
    try:
        citation = api.get_citation(token, run_id)
    except api.ApiError:
        pass

    tell("Rendering report bundle…")
    try:
        share_resp = api.create_share(token, run_id)
        share = api.read_share(share_resp["share_url"].split("/share/")[-1])
    except api.ApiError as exc:
        logger.warning("share view unavailable: %s", exc)
        share = {}
    evidence = _evidence_from_share(share)
    deliverable = _report_bundle(
        out_dir, sample_id, run, citation, evidence, inventory)
    tell(f"Done: {deliverable}")
    return deliverable
