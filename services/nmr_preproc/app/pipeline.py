"""In-house NMR spectral preprocessing pipeline (ADR-0068).

Implements the standard 1H-NMR metabolomics preprocessing chain on
numpy + scipy -- no wrapped external tool (nmrmetaproc and nmrglue are
not installed; R/metabom8 is GPL and excluded by the adapter policy in
ADR-0018/0068).  Every step's parameters are captured in a
``PreprocessingRecord`` so the run is reproducible and auditable:

    apodisation (exponential, LB Hz)
      -> zero-fill (explicit factor)
      -> FFT (absorption or magnitude mode)
      -> phase correction (zero/first order; auto grid search, phases stored)
      -> chemical-shift referencing (TSP/DSS, applied shift stored)
      -> baseline correction (asLS, Eilers lambda/p stored)
      -> alignment (whole-spectrum FFT cross-correlation, lags stored)
      -> PQN normalisation (Dieterle 2006)
      -> bucketing (explicit bin width, excised regions recorded)

Known limitations, stated here because they are the weakest links of the
in-house path: (i) the automatic phasing objective (penalised negative
area) can be fooled by dispersive-heavy spectra -- the applied phases
are stored so a reviewer can detect and override; (ii) alignment is
integer-point whole-spectrum shift, no non-integer or warping
correction; (iii) no Bruker digital-filter (grpdelay) pre-correction --
teaching-grade and synthetic FIDs do not carry the filter.

Inputs are Bruker-style experiment directories: ``<exp>/1/fid`` +
``<exp>/1/acqus`` (full pipeline) or ``<exp>/1/pdata/1/1r`` +
``<exp>/1/pdata/1/procs`` (spectrum-level path: baseline -> alignment
-> PQN -> bucketing only; apodisation/zero-fill/phase are upstream and
recorded as None).  Anything else is a named skip, never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import spsolve

from phyto_shared.metabolomics import PreprocessingRecord

__all__ = [
    "PreprocessParams",
    "PreprocessResult",
    "SkipPreprocessing",
    "parse_jcamp",
    "apodise_exponential",
    "zero_fill",
    "fft_spectrum",
    "auto_phase",
    "apply_phase",
    "reference_to_standard",
    "asls_baseline",
    "align_spectra",
    "pqn_normalise",
    "bucket_spectra",
    "load_experiment",
    "preprocess_experiments",
    "fid_decay_ratio",
    "fid_data_type",
]


class SkipPreprocessing(Exception):
    """Named skip: the input is not processable NMR data, with a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PreprocessParams:
    lb_hz: float = 1.0
    zero_fill_factor: int = 2
    spectral_mode: str = "absorption"  # or "magnitude"
    reference_ppm: float = 0.0  # TSP / DSS position
    reference_window_ppm: float = 0.25
    baseline_lambda: float = 1e5
    baseline_p: float = 0.01
    baseline_iterations: int = 10
    bucket_width_ppm: float = 0.04
    excised_regions: list[tuple[float, float]] = field(default_factory=list)
    solvent: str | None = None


@dataclass
class PreprocessResult:
    matrix: np.ndarray  # samples x buckets
    ppm: np.ndarray  # bucket centres, descending
    sample_ids: list[str]
    record: PreprocessingRecord
    notes: list[str]


# ---------------------------------------------------------------------------
# Bruker file parsing
# ---------------------------------------------------------------------------


def parse_jcamp(path: Path) -> dict:
    """Parse a Bruker JCAMP-style parameter file (``##$KEY= value``)."""
    params: dict = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("##$"):
            key, _, value = line[3:].partition("=")
            value = value.strip()
            if value.startswith("<") and value.endswith(">"):
                params[key.strip()] = value[1:-1]
            else:
                try:
                    number = float(value)
                    params[key.strip()] = (
                        int(number) if number.is_integer() else number
                    )
                except ValueError:
                    params[key.strip()] = value
    return params


def _fid_data_type(acqus: dict) -> np.dtype:
    """Data type of the interleaved re/im FID pairs from ``acqus``.

    DTYPA is honoured per the Bruker/nmrglue convention: 2 means the FID
    was stored as float64 re/im pairs (DTYPA=2 is written e.g. by
    IconNMR for digitizer data; case-study finding: every 1H FID in the
    'NMR file for prediction' export carries DTYPA=2 and reading it as
    int32 produced garbage and a false 'no decay' verdict), anything
    else means int32.  Unknown/absent keys fall back to int32 -- the
    historical behaviour -- and the chosen type is recorded in the
    provenance metadata.
    """
    byte_order = int(acqus.get("BYTORDA", 0) or 0)
    endian = ">" if byte_order == 1 else "<"
    is_float = int(acqus.get("DTYPA", 0) or 0) == 2
    return np.dtype(endian + ("f8" if is_float else "i4"))


