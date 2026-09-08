from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsView, QToolTip
from domain.geometry.types import PlanPoint


class PlanView(QGraphicsView):
    cursor_moved = Signal(float, float)
    escape_requested = Signal()
    workflow_key_requested = Signal(str)
    scene_clicked = Signal(float, float)
    scene_double_clicked = Signal(float, float)

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self._drawing_mode = False
        self._middle_panning = False
        self._pan_view_position = None
        self._direction_annotation = None
        self._skipped_annotations = ()

    def set_direction_annotation(self, scene_point, direction_xy, color: QColor | None = None):
        """Render a selected-profile +U marker in viewport pixels, not scene space."""
        self._direction_annotation = (
            (scene_point, direction_xy, QColor(color))
            if scene_point is not None and direction_xy is not None
            else None
        )
        self.viewport().update()

    def set_skipped_annotations(self, annotations) -> None:
        """Render skipped-station marks in viewport space, outside scene bounds."""
        self._skipped_annotations = tuple(annotations)
        self.viewport().update()

    def direction_annotation_screen_points(self):
        """Return the cosmetic arrow endpoints in viewport pixels for drawing/tests."""
        if self._direction_annotation is None:
            return None
        scene_point, (nx, ny), _color = self._direction_annotation
        start = self.mapFromScene(scene_point)
        # Domain y is inverted in the plan scene.  Project one physical metre
        # through the current view transform to retain the actual +U heading.
        reference = self.mapFromScene(QPointF(scene_point.x() + nx, scene_point.y() - ny))
        dx, dy = reference.x() - start.x(), reference.y() - start.y()
        length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        scale = 22.0 / length
        return (
            QPointF(start.x(), start.y()),
            QPointF(start.x() + dx * scale, start.y() + dy * scale),
        )

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        self._draw_skipped_annotations(painter)
        if self._direction_annotation is None:
            return
        _scene_point, _direction_xy, color = self._direction_annotation
        start, end = self.direction_annotation_screen_points()
        dx, dy = end.x() - start.x(), end.y() - start.y()
        length = max((dx * dx + dy * dy) ** 0.5, 1e-9)
        normal_x, normal_y = -dy / length, dx / length
        painter.save()
        painter.resetTransform()
        pen = QPen(color, 2.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(start, end)
        head = 6.0
        painter.drawLine(
            end,
            QPointF(
                end.x() - dx / length * head + normal_x * head * 0.55,
                end.y() - dy / length * head + normal_y * head * 0.55,
            ),
        )
        painter.drawLine(
            end,
            QPointF(
                end.x() - dx / length * head - normal_x * head * 0.55,
                end.y() - dy / length * head - normal_y * head * 0.55,
            ),
        )
        painter.drawText(end + QPointF(5, -4), "+U")
        painter.restore()

    def _draw_skipped_annotations(self, painter) -> None:
        if not self._skipped_annotations:
            return
        painter.save()
        painter.resetTransform()
        for scene_point, _tooltip, color in self._skipped_annotations:
            point = self.mapFromScene(scene_point)
            arm = 5.0
            pen = QPen(color, 2.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(point.x() - arm, point.y() - arm),
                QPointF(point.x() + arm, point.y() + arm),
            )
            painter.drawLine(
                QPointF(point.x() - arm, point.y() + arm),
                QPointF(point.x() + arm, point.y() - arm),
            )
        painter.restore()

    def _update_skipped_annotation_tooltip(self, event) -> None:
        cursor = event.position().toPoint()
        for scene_point, tooltip, _color in self._skipped_annotations:
            point = self.mapFromScene(scene_point)
            if (point - cursor).manhattanLength() <= 9:
                QToolTip.showText(event.globalPosition().toPoint(), tooltip, self.viewport())
                return
        QToolTip.hideText()

    def set_polygon_drawing_mode(self, enabled: bool):
        self._drawing_mode = enabled
        # Left click belongs to the boundary while drawing.  Middle-drag is
        # always available for navigation and is handled explicitly below.
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag)
        if enabled:
            self.setFocus()

    def set_polygon_refinement_mode(self):
        """Disable viewport panning while movable scene handles are active."""
        self._drawing_mode = False
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setFocus()

    @staticmethod
    def scene_to_domain(point):
        return PlanPoint(point.x(), -point.y())

    def wheelEvent(self, event):
        scale = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(scale, scale)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.escape_requested.emit()
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.workflow_key_requested.emit("enter")
        elif key == Qt.Key.Key_Backspace:
            self.workflow_key_requested.emit("back")
        elif key == Qt.Key.Key_Delete:
            self.workflow_key_requested.emit("delete")
        elif key in {Qt.Key.Key_Up, Qt.Key.Key_Left}:
            self.workflow_key_requested.emit("candidate_previous")
        elif key in {Qt.Key.Key_Down, Qt.Key.Key_Right}:
            self.workflow_key_requested.emit("candidate_next")
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mouseMoveEvent(self, event):
        self._update_skipped_annotation_tooltip(event)
        if self._middle_panning and self._pan_view_position is not None:
            current = event.position().toPoint()
            delta = current - self._pan_view_position
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_view_position = current
            event.accept()
            return
        pos = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(pos.x(), -pos.y())
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_panning = True
            self._pan_view_position = event.position().toPoint()
            self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return
        if self._drawing_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.scene_to_domain(self.mapToScene(event.position().toPoint()))
            self.scene_clicked.emit(point.x, point.y)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_panning:
            self._middle_panning = False
            self._pan_view_position = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._drawing_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.scene_to_domain(self.mapToScene(event.position().toPoint()))
            self.scene_double_clicked.emit(point.x, point.y)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def fit_to_extent(self):
        rect = self.scene().itemsBoundingRect()
        if not rect.isNull():
            margin = max(min(max(rect.width(), rect.height()) * 0.03, 100.0), 1.0)
            self.fit_to_rect(rect.adjusted(-margin, -margin, margin, margin))

    def fit_to_rect(self, rect):
        """Use one aspect-ratio-preserving framing path for every plan view."""
        if not rect.isNull():
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
