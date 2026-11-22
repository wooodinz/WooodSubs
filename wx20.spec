# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project_root = Path(SPECPATH)
icon_path = project_root / "assets" / "wooodsubs.ico"

datas = [
    ("ui", "ui"),
    ("assets", "assets"),
    ("backend/resolve_tools/subject_matte.py", "runtime_scripts"),
    ("backend/resolve_tools/whisperx_speech.py", "runtime_scripts"),
]
binaries = []
hiddenimports = [
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "clr",
]

for pkg in ("webview", "pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app.py"],
    pathex=[str(project_root), str(project_root / "backend" / "resolve_tools")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["caption_editor", "reference_caption_style"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "numpy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WooodSubs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
