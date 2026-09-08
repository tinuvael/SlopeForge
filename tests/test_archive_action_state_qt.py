from types import ModuleType, SimpleNamespace
import sys

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QWidget

from app.icons.ui.ui_icons import ui_icon
from ui.header import Header
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class EntityPage(QWidget):
    edit_boundaries_requested = Signal(object)
    metadata_saved = Signal(object, object)
    related_assessment_requested = Signal(object, object)
    related_blast_event_requested = Signal(object, object, object)

    def __init__(self, entity, controller=None):
        super().__init__()
        self.area = entity
        self.blast_event = entity
        self.controller = controller


def _window(app, *, can_edit=True):
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window.context = SimpleNamespace(current_user=SimpleNamespace(id=7, can_edit=can_edit))
    window.header = Header(window.context)
    window.page_stack = QStackedWidget(window)
    window.block_page = EntityPage(SimpleNamespace())
    window.page_stack.addWidget(window.block_page)
    window.assessment_page = None
    window._guard_leave = lambda: True
    window.navigation_queries = SimpleNamespace(
        get_domain_context=lambda _id: SimpleNamespace(
            site_id=1, site_name="Project", domain_name="Domain"
        ),
        project_has_active_lines=lambda _id: True,
    )
    window._update_add = lambda: None
    return window


def _assert_archive_button(header, archived, *, enabled=True):
    assert header.archive_button.isEnabled() is enabled
    assert header.archive_button.text() == ("Restore" if archived else "Archive")
    expected = ui_icon("restore" if archived else "archive")
    actual_image = header.archive_button.icon().pixmap(24, 24).toImage()
    expected_image = expected.pixmap(24, 24).toImage()
    assert actual_image == expected_image


@pytest.mark.parametrize("archived", [False, True], ids=["active", "archived"])
def test_block_open_synchronizes_archive_action(app, archived):
    window = _window(app)
    block = SimpleNamespace(is_archived=archived)
    window.block_page.open_block_id = lambda _id: setattr(window.block_page, "current_block", block)

    assert window.open_block_from_tree("BE-10", 2, 1)
    _assert_archive_button(window.header, archived)
    window.close()


@pytest.mark.parametrize("kind", ["contour", "area"])
@pytest.mark.parametrize("archived", [False, True], ids=["active", "archived"])
def test_area_and_contour_open_synchronize_archive_action(
    app, monkeypatch, kind, archived
):
    window = _window(app)
    entity = SimpleNamespace(is_archived=archived)
    module_name = (
        "ui.pages.contour_event_page" if kind == "contour"
        else "ui.pages.assessment_area_page"
    )
    class Page(EntityPage):
        def __init__(self, *_args): super().__init__(entity)
    module = ModuleType(module_name)
    setattr(module, "ContourEventPage" if kind == "contour" else "AssessmentAreaPage", Page)
    monkeypatch.setitem(sys.modules, module_name, module)
    wall_installs = []
    wall_module = ModuleType("ui.pages.wall_conformance_install")
    wall_module.install_wall_conformance_tab = lambda page: wall_installs.append(page)
    monkeypatch.setitem(sys.modules, "ui.pages.wall_conformance_install", wall_module)
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.critical",
        lambda *_args: pytest.fail("successful page composition must not open a modal error"),
    )

    if kind == "contour":
        assert window.open_contour_from_tree("C-1", 2, 1, "Domain")
    else:
        assert window.open_area_from_tree("A-1", 2, 1, "Domain")
        assert wall_installs == [window.area_page]
    _assert_archive_button(window.header, archived)
    window.close()


def test_navigation_clears_stale_restore_state_and_respects_permissions(app):
    window = _window(app)
    window.header.set_archive_context(True, True)
    _assert_archive_button(window.header, True)

    window._set_context(1, "Project")
    _assert_archive_button(window.header, False, enabled=False)
    window.header.set_archive_context(True, True)
    window._set_context(1, "Project", 2, "Domain")
    _assert_archive_button(window.header, False, enabled=False)

    viewer = _window(app, can_edit=False)
    viewer.header.set_archive_context(True, True)
    _assert_archive_button(viewer.header, True, enabled=False)
    window.close(); viewer.close()


