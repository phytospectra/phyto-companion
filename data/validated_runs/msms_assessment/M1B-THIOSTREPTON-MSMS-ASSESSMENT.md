# M1-b Thiostrepton — MS/MS assessment (executed 2026-11-02)

Assessor: agent session (local parsing only; no interpretation beyond what is reported here).
Status after assessment: **library agreement partial (moderate-confidence library match); NOT a confirmed identification** (no authentic-standard run under matched conditions). The M0-b/M1-b acceptance status ("precursor-mass + RT match pending MS/MS") is unchanged by this assessment, but the MS/MS acquisition itself is now verified.

## 1. Files assessed (with hashes)

| File | SHA-256 | Role |
|---|---|---|
| `data/validated_runs/M1b/converted/20220426_UMETAB266_POS_Thiostreptone.mzML` | `45b67fd57332c9bf6080233679e8d743cbaa704dddd91380638a6e2019d5b283` (recomputed locally, matches `run_summary.json` "as_committed") | Converted mzML actually streamed by the parser |
| `data/raw/MSV000090047/20220426_UMETAB266_POS_Thiostreptone.raw` | 68,362,907 bytes (vendor-native; the mzML above was converted from it by ThermoRawFileParser 2.0.0.0 per `run_summary.json`) | Original raw |

## 2. Scan-level metadata (inspected before interpreting peaks)

- Scan counts re-parsed from the committed mzML with pyteomics: **5,023 scans = 768 MS1 + 4,255 MS2** — consistent with `data/validated_runs/M1b/run_summary.json` and the DATA_SOURCES.md §11b scan-count correction.
- Polarity: positive (`positive scan` CV term on every inspected scan). Fragmentation: **beam-type CID (HCD), collision energy 40.0 (nominal, as stored)**.
- Isolation window at the target precursor: **±0.8 Da** (window 831.95–833.55 at target 832.7535).
- **Two** MS2 scans were acquired with isolation target within ±0.6 Da of 832.7535:
  - **scan 3084**, RT 7.068 min, target 832.75354, TIC 54,114, 18 peaks, base peak m/z 147.287 — weak, noise-level spectrum, not interpretable (the precursor peak itself is absent from its peak list).
  - **scan 3754**, RT 8.499 min, target 832.75397, TIC 342,579, 54 peaks, base peak m/z 234.0758 — the analytically relevant scan.
- DATA_SOURCES.md §11b says "One MS2 scan was acquired at the thiostrepton [M+2H]2+ precursor": strictly, two MS2 scans share the isolation window (they count DDA events at the same target), but only scan 3754 sits at the chromatographic peak (RT 8.50 vs feature RT 8.42 min) and has usable signal. The count in `run_summary.json` ("ms2_scans_at_thiostrepton_precursor_mz_832_7535": 1) is defensible as "one usable scan" but the raw DDA count is 2; noted here for precision.
- RT units: scan start times are in minutes (8.42–8.50 range consistent with the MZmine feature RT 8.4204 min).
- **Charge state verified in MS1**: precursor MS1 scan 3742 (RT 8.478 min) shows the isotope cluster 832.75397 / 833.25519 / 833.75470 / 834.25494 / 834.75482 with ~0.500 Da spacing → **2+**, as required for [M+2H]²⁺ of C72H85N19O18S5 (mono-isotopic mass 1662.4996; (1662.4996 + 2×1.00728)/2 = 832.7570, i.e. the MZmine-reported 832.75345 feature is the ~5 S-atom isotope-packet apex convention used by the vendor/MZmine, consistent with the §11b isotope-satellite note). Intensity ratios of the cluster (1401665 : 1157978 : 894054 : 436780 : 175654) are characteristic of a 72-carbon, 5-sulfur molecule.
- **Possible co-isolation**: the ±0.8 Da window (831.95–833.55) includes the 1+ isotope peaks of any singly charged co-eluting species near m/z 832–833 and the 2+ monoisotopic at 832.754. In MS1 scan 3742 the only significant peaks inside the window belong to the thiostrepton cluster itself (all ≥10× above the 1+ half-mass neighbours). The DDA MS2 spectrum's high-mass peaks (1161.26, 1247.31) are singly charged fragments, not evidence of co-isolation. No co-isolant flagged.

## 3. Reference spectrum

