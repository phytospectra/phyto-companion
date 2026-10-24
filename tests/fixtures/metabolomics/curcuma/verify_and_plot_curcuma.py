import numpy as np, glob, os, csv
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "Curcuma_longa_NMR_dataset")


def parse_jcamp(path):
    params = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("##$"):
                key, _, val = line[3:].partition("=")
                val = val.strip()
                if val.startswith("<") and val.endswith(">"):
                    val = val[1:-1]
                else:
                    try:
                        val = float(val)
                        if val.is_integer():
                            val = int(val)
                    except ValueError:
                        pass
                params[key.strip()] = val
    return params


def load_sample(exp_dir):
    procs = parse_jcamp(os.path.join(exp_dir, "pdata", "1", "procs"))
    si = int(procs["SI"]); nc = int(procs["NC_proc"])
    offset = float(procs["OFFSET"]); sw_p = float(procs["SW_p"]); sf = float(procs["SF"])
    raw = np.fromfile(os.path.join(exp_dir, "pdata", "1", "1r"), dtype=np.int32)
    assert len(raw) == si
    intensity = raw.astype(np.float64) * (2.0 ** nc)
    sw_ppm = sw_p / sf
    ppm = offset - np.arange(si) * (sw_ppm / si)
    return ppm, intensity


sheet = {}
with open(os.path.join(HERE, "sample_sheet_Curcuma_longa.csv")) as f:
    for row in csv.DictReader(f):
        sheet[row["sample_id"]] = row

by_day = {}
ppm_ref = None
for d in sorted(glob.glob(os.path.join(DATASET, "*", "1"))):
    sample_id = os.path.basename(os.path.dirname(d))
    ppm, inten = load_sample(d)
    ppm_ref = ppm
    day = int(sheet[sample_id]["storage_day"])
    by_day.setdefault(day, []).append(inten)

days = sorted(by_day.keys())
cmap = plt.cm.viridis(np.linspace(0, 1, len(days)))

fig, axes = plt.subplots(2, 1, figsize=(9, 8))
ax = axes[0]
for day, col in zip(days, cmap):
    for s in by_day[day]:
        ax.plot(ppm_ref, s, color=col, alpha=0.7, lw=0.8)
ax.set_xlim(12, 0); ax.set_xlabel("ppm")
ax.set_title("Curcuma longa: full spectra by storage day (dark=day0 -> yellow=day28)")

ax2 = axes[1]
mask = (ppm_ref > 3.6) & (ppm_ref < 7.8)
for day, col in zip(days, cmap):
    for s in by_day[day]:
        ax2.plot(ppm_ref[mask], s[mask], color=col, alpha=0.8, lw=1.0)
for shift, lab in [(3.85, "Curcumin OCH3"), (5.95, "Curcumin enol-CH"), (6.75, "Curcumin ArH"),
                    (6.95, "Demethoxycurcumin"), (7.15, "Bisdemethoxycurcumin"), (7.55, "Oxidation product")]:
    ax2.axvline(shift, color="k", ls="--", lw=0.4)
ax2.set_xlim(7.8, 3.6); ax2.set_xlabel("ppm")
ax2.set_title("Zoom: curcuminoid + degradation marker region")

plt.tight_layout()
out = os.path.join(HERE, "figures_Curcuma", "curcuma_storage_trend.png")
os.makedirs(os.path.dirname(out), exist_ok=True)
plt.savefig(out, dpi=150)
print("Saved:", out)

idx_ocm3 = np.argmin(np.abs(ppm_ref - 3.85))
idx_ox   = np.argmin(np.abs(ppm_ref - 7.55))
print("Day  CurcuminOCH3_mean  OxidationMarker_mean")
for day in days:
    v1 = np.mean([s[idx_ocm3] for s in by_day[day]])
    v2 = np.mean([s[idx_ox] for s in by_day[day]])
    print(f"{day:4d} {v1:18.3f} {v2:20.3f}")
