# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

# Fail the release build before Analysis if the interpreter selected by the
# Windows build does not have a usable pywin32 Credential Manager runtime.
# The application imports this dynamically, so PyInstaller cannot infer it.
import pythoncom  # noqa: F401
import pywintypes  # noqa: F401
import win32cred  # noqa: F401
import win32timezone  # noqa: F401


root = Path(SPEC).resolve().parent

datas = [
    (str(root / "app" / "icons"), "app/icons"),
    (str(root / "alembic.ini"), "."),
    (str(root / "alembic"), "alembic"),
]

hiddenimports = [
    "logging.config",
    # The updater reads saved PostgreSQL passwords from Windows Credential
    # Manager. Treat this as a hard packaging dependency so release builds do
    # not silently produce an executable that cannot use existing profiles.
    "win32cred",
    "win32timezone",
    "pythoncom",
    "pywintypes",
]


a = Analysis(
    [str(root / "updater_main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SlopeForgeUpdater",
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
    icon=str(root / "app" / "icons" / "slopeforge_icon.ico"),
)
