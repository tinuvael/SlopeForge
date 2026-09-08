from __future__ import annotations

import sys

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from app.platform import set_windows_app_user_model_id
from app.credential_store import CredentialStoreError, credential_runtime_smoke_test
from app.qt import apply_application_icon
from ui.application_theme import initialize_application_theme
from ui.updater_window import SlopeForgeUpdaterWindow


class UpdaterMainWindow(SlopeForgeUpdaterWindow):
    """Keep the process alive while backup or schema maintenance is running."""

    def closeEvent(self, event) -> None:
        if self._busy:
            QMessageBox.warning(
                self,
                "Operation in progress",
                "Wait for the current backup or database operation to finish before closing SlopeForge Updater.",
            )
            event.ignore()
            return
        super().closeEvent(event)


def _polish_disabled_upgrade_action(window: UpdaterMainWindow) -> None:
    """Keep the gated primary action readable in both light and dark themes."""
    palette = window.palette()
    disabled = QPalette.ColorGroup.Disabled
    text = palette.color(disabled, QPalette.ColorRole.ButtonText).name()
    background = palette.color(disabled, QPalette.ColorRole.Button).name()
    border = palette.color(disabled, QPalette.ColorRole.Mid).name()
    window.upgrade_button.setStyleSheet(
        "QPushButton:disabled {"
        f"color: {text}; background: {background}; border: 1px solid {border};"
        "}"
    )


def _smoke_test_requested(argv: list[str] | None = None) -> bool:
    return "--smoke-test" in (argv if argv is not None else sys.argv[1:])


def _credential_smoke_test_requested(argv: list[str] | None = None) -> bool:
    return "--credential-smoke-test" in (argv if argv is not None else sys.argv[1:])


def main() -> int:
    if _credential_smoke_test_requested():
        try:
            credential_runtime_smoke_test()
        except CredentialStoreError:
            return 1
        return 0
    set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName("SlopeForge Updater")
    apply_application_icon(app)
    initialize_application_theme(app)
    window = UpdaterMainWindow()
    _polish_disabled_upgrade_action(window)
    window.show()

    if _smoke_test_requested():
        # Packaging smoke only: construct and render one event-loop pass, but do
        # not connect to PostgreSQL, create backups, or mutate user settings.
        app.processEvents()
        window.close()
        app.processEvents()
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
