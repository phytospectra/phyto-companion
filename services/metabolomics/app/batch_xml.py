"""MZmine 4.11 batch XML generation for LC-MS/MS feature detection.

Every element name, parameter name, attribute and enum value here was
extracted from the official MZmine v4.11 repository (tag ``v4.11``):
``mzmine-community/src/test/resources/rawdatafiles/integration_tests/
workshop_dataset/workshop_dataset_full.mzbatch`` (a batch the MZmine CI
integration harness actually executes headlessly) and cross-checked
against ``BatchQueue.java`` (root attribute ``mzmine_version`` compared
semantically against the running version; module ``method`` names are
matched strictly; ``parameter_version`` must equal the module's
``ParameterSet.getVersion()``).

The batch is written with ``mzmine_version="4.11.0"`` and is therefore
PINNED to MZmine 4.11: a different engine version must regenerate the
batch, not reuse it.  Raw LC-MS processing claims must cite this pin
(ADR-0084/ADR-0085).

Vendor-native conversion (.raw/.d) is NOT part of this contract: the
batch consumes mzML only (AllSpectralDataImportModule handles mzML and
the already-converted formats MZmine itself reads).
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .feature_detection import DetectParams

__all__ = ["MZMINE_VERSION", "build_batch_xml", "BATCH_STEPS"]

#: The engine versions this generator's contract covers.  The batch was
#: authored from the v4.11 repository and every module class name and
#: parameter version was cross-checked at tags v4.10.90 and v4.10.6 as
#: well, so all three engines accept the same XML (only the root
#: version attribute differs).  MEASURED: every one of these engines
#: requires an authenticated user for CLI batch runs (the login gate
#: exists in source since v4.0.x), so a real run additionally needs
#: ``MZMINE_USER_FILE`` (ADR-0085).
SUPPORTED_MZMINE_VERSIONS: tuple[str, ...] = ("4.11.0", "4.10.90", "4.10.6")

#: Default pin for new batches.
MZMINE_VERSION = SUPPORTED_MZMINE_VERSIONS[0]

_MODULE_IMPORT = "io.github.mzmine.modules.io.import_rawdata_all.AllSpectralDataImportModule"
_MODULE_MS1 = "io.github.mzmine.modules.dataprocessing.featdet_massdetection.MassDetectionModule"
_MODULE_MS2 = _MODULE_MS1
_MODULE_ADAP = (
    "io.github.mzmine.modules.dataprocessing.featdet_adapchromatogrambuilder"
    ".ModularADAPChromatogramBuilderModule"
)
_MODULE_SMOOTH = "io.github.mzmine.modules.dataprocessing.featdet_smoothing.SmoothingModule"
_MODULE_RESOLVER = (
    "io.github.mzmine.modules.dataprocessing.featdet_chromatogramdeconvolution"
    ".minimumsearch.MinimumSearchFeatureResolverModule"
)
_MODULE_ISOGROUP = (
    "io.github.mzmine.modules.dataprocessing.filter_isotopegrouper.IsotopeGrouperModule"
)
_MODULE_ALIGN = "io.github.mzmine.modules.dataprocessing.align_join.JoinAlignerModule"
_MODULE_GAPFILL = (
    "io.github.mzmine.modules.dataprocessing.gapfill_peakfinder.multithreaded"
    ".MultiThreadPeakFinderModule"
)
_MODULE_DUP = "io.github.mzmine.modules.dataprocessing.filter_duplicatefilter.DuplicateFilterModule"
_MODULE_CSV = "io.github.mzmine.modules.io.export_features_csv.CSVExportModularModule"

#: (method, module_name, parameter_version) in pipeline order.
BATCH_STEPS: tuple[tuple[str, str, int], ...] = (
    (_MODULE_IMPORT, "Import MS data", 1),
    (_MODULE_MS1, "Mass detection", 1),
    (_MODULE_MS2, "Mass detection", 1),
    (_MODULE_ADAP, "Chromatogram builder", 1),
    (_MODULE_SMOOTH, "Smoothing", 1),
    (_MODULE_RESOLVER, "Local minimum feature resolver", 2),
    (_MODULE_ISOGROUP, "13C isotope filter (formerly: isotope grouper)", 1),
    (_MODULE_ALIGN, "Join aligner", 1),
    (_MODULE_GAPFILL, "Feature finder (multithreaded)", 1),
    (_MODULE_DUP, "Duplicate peak filter", 1),
    (_MODULE_CSV, "Export to CSV file (modular)", 1),
)

_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'


def _p(
    name: str, inner: str = "", attrs: dict[str, str] | None = None, indent: str = "        "
) -> str:
    """One <parameter> element; empty inner renders a self-closing tag."""
    attr_s = ""
    for key, value in (attrs or {}).items():
        attr_s += f' {key}="{escape(str(value), {chr(34): "&quot;"})}"'
    if inner:
        return f'{indent}<parameter name="{escape(name)}"{attr_s}>{inner}</parameter>'
    return f'{indent}<parameter name="{escape(name)}"{attr_s}/>'


def _mz_tolerance(abs_tol: float, ppm_tol: float, indent: str = "        ") -> str:
    return (
        f'{indent}<parameter name="m/z tolerance">'
        f"<absolutetolerance>{abs_tol}</absolutetolerance>"
        f"<ppmtolerance>{ppm_tol}</ppmtolerance>"
        "</parameter>"
    )


def _import_step(paths: list[str]) -> str:
    files = "".join(f"\n            <file>{escape(p)}</file>" for p in paths)
    return f"""    <batchstep method="{_MODULE_IMPORT}" module_name="Import MS data" parameter_version="1">
        <parameter name="File names">{files}
        </parameter>{_p("Vendor data options", attrs={"selected": "false"})}{_p("Advanced import", attrs={"selected": "false"})}{_p("Extract sample metadata", "false")}{_p("Sort and color", "true")}
    </batchstep>"""


def _mass_detection_step(level: int, noise_factor: float) -> str:
    return f"""    <batchstep method="{_MODULE_MS1}" module_name="Mass detection" parameter_version="1">
      <parameter name="Raw data files" type="BATCH_LAST_FILES"/>
{_p("Scan filters", f'<parameter name="MS level filter" selected="{"MS1, level = 1" if level == 1 else "MSn, level >= 2"}">{level if level == 1 else 3}</parameter>', indent="        ")}{_p("Scan types (IMS)", "All scan types")}{_p("Denormalize fragment scans (traps)", "true")}
        <parameter name="Mass detector" selected_item="Factor of lowest signal">
            <module name="Factor of lowest signal">
                <parameter name="Noise factor">{noise_factor}</parameter>
            </module>
        </parameter>
    </batchstep>"""


def _adap_step(params: DetectParams) -> str:
    # DetectParams uses seconds; MZmine RT ranges are minutes.
    min_intensity = params.min_intensity
    noise = params.noise_threshold
    abs_tol = 0.002
    ppm_tol = 10.0
    return f"""    <batchstep method="{_MODULE_ADAP}" module_name="Chromatogram builder" parameter_version="1">
      <parameter name="Raw data files" type="BATCH_LAST_FILES"/>
        <parameter name="Scan filters" selected="true">
            <parameter name="MS level filter" selected="MS1, level = 1">1</parameter>
            <parameter name="Polarity">Any</parameter>
            <parameter name="Spectrum type">ANY</parameter>
        </parameter>
        <parameter name="Minimum consecutive scans">6</parameter>
        <parameter name="Minimum intensity for consecutive scans">{noise}</parameter>
        <parameter name="Minimum absolute height">{min_intensity}</parameter>
        <parameter name="m/z tolerance (scan-to-scan)">
            <absolutetolerance>{abs_tol}</absolutetolerance>
            <ppmtolerance>{ppm_tol}</ppmtolerance>
        </parameter>
        <parameter name="Suffix">eics</parameter>
    </batchstep>"""


def _smoothing_step() -> str:
    return f"""    <batchstep method="{_MODULE_SMOOTH}" module_name="Smoothing" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Smoothing algorithm" selected_item="Savitzky Golay">
            <module name="Savitzky Golay">
                <parameter name="Retention time width (scans)" selected="true">7</parameter>
            </module>
        </parameter>
        <parameter name="Original feature list">REMOVE</parameter>
        <parameter name="Suffix">sm</parameter>
    </batchstep>"""


def _resolver_step(params: DetectParams) -> str:
    min_height = params.min_intensity
    return f"""    <batchstep method="{_MODULE_RESOLVER}" module_name="Local minimum feature resolver" parameter_version="2">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Suffix">r</parameter>
        <parameter name="Original feature list">REMOVE</parameter>
        <parameter name="MS/MS scan pairing" selected="true">
            <parameter name="MS1 to MS2 precursor tolerance (m/z)">
                <absolutetolerance>0.01</absolutetolerance>
                <ppmtolerance>10.0</ppmtolerance>
            </parameter>
            <parameter name="Retention time filter" selected="Use feature edges" unit="MINUTES">0.04</parameter>
            <parameter name="Minimum relative feature height" selected="true">0.25</parameter>
            <parameter name="Minimum required signals" selected="true">1</parameter>
            <parameter name="Limit by ion mobility edges">true</parameter>
            <parameter name="Merge MS/MS spectra (TIMS)">false</parameter>
            <parameter name="Minimum detections in IMS dimension">2</parameter>
        </parameter>
        <parameter name="Dimension">Retention time</parameter>
        <parameter name="Chromatographic threshold">0.3</parameter>
        <parameter name="Minimum search range RT/Mobility (absolute)">0.04</parameter>
        <parameter name="Minimum relative height">0.0</parameter>
        <parameter name="Minimum absolute height">{min_height}</parameter>
        <parameter name="Min ratio of peak top/edge">2.0</parameter>
        <parameter name="Peak duration range (min/mobility)">
            <min>0.0</min>
            <max>1.2</max>
        </parameter>
        <parameter name="Minimum scans (data points)">6</parameter>
    </batchstep>"""


def _isogroup_step(params: DetectParams) -> str:
    return f"""    <batchstep method="{_MODULE_ISOGROUP}" module_name="13C isotope filter (formerly: isotope grouper)" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Name suffix">deiso</parameter>
        <parameter name="m/z tolerance (intra-sample)">
            <absolutetolerance>0.0015</absolutetolerance>
            <ppmtolerance>{params.mz_tolerance_ppm}</ppmtolerance>
        </parameter>
        <parameter name="Retention time tolerance" unit="MINUTES">0.04</parameter>
        <parameter name="Monotonic shape">true</parameter>
        <parameter name="Maximum charge">2</parameter>
        <parameter name="Representative isotope">Most intense</parameter>
        <parameter name="Never remove feature with MS2">true</parameter>
        <parameter name="Original feature list">REMOVE</parameter>
    </batchstep>"""


def _align_step(params: DetectParams) -> str:
    rt_min = params.rt_tolerance_s / 60.0
    return f"""    <batchstep method="{_MODULE_ALIGN}" module_name="Join aligner" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Feature list name">Aligned feature list</parameter>
        <parameter name="m/z tolerance (sample-to-sample)">
            <absolutetolerance>0.0015</absolutetolerance>
            <ppmtolerance>{params.mz_tolerance_ppm}</ppmtolerance>
        </parameter>
        <parameter name="Weight for m/z">3.0</parameter>
        <parameter name="Retention time tolerance" unit="MINUTES">{rt_min:.6f}</parameter>
        <parameter name="Weight for RT">1.0</parameter>
        <parameter name="Require same charge state">false</parameter>
        <parameter name="Require same ID">false</parameter>
        <parameter name="Original feature list">REMOVE</parameter>
    </batchstep>"""


def _gapfill_step(params: DetectParams) -> str:
    rt_min = params.rt_tolerance_s / 60.0
    return f"""    <batchstep method="{_MODULE_GAPFILL}" module_name="Feature finder (multithreaded)" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Name suffix">gaps</parameter>
        <parameter name="Intensity tolerance">0.2</parameter>
        <parameter name="m/z tolerance (sample-to-sample)">
            <absolutetolerance>0.002</absolutetolerance>
            <ppmtolerance>{params.mz_tolerance_ppm}</ppmtolerance>
        </parameter>
        <parameter name="Retention time tolerance" unit="MINUTES">{rt_min:.6f}</parameter>
        <parameter name="Minimum scans (data points)">3</parameter>
        <parameter name="Original feature list">REMOVE</parameter>
    </batchstep>"""


def _dup_step(params: DetectParams) -> str:
    return f"""    <batchstep method="{_MODULE_DUP}" module_name="Duplicate peak filter" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Name suffix">dup</parameter>
        <parameter name="Filter mode">NEW AVERAGE</parameter>
        <parameter name="m/z tolerance">
            <absolutetolerance>7.5E-4</absolutetolerance>
            <ppmtolerance>{max(params.mz_tolerance_ppm / 4.0, 1.0)}</ppmtolerance>
        </parameter>
        <parameter name="RT tolerance" unit="MINUTES">0.028</parameter>
        <parameter name="Require same identification">false</parameter>
        <parameter name="Original feature list">REMOVE</parameter>
    </batchstep>"""


def _csv_export_step(output_csv: str) -> str:
    return f"""    <batchstep method="{_MODULE_CSV}" module_name="Export to CSV file (modular)" parameter_version="1">
        <parameter name="Feature lists" type="BATCH_LAST_FEATURELISTS"/>
        <parameter name="Row selection">ALL</parameter>
        <parameter name="Filename">
            <current_file>{escape(output_csv)}</current_file>
            <last_file>{escape(output_csv)}</last_file>
        </parameter>
        <parameter name="Field separator">,</parameter>
        <parameter name="Identification separator">;</parameter>
        <parameter name="Remove empty columns">true</parameter>
        <parameter name="Filter rows">ALL</parameter>
    </batchstep>"""


def build_batch_xml(
    mzml_paths: list[str],
    output_csv: str,
    params: DetectParams,
    mzmine_version: str = MZMINE_VERSION,
) -> str:
    """Render the version-pinned LC-MS/MS feature-detection batch.

    ``mzmine_version`` must match the engine that will run the batch:
    MZmine compares the root attribute against its own version and
    treats a mismatch as a parameter warning, which fails the CLI run
    by default (v4.11 ``BatchQueue``).  Only versions whose module
    classes and parameter versions were verified against this
    generator (``SUPPORTED_MZMINE_VERSIONS``) are accepted -- fail
    closed rather than emit a batch for an unverified engine.
    """
    if mzmine_version not in SUPPORTED_MZMINE_VERSIONS:
        raise ValueError(
            f"batch XML contract covers only {', '.join(SUPPORTED_MZMINE_VERSIONS)}; "
            f"refusing to emit a batch claiming version {mzmine_version!r} -- "
            "verify the module contract against that engine version first"
        )
    if not mzml_paths:
        raise ValueError("no mzML paths given for the import step")
    if not output_csv:
        raise ValueError("no output CSV path given for the export step")

    # Mass detection noise is expressed as a factor of the lowest signal
    # in the scan (MZmine's "Factor of lowest signal" detector), not an
    # absolute threshold; derive a bounded factor from DetectParams so
    # the request's noise sensitivity is honoured without letting a
    # near-zero factor flood the feature list.  The ADAP step uses the
    # absolute noise_threshold directly.
    ms1_factor = max(1.0, min(params.noise_threshold / 100.0, 50.0))
    steps = [
        _import_step(mzml_paths),
        _mass_detection_step(1, ms1_factor),
        _mass_detection_step(2, max(1.0, ms1_factor / 2)),
        _adap_step(params),
        _smoothing_step(),
        _resolver_step(params),
        _isogroup_step(params),
        _align_step(params),
        _gapfill_step(params),
        _dup_step(params),
        _csv_export_step(output_csv),
    ]
    return (
        f'{_HEADER}\n<batch mzmine_version="{mzmine_version}">\n'
        + "\n".join(steps)
        + "\n</batch>\n"
    )
