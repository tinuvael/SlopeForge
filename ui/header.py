from app.localization import tr
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu, QPushButton, QWidget

from app.icons.ui.ui_icons import ui_icon
from ui.settings_dialog import SettingsDialog
from ui.widgets.design_system import high_contrast_icon, set_button_role


class SearchLineEdit(QLineEdit):
    """Header-only search behavior; Escape remains untouched elsewhere."""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)


class Header(QWidget):
    catalogue_changed = Signal()
    add_project_requested = Signal()
    add_domain_requested = Signal()
    add_blast_event_requested = Signal()
    add_assessment_area_requested = Signal()
    archive_requested = Signal()
    analysis_requested = Signal()
    report_requested = Signal()
    navigation_toggle_requested = Signal()
    switch_server_requested = Signal()

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.setFixedHeight(60)
        layout = QHBoxLayout(self)

        self.navigation_button = QPushButton()
        self.navigation_button.setObjectName("navigationToggleButton")
        self.navigation_button.setFixedSize(36, 32)
        self.navigation_button.clicked.connect(self.navigation_toggle_requested)
        self.set_navigation_visible(True)
        self.navigation_button.hide()

        self.add_button = QPushButton(tr("Add"))
        self.add_menu = QMenu(self)
        self.add_project_action = self.add_menu.addAction(tr("Add project"))
        self.add_domain_action = self.add_menu.addAction(tr("Add domain"))
        self.add_blast_event_action = self.add_menu.addAction(tr("Add blast event"))
        self.add_assessment_area_action = self.add_menu.addAction(
            tr("Add assessment area")
        )
        self.add_button.setIcon(high_contrast_icon(ui_icon("add")))
        self.add_project_action.setIcon(ui_icon("folder-open"))
        self.add_domain_action.setIcon(ui_icon("domain"))
        self.add_blast_event_action.setIcon(ui_icon("blast-blocks"))
        self.add_assessment_area_action.setIcon(ui_icon("assessment-area"))
        self.add_button.setToolTip(
            tr("Create a project, domain, blast event, or assessment area")
        )
        self.add_project_action.triggered.connect(self.add_project_requested)
        self.add_domain_action.triggered.connect(self.add_domain_requested)
        self.add_blast_event_action.triggered.connect(self.add_blast_event_requested)
        self.add_assessment_area_action.triggered.connect(
            self.add_assessment_area_requested
        )
        self.add_button.setMenu(self.add_menu)

        self.archive_button = QPushButton(tr("Archive"))
        self.archive_button.setEnabled(False)
        self.archive_button.clicked.connect(self.archive_requested)
        set_button_role(self.add_button, "primary")
        set_button_role(self.archive_button, "secondary")
        self.archive_button.setIcon(ui_icon("archive"))

        self.search = SearchLineEdit()
        self.search.setObjectName("GlobalSearch")
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(tr("Search projects, domains and entities…"))
        self.search.setMinimumWidth(240)
        self.search.setMaximumWidth(380)

        self.analysis_button = QPushButton(tr("Analysis"))
        self.analysis_button.setObjectName("analysisModeButton")
        self.analysis_button.setCheckable(True)
        self.analysis_button.setIcon(ui_icon("analytics"))
        self.analysis_button.clicked.connect(self.analysis_requested)
        self.report_button = QPushButton(tr("Report"))
        self.report_button.setIcon(ui_icon("report","blue"))
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.report_requested)

        server_name = (
            getattr(context, "connection_profile_name", "") or tr("Server")
        )
        self.server_button = QPushButton(server_name)
        self.server_button.setObjectName("ServerProfileButton")
        self.server_button.setToolTip(
            f"{tr('Current server')}: {server_name}\n{tr('Switch server…')}"
        )
        self.server_button.clicked.connect(self._switch_server)
        self.server_button.setEnabled(
            getattr(context, "runtime_control", None) is not None
        )

        self.settings = QPushButton(tr("Settings"))
        self.settings.setIcon(ui_icon("settings"))
        self.settings.clicked.connect(self.open_settings)
        for button in (
            self.analysis_button,
            self.report_button,
            self.server_button,
            self.settings,
        ):
            set_button_role(button, "secondary")

        self.search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.search_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.search_shortcut.activated.connect(self.focus_search)

        layout.addWidget(self.navigation_button)
        layout.addWidget(self.add_button)
        layout.addWidget(self.archive_button)
        layout.addStretch()
        layout.addWidget(self.search)
        layout.addStretch()
        layout.addWidget(self.analysis_button)
        layout.addWidget(self.report_button)
        layout.addWidget(self.server_button)
        layout.addWidget(self.settings)
        self.update_add_availability(False, False, False)

    def _switch_server(self):
        control = getattr(self.context, "runtime_control", None)
        if control is not None:
            control.request_switch(parent=self.window())
        self.switch_server_requested.emit()

    def update_add_availability(self, has_site, has_domain, has_active_dataset):
        self.report_button.setEnabled(has_site)
        editable = self.context.current_user.can_edit
        self.add_button.setEnabled(editable)
        self.add_project_action.setEnabled(editable)
        self.add_domain_action.setEnabled(editable and has_site)
        self.add_blast_event_action.setEnabled(editable and has_domain)
        self.add_assessment_area_action.setEnabled(
            editable and has_domain and has_active_dataset
        )

    def set_archive_context(self, enabled, archived=False):
        self.archive_button.setEnabled(
            self.context.current_user.can_edit and enabled
        )
        self.archive_button.setText(tr("Restore") if archived else tr("Archive"))
        self.archive_button.setIcon(ui_icon("restore" if archived else "archive"))

    def set_navigation_visible(self, visible):
        self.navigation_button.setIcon(ui_icon("hide" if visible else "eye"))
        self.navigation_button.setToolTip(
            tr("Hide navigation") if visible else tr("Show navigation")
        )
        self.navigation_button.setAccessibleName(self.navigation_button.toolTip())

    def set_analysis_active(self, active: bool):
        self.analysis_button.setChecked(active)
        self.analysis_button.setToolTip(
            tr("Return to project workspace") if active else tr("Open Analysis workspace")
        )
        self.analysis_button.setAccessibleName(self.analysis_button.toolTip())
        if active:
            for control in (self.add_button, self.archive_button, self.report_button, self.search):
                control.setEnabled(False)
        else:
            self.search.setEnabled(True)

    def open_settings(self):
        dialog = SettingsDialog(self.context, self)
        dialog.catalogue_changed.connect(self.catalogue_changed)
        dialog.exec()
        profile_id = dialog.requested_switch_profile_id
        if not profile_id:
            return
        control = getattr(self.context, "runtime_control", None)
        if control is not None:
            control.request_switch(profile_id, parent=self.window())

    def focus_search(self):
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Find):
            self.focus_search()
            event.accept()
            return
        super().keyPressEvent(event)
