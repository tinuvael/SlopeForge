import pytest
from PySide6.QtWidgets import QApplication

try:
    from app.icons.ui.ui_icons import ui_icon
except ImportError as exc:
    pytest.skip(f"Qt runtime unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("name,variant",[("domain","neutral"),("block","neutral"),("assessment-area","neutral"),("analytics","neutral"),("success","semantic")])
def test_committed_ui_icon_loads(app, name, variant):
    icon = ui_icon(name, variant)

    assert not icon.isNull()
    assert not icon.pixmap(24, 24).isNull()

def test_missing_icon_fails_clearly():
    with pytest.raises(FileNotFoundError): ui_icon("not-a-real-icon")
