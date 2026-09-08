from pathlib import Path


def test_release_build_packages_updater_next_to_main_application() -> None:
    script = Path("build_release.bat").read_text(encoding="utf-8")
    assert "SlopeForgeUpdater.spec" in script
    assert 'dist\\SlopeForgeUpdater.exe' in script
    assert 'dist\\SlopeForge\\SlopeForgeUpdater.exe' in script


def test_release_build_requires_python_314() -> None:
    source = Path("build_release.bat").read_text(encoding="utf-8")
    assert 'set "PYTHON_COMMAND=py -3.14"' in source
    assert "sys.version_info[:2] == (3, 14)" in source
    assert "Python 3.14" in source


def test_windows_workflow_uses_python_314() -> None:
    source = Path(".github/workflows/windows-build.yml").read_text(encoding="utf-8")
    assert source.count('python-version: "3.14"') == 3


def test_payload_validator_requires_updater_executable() -> None:
    source = Path("tools/validate_windows_payload.py").read_text(encoding="utf-8")
    assert 'Path("SlopeForgeUpdater.exe")' in source


def test_installer_exposes_updater_start_menu_shortcut() -> None:
    source = Path("installer/SlopeForge.iss").read_text(encoding="utf-8")
    assert '#define UpdaterExeName "SlopeForgeUpdater.exe"' in source
    assert 'Name: "{group}\\SlopeForge Updater"' in source


def test_updater_pyinstaller_bundle_contains_alembic_graph() -> None:
    source = Path("SlopeForgeUpdater.spec").read_text(encoding="utf-8")
    assert 'updater_main.py' in source
    assert 'alembic.ini' in source
    assert 'root / "alembic"' in source
    assert 'name="SlopeForgeUpdater"' in source
    assert '"win32cred"' in source
    assert '"win32timezone"' in source
    assert '"pythoncom"' in source
    assert '"pywintypes"' in source
    assert "import win32cred" in source
    assert "import win32timezone" in source
    assert "import pythoncom" in source
    assert "import pywintypes" in source
    assert "try:" not in source
    assert "except ImportError" not in source


def test_windows_build_smoke_tests_updater_credential_manager_runtime() -> None:
    source = Path(".github/workflows/windows-build.yml").read_text(encoding="utf-8")
    assert '"--credential-smoke-test"' in source
    assert "Credential Manager smoke test" in source


def test_updater_exposes_credential_manager_smoke_test() -> None:
    source = Path("updater_main.py").read_text(encoding="utf-8")
    assert '"--credential-smoke-test"' in source
    assert "credential_runtime_smoke_test()" in source
