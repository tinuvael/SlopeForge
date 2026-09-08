from __future__ import annotations

import logging

from PySide6.QtCore import QElapsedTimer, QRect, QThread, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .config import APP_COPYRIGHT, APP_NAME, APP_SPLASH_PATH, APP_VERSION_DISPLAY
from .qt import apply_window_icon
from .resources import resource_path

logger = logging.getLogger(__name__)


class SlopeForgeSplash(QSplashScreen):
    def __init__(self) -> None:
        pixmap = self._load_pixmap()
        super().__init__(
            pixmap,
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint,
        )
        apply_window_icon(self)
        self._timer = QElapsedTimer()
        self._timer.start()

    def _load_pixmap(self) -> QPixmap:
        splash_path = resource_path(APP_SPLASH_PATH)
        if splash_path is not None:
            pixmap = QPixmap(str(splash_path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    512,
                    512,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            logger.warning("Splash image could not be loaded: %s", splash_path)
        fallback = QPixmap(512, 512)
        fallback.fill(QColor("white"))
        return fallback

    def show_status(self, _message: str) -> None:
        """Keep startup responsive without drawing initialization messages."""
        QApplication.processEvents()

    @staticmethod
    def _draw_overlay_text(
        painter: QPainter,
        rect: QRect,
        text: str,
        *,
        alignment: Qt.AlignmentFlag,
        font: QFont,
    ) -> None:
        painter.setFont(font)
        shadow = rect.translated(1, 1)
        painter.setPen(QColor(0, 0, 0, 190))
        painter.drawText(shadow, alignment, text)
        painter.setPen(QColor("white"))
        painter.drawText(rect, alignment, text)

    def drawContents(self, painter: QPainter) -> None:  # noqa: N802 - Qt override
        """Draw only compact corner metadata over the original splash artwork."""
        rect = self.rect()

        self._draw_overlay_text(
            painter,
            QRect(3, rect.height() - 18, 150, 18),
            f"version {APP_VERSION_DISPLAY}",
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            font=QFont("Segoe UI", 8),
        )
        self._draw_overlay_text(
            painter,
            QRect(rect.width() - 243, rect.height() - 36, 240, 18),
            APP_NAME,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            font=QFont("Segoe UI", 9, QFont.Weight.Bold),
        )
        self._draw_overlay_text(
            painter,
            QRect(rect.width() - 323, rect.height() - 16, 320, 16),
            APP_COPYRIGHT,
            alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            font=QFont("Segoe UI", 8),
        )

    def close_with_fade(self, minimum_ms: int = 2000, fade_ms: int = 350) -> None:
        while self._timer.elapsed() < minimum_ms:
            QApplication.processEvents()
            QThread.msleep(20)
        steps = max(1, int(fade_ms / 25))
        for step in range(steps, -1, -1):
            self.setWindowOpacity(step / steps)
            QApplication.processEvents()
            QThread.msleep(max(1, int(fade_ms / steps)))
        self.close()