def fid_decay_ratio(fid: np.ndarray, quarters: int = 4) -> float:
    """Mean |FID| of the first quarter divided by that of the last.

    A physical FID decays monotonically, so a healthy acquisition has a
    ratio well above 1; a flat envelope (near 1.0) indicates a corrupt or
    placeholder export, not a spectrum.  Deterministic, allocation-light,
    and recorded as a sanity figure in the provenance chain.
    """
    mag = np.abs(np.asarray(fid) - np.asarray(fid).mean())
    quarter = max(mag.size // quarters, 1)
    early = float(mag[:quarter].mean())
    late = float(mag[-quarter:].mean())
    return early / late if late > 0 else np.inf


def _load_fid(exp_dir: Path) -> tuple[np.ndarray, dict]:
    acqus = parse_jcamp(exp_dir / "acqus")
    dtype = _fid_data_type(acqus)
    raw = np.fromfile(exp_dir / "fid", dtype=dtype)
    # TD counts COMPLEX points; the file holds 2*TD interleaved re/im
    # values of the DTYPA-selected width.
    td = int(acqus.get("TD", raw.size // 2))
    fid = raw[: 2 * td].astype(np.float64)
    complex_fid = fid[0::2] + 1j * fid[1::2]
    # SF is not always written to acqus (it lives in procs after
    # processing); SFO1 carries the same spectrometer frequency.
    sf_mhz = float(acqus.get("SF", acqus.get("SFO1", 0.0)))
    params = {
        "sw_hz": float(acqus["SW_h"]),
        "sf_mhz": sf_mhz,
        "offset_ppm": float(acqus.get("O1_ppm", acqus.get("O1", 0.0))
                            / sf_mhz)
        + float(acqus["SW_h"]) / sf_mhz / 2.0,
        "source": "raw fid",
        "fid_data_type": dtype.str,  # DTYPA honoured: recorded, auditable
        "pulse_program": str(acqus.get("PULPROG", "")) or None,
        "solvent": str(acqus.get("SOLVENT", "")) or None,
        "decay_ratio": round(fid_decay_ratio(complex_fid), 3),
    }
    return complex_fid, params


def _load_processed(exp_dir: Path) -> tuple[np.ndarray, dict]:
    procs = parse_jcamp(exp_dir / "pdata" / "1" / "procs")
    si = int(procs["SI"])
    nc = int(procs["NC_proc"])
    # Processed data follows the DTYPP/BYTORDP convention (nmrglue
    # read_pdata); fall back to the old BYTORDA-int32 behaviour when the
    # processed keys are absent.
    big = int(procs.get("BYTORDP", procs.get("BYTORDA", 0)) or 0) == 1
    is_float = int(procs.get("DTYPP", 0) or 0) == 2
    raw = np.fromfile(
        exp_dir / "pdata" / "1" / "1r",
        dtype=np.dtype((">" if big else "<") + ("f8" if is_float else "i4")),
    )
    spectrum = raw.astype(np.float64) * (2.0**nc)
    sf = float(procs["SF"])
    sw_ppm = float(procs["SW_p"]) / sf
    ppm = float(procs["OFFSET"]) - np.arange(si) * (sw_ppm / si)
    params = {
        "sw_hz": float(procs["SW_p"]),
        "sf_mhz": sf,
        "offset_ppm": float(procs["OFFSET"]),
        "source": "processed 1r",
    }
    return spectrum, {"spectrum": spectrum, "ppm": ppm, **params}


def load_experiment(exp_dir: str | Path) -> tuple[np.ndarray, dict]:
    """Load one experiment directory; raise SkipPreprocessing when it is
    not usable Bruker data (raw fid or processed 1r), with the reason.

    Returns ``(data, meta)``.  For raw FIDs ``data`` is the complex FID
    and the ppm axis is derived downstream; for processed data ``data``
    is the real spectrum, ``meta["ppm"]`` the axis and ``meta["spectrum"]``
    the same array (the processed branch of ``preprocess_experiments``
    reads it from ``meta``).
    """
    path = Path(exp_dir)
    if not path.is_dir():
        raise SkipPreprocessing(f"not a directory: {path}")
    raw_dir = path / "1" if (path / "1").is_dir() else path
    pdata_dir = raw_dir if (raw_dir / "pdata" / "1" / "1r").exists() else path
    if (raw_dir / "fid").exists() and (raw_dir / "acqus").exists():
        return _load_fid(raw_dir)
    if (pdata_dir / "pdata" / "1" / "1r").exists() and (
        pdata_dir / "pdata" / "1" / "procs"
    ).exists():
        return _load_processed(pdata_dir)
    raise SkipPreprocessing(
        f"no usable Bruker data under {path}: expected 1/fid + 1/acqus "
        "(raw) or 1/pdata/1/1r + 1/pdata/1/procs (processed)"
    )


# ---------------------------------------------------------------------------
# Processing steps (each pure and independently testable)
# ---------------------------------------------------------------------------


def apodise_exponential(fid: np.ndarray, lb_hz: float, sw_hz: float) -> np.ndarray:
    """Exponential apodisation e^{-pi*LB*t} with t = k/sw_hz."""
    t = np.arange(fid.size) / sw_hz
    return fid * np.exp(-np.pi * lb_hz * t)


def zero_fill(fid: np.ndarray, factor: int) -> np.ndarray:
    """Zero-fill to factor * len(fid), rounded up to a power of two."""
    target = int(2 ** np.ceil(np.log2(max(fid.size * factor, 1))))
    out = np.zeros(target, dtype=complex)
    out[: fid.size] = fid
    return out


def fft_spectrum(fid: np.ndarray) -> np.ndarray:
    """FFT with centre shift; returns the complex spectrum."""
    return np.fft.fftshift(np.fft.fft(fid))


def apply_phase(spectrum: np.ndarray, phase0_deg: float, phase1_deg: float) -> np.ndarray:
    """Apply zero/first-order phase; ph1 sweeps linearly across the sweep."""
    frac = np.linspace(-0.5, 0.5, spectrum.size)
    phase = np.deg2rad(phase0_deg + phase1_deg * frac)
    return (spectrum * np.exp(1j * phase)).real


def auto_phase(spectrum: np.ndarray) -> tuple[float, float]:
    """Grid-search ph0/ph1 minimising penalised negative area.

    Objective: negative area + 0.01 * total variation of the real part.
    Coarse by design (10-degree ph0, 8-degree ph1 steps): the point is a
    defensible deterministic starting phase that the record exposes, not
    a claim of optimality.
    """
    best = (0.0, 0.0)
    best_cost = np.inf
    for ph1 in np.arange(-40.0, 40.01, 8.0):
        for ph0 in np.arange(-180.0, 180.0, 10.0):
            real = apply_phase(spectrum, ph0, ph1)
            negative = float(np.sum(np.minimum(real, 0.0) ** 2))
            variation = float(np.sum(np.abs(np.diff(real))))
            cost = negative + 0.01 * variation
            if cost < best_cost:
                best_cost = cost
                best = (round(float(ph0), 2), round(float(ph1), 2))
    return best


def reference_to_standard(
    ppm: np.ndarray, spectrum: np.ndarray, reference_ppm: float, window_ppm: float
) -> float:
    """Shift the axis so the tallest peak near the standard sits at
    reference_ppm; returns the applied shift (NaN-free: 0.0 when no peak
    is found in the window -- recorded as 'not applied', never guessed)."""
    mask = np.abs(ppm - reference_ppm) <= window_ppm
    if not np.any(mask):
        return 0.0
    observed = float(ppm[mask][np.argmax(np.abs(spectrum[mask]))])
    return observed - reference_ppm


def asls_baseline(
    y: np.ndarray, lam: float = 1e5, p: float = 0.01, n_iter: int = 10
) -> np.ndarray:
    """Asymmetric least squares baseline (Eilers-style asLS).

    Solves (W + lam * D'D) z = W y iteratively, with weights p for
    points above the baseline and 1-p below (second-order difference D).
    """
    n = y.size
    diff2 = diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n - 2, n))
    penalty = lam * (diff2.T @ diff2)
    weights = np.ones(n)
    baseline = y.copy()
    for _ in range(n_iter):
        weighted = diags(weights) + penalty
        baseline = spsolve(weighted.tocsc(), weights * y)
        weights = np.where(y > baseline, p, 1.0 - p)
    return np.asarray(baseline, dtype=np.float64)


def align_spectra(spectra: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Whole-spectrum alignment to the pointwise median via FFT
    cross-correlation; integer-point shifts only (documented limit)."""
    reference = np.median(spectra, axis=0)
    aligned = np.empty_like(spectra)
    shifts: list[int] = []
    for i, spectrum in enumerate(spectra):
        corr = np.fft.ifft(
            np.fft.fft(spectrum) * np.conj(np.fft.fft(reference))
        ).real
        lag = int(np.argmax(corr))
        # roll left by lag so spectrum's features sit on the reference's
        aligned[i] = np.roll(spectrum, -lag)
        shifts.append(lag)
    return aligned, shifts


def pqn_normalise(spectra: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Probabilistic quotient normalisation (Dieterle 2006) over rows."""
    reference = np.median(spectra, axis=0)
    safe = np.where(np.abs(reference) < 1e-12, np.nan, reference)
    factors = np.array(
        [
            np.nanmedian(spectrum / safe) if np.any(~np.isnan(safe)) else 1.0
            for spectrum in spectra
        ]
    )
    factors[~np.isfinite(factors)] = 1.0
    factors[factors == 0.0] = 1.0
    return spectra / factors[:, None], factors


def bucket_spectra(
    ppm: np.ndarray,
    spectra: np.ndarray,
    width_ppm: float,
    excised: list[tuple[float, float]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Integrate spectra into fixed-width ppm buckets, skipping excised
    regions entirely (they are recorded, not silently dropped)."""
    excised = excised or []
    lo, hi = float(np.min(ppm)), float(np.max(ppm))
    edges = np.arange(hi, lo - width_ppm, -width_ppm)
    centres: list[float] = []
    buckets: list[np.ndarray] = []
    for left, right in zip(edges[:-1], edges[1:]):
        centre = (left + right) / 2.0
        if any(
            centre < max(a, b) and centre > min(a, b) for a, b in excised
        ):
            continue
        mask = (ppm <= left) & (ppm > right)
        if not np.any(mask):
            continue
        centres.append(centre)
        buckets.append(spectra[:, mask].sum(axis=1))
    if not buckets:
        raise SkipPreprocessing(
            f"bucketing produced zero buckets (width {width_ppm} ppm over "
            f"[{lo}, {hi}] ppm with {len(excised)} excised regions)"
        )
    return np.column_stack(buckets), np.array(centres)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def preprocess_experiments(
    experiment_dirs: list[str | Path], params: PreprocessParams | None = None
) -> PreprocessResult:
    """Run the full chain over experiment directories, or skip with a
    named reason when any input is not usable NMR data."""
    params = params or PreprocessParams()
    loaded: list[tuple[str, np.ndarray, dict]] = []
    reasons: list[str] = []
    for exp_dir in experiment_dirs:
        try:
            data, meta = load_experiment(exp_dir)
        except SkipPreprocessing as skip:
            reasons.append(skip.reason)
            continue
        sample_id = Path(exp_dir).name
        if Path(exp_dir).parent.name.isdigit():
            sample_id = Path(exp_dir).parent.parent.name  # <exp>/1 form
        loaded.append((sample_id, data, meta))
    if reasons or not loaded:
        raise SkipPreprocessing(
            "; ".join(reasons)
            or "no experiment directories given"
        )
    from_raw = loaded[0][2]["source"] == "raw fid"
    if any(meta["source"] != loaded[0][2]["source"] for _, _, meta in loaded):
        raise SkipPreprocessing(
            "mixed input modalities: some directories carry raw fid, "
            "others only processed 1r; preprocess each kind separately"
        )

    notes: list[str] = []
    phase0 = phase1 = None
    if from_raw:
        spectra: list[np.ndarray] = []
        for _sample, fid, meta in loaded:
            # Fail-loud sanity check: a physical FID decays, so a flat
            # envelope means the file is corrupt/placeholder (e.g. a
            # broken cloud export).  Recorded as a note, not silently
            # processed into a garbage spectrum.
            if meta.get("decay_ratio", float("inf")) < 1.2:
                notes.append(
                    f"{_sample}: FID shows no decay (early/late magnitude "
                    f"ratio {meta.get('decay_ratio')}); the raw file looks "
                    "corrupt or is a placeholder export — processed anyway, "
                    "treat the spectrum as untrusted"
                )
            sw_hz = meta["sw_hz"]
            worked = apodise_exponential(fid, params.lb_hz, sw_hz)
            worked = zero_fill(worked, params.zero_fill_factor)
            spectrum = fft_spectrum(worked)
            if params.spectral_mode == "magnitude":
                spectra.append(np.abs(spectrum))
            else:
                spectra.append(spectrum)
        stack = np.vstack([s[np.newaxis, :] for s in spectra])
        if params.spectral_mode == "absorption":
            phases = [auto_phase(spectrum) for spectrum in stack]
            # one common phase per dataset (shared acquisition), from the
            # first spectrum; per-spectrum phases are stored in the notes
            phase0, phase1 = phases[0]
            stack = np.vstack(
                [apply_phase(spectrum, p0, p1) for spectrum, (p0, p1) in zip(stack, phases)]
            )
            if len(set(phases)) > 1:
                notes.append(
                    "auto-phase varied per spectrum: "
                    + "; ".join(f"{p0}deg/{p1}deg" for p0, p1 in phases)
                )
    else:
        stack = np.vstack([meta["spectrum"][np.newaxis, :] for _, _, meta in loaded])
        notes.append(
            "processed from pdata/1/1r (no raw fid): apodisation, "
            "zero-fill and phase were applied upstream and are recorded "
            "as absent, not guessed"
        )

    # common ppm axis (identical acquisition across the set, verified by
    # the procs/acqus offsets matching)
    meta0 = loaded[0][2]
    sw_ppm = meta0["sw_hz"] / meta0["sf_mhz"]
    si = stack.shape[1]
    ppm = meta0["offset_ppm"] - np.arange(si) * (sw_ppm / si)
    for _sample, _data, meta in loaded[1:]:
        if abs(meta["offset_ppm"] - meta0["offset_ppm"]) > 1e-6 or abs(
            meta["sf_mhz"] - meta0["sf_mhz"]
        ) > 1e-6:
            raise SkipPreprocessing(
                "inconsistent acquisition parameters across experiments "
                "(offset/SF differ); a shared ppm axis cannot be assumed"
            )

    ref_shift = reference_to_standard(
        ppm, stack[0], params.reference_ppm, params.reference_window_ppm
    )
    if ref_shift != 0.0:
        ppm = ppm - ref_shift
    else:
        notes.append(
            f"referencing: no peak found within {params.reference_window_ppm} "
            f"ppm of {params.reference_ppm} ppm; no shift applied"
        )

    # baseline on the positive-part envelope (PQN assumption)
    stack = np.vstack(
        [
            (row - asls_baseline(row, params.baseline_lambda, params.baseline_p,
                                 params.baseline_iterations))[np.newaxis, :]
            for row in stack
        ]
    )
    stack, _shifts = align_spectra(stack)
    stack, _factors = pqn_normalise(stack)
    matrix, centres = bucket_spectra(
        ppm, stack, params.bucket_width_ppm, params.excised_regions
    )

    record = PreprocessingRecord(
        modality="nmr",
        apodisation_function="exponential" if from_raw else None,
        line_broadening_hz=params.lb_hz if from_raw else None,
        zero_fill_factor=params.zero_fill_factor if from_raw else None,
        phase_method="auto grid (penalised negative area)" if from_raw else None,
        phase0_deg=phase0,
        phase1_deg=phase1,
        referencing_standard=f"{params.reference_ppm:.2f} ppm (TSP/DSS)",
        referencing_shift_ppm=round(ref_shift, 5),
        baseline_method="asLS (Eilers)",
        baseline_lambda=params.baseline_lambda,
        baseline_p=params.baseline_p,
        alignment_method="whole-spectrum FFT cross-correlation (integer)",
        bucket_width_ppm=params.bucket_width_ppm,
        excised_regions=[tuple(r) for r in params.excised_regions] or None,
        solvent=params.solvent or (loaded[0][2].get("solvent")),
        spectrometer_frequency_mhz=meta0["sf_mhz"],
        pulse_program=loaded[0][2].get("pulse_program"),
        normalisation_method="pqn",
        sample_ids=[sample_id for sample_id, _, _ in loaded],
    )
    return PreprocessResult(
        matrix=matrix,
        ppm=centres,
        sample_ids=[sample_id for sample_id, _, _ in loaded],
        record=record,
        notes=notes,
    )