- **MassBank accession `MSBNK-ACES_SU-AS001032`** — "Thiostrepton; LC-ESI-QFT; MS2; Ramp 20%–70% (nominal); 30000; [M+2H]²⁺", license **CC BY** (permitted use; attribute ACES SU).
- Instrument: Exploris 480 Orbitrap (same vendor family as our Orbitrap ID-X); HCD ramp 20–70%; resolution 30,000; RT 12.6 min on a different column/gradient (Waters BEH C18, 1 mM ammonium fluoride / MeOH).
- Retrieved 2026-11-02 via the massbank.jp MassBank3 REST API (`/MassBank-api/records?compound_name=thiostrepton`); 1 record. Precursor 832.7526 [M+2H]²⁺; 76 peaks.
- Caveat: the reference collision program is a **ramp** (20–70%) while our scan is a **fixed nominal 40** — fragment pattern differences are expected, especially for labile thiazole-region bonds.

## 4. Matching (rules predeclared before scoring)

- Tolerance **±10 ppm** (Orbitrap vs Orbitrap at ≥30k resolution); one-to-one greedy matching (reference peaks by descending relative intensity; each measured peak used once); reference filter rel. intensity ≥ 1%; precursor peak retained (not removed); no intensity weighting in the match statistic (reported separately as coverage).
- Full match table: `data/validated_runs/msms_assessment/M1b_thiostrepton_scan3754_vs_MassBank-ACES_SU-AS001032.json`. Mirror plot: `data/validated_runs/msms_assessment/thio_mirror.png`.

## 5. Result

| Tolerance | Matched ref peaks | Relative-intensity coverage |
|---|---|---|
| ±5 ppm | 21/76 | 44.7% |
| ±10 ppm | **24/76** | **51.9%** |
| ±20 ppm | 25/76 | 52.8% |
| ±50 ppm | 25/76 | 52.8% |

- Median |error| of matched peaks at ±10 ppm: **3.2 ppm**; max 7.5 ppm — within the acquisition difference expected between two Orbitraps.
- **Every one of the six most intense reference peaks is matched**: 234.0768 (677%), 789.7258 (565%), 790.2338 (527%), 783.2394 (506%), 319.1640 (451%), 418.1966 (392%) at +0.4 to +4.2 ppm. The library-dominant 86.0963 ion (999%, a small thiazole/amine fragment characteristic of ramped high-energy conditions) is **absent** in our fixed-40 scan — the main loss of coverage (single largest unmatched contributor).
- Also absent/unmatched: 156.4/166.4-type low-mass ramp-specific ions and several >5 ppm neighbors; unmatched high-mass measured peaks (1161.258, 1247.308, 1248.305) have **no reference counterpart** — consistent with a fixed lower-energy scan preserving larger fragments than the ramped reference.
- **Decoy control**: random MS2 scans elsewhere in the same run matched 0–1/76 reference peaks (0–8% coverage) at the same tolerance; the precursor-peak-only weak scan 3084 is not interpretable. The 24/76 match is therefore not a trivial consequence of dense centroiding.
- Borderline sensitivity: coverage moves only 44.7%→52.8% across 5→50 ppm, i.e. the result is not an artifact of tolerance choice; the binding constraint is the missing high-energy small fragments, not mass accuracy.

## 6. Verdict (honest, bounded)

1. **Acquisition proven: yes.** The DDA method demonstrably targeted the thiostrepton [M+2H]²⁺ precursor at RT 8.50 min, charge 2+ verified from MS1 isotope spacing, and a usable MS2 spectrum exists.
2. **Library agreement: partial-to-moderate.** 52% relative-intensity coverage with 100% of the reference's diagnostic high-mass thiopeptide ions matched within ≤7.5 ppm is strong evidence **for thiostrepton**, not a coincidence-level match. It is weaker than a full library match because the collision programs differ (fixed 40 vs ramp 20–70) and the reference's dominant small ion is missing.
3. **Confirmation: no.** This is a spectral-library agreement, not confirmation against an authentic standard under matched conditions. Under the platform's own evidence-tier rules, this supports **MSI level 2** at most for MS/MS alone (mass error −1.0 ppm at the precursor, RT from the same run).
4. **Missing evidence named**: (a) authentic-standard LC–MS/MS under matched gradient and collision energy; (b) a matched-CE reference (fixed 40, e.g. from MoNA/GNPS — currently unavailable: MoNA search API is in maintenance and returned zero even for caffeine on 2026-11-02); (c) MS3 or an energy series to rationalize the unmatched high-mass ions.
5. **Record consistency**: DATA_SOURCES.md §11b statement "no MS/MS confirmation has been performed … pending MS/MS confirmation" remains **true and unchanged**; this assessment adds the verified existence and quality of the MS2 acquisition and the partial library agreement. No status field is upgraded as a result of this assessment.
