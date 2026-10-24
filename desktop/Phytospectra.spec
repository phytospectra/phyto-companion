# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Phytospectra Desktop.
#
# The report engine (nmr_preproc) is vendored at build time by
# scripts/build_desktop.sh so the frozen app can render the DOCX report
# and the zipped publication bundle without the full repository.

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = [
    "phytospectra_desktop",
    "nmr_preproc",
    "nmr_preproc.report",
    "scipy.ndimage",
    "matplotlib",
    "docx",
]

a = Analysis(
    ["__main__.py"],
    pathex=[".", "report_engine"],
    binaries=[],
    datas=[
        ("report_engine/nmr_preproc", "nmr_preproc"),
    ],
    hiddenimports=hidden + collect_submodules("phytospectra_desktop"),
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test", "unittest"],
    win_no_prefer_redirects=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Phytospectra",
    console=False,
    icon="assets/icon.icns" if os.path.exists("assets/icon.icns") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Phytospectra",
)