@pytest.mark.parametrize("kind", ["assessment", "contour"])
@pytest.mark.parametrize("initial", [False, True], ids=["archive", "restore"])
def test_successful_archive_command_reopens_with_new_header_state(
    app, monkeypatch, kind, initial
):
    window = _window(app)
    entity = SimpleNamespace(id="E-1", name="West", is_archived=initial)
    controller = SimpleNamespace()
    method = "set_assessment_area_archived" if kind == "assessment" else "set_contour_event_archived"
    setattr(controller, method, lambda item, value: setattr(item, "is_archived", value))
    page = SimpleNamespace(controller=controller)
    if kind == "assessment":
        page.area = entity; window.area_page = page
        window.selected_assessment_area_id = entity.id; window.selected_contour_event_id = None
        reopen = "open_area_from_tree"
    else:
        page.blast_event = entity; window.contour_page = page
        window.selected_assessment_area_id = None; window.selected_contour_event_id = entity.id
        reopen = "open_contour_from_tree"
    window.selected_block_id = None
    window.selected_domain_id = 2; window.selected_site_id = 1; window.selected_domain_name = "Domain"
    window.refresh_project_data = lambda: None
    setattr(window, reopen, lambda *_args: window.header.set_archive_context(True, entity.is_archived))
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_args: QtWidgets.QMessageBox.StandardButton.Yes)

    window.header.set_archive_context(True, initial)
    window._archive_selected()
    _assert_archive_button(window.header, not initial)
    window.close()


@pytest.mark.parametrize("initial", [False, True], ids=["archive", "restore"])
def test_successful_block_archive_command_reopens_with_new_header_state(
    app, monkeypatch, initial
):
    window = _window(app)
    block = SimpleNamespace(
        id="BE-P-10", domain_id=2, site_id=1, block_number="P-10",
        is_archived=initial, domain_version=3,
    )
    window.block_page.current_block = block
    window.block_page.entity_controller = SimpleNamespace(expected_version=3)
    window.block_page.block_service = SimpleNamespace(
        set_archived=lambda event_id, archived, user, expected_version: setattr(block, "is_archived", archived)
    )
    window.selected_block_id = block.id
    window.selected_assessment_area_id = None; window.selected_contour_event_id = None
    window.refresh_project_data = lambda: None
    window.open_block_from_tree = lambda *_args: window.header.set_archive_context(True, block.is_archived)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_args: QtWidgets.QMessageBox.StandardButton.Yes)

    window.header.set_archive_context(True, initial)
    window._archive_selected()
    _assert_archive_button(window.header, not initial)
    window.close()


@pytest.mark.parametrize("kind", ["assessment", "contour"])
def test_failed_archive_command_preserves_page_and_header(app, monkeypatch, kind):
    window = _window(app)
    entity = SimpleNamespace(id="E-1", name="West", is_archived=False)
    def fail(*_args): raise RuntimeError("database unavailable")
    controller = SimpleNamespace()
    method = "set_assessment_area_archived" if kind == "assessment" else "set_contour_event_archived"
    setattr(controller, method, fail)
    page = SimpleNamespace(controller=controller)
    if kind == "assessment":
        page.area = entity; window.area_page = page
        window.selected_assessment_area_id = entity.id; window.selected_contour_event_id = None
    else:
        page.blast_event = entity; window.contour_page = page
        window.selected_assessment_area_id = None; window.selected_contour_event_id = entity.id
    window.selected_block_id = None
    window.header.set_archive_context(True, False)
    window.refresh_project_data = lambda: pytest.fail("failure must not refresh")
    warnings = []
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_args: QtWidgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *args: warnings.append(args))

    window._archive_selected()
    assert warnings and "database unavailable" in warnings[0][-1]
    assert entity.is_archived is False
    _assert_archive_button(window.header, False)
    assert (window.area_page if kind == "assessment" else window.contour_page) is page
    window.close()
