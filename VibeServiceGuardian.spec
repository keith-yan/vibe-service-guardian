# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

project_root = Path(SPECPATH).resolve()
hiddenimports = ["psutil"]
if sys.platform == "win32":
    hiddenimports.append("psutil._pswindows")
elif sys.platform == "darwin":
    hiddenimports.append("psutil._psosx")
elif sys.platform.startswith("linux"):
    hiddenimports.append("psutil._pslinux")

target_arch = os.environ.get("VSG_TARGET_ARCH") or None

a = Analysis(
    [str(project_root / "vsg" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "vsg" / "web"), "vsg/web"),
        (str(project_root / "vsg" / "catalog"), "vsg/catalog"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VibeServiceGuardian",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
)
