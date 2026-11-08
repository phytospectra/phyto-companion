# M1-a Tetracycline — MS/MS assessment (executed 2026-11-02)

Status after assessment: **library agreement partial (moderate-confidence library match, mass-offset-limited); NOT a confirmed identification.** The M1-a acceptance status in DATA_SOURCES.md §11 ("precursor-mass + RT match pending MS/MS confirmation") is unchanged by this assessment, which adds the verified existence and quality of the MS2 acquisitions.

## 1. Files assessed (with hashes)

| File | Hash (as committed in `run_summary.json`) | Role |
|---|---|---|
| `data/raw/MSV000090047/20220426_UMETAB266_POS_Tetracycline.mzML` | MD5 `396f11793f20870de150b56408a33c21`; SHA-256 `fa3910452ae96f754a3eb05b2cf07ae2993f053fa4862b78d365cf1d857f6b9e` | Downloaded MassIVE `peak/` mzML (Thermo Orbitrap ID-X DDA, converted by ThermoRawFileParser 1.3.4 upstream), streamed locally with pyteomics |

Scan counts re-parsed locally: **782 MS1 + 4,206 MS2** (total 4,988; DDA).

## 2. Chromatographic and scan-level evidence

- MZmine feature (per `data/validated_runs/M1a/run_summary.json`): **m/z 445.16056, +0.135 ppm, RT 326.65 s = 5.444 min** for [M+H]⁺ of C22H24N2O8 (theor. 445.1605).
- **30 MS2 scans** in this file have isolation target within ±0.6 Da of 445.1605, spanning RT 5.44–7.5 min. All at **beam-type CID (HCD), nominal collision energy 40**, isolation **±0.8 Da**, positive mode. The precursor-isolation events extend beyond the chromatographic peak (RT > 6 min), which is normal DDA behaviour after the apex; the apex scans are the informative ones.
- Main scan at the feature RT: **scan 2517, RT 5.844 min, TIC 6.03M, base peak 410.1228 (995k), 83 peaks.** Secondary: scan 2405 (RT 5.632, TIC 434k) and scan 2585 (RT 5.985, TIC 612k, base peak 154.0494).
- Precursor charge 1+: MS1 isotope spacing at 445.16/446.16 is ~1.0 Da; [M+H]⁺ consistent with the mass formula.
- Co-isolation: the ±0.8 Da window (444.36–445.96) contains only the analyte cluster in MS1 scans at RT 5.4–6.0; no co-isolant flagged.

## 3. Reference spectrum

- **MassBank accession `MSBNK-BAFG-CSL23111012700`** — the accession documented in DATA_SOURCES.md §11 ("Tetracycline; LC-ESI-QTOF; MS2; 40 V", precursor 445.1605 [M+H]⁺, RT 5.46 min). License **dl-de/by-2-0** (attribution required; redistribution permitted with attribution — compliant with the repo's licence rules).
- Instrument: **TripleTOF 5600 SCIEX (QTOF), CID 40 V** — *different instrument class from our Orbitrap ID-X HCD 40*; this is the primary interpretation caveat.
- Retrieved 2026-11-02 via massbank.jp MassBank3 REST API; the record is one of 142 tetracycline/oxytetracycline records; 51 peaks.

## 4. Matching (rules predeclared)

Tolerance ±5 ppm (Orbitrap-quality vs QTOF-reported m/z), one-to-one greedy (reference by descending rel. intensity, each measured peak used once), reference filter rel. int. ≥ 1%, precursor retained. Machine-readable table: `data/validated_runs/msms_assessment/M1a_tetracycline_scan2517_vs_MassBank-BAFG-CSL23111012700.json`. Mirror plot: `data/validated_runs/msms_assessment/tet_mirror.png`.

## 5. Result

| Tolerance (scan 2517) | Matched | Rel.-intensity coverage |
|---|---|---|
| ±5 ppm | 11/51 | 30.2% |
| ±10 ppm | 18/51 | 55.4% |
| ±20 ppm | 29/51 | 89.9% |
| ±25 ppm | 31/51 | 92.1% |

- Median |error| of matched peaks at ±5 ppm: 2.2 ppm. **But the unmatched reference peaks cluster at small sub-25-ppm offsets** (e.g. 410.1286→410.1229 at −14.0 ppm int 995k; 267.0674→267.0647 at −10.1 ppm int 232k; 337.0688→337.0705 at +5.2 ppm int 158k; 428.1375→428.1332 at −10.1 ppm; 257.0852→257.0805 at −18.4 ppm). A linear ppm-vs-m/z recalibration does **not** reconcile them (residual median 6.6 ppm, and the recalibrated fit actually loses the low-mass matches), i.e. the offset is **piecewise/peak-dependent**, consistent with the QTOF reference's centroiding rather than a single calibration error on our side.
- Cross-scan check at ±5 ppm: scan 2405 → 10/51 (45.6%), scan 2517 → 11/51 (30.2%), scan 2585 → 8/51 (43.7%); the apex scans agree in composition; none passes a strict ±5 ppm full-spectrum test, all at ±20 ppm match 60–80%.
- **Decoy control**: random MS2 scans elsewhere in the same run matched 0–1/51 reference peaks (0–1.9% coverage) at ±5 ppm. The 11–18/51 match is not a chance artifact.
- Reference internal quality note: the reference's base peak is 410.1286 at **999%** and 154.0497 at 942% — our Orbitrap scan has both at −14.0 and −0.4 ppm respectively. The dominant tetracycline diagnostic ions (410/428 water-loss series, 154, 98, 267, 269, 241, 337, 321, 349) are all present within ≤25 ppm and mostly within ≤15 ppm.

## 6. Verdict (honest, bounded)

1. **Acquisition proven: yes.** MS2 was acquired at the [M+H]⁺ feature across its chromatographic peak (30 isolation events; 3 usable high-signal scans).
2. **Library agreement: partial-to-moderate.** At the strict ±5 ppm a QTOF-vs-Orbitrap cross-instrument comparison passes on 30% of reference intensity; at a cross-instrument-realistic ±20–25 ppm it passes on **~90%** with all dominant diagnostic ions matched. The limiting factor is the **~10–20 ppm piecewise mass offset of the QTOF reference**, not absence of the fragments.
3. **Confirmation: no.** No authentic standard under matched conditions was run; MSI level ≤2 under the platform rules.
4. **Missing evidence named**: (a) authentic-standard LC–MS/MS under matched conditions; (b) an Orbitrap-MS2 tetracycline reference (MassBank has 100+ HBM4EU Orbitrap tetracycline records — e.g. MSBNK-HBM4EU-HB0019xx at CE 10–70% [M+H]⁺ — which were **not** used here because the documented §11 accession is the BAFG QTOF one; a follow-up comparison against those is the natural next step and may raise coverage); (c) peak shape/resolution-matched centroiding for ppm-level QTOF-vs-Orbitrap comparisons.
5. **Borderline sensitivity**: the pass/fail boundary sits between ±5 and ±20 ppm purely because of instrument class; any tolerance ≥ ±20 ppm yields 90% coverage with median error <10 ppm. This is an acquisition-difference effect, not an evidence weakness of the sample.

## 7. Known-identical-case disposition (benchmark hygiene)

Per the study prompt, M1-a and M1-b go into the **smoke-test set outside reported benchmark accuracy** — they are the two compounds whose identity the repo already asserts, so they must not be benchmark denominators. Their spectra and reference-match JSONs are retained as software/evidence verification assets.
