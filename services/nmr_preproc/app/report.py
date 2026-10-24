"""Publication-bundle export for NMR analyses (ADR-0070, case-study driven).

Companion to ``pipeline.py``: takes the artifacts a Phytospectra NMR run
produces -- processed spectra, picked peak lists, candidate rankings,
provenance manifest, published-source notes -- and writes them as ONE
zipped bundle a chemist can drop into a manuscript repository or send to
a collaborator.

Q1-report rules (enforced, not stylistic suggestions) -- a report that
violates any rule is refused, because these rules are what make the
output publishable rather than a debug dump:

    R1  Every sample carries a StructureProposal OR an explicit deferral
        entry (class-level assignment + the experiment that would resolve
        it).  A sample is never silently left without a proposal.
    R2  Every proposed structure is backed by >= 3 PeakAssignments, and
        every PeakAssignment states its evidence basis (observed signal ->
        structural element -> published/derived support).  Assignment
        tables are evidence-based, not asserted.
    R3  Figures are annotated: each assignment that maps onto a spectrum
        region is labelled on the figure, so a reader sees which part of
        the spectrum supports which structural element.
    R4  The Discussion cites published sources; every citation must be
        resolvable (authors, year, journal/pages or DOI/database id).
    R5  A Limitations section is mandatory and non-empty.
    R6  Tone: findings are stated constructively at their evidence level
        ("consistent with", "supports", "assigned on the basis of");
        capability gaps are phrased as next experiments, not refusals.

Bundle layout:

    report.docx                     Q1-style report (proposed-structures
                                    table, annotated figures, assignment
                                    tables, discussion, limitations,
                                    references)
    figures/spectrum_<id>.png       annotated figure per sample (also
                                    embedded in report.docx)
    tables/experiment_inventory.csv identity, pulse, solvent, referencing
    tables/peak_lists.csv           all picked peaks (ppm, rel. prominence)
    tables/peak_assignments.csv     assignment -> evidence basis mapping
    tables/candidate_rankings.csv   every ranked candidate + verdicts
    provenance/manifest.json        tool + model versions, parameters
    provenance/execution_log.txt    what ran, in order, with timestamps
    provenance/checksums.json       SHA256 of every file (except itself)
    README.txt                      plain-language inventory + caveats
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

__all__ = [
    "ReportError",
    "SampleReport",
    "CandidateRankRow",
    "StructureProposal",
    "PeakAssignment",
    "PublishedSource",
    "REPORT_VOICE_RULES",
    "_layout_label_levels",
    "render_report_docx",
    "export_bundle",
]


class ReportError(Exception):
    """A Q1-report rule was violated or a component is missing."""


REPORT_VOICE_RULES = [
    "R1 every sample carries a structure proposal or an explicit deferral",
    "R2 assignments are evidence-based: >=3 per proposed structure",
    "R3 figures are annotated with the assignment labels",
    "R4 discussion citations are resolvable published sources",
    "R5 limitations section is mandatory",
    "R6 constructive tone at the stated evidence level",
]


@dataclass
class CandidateRankRow:
    """One row of a candidate ranking, as persisted by the ranker."""

    sample_id: str
    candidate_name: str
    score: float
    ranking_convention: str  # 'lower_is_better' | 'higher_is_better'
    coverage: float | None
    ranking_interval: tuple[int, int] | None
    verdict: str
    evaluated_modalities: list[str] = field(default_factory=list)
    unavailable_modalities: list[str] = field(default_factory=list)


@dataclass
class StructureProposal:
    """A proposed (or deferred) structure for one sample.

    ``confidence`` states the evidence level constructively, e.g.
    "structure level (multi-spectral match)" or
    "class level (1H only; 13C/HSQC will resolve)".

    ``smiles`` + ``atom_labels`` enable the Mnova-NMRPredict-style panel:
    the structure is drawn with positional labels (e.g. "H-11a", "OCH2O")
    at the labelled atoms, so the structure, the annotated spectrum and
    the assignment table share one numbering (R3).
    """

    sample_id: str
    structure_name: str
    compound_class: str
    formula: str | None
    key_evidence: str
    confidence: str
    deferred: bool = False  # True = class-level deferral entry (R1)
    resolves_with: str | None = None  # required experiment when deferred
    smiles: str | None = None
    atom_labels: dict[int, str] = field(default_factory=dict)


@dataclass
class PeakAssignment:
    """One evidence-based assignment: observed signal -> structural element.

    ``observed`` describes the measured signal (ppm, multiplicity, J),
    ``assignment`` the positional label, ``structural_element`` the
    fragment it evidences, and ``evidence_basis`` why (published shift,
    coupling pattern, integral, or correlation).  R2.
    """

    sample_id: str
    ppm: float
    observed: str
    assignment: str
    structural_element: str
    evidence_basis: str
    annotation_x: float | None = None  # ppm to point the figure label at


@dataclass
class PublishedSource:
    """A resolvable published source used in the discussion (R4)."""

    key: str
    citation: str


@dataclass
class CorrelationEvidence:
    """One 2D cross-peak tied to assignments (COSY H-H / HMBC H-C)."""

    sample_id: str
    kind: str  # 'COSY' | 'HMBC'
    f2_ppm: float
    f1_ppm: float
    interpretation: str  # e.g. 'H-11a <-> H-6a'


@dataclass
class TwoDSpectrum:
    """A processed 2D experiment rendered into the bundle."""

    sample_id: str
    kind: str
    matrix: np.ndarray
    f2_ppm: np.ndarray
    f1_ppm: np.ndarray
    title: str
    correlations: list[CorrelationEvidence] = field(default_factory=list)


@dataclass
class PharmacologyRow:
    """One predicted (or reported) compound-target association."""

    compound: str
    target: str
    method: str  # 'similarity' | 'reported' | ...
    confidence: float
    confidence_basis: str
    citation: str | None = None


@dataclass
class DereplicationResult:
    """Outcome of the Tier-1 home-library match for one sample."""

    sample_id: str
    best_match: str
    citation: str
    coverage: float
    mean_abs_deviation: float
    verdict: str
    per_peak: list[tuple[str, float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# NMReDATA + JSON export
# ---------------------------------------------------------------------------

def assignments_to_json(
    sample: SampleReport,
    assignments: list[PeakAssignment],
    proposal: StructureProposal | None,
    correlations: list[CorrelationEvidence] | None = None,
) -> str:
    """Machine-readable assignment record (web-native JSON schema)."""
    import json as _json

    payload = {
        "molecule": proposal.smiles if proposal and proposal.smiles else None,
        "proposed_structure": proposal.structure_name if proposal else None,
        "sample_id": sample.sample_id,
        "nucleus": "13C" if "13" in (sample.pulse_program or "") else "1H",
        "frequency_mhz": sample.spectrometer_frequency_mhz,
        "solvent": sample.solvent,
        "assignments": [
            {
                "atomId": a.assignment,
                "shift": a.ppm,
                "observed": a.observed,
                "structural_element": a.structural_element,
                "evidence_basis": a.evidence_basis,
            }
            for a in assignments if a.sample_id == sample.sample_id
        ],
        "correlations": [
            {"kind": c.kind, "F2": c.f2_ppm, "F1": c.f1_ppm,
             "interpretation": c.interpretation}
            for c in (correlations or []) if c.sample_id == sample.sample_id
        ],
    }
    return _json.dumps(payload, indent=2)


def assignments_to_nmredata(
    sample: SampleReport,
    assignments: list[PeakAssignment],
    proposal: StructureProposal | None,
) -> str:
    """NMReDATA-compatible SDF text (V2000 molblock + NMREDATA_* tags)."""
    from rdkit import Chem

    if not (proposal and proposal.smiles):
        raise ReportError(
            f"NMReDATA export for {sample.sample_id} requires a structure "
            "proposal with SMILES"
        )
    mol = Chem.MolFromSmiles(proposal.smiles)
    if mol is None:
        raise ReportError(f"cannot parse proposal SMILES for {sample.sample_id}")
    block = Chem.MolToMolBlock(mol)

    freq = f"{sample.spectrometer_frequency_mhz:.0f} MHz" if sample.spectrometer_frequency_mhz else ""
    acs = format_nmr_text_acs(sample, assignments)
    lines = [block.rstrip("\n"), ">  <NMREDATA_VERSION>", "1.1",
             ">  <NMREDATA_SMILES>", proposal.smiles]
    if proposal.atom_labels:
        label_map = "; ".join(f"idx{idx}={label}" for idx, label in sorted(proposal.atom_labels.items()))
        lines += [">  <NMREDATA_ASSIGNMENT>", label_map]
    lines += [
        ">  <NMREDATA_1D_1H>",
        f"Larmor={freq}",
        f"Solvent={sample.solvent or 'unknown'}",
        f"ACS={acs}",
        "",
        "$$$$",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class SampleReport:
    """Everything the bundle needs about one processed sample.

    ``regions`` are diagnostic zoom windows (lo, hi, title): each becomes
    an extra focused panel under the full sweep so crowded spectra (e.g.
    a 220 ppm 13C sweep) stay readable -- the manuscript expansion-panel
    convention.  ``predicted_smiles`` enables the predict-vs-experimental
    mirror overlay figure.
    """

    sample_id: str
    display_name: str
    spectrum: np.ndarray  # processed real spectrum
    ppm: np.ndarray  # matching ppm axis (descending)
    solvent: str | None
    pulse_program: str | None
    spectrometer_frequency_mhz: float | None
    referencing_shift_ppm: float
    peaks: list[tuple[float, float]]  # (ppm, relative prominence)
    excluded_regions: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    regions: list[tuple[float, float, str]] = field(default_factory=list)
    predicted_smiles: str | None = None


# ---------------------------------------------------------------------------
# Q1 rule enforcement
# ---------------------------------------------------------------------------

def _check_q1_rules(
    samples: list[SampleReport],
    proposals: list[StructureProposal],
    assignments: list[PeakAssignment],
    sources: list[PublishedSource],
    limitations: list[str],
) -> None:
    if not samples:
        raise ReportError("no samples: refusing to write an empty bundle")
    sample_ids = {s.sample_id for s in samples}
    # R1
    proposed_ids = {p.sample_id for p in proposals}
    missing = sample_ids - proposed_ids
    if missing:
        raise ReportError(
            f"R1 violated: no StructureProposal for sample(s) {sorted(missing)}; "
            "add a proposal or an explicit deferral entry"
        )
    for p in proposals:
        if p.deferred and not p.resolves_with:
            raise ReportError(
                f"R1 violated: deferral for {p.sample_id} must state the "
                "experiment that resolves it (resolves_with)"
            )
    # R2
    for p in proposals:
        if p.deferred:
            continue
        backed = [a for a in assignments if a.sample_id == p.sample_id]
        if len(backed) < 3:
            raise ReportError(
                f"R2 violated: proposal '{p.structure_name}' for "
                f"{p.sample_id} has {len(backed)} assignments (< 3); "
                "assignments must be evidence-based"
            )
    # R4
    for src in sources:
        if len(src.citation) < 15 or not any(
            ch.isdigit() for ch in src.citation
        ):
            raise ReportError(
                f"R4 violated: citation for '{src.key}' is not resolvable: "
                f"{src.citation!r}"
            )
    # R5
    if not limitations:
        raise ReportError("R5 violated: limitations section is empty")


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

def _structure_figure(proposal: StructureProposal) -> bytes | None:
    """Draw the proposed structure with positional labels (Mnova-style).

    Labels are applied via RDKit atom map numbers, so the drawing, the
    annotated spectrum and the assignment table share one numbering.
    Returns None when the proposal carries no structure (mixture or
    class-level without a drawable scaffold).
    """
    if not proposal.smiles:
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem  # noqa: F401 - used as Chem.AllChem below
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError as exc:  # pragma: no cover - dependency declared
        raise ReportError("rdkit is not installed; cannot draw structures") from exc

    mol = Chem.MolFromSmiles(proposal.smiles)
    if mol is None:
        raise ReportError(
            f"cannot parse SMILES for proposal '{proposal.structure_name}': "
            f"{proposal.smiles!r}"
        )
    n_atoms = mol.GetNumAtoms()
    for idx, label in (proposal.atom_labels or {}).items():
        if not 0 <= idx < n_atoms:
            raise ReportError(
                f"atom label index {idx} out of range (0..{n_atoms - 1}) "
                f"for proposal '{proposal.structure_name}'"
            )
        # 'atomLabel' (not molAtomMapNumber): renders the text next to the
        # atom in every RDKit build we test against
        mol.GetAtomWithIdx(idx).SetProp("atomLabel", label)
    Chem.AllChem.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DCairo(700, 420)
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def _layout_label_levels(
    positions: list[tuple[float, float]],
) -> list[int]:
    """Assign each label a vertical level so no two labels on the same
    level overlap horizontally (Mnova-style stacking).

    ``positions`` are ``(x_center, half_width)`` in data (ppm) units.
    Greedy left-to-right: the first level whose last label ends before
    this label starts wins; labels are assumed sorted or handled by
    checking ALL occupants of a level, not just the last.  Pure and
    deterministic; exported for tests.
    """
    levels: list[int] = []
    occupied: list[list[tuple[float, float]]] = []  # level -> intervals
    for x_center, half_width in positions:
        lo, hi = x_center - half_width, x_center + half_width
        placed = False
        for lvl in range(len(occupied)):
            if all(hi < iv_lo or lo > iv_hi for iv_lo, iv_hi in occupied[lvl]):
                occupied[lvl].append((lo, hi))
                levels.append(lvl)
                placed = True
                break
        if not placed:
            occupied.append([(lo, hi)])
            levels.append(len(occupied) - 1)
    return levels


def _annotate_panel(
    ax, sample: SampleReport, assignments: list[PeakAssignment],
    x_lo: float, x_hi: float, panel_ppm_per_in: float,
) -> None:
    """Draw Mnova-style stacked labels on one axes restricted to [x_lo, x_hi]."""
    y = sample.spectrum / max(np.percentile(np.abs(sample.spectrum), 99.9), 1e-12)
    ax.plot(sample.ppm, np.clip(y, -0.15, 1.0), lw=0.5, color="#1a4d8f")
    ax.set_xlim(x_lo, x_hi)
    ax.set_ylim(-0.2, 1.0)

    labels = [
        a for a in assignments
        if a.annotation_x is not None and x_lo <= a.annotation_x <= x_hi
    ]
    if not labels:
        return
    x_span = abs(x_hi - x_lo)
    win = max(x_span * 0.01, 1e-6)

    def apex_y(x: float) -> float:
        m = np.abs(sample.ppm - x) <= win
        return float(np.max(np.clip(y[m], -0.15, 1.0))) if np.any(m) else 0.0

    texts = [
        f"{a.assignment} {a.ppm:.2f}" if a.ppm is not None else a.assignment
        for a in labels
    ]
    half_widths = [
        0.55 * len(t) * 7.0 / 72.0 * panel_ppm_per_in for t in texts
    ]
    order = sorted(range(len(labels)), key=lambda i: labels[i].annotation_x)
    positions = [(labels[i].annotation_x, half_widths[i]) for i in order]
    levels_sorted = _layout_label_levels(positions)
    level_of = dict(zip(order, levels_sorted))

    row = 0.16  # data units between label rows
    for i, a in enumerate(labels):
        lvl = level_of[i]
        y_text = 1.02 + lvl * row
        half = half_widths[i]
        x_draw = min(max(a.annotation_x, x_lo + half), x_hi - half)
        ax.annotate(
            texts[i],
            xy=(a.annotation_x, apex_y(a.annotation_x) + 0.02),
            xytext=(x_draw, y_text),
            fontsize=7, ha="center", va="bottom", color="#7a1f1f",
            arrowprops=dict(arrowstyle="-", lw=0.5, color="#7a1f1f",
                            alpha=0.55, shrinkA=1, shrinkB=0),
        )
    ax.set_ylim(-0.2, 1.02 + (max(level_of.values()) + 1) * row)


def _annotated_figure(
    sample: SampleReport, assignments: list[PeakAssignment]
) -> bytes:
    """Full sweep + diagnostic zoom panels, Mnova-style labels (R3).

    Each entry of ``sample.regions`` becomes a focused expansion panel so
    crowded regions of a wide sweep (e.g. the 100-110 ppm cluster of a
    220 ppm 13C spectrum) get their own readable x-axis instead of
    overlapping labels.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x0, x1 = float(sample.ppm[0]), float(sample.ppm[-1])
    full_lo, full_hi = max(x0, x1), min(x0, x1)
    panels = [(full_lo, full_hi, sample.display_name)] + [
        (r[0], r[1], r[2]) for r in sample.regions
    ]
    fig, axes = plt.subplots(
        len(panels), 1, figsize=(9, 2.4 * len(panels) + 0.8),
        squeeze=False,
    )
    usable_in = fig.get_size_inches()[0] * 0.80
    for ax, (lo, hi, title) in zip(axes[:, 0], panels):
        panel_ppm_per_in = abs(hi - lo) / usable_in
        _annotate_panel(ax, sample, assignments, lo, hi, panel_ppm_per_in)
        ax.set_title(title, fontsize=9, loc="left")
        ax.set_xlabel("δ (ppm)")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _overlay_figure(
    sample: SampleReport, predicted_smiles: str, model_id: str
) -> bytes:
    """Predict-vs-experimental mirror view: experimental (black, top) over
    the inverted rule-based prediction (red, bottom) on a shared ppm axis."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .predict import NMRPredictionEngine

    engine = NMRPredictionEngine(model_id=model_id)
    preds = engine.predict_shifts(predicted_smiles)
    x0, x1 = float(sample.ppm[0]), float(sample.ppm[-1])
    lo, hi = min(x0, x1), max(x0, x1)
    pred_ppm, pred_y = engine.simulate_spectrum(
        preds, ppm_start=hi, ppm_end=lo, points=6000
    )
    y = sample.spectrum / max(np.percentile(np.abs(sample.spectrum), 99.9), 1e-12)

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(9, 4.2), sharex=True,
        gridspec_kw={"height_ratios": [1, 1]},
    )
    ax_top.plot(sample.ppm, np.clip(y, -0.15, 1.1), lw=0.5, color="black")
    ax_top.set_ylim(-0.2, 1.15)
    ax_top.set_ylabel("experimental")
    ax_bot.plot(pred_ppm, -pred_y, lw=0.7, color="#b22222")
    ax_bot.set_ylim(-1.15, 0.2)
    ax_bot.set_ylabel("predicted\n(inverted)")
    ax_bot.set_xlabel("δ (ppm)")
    ax_bot.set_xlim(x0, x1)
    ax_top.set_title(
        f"{sample.display_name} -- overlay with rule-based prediction "
        f"({model_id})",
        fontsize=9, loc="left",
    )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def format_nmr_text_acs(
    sample: SampleReport, assignments: list[PeakAssignment]
) -> str:
    """ACS/RSC-style 1H NMR text line from the evidence-based assignments.

    Example::

        1H NMR (500 MHz, CDCl3) delta 7.35 (d, J 8.4 Hz, H-1), ...
            6.55 (dd, J 8.4/2.4 Hz, H-2), ...
    """
    nucleus = "13C" if sample.pulse_program and "13" in (sample.pulse_program or "") else "1H"
    freq = (
        f"{sample.spectrometer_frequency_mhz:.0f} MHz"
        if sample.spectrometer_frequency_mhz else ""
    )
    solvent = sample.solvent or ""
    parts = [
        f"{a.ppm:.2f} ({a.observed}, {a.assignment})"
        for a in sorted(
            (a for a in assignments if a.sample_id == sample.sample_id),
            key=lambda a: -a.ppm,
        )
    ]
    head = f"{nucleus} NMR ({freq}, {solvent})".replace(", )", ")")
    body = ", ".join(parts) if parts else "no assignments recorded"
    return f"{head} δ {body}."


# ---------------------------------------------------------------------------
# docx rendering
# ---------------------------------------------------------------------------

def _add_table(document, headers: list[str], rows: list[list[str]], style="Light Grid Accent 1"):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = style
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for r in rows:
        cells = table.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = str(v)
    return table


def render_report_docx(
    title: str,
    samples: list[SampleReport],
    rankings: list[CandidateRankRow],
    proposals: list[StructureProposal],
    assignments: list[PeakAssignment],
    sources: list[PublishedSource],
    limitations: list[str],
    discussion: list[str],
    twod: list[TwoDSpectrum] | None = None,
    dereplications: list[DereplicationResult] | None = None,
    pharmacology: list[PharmacologyRow] | None = None,
) -> bytes:
    """Render the Q1-style report as .docx bytes (python-docx)."""
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover - dependency declared
        raise ReportError("python-docx is not installed") from exc

    _check_q1_rules(samples, proposals, assignments, sources, limitations)

    document = Document()
    document.add_heading(title, level=0)
    document.add_paragraph(
        f"Generated by Phytospectra (phyto-companion) on "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}. Spectra were "
        "processed through the platform's deterministic NMR chain "
        "(baseline correction, solvent referencing, threshold-recorded "
        "peak picking); assignments link observed signals to structural "
        "elements with their evidence basis, and the discussion anchors "
        "the interpretation to published sources."
    )

    # Table 1: proposed structures
    document.add_heading("Proposed structures", level=1)
    _add_table(
        document,
        ["Sample", "Proposed structure", "Class", "Formula",
         "Key evidence", "Confidence"],
        [
            [p.sample_id, p.structure_name, p.compound_class,
             p.formula or "-", p.key_evidence, p.confidence]
            for p in proposals
        ],
    )
    for p in proposals:
        if p.deferred:
            document.add_paragraph(
                f"{p.sample_id}: structure deferred at class level; "
                f"resolved by {p.resolves_with}."
            )

    # per-sample sections: structure panel, annotated figure, tables
    fig_no = 0
    for s in samples:
        fig_no += 1
        document.add_heading(f"Sample {s.display_name}", level=1)
        document.add_paragraph(
            f"Referencing shift {s.referencing_shift_ppm:+.3f} ppm; "
            f"{len(s.peaks)} analyte peaks; {len(s.excluded_regions)} "
            "excluded solvent/contaminant regions (tables/peak_lists.csv)."
        )
        for prop in proposals:
            if prop.sample_id != s.sample_id:
                continue
            png = _structure_figure(prop)
            if png:
                document.add_picture(io.BytesIO(png), width=Pt(300))
                document.add_paragraph(
                    f"Proposed structure: {prop.structure_name} "
                    f"({prop.compound_class}). Positional labels correspond "
                    "to the spectrum annotations and the assignment table "
                    "below."
                ).runs[0].font.size = Pt(9)
        png = _annotated_figure(s, assignments)
        document.add_picture(io.BytesIO(png), width=Pt(430))
        document.add_paragraph(
            f"Figure {fig_no}. {s.display_name}: full sweep with Mnova-style "
            "assignment labels"
            + (f" plus {len(s.regions)} diagnostic expansion panel(s)"
               if s.regions else "")
            + "; labels correspond to the assignment table below."
        ).runs[0].font.size = Pt(9)
        if s.predicted_smiles:
            model_id = "increment-rules-0.1.0"
            try:
                overlay = _overlay_figure(s, s.predicted_smiles, model_id)
                document.add_picture(io.BytesIO(overlay), width=Pt(430))
                document.add_paragraph(
                    f"Figure {fig_no}b. Predict-vs-experimental mirror "
                    f"overlay for the proposed structure (rule-based "
                    f"prediction, {model_id}); the deterministic Tier-1 "
                    "predictor orders environments but is not shift-accurate "
                    "-- see Limitations."
                ).runs[0].font.size = Pt(9)
            except (ReportError, ValueError):
                document.add_paragraph(
                    "Predict-vs-experimental overlay unavailable for this "
                    "sample (structure could not be predicted locally)."
                )
        acs_line = format_nmr_text_acs(s, assignments)
        document.add_paragraph("Publication-format spectral data:").runs[
            0
        ].font.size = Pt(9)
        p = document.add_paragraph(acs_line)
        p.runs[0].font.size = Pt(9)

        # 2D correlation panels + evidence table
        twod_spectra = [t for t in (twod or []) if t.sample_id == s.sample_id]
        for t in twod_spectra:
            from .twod import contour_figure

            markers = [(c.f2_ppm, c.f1_ppm, c.interpretation)
                       for c in t.correlations]
            document.add_picture(
                io.BytesIO(contour_figure(t, markers)), width=Pt(330)
            )
            document.add_paragraph(
                f"{t.kind} contour with matched correlations marked (x)."
            ).runs[0].font.size = Pt(9)
            if t.correlations:
                _add_table(
                    document,
                    ["F2 (ppm)", "F1 (ppm)", "Correlation"],
                    [[f"{c.f2_ppm:.2f}", f"{c.f1_ppm:.2f}", c.interpretation]
                     for c in t.correlations],
                )

        rows = [
            [f"{a.ppm:.2f}" if a.ppm is not None else "-",
             a.observed, a.assignment, a.structural_element, a.evidence_basis]
            for a in assignments if a.sample_id == s.sample_id
        ]
        if rows:
            document.add_paragraph(f"Table {fig_no + 1}. Assignment evidence.")
            _add_table(
                document,
                ["δ (ppm)", "Observed", "Assignment", "Structural element",
                 "Evidence basis"],
                rows,
            )

        rank_rows = [r for r in rankings if r.sample_id == s.sample_id]
        if rank_rows:
            document.add_paragraph("Candidate ranking (blind, supplied set):")
            _add_table(
                document,
                ["Candidate", "Score", "Convention", "Interval", "Verdict"],
                [
                    [r.candidate_name, f"{r.score:.4f}",
                     r.ranking_convention,
                     f"{r.ranking_interval[0]}–{r.ranking_interval[1]}"
                     if r.ranking_interval else "-",
                     r.verdict]
                    for r in rank_rows
                ],
            )

    # Tier-1 home-library dereplication section
    if dereplications:
        document.add_heading("Home-library dereplication (Tier 1)", level=1)
        document.add_paragraph(
            "Observed peaks were matched against the laboratory's curated "
            "reference shift tables; every library row carries its "
            "citation.  Level 1 is only ever stamped pending human "
            "sign-off."
        )
        _add_table(
            document,
            ["Sample", "Best match", "Coverage", "Mean |delta| (ppm)", "Verdict"],
            [[d.sample_id, f"{d.best_match} [{d.citation}]",
              f"{d.coverage:.0%}",
              f"{d.mean_abs_deviation:.3f}" if d.mean_abs_deviation != float("inf") else "-",
              d.verdict] for d in dereplications],
        )

    # Pharmacology section
    if pharmacology:
        document.add_heading("Pharmacology (predicted)", level=1)
        document.add_paragraph(
            "Tanimoto-based ligand matching against the platform's "
            "ligand knowledge base (local plane: curated entries with "
            "citations; cloud plane: ChEMBL).  ADMET: no model deployed "
            "-- reported as unavailable, never guessed."
        )
        _add_table(
            document,
            ["Compound", "Target", "Method", "Confidence", "Basis", "Citation"],
            [[p.compound, p.target, p.method, f"{p.confidence:.2f}",
              p.confidence_basis, p.citation or "-"] for p in pharmacology],
        )

    # discussion
    document.add_heading("Discussion", level=1)
    for para in discussion:
        document.add_paragraph(para)

    # limitations
    document.add_heading("Limitations", level=1)
    for lim in limitations:
        document.add_paragraph(lim, style="List Bullet")

    # references
    document.add_heading("References", level=1)
    for src in sources:
        document.add_paragraph(
            f"[{src.key}] {src.citation}"
        ).runs[0].font.size = Pt(9)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# bundle export
# ---------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def export_bundle(
    path: str | Path,
    *,
    title: str,
    samples: list[SampleReport],
    rankings: list[CandidateRankRow],
    proposals: list[StructureProposal],
    assignments: list[PeakAssignment],
    sources: list[PublishedSource],
    limitations: list[str],
    discussion: list[str],
    provenance: dict,
    execution_log: list[str],
    caveats: list[str],
    twod: list[TwoDSpectrum] | None = None,
    dereplications: list[DereplicationResult] | None = None,
    pharmacology: list[PharmacologyRow] | None = None,
) -> Path:
    """Write the complete zipped publication bundle; fail loud on gaps.

    Enforces the Q1 report rules (R1-R6) before anything is written, so
    a refused bundle leaves no partial file behind.  Every payload file
    is checksummed in ``provenance/checksums.json``.
    """
    _check_q1_rules(samples, proposals, assignments, sources, limitations)
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    checksums: dict[str, str] = {}

    def _add(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
        zf.writestr(name, data)
        checksums[name] = _sha256_bytes(data)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        inv = io.StringIO()
        w = csv.writer(inv)
        w.writerow(
            ["sample_id", "display_name", "pulse", "solvent",
             "frequency_mhz", "referencing_shift_ppm", "n_peaks",
             "excluded_regions"]
        )
        for s in samples:
            w.writerow(
                [s.sample_id, s.display_name, s.pulse_program or "",
                 s.solvent or "",
                 f"{s.spectrometer_frequency_mhz:.4f}" if s.spectrometer_frequency_mhz else "",
                 f"{s.referencing_shift_ppm:+.4f}", len(s.peaks),
                 ";".join(f"{a}-{b}" for a, b in s.excluded_regions)]
            )
        _add(zf, "tables/experiment_inventory.csv", inv.getvalue().encode())

        pk = io.StringIO()
        w = csv.writer(pk)
        w.writerow(["sample_id", "ppm", "relative_prominence"])
        for s in samples:
            for ppm, prom in s.peaks:
                w.writerow([s.sample_id, f"{ppm:.3f}", f"{prom:.4f}"])
        _add(zf, "tables/peak_lists.csv", pk.getvalue().encode())

        asg = io.StringIO()
        w = csv.writer(asg)
        w.writerow(
            ["sample_id", "ppm", "observed", "assignment",
             "structural_element", "evidence_basis"]
        )
        for a in assignments:
            w.writerow(
                [a.sample_id, f"{a.ppm:.3f}" if a.ppm is not None else "",
                 a.observed, a.assignment, a.structural_element,
                 a.evidence_basis]
            )
        _add(zf, "tables/peak_assignments.csv", asg.getvalue().encode())

        rk = io.StringIO()
        w = csv.writer(rk)
        w.writerow(
            ["sample_id", "candidate", "score", "convention", "coverage",
             "ranking_interval", "verdict", "evaluated", "unavailable"]
        )
        for r in rankings:
            w.writerow(
                [r.sample_id, r.candidate_name, f"{r.score:.6f}",
                 r.ranking_convention,
                 f"{r.coverage:.3f}" if r.coverage is not None else "",
                 f"{r.ranking_interval[0]}-{r.ranking_interval[1]}"
                 if r.ranking_interval else "",
                 r.verdict,
                 ";".join(r.evaluated_modalities),
                 ";".join(r.unavailable_modalities)]
            )
        _add(zf, "tables/candidate_rankings.csv", rk.getvalue().encode())

        for s in samples:
            _add(zf, f"figures/spectrum_{s.sample_id}.png",
                 _annotated_figure(s, assignments))
            if s.predicted_smiles:
                try:
                    _add(zf, f"figures/overlay_{s.sample_id}.png",
                         _overlay_figure(
                             s, s.predicted_smiles, "increment-rules-0.1.0"
                         ))
                except (ReportError, ValueError):
                    pass  # overlay recorded as absent, never fatal
            _add(zf, f"tables/nmr_text_{s.sample_id}.txt",
                 (format_nmr_text_acs(s, assignments) + "\n").encode())
        for prop in proposals:
            png = _structure_figure(prop)
            if png:
                _add(zf, f"figures/structure_{prop.sample_id}.png", png)

        _add(
            zf, "report.docx",
            render_report_docx(
                title, samples, rankings, proposals, assignments,
                sources, limitations, discussion,
                twod=twod, dereplications=dereplications,
                pharmacology=pharmacology,
            ),
        )

        # 2D spectra, correlation tables, dereplication, pharmacology
        from .twod import contour_figure

        for t in twod or []:
            markers = [(c.f2_ppm, c.f1_ppm, c.interpretation)
                       for c in t.correlations]
            _add(zf, f"figures/spectrum2d_{t.sample_id}_{t.kind}.png",
                 contour_figure(t, markers))
        if twod:
            co = io.StringIO()
            w = csv.writer(co)
            w.writerow(["sample_id", "kind", "f2_ppm", "f1_ppm", "interpretation"])
            for t in twod:
                for c in t.correlations:
                    w.writerow([t.sample_id, c.kind, f"{c.f2_ppm:.3f}",
                                f"{c.f1_ppm:.3f}", c.interpretation])
            _add(zf, "tables/correlations.csv", co.getvalue().encode())
        if dereplications:
            dp = io.StringIO()
            w = csv.writer(dp)
            w.writerow(["sample_id", "best_match", "citation", "coverage",
                        "mean_abs_deviation_ppm", "verdict"])
            for d in dereplications:
                w.writerow([d.sample_id, d.best_match, d.citation,
                            f"{d.coverage:.3f}",
                            f"{d.mean_abs_deviation:.4f}"
                            if d.mean_abs_deviation != float("inf") else "",
                            d.verdict])
            _add(zf, "tables/dereplication.csv", dp.getvalue().encode())
        if pharmacology:
            ph = io.StringIO()
            w = csv.writer(ph)
            w.writerow(["compound", "target", "method", "confidence",
                        "confidence_basis", "citation"])
            for p in pharmacology:
                w.writerow([p.compound, p.target, p.method,
                            f"{p.confidence:.3f}", p.confidence_basis,
                            p.citation or ""])
            _add(zf, "tables/pharmacology.csv", ph.getvalue().encode())

        # NMReDATA + JSON per sample with a structure proposal
        for s in samples:
            proposal = next(
                (p for p in proposals
                 if p.sample_id == s.sample_id and p.smiles), None
            )
            if proposal:
                _add(zf, f"nmredata/{s.sample_id}.nmredata.sdf",
                     assignments_to_nmredata(s, assignments, proposal).encode())
                _add(zf, f"tables/assignments_{s.sample_id}.json",
                     assignments_to_json(
                         s, assignments, proposal,
                         [c for t in (twod or []) for c in t.correlations],
                     ).encode())
        _add(
            zf, "provenance/manifest.json",
            json.dumps(provenance, indent=2, default=str).encode(),
        )
        stamped = [
            f"{datetime.now(UTC).isoformat(timespec='seconds')} {line}"
            for line in execution_log
        ]
        _add(zf, "provenance/execution_log.txt",
             ("\n".join(stamped) + "\n").encode())

        readme = [
            f"PHYTOSPECTRA NMR ANALYSIS BUNDLE -- {title}",
            "",
            f"Samples processed: {len(samples)} ({', '.join(s.sample_id for s in samples)}).",
            "report.docx is the Q1-style report: proposed structures,",
            "annotated figures, evidence-based assignment tables,",
            "discussion with published sources, limitations, references.",
            "figures/ holds the annotated PNG per sample (also embedded",
            "in the docx). tables/ holds SI-ready CSVs (inventory, peaks,",
            "assignments, rankings). provenance/ records tool + model",
            "versions, the run order, and a SHA256 per file (except",
            "checksums.json itself).",
            "",
            "REPORT RULES ENFORCED (see module docstring):",
        ]
        readme.extend(f"  - {r}" for r in REPORT_VOICE_RULES)
        readme.append("")
        readme.append("CAVEATS:")
        readme.extend(f"  - {c}" for c in caveats)
        _add(zf, "README.txt", ("\n".join(readme) + "\n").encode())
        _add(zf, "provenance/checksums.json",
             json.dumps(checksums, indent=2, sort_keys=True).encode())

    return path
