# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

project_root = Path.cwd()
icon_path = project_root / "app" / "icons" / "slopeforge_icon.ico"

# Keep runtime assets available for resource_path() both from source and PyInstaller.
datas = [
    (str(project_root / "app" / "icons"), "app/icons"),
    (str(project_root / "translations"), "translations"),
    (str(project_root / "alembic.ini"), "."),
    (str(project_root / "alembic"), "alembic"),
]

# alembic/env.py and the optional Datamine adapter both contain runtime/dynamic
# imports that PyInstaller cannot discover from main.py's static import graph.
hiddenimports = [
    "logging.config",
    # Analysis distribution and comparison views use the PySide6 Addons
    # QtCharts module. Keep the Windows package contract explicit.
    "PySide6.QtCharts",
]
if sys.platform == "win32":
    hiddenimports.extend([
        "win32com.client",
        "pythoncom",
        "pywintypes",
    ])

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SlopeForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is optional; PyInstaller builds must not depend on it being installed.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon_path) if icon_path.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SlopeForge",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SlopeForge.app",
        icon=str(project_root / "app" / "icons" / "slopeforge_icon.icns"),
        bundle_identifier="com.tinuvael.slopeforge",
    )
