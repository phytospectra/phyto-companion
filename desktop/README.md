# Phytospectra Desktop

The installable companion app: point it at a folder that holds your NMR
or LC-MS data, and it runs the full Phytospectra analysis on the
Phytospectra cloud, then writes a complete report package back into the
folder you choose:

- `report_<sample>.docx` — the Q1-style analysis report
- `report_<sample>_bundle.zip` — the report plus tables, input inventory
  and the provenance record (run id, citation, checksums)
- `input_inventory.json` — exactly what the scanner found and skipped

## The workflow

1. **Sign in** — the app opens the account page at
   `https://app.phytospectra.com` in your browser; sign in there and the
   browser hands the session back to the app (loopback handshake on
   `127.0.0.1:8765`).
2. **Pick the data folder** — Bruker experiment directories (with
   `acqus` + `pdata/`), zipped Bruker exports, and `mzML`/`mzXML`/`mgf`/
   `.d`/`.raw` files are recognized automatically; one sample per
   subfolder.
3. **Run analysis** — the folder is reduced to an inventory + peak/metadata
   payload, the analysis runs on Google Cloud (Cloud Run + Firestore),
   checkpoints are applied as configured for your account, and the DOCX
   report + zipped bundle are written to the output folder (defaults to
   the data folder).
4. **Buy tokens** — the "Buy tokens" button opens the billing page in
   your browser (Stripe checkout lives on the web side; the desktop app
   never sees card data).

## Layout

| Module | Role |
|---|---|
| `phytospectra_desktop/__main__.py` | Tkinter GUI: folder pickers, progress, sign-in |
| `phytospectra_desktop/ingest.py` | Folder scanner (Bruker dirs, MS files, zips) |
| `phytospectra_desktop/api.py` | Orchestrator API client (submit / poll / approve / share / citation) |
| `phytospectra_desktop/pipeline.py` | End-to-end run + DOCX/zip report rendering |
| `phytospectra_desktop/auth_desktop.py` | Browser sign-in handshake + purchase deep links |
| `phytospectra_desktop/credentials.py` | Token storage under the user's home dir |
| `report_engine/` | Vendored `nmr_preproc` report engine (populated at build time) |

## Running from source

```bash
cd desktop
pip install python-docx numpy scipy matplotlib
python -m phytospectra_desktop
```

`PHYTO_API_BASE` / `PHYTO_WEB_BASE` environment variables (or a
`config.json` next to the executable with `{"api_base": ..., "web_base":
...}`) override the production endpoints — used for staging and tests.

## Building installers

```bash
scripts/build_desktop.sh            # one-folder bundle in desktop/dist/
scripts/build_desktop.sh --onefile  # single executable
```

macOS: the bundle can be wrapped into a `.dmg` (`hdiutil create ...`).
Windows: run the same script with PyInstaller to produce `Phytospectra.exe`,
then wrap with Inno Setup / NSIS. GitHub Actions workflows for both
platforms live in `.github/workflows/desktop-release.yml`.

The app is distributed for download at **https://pytospectra.com**; the
download links are managed by the site's hosting configuration
(`infrastructure/download_page/`).
