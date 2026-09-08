from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from app import config
from app.resources import resource_path


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets", reason="Qt libraries are not available in this environment", exc_type=ImportError)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_app_metadata_is_present_and_version_is_release_1() -> None:
    assert config.APP_NAME
    assert config.APP_VERSION == "1.1.0"
    assert config.APP_VERSION_DISPLAY == "1.1"
    assert config.APP_AUTHOR
    assert config.APP_COPYRIGHT
    assert "All rights reserved" not in config.APP_COPYRIGHT
    assert re.fullmatch(r"\d+\.\d+\.\d+", config.APP_VERSION)


def test_resource_path_is_safe_for_existing_and_missing_assets() -> None:
    icon_path = resource_path(config.APP_ICON_PATH)
    assert icon_path is not None
    assert icon_path.exists()
    assert resource_path("does/not/exist.png") is None


def test_runtime_ui_does_not_hardcode_release_semver() -> None:
    current_version = config.APP_VERSION
    for path in [Path("app/splash.py"), Path("ui/about_dialog.py"), Path("ui/settings_dialog.py")]:
        assert current_version not in path.read_text(encoding="utf-8")


def test_splash_and_about_can_be_created_offscreen(qt_app) -> None:
    from app.qt import apply_application_icon
    from app.splash import SlopeForgeSplash
    from ui.about_dialog import AboutDialog

    apply_application_icon(qt_app)
    splash = SlopeForgeSplash()
    splash.show_status("Initialization should stay hidden")
    about = AboutDialog()
    assert splash.pixmap().isNull() is False
    assert splash.message() == ""
    assert config.APP_NAME in about.windowTitle()
    splash.close()
    about.close()


def test_splash_has_no_opaque_footer_or_startup_status_text():
    source = Path("app/splash.py").read_text(encoding="utf-8")
    assert "painter.fillRect" not in source
    assert "self.showMessage" not in source
    assert "APP_COPYRIGHT" in source
    assert 'f"version {APP_VERSION_DISPLAY}"' in source
    assert "minimum_ms: int = 2000" in source
