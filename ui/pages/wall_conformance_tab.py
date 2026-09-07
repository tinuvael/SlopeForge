from __future__ import annotations

from math import atan2, degrees, hypot
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from app.use_case_factory import create_project_surface_dataset_service
from application.services.wall_conformance import (
    WallConformanceDiagnosticService,
    WallConformanceDiagnosticSettings,
)
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import WallAlignment, has_compatible_actual_wall_section
from domain.wall_conformance.models import SectionPoint
from ui.widgets.design_system import set_status_role
from ui.widgets.plan_view import PlanView


def _canvas_palette() -> dict[str, QColor]:
    app = QApplication.instance()
    if app is not None and app.property("slopeforgeTheme") == "dark":
        return {
            "background": QColor("#252c36"),
            "border": QColor("#4a5665"),
            "annotation_background": QColor(37, 44, 54, 232),
            "annotation_border": QColor(87, 103, 120, 190),
        }
    return {
        "background": QColor("#f8fafc"),
        "border": QColor("#d7dde6"),
        "annotation_background": QColor(248, 250, 252, 232),
        "annotation_border": QColor(183, 194, 207, 195),
    }


class WallCanvasHost(QFrame):
    """Shared framed viewer with a compact local header and drawing surface."""

    _CANVAS_INSET = 4
    _ANNOTATION_MARGIN = 10

    def __init__(self, canvas: QWidget, header=None, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.header = header
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas.setParent(self)
        if self.header is not None:
            self.header.setParent(self)

    def _layout_children(self) -> None:
        bounds = self.contentsRect()
        inset = self._CANVAS_INSET
        header_height = 0
        if self.header is not None:
            header_height = self.header.height() or self.header.sizeHint().height()
            self.header.setGeometry(
                bounds.left() + inset,
                bounds.top() + inset,
                max(1, bounds.width() - 2 * inset),
                header_height,
            )
        self.canvas.setGeometry(
            bounds.left() + inset,
            bounds.top() + inset + header_height,
            max(1, bounds.width() - 2 * inset),
            max(1, bounds.height() - 2 * inset - header_height),
        )

    def refresh_layout(self) -> None:
        self._layout_children()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = _canvas_palette()
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(colors["border"], 1))
        painter.setBrush(QBrush(colors["background"]))
        painter.drawRoundedRect(rect, 5, 5)
        if self.header is not None:
            divider_y = self._CANVAS_INSET + self.header.height()
            painter.setPen(QPen(colors["border"], 1))
            painter.drawLine(
                self._CANVAS_INSET,
                divider_y,
                max(self._CANVAS_INSET, self.width() - self._CANVAS_INSET),
                divider_y,
            )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_children()


class WallProfileSchedule(QFrame):
    """Right-hand technical schedule sharing the profile drawing background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    @staticmethod
    def background_color() -> QColor:
        return _canvas_palette()["background"]

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = _canvas_palette()
        painter.fillRect(self.rect(), self.background_color())
        painter.setPen(QPen(colors["border"], 1))
        painter.drawLine(0, 0, 0, max(0, self.height() - 1))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.update()


class WallProfileDrawingBody(QWidget):
    """One drawing body: metric section plot at left, schedule at right."""

    def __init__(self, profile_plot, schedule, parent=None):
        super().__init__(parent)
        self.profile_plot = profile_plot
        self.schedule = schedule
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.profile_plot, 1)
        layout.addWidget(self.schedule, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.schedule.isHidden():
            return
        preferred_width = min(320, max(260, self.width() // 3))
        # Keep a useful metric viewport where possible; only a very narrow host
        # lets the schedule contract below its normal drawing-schedule width.
        available_width = max(1, self.width() - 340)
        self.schedule.setFixedWidth(min(preferred_width, available_width))


class WallProfileDrawingHost(WallCanvasHost):
    """Profile viewer frame whose drawing body owns plot and schedule side by side."""

    def __init__(self, profile_plot, header, schedule, parent=None):
        self.profile_plot = profile_plot
        self.schedule = schedule
        self.drawing_body = WallProfileDrawingBody(profile_plot, schedule)
        super().__init__(self.drawing_body, header, parent)


class WallConformancePlanWidget(QWidget):
    profile_selected = Signal(int)
    alignment_completed = Signal(object)
    alignment_drawing_cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view = PlanView(self.scene)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.scene_clicked.connect(self._handle_scene_click)
        self.view.scene_double_clicked.connect(self._complete_draft_from_double_click)
        self.view.escape_requested.connect(self.cancel_alignment_drawing)
        self.view.workflow_key_requested.connect(self._handle_workflow_key)
        # The existing signal is used for both profile selection and alignment
        # drawing. Middle-drag remains available for navigation.
        self.view.set_polygon_drawing_mode(True)
        self._assessment_polygon = None
        self._wall_alignment = None
        self._draft_alignment_points = []
        self._drawing_alignment = False
        self._profiles = ()
        self._diagnostics = ()
        self._profile_items = []
        self._area_item = None
        self._alignment_item = None
        self._draft_alignment_item = None
        self._direction_annotation = None
        self._skipped_annotations = []
        self._selected_index = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.plan_header = QWidget()
        self.plan_header.setFixedHeight(58)
        header_layout = QVBoxLayout(self.plan_header)
        header_layout.setContentsMargins(8, 5, 8, 4)
        header_layout.setSpacing(1)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        title = QLabel(tr("Plan / transverse profiles"))
        title.setObjectName("EngineeringSectionTitle")
        bar.addWidget(title)
        bar.addStretch()
        fit = QPushButton(tr("Fit"))
        fit.clicked.connect(self._fit_to_engineering_extent)
        bar.addWidget(fit)
        header_layout.addLayout(bar)
        self.legend = QLabel()
        self.legend.setObjectName("MutedText")
        self.legend.setWordWrap(True)
        self.legend.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        header_layout.addWidget(self.legend)
        self.plan_canvas = WallCanvasHost(self.view, self.plan_header)
        root.addWidget(self.plan_canvas, 1)
        self._apply_theme()

    @staticmethod
    def _dark_theme() -> bool:
        app = QApplication.instance()
        return bool(app is not None and app.property("slopeforgeTheme") == "dark")

    @classmethod
    def _colors(cls):
        if cls._dark_theme():
            return {
                "background": QColor("#252c36"),
                "area": QColor("#5aa7e8"),
                "area_fill": QColor(90, 167, 232, 26),
                "alignment": QColor("#8bd39a"),
                "draft_alignment": QColor("#79b9ee"),
                "profile": QColor("#718096"),
                "selected": QColor("#f0c66e"),
                "skipped": QColor("#ef8b7d"),
            }
        return {
            "background": QColor("#f8fafc"),
            "area": QColor("#1261a0"),
            "area_fill": QColor(18, 97, 160, 22),
            "alignment": QColor("#2f855a"),
            "draft_alignment": QColor("#4f78a8"),
            "profile": QColor("#94a3b8"),
            "selected": QColor("#d97706"),
            "skipped": QColor("#c2410c"),
        }

    def _legend_html(self) -> str:
        colors = self._colors()
        def swatch(key):
            return f'<span style="color:{colors[key].name()}">&#9632;</span>'
        items = (
            f"{swatch('area')} {tr('Assessment area')} · "
            f"{swatch('alignment')} {tr('Wall Alignment')} · "
            f"{swatch('profile')} {tr('Profiles')} · "
            f"{swatch('selected')} {tr('Selected profile')}"
        )
        if self._skipped_annotations:
            items += f" · {swatch('skipped')} {tr('Skipped station')}"
        return items

    def _apply_theme(self):
        colors = self._colors()
        self.legend.setText(self._legend_html())
        self.scene.setBackgroundBrush(QBrush(colors["background"]))
        self.view.setBackgroundBrush(QBrush(colors["background"]))
        if self._area_item is not None:
            self._area_item.setPen(self._cosmetic_pen(colors["area"], 2.0))
            self._area_item.setBrush(QBrush(colors["area_fill"]))
        if self._alignment_item is not None:
            self._alignment_item.setPen(self._cosmetic_pen(colors["alignment"], 3.0))
        if self._draft_alignment_item is not None:
            self._draft_alignment_item.setPen(
                self._cosmetic_pen(colors["draft_alignment"], 2.0, Qt.PenStyle.DashLine)
            )
        for index, item in enumerate(self._profile_items):
            item.setPen(self._profile_pen(index == self._selected_index))
        self._update_selected_direction_annotation()
        self._skipped_annotations = [
            (point, tooltip, colors["skipped"])
            for point, tooltip, _color in self._skipped_annotations
        ]
        self.view.set_skipped_annotations(self._skipped_annotations)
        self.plan_canvas.update()
        self.view.viewport().update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._apply_theme()

    @classmethod
    def _profile_pen(cls, selected: bool) -> QPen:
        colors = cls._colors()
        pen = QPen(
            colors["selected"] if selected else colors["profile"],
            3.0 if selected else 1.0,
        )
        pen.setCosmetic(True)
        return pen

    @staticmethod
    def _cosmetic_pen(color: QColor, width: float, style=Qt.PenStyle.SolidLine) -> QPen:
        pen = QPen(color, width, style)
        pen.setCosmetic(True)
        return pen

    @staticmethod
    def _polygon_path(polygon: PlanPolygon) -> QPainterPath:
        path = QPainterPath()
        if not polygon.ring:
            return path
        path.moveTo(QPointF(polygon.ring[0].x, -polygon.ring[0].y))
        for point in polygon.ring[1:]:
            path.lineTo(QPointF(point.x, -point.y))
        path.closeSubpath()
        return path

    @staticmethod
    def _line_path(points) -> QPainterPath:
        path = QPainterPath()
        if not points:
            return path
        path.moveTo(QPointF(points[0].x, -points[0].y))
        for point in points[1:]:
            path.lineTo(QPointF(point.x, -point.y))
        return path

    def set_assessment_polygon(self, assessment_polygon: PlanPolygon) -> None:
        self._assessment_polygon = assessment_polygon
        self._refresh_scene()
        self._fit_to_engineering_extent()

    def set_wall_alignment(self, alignment: WallAlignment | None) -> None:
        self._wall_alignment = alignment
        self._draft_alignment_points = []
        self._drawing_alignment = False
        self._refresh_scene()

    @property
    def wall_alignment(self) -> WallAlignment | None:
        return self._wall_alignment

    @property
    def drawing_alignment(self) -> bool:
        return self._drawing_alignment

    def begin_alignment_drawing(self) -> None:
        self._draft_alignment_points = []
        self._drawing_alignment = True
        self.view.setFocus()
        self._refresh_scene()

    def cancel_alignment_drawing(self) -> None:
        if not self._drawing_alignment:
            return
        self._draft_alignment_points = []
        self._drawing_alignment = False
        self._refresh_scene()
        self.alignment_drawing_cancelled.emit()

    def _handle_workflow_key(self, key: str) -> None:
        if not self._drawing_alignment:
            return
        if key == "enter":
            self.complete_alignment_drawing()
        elif key == "back" and self._draft_alignment_points:
            self._draft_alignment_points.pop()
            self._refresh_scene()

    def _handle_scene_click(self, x: float, y: float) -> None:
        if self._drawing_alignment:
            self._draft_alignment_points.append(PlanPoint(x, y))
            self._refresh_scene()
            return
        self._select_nearest_profile(x, y)

    def _complete_draft_from_double_click(self, x: float, y: float) -> None:
        if not self._drawing_alignment:
            return
        point = PlanPoint(x, y)
        if not self._draft_alignment_points or self._draft_alignment_points[-1] != point:
            self._draft_alignment_points.append(point)
        self.complete_alignment_drawing()

    def complete_alignment_drawing(self) -> WallAlignment | None:
        if not self._drawing_alignment:
            return self._wall_alignment
        try:
            alignment = WallAlignment(tuple(self._draft_alignment_points))
        except ValueError:
            return None
        self._wall_alignment = alignment
        self._draft_alignment_points = []
        self._drawing_alignment = False
        self._refresh_scene()
        self.alignment_completed.emit(alignment)
        return alignment

    def set_result(self, diagnostic_result) -> None:
        self._profiles = diagnostic_result.profile_sections.profiles
        self._selected_index = -1
        self._diagnostics = tuple(getattr(diagnostic_result, "diagnostics", ()) or ())
        self._refresh_scene()
        self._fit_to_engineering_extent()

    def _refresh_scene(self) -> None:
        self.scene.clear()
        self._profile_items = []
        self._area_item = None
        self._alignment_item = None
        self._draft_alignment_item = None
        self._direction_annotation = None
        self._skipped_annotations = []
        self.view.set_skipped_annotations(())
        colors = self._colors()
        self.scene.setBackgroundBrush(QBrush(colors["background"]))
        if self._assessment_polygon is not None:
            self._area_item = self.scene.addPath(
                self._polygon_path(self._assessment_polygon),
                self._cosmetic_pen(colors["area"], 2.0),
                QBrush(colors["area_fill"]),
            )
        if self._wall_alignment is not None:
            self._alignment_item = self.scene.addPath(
                self._line_path(self._wall_alignment.points),
                self._cosmetic_pen(colors["alignment"], 3.0),
            )
        if self._draft_alignment_points:
            self._draft_alignment_item = self.scene.addPath(
                self._line_path(self._draft_alignment_points),
                self._cosmetic_pen(colors["draft_alignment"], 2.0, Qt.PenStyle.DashLine),
            )

        for profile in self._profiles:
            origin = profile.alignment.origin
            nx, ny = profile.alignment.normal_xy
            if profile.assessment_u_interval is None:
                continue
            lower, upper = profile.assessment_u_interval
            first = QPointF(
                origin.x + nx * lower,
                -(origin.y + ny * lower),
            )
            second = QPointF(
                origin.x + nx * upper,
                -(origin.y + ny * upper),
            )
            self._profile_items.append(
                self.scene.addLine(
                    first.x(), first.y(), second.x(), second.y(), self._profile_pen(False)
                )
            )
        self._add_skipped_station_markers()
        self._update_selected_direction_annotation()
        self.legend.setText(self._legend_html())
        self._fit_to_engineering_extent()

    def clear_result(self) -> None:
        self._profiles = ()
        self._diagnostics = ()
        self._selected_index = -1
        self._refresh_scene()

    def set_selected_profile(self, index: int) -> None:
        if not 0 <= index < len(self._profile_items):
            self._selected_index = -1
        else:
            self._selected_index = index
        for item_index, item in enumerate(self._profile_items):
            item.setPen(self._profile_pen(item_index == self._selected_index))
        self._update_selected_direction_annotation()

    def _update_selected_direction_annotation(self) -> None:
        if not 0 <= self._selected_index < len(self._profiles):
            self._direction_annotation = None
            self.view.set_direction_annotation(None, None)
            return
        profile = self._profiles[self._selected_index]
        interval = profile.assessment_u_interval
        if interval is None:
            self._direction_annotation = None
            self.view.set_direction_annotation(None, None)
            return
        origin = profile.alignment.origin
        nx, ny = profile.alignment.normal_xy
        midpoint = (interval[0] + interval[1]) / 2.0
        scene_point = QPointF(
            origin.x + nx * midpoint,
            -(origin.y + ny * midpoint),
        )
        self._direction_annotation = (scene_point, (nx, ny))
        self.view.set_direction_annotation(
            scene_point, (nx, ny), self._colors()["selected"]
        )

    def _add_skipped_station_markers(self) -> None:
        if self._wall_alignment is None:
            return
        seen = set()
        for diagnostic in getattr(self, "_diagnostics", ()):
            chainage = getattr(diagnostic, "chainage_m", None)
            if chainage is None or chainage in seen:
                continue
            seen.add(chainage)
            point, _ = self._wall_alignment.point_and_tangent_at(chainage)
            self._skipped_annotations.append((
                QPointF(point.x, -point.y),
                tr("Profile skipped")
                + "\n"
                + str(getattr(diagnostic, "message", "")),
                self._colors()["skipped"],
            ))
        self.view.set_skipped_annotations(self._skipped_annotations)

    def _fit_to_engineering_extent(self) -> None:
        """Fit only engineering geometry; cosmetic markers must not move the view."""
        items = [
            item for item in (
                self._area_item,
                self._alignment_item,
                self._draft_alignment_item,
                *self._profile_items,
            ) if item is not None
        ]
        if not items:
            return
        rect = items[0].sceneBoundingRect()
        for item in items[1:]:
            rect = rect.united(item.sceneBoundingRect())
        margin = max(min(max(rect.width(), rect.height()) * 0.03, 100.0), 1.0)
        self.view.fit_to_rect(rect.adjusted(-margin, -margin, margin, margin))

    @staticmethod
    def _distance_to_segment(px, py, ax, ay, bx, by) -> float:
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-18:
            return hypot(px - ax, py - ay)
        fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
        x = ax + dx * fraction
        y = ay + dy * fraction
        return hypot(px - x, py - y)

    def _scene_tolerance(self) -> float:
        left = self.view.mapToScene(QPoint(0, 0))
        right = self.view.mapToScene(QPoint(8, 0))
        return max(abs(right.x() - left.x()), 0.5)

    def _select_nearest_profile(self, x: float, y: float) -> None:
        if not self._profiles:
            return
        candidates = []
        for index, profile in enumerate(self._profiles):
            origin = profile.alignment.origin
            nx, ny = profile.alignment.normal_xy
            if profile.assessment_u_interval is None:
                continue
            lower, upper = profile.assessment_u_interval
            ax = origin.x + nx * lower
            ay = origin.y + ny * lower
            bx = origin.x + nx * upper
            by = origin.y + ny * upper
            candidates.append((self._distance_to_segment(x, y, ax, ay, bx, by), index))
        if not candidates:
            return
        distance, index = min(candidates)
        if distance <= self._scene_tolerance():
            self.profile_selected.emit(index)


class WallProfilePlot(QWidget):
    DISPLAY_CONTEXT_EXTENSION_M = 1.0
    measurement_state_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profile = None
        self.profile_set = None
        self.measurement = None
        self.measurements = ()
        self._actual_landmark_hit_targets = ()
        self.variant_index = 0
        self.mode = "empty"
        self.measure_mode = False
        self.measure_point_a = None
        self.measure_point_b = None
        self.setMinimumSize(340, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @staticmethod
    def _dark_theme() -> bool:
        app = QApplication.instance()
        return bool(app is not None and app.property("slopeforgeTheme") == "dark")

    @staticmethod
    def _equal_aspect_bounds(
        plot: QRectF, u_min, u_max, z_min, z_max, *, left_extra_u_fraction=0.35
    ):
        """Keep equal metric scale with a modest bias toward right-side note space."""
        scale = min(
            plot.width() / max(u_max - u_min, 1e-12),
            plot.height() / max(z_max - z_min, 1e-12),
        )
        required_u_span = plot.width() / scale
        required_z_span = plot.height() / scale
        # Extra horizontal range is display-only. A modest rightward bias
        # preserves naturally quiet note space without compressing the plot.
        extra_u = max(0.0, required_u_span - (u_max - u_min))
        extra_z = max(0.0, required_z_span - (z_max - z_min))
        return (
            u_min - extra_u * left_extra_u_fraction,
            u_max + extra_u * (1.0 - left_extra_u_fraction),
            z_min - extra_z / 2.0,
            z_max + extra_z / 2.0,
        )

    @classmethod
    def _colors(cls):
        if cls._dark_theme():
            return {
                "background": QColor("#252c36"),
                "border": QColor("#4a5665"),
                "grid": QColor("#3b4654"),
                "text": QColor("#d5dbe3"),
                "design": QColor("#5aa7e8"),
                "actual": QColor("#f0c66e"),
                "face": QColor("#67c587"),
                "berm": QColor("#5aa7e8"),
                "road": QColor("#b9a4ef"),
                "unknown": QColor("#9aa6b2"),
                "ignore": QColor("#718096"),
            }
        return {
            "background": QColor("#f8fafc"),
            "border": QColor("#d7dde6"),
            "grid": QColor("#e2e8f0"),
            "text": QColor("#475467"),
            "design": QColor("#1261a0"),
            "actual": QColor("#d97706"),
            "face": QColor("#27864f"),
            "berm": QColor("#1261a0"),
            "road": QColor("#7657a8"),
            "unknown": QColor("#7a8696"),
            "ignore": QColor("#94a3b8"),
        }

    def set_profile(self, profile, measurement=None) -> None:
        self.clear_measurement()
        self.set_measure_mode(False)
        self.profile = profile
        self.profile_set = None
        self.measurement = measurement
        self.measurements = ()
        self.mode = "selected" if profile is not None else "empty"
        self.measurement_state_changed.emit()
        self.update()

    def set_overview(self, profile_set, variant_index: int = 0, measurements=()) -> None:
        self.clear_measurement()
        self.set_measure_mode(False)
        self.profile = None
        self.profile_set = profile_set
        self.measurement = None
        self.measurements = tuple(measurements)
        self.variant_index = variant_index
        self.mode = "overview"
        self.measurement_state_changed.emit()
        self.update()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.update()

    def _points(self):
        design, _ = self._geometry()
        evaluated_actual, context_actual = self._actual_render_layers()
        return tuple(
            point
            for segment in (*design, *evaluated_actual, *context_actual)
            for point in (segment.start, segment.end)
        )

    def _geometry(self):
        if self.mode == "selected" and self.profile is not None:
            context = getattr(self.profile.design_section, "upstream_context", None)
            design = (
                *((
                    SimpleNamespace(
                        start=context.start,
                        end=context.end,
                        semantic_role=context.role,
                    ),
                ) if context is not None else ()),
                *(s for s in self.profile.design_segments if s.semantic_role != "ignore"),
            )
            return design, self.profile.actual_segments
        if self.mode != "overview" or not self.profile_set.design_variants:
            return (), ()
        variant = self.profile_set.design_variants[self.variant_index]
        representative_elements = (
            *((variant.upstream_context,) if variant.upstream_context else ()),
            *variant.elements,
        )
        design = tuple(
            SimpleNamespace(
                start=SectionPoint(e.start_u, e.start_dz, e.start_u, 0),
                end=SectionPoint(e.end_u, e.end_dz, e.end_u, 0),
                semantic_role=e.role,
            ) for e in representative_elements if e.role != "ignore"
        )
        return design, self._overview_actual_geometry(variant)

    @staticmethod
    def _interpolate_section_point(start, end, fraction: float):
        """Return a presentation-only point on an existing section segment."""
        return SimpleNamespace(
            u=start.u + (end.u - start.u) * fraction,
            z=start.z + (end.z - start.z) * fraction,
            x=start.x + (end.x - start.x) * fraction,
            y=start.y + (end.y - start.y) * fraction,
        )

    @classmethod
    def _clip_segments_to_u_interval(cls, segments, interval):
        """Precisely clip existing section geometry without changing its source."""
        if interval is None:
            return ()
        lower, upper = sorted(interval)
        clipped = []
        for segment in segments:
            start, end = segment.start, segment.end
            delta_u = end.u - start.u
            fractions = [0.0, 1.0]
            if abs(delta_u) > 1e-12:
                for boundary in (lower, upper):
                    fraction = (boundary - start.u) / delta_u
                    if 1e-12 < fraction < 1.0 - 1e-12:
                        fractions.append(fraction)
            fractions = sorted(set(fractions))
            for first, second in zip(fractions, fractions[1:]):
                midpoint_u = start.u + delta_u * ((first + second) / 2.0)
                if not lower <= midpoint_u <= upper:
                    continue
                clipped.append(
                    SimpleNamespace(
                        start=cls._interpolate_section_point(start, end, first),
                        end=cls._interpolate_section_point(start, end, second),
                        semantic_role=getattr(segment, "semantic_role", None),
                    )
                )
        return tuple(clipped)

    @staticmethod
    def _profile_actual_measurement_geometry(profile):
        context = getattr(profile, "measurement_context", None)
        return context.actual_segments if context is not None else profile.actual_segments

    @staticmethod
    def _reliable_actual_landmark_from(measurement, name):
        landmarks = getattr(measurement, "actual_landmarks", None)
        landmark = getattr(landmarks, name, None)
        return landmark.point if landmark is not None and landmark.detection.reliable else None

    def _overview_actual_geometry(self, variant):
        """Render each profile's measured physical wall interval only.

        The Overview is a family of independently measured profiles.  It uses
        no selected-profile display context and never falls back to the
        Assessment clip: a profile without reliable physical endpoints simply
        contributes no Actual trace until its geometry can be diagnosed.
        """
        actual = []
        for index in variant.profile_indices:
            if not 0 <= index < len(self.measurements):
                continue
            profile = self.profile_set.profiles[index]
            measurement = self.measurements[index]
            upper_start = self._reliable_actual_landmark_from(
                measurement, "upper_berm_start"
            )
            upper_crest = self._reliable_actual_landmark_from(
                measurement, "upper_crest"
            )
            lower_toe = self._reliable_actual_landmark_from(
                measurement, "lower_toe"
            )
            upper = upper_start or upper_crest
            if upper is None or lower_toe is None or lower_toe.u < upper.u:
                continue
            measured_segments = self._profile_actual_measurement_geometry(profile)
            if not self._clip_segments_to_u_interval(
                measured_segments, (upper.u, lower_toe.u),
            ):
                continue
            origin_z = profile.alignment.origin.z
            actual.extend(
                SimpleNamespace(
                    start=SectionPoint(s.start.u, s.start.z - origin_z, s.start.x, s.start.y),
                    end=SectionPoint(s.end.u, s.end.z - origin_z, s.end.x, s.end.y),
                    semantic_role=None,
                )
                for s in self._clip_segments_to_u_interval(
                    measured_segments,
                    (upper.u, lower_toe.u),
                )
            )
        return tuple(actual)

    def _raw_actual_measurement_geometry(self):
        if self.mode != "selected" or self.profile is None:
            return ()
        return self._profile_actual_measurement_geometry(self.profile)

    def _display_fallback_actual_geometry(self):
        """Conservative display-only fallback when physical bounds are unknown."""
        if self.mode != "selected" or self.profile is None:
            return ()
        # ``actual_segments`` retains the established presentation clip.  It
        # must not feed detection, which now needs the full bounded U context
        # for displaced floors and crests.
        return self.profile.actual_segments

    def _reliable_actual_landmark(self, name):
        return self._reliable_actual_landmark_from(self.measurement, name)

    def _visible_actual_interval(self):
        """Physical display endpoints, never the spatial Assessment mask."""
        upper_start = self._reliable_actual_landmark("upper_berm_start")
        upper_crest = self._reliable_actual_landmark("upper_crest")
        lower_toe = self._reliable_actual_landmark("lower_toe")
        if lower_toe is None:
            return None
        upper = upper_start or upper_crest
        if upper is None:
            return None
        return tuple(sorted((upper.u, lower_toe.u)))

    @classmethod
    def _profile_has_compatible_actual_display(cls, profile, measurement):
        return has_compatible_actual_wall_section(profile, measurement)

    @classmethod
    def _profile_has_overview_actual_display(cls, profile, measurement):
        """Whether the evaluated measurement span can contribute to Overview."""
        upper_start = cls._reliable_actual_landmark_from(
            measurement, "upper_berm_start"
        )
        upper_crest = cls._reliable_actual_landmark_from(
            measurement, "upper_crest"
        )
        lower_toe = cls._reliable_actual_landmark_from(measurement, "lower_toe")
        upper = upper_start or upper_crest
        if upper is None or lower_toe is None or lower_toe.u < upper.u:
            return False
        return bool(cls._clip_segments_to_u_interval(
            cls._profile_actual_measurement_geometry(profile),
            (upper.u, lower_toe.u),
        ))

    def _has_compatible_actual_display(self, interval):
        """Keep raw detector input separate from a normal evaluated trace."""
        if self.profile is None or interval is None:
            return False
        # ``actual_segments`` is the existing local presentation clip.  It is
        # never fed back into landmark detection, but it provides a conservative
        # guard against presenting a remote floating intersection as the wall.
        return self._profile_has_compatible_actual_display(
            self.profile, self.measurement,
        )

    def _measurement_context_geometry(self):
        """Small real continuations outside the detected engineering section."""
        interval = self._visible_actual_interval()
        if interval is None:
            return self._display_fallback_actual_geometry()
        lower, upper = interval
        raw = self._raw_actual_measurement_geometry()
        return (
            *self._clip_segments_to_u_interval(
                raw, (lower - self.DISPLAY_CONTEXT_EXTENSION_M, lower)
            ),
            *self._clip_segments_to_u_interval(
                raw, (upper, upper + self.DISPLAY_CONTEXT_EXTENSION_M)
            ),
        )

    def _actual_render_layers(self):
        """Return physical engineering Actual and secondary real context."""
        # Overview represents every evaluated Actual section in the selected
        # Design variant. It has no selected measurement/landmark interval, so
        # selected-profile context rules must not suppress this layer.
        if self.mode == "overview":
            return self._geometry()[1], ()
        interval = self._visible_actual_interval()
        if interval is None:
            return (), self._measurement_context_geometry()
        if not self._has_compatible_actual_display(interval):
            return (), self._measurement_context_geometry()
        evaluated = self._clip_segments_to_u_interval(
            self._raw_actual_measurement_geometry(), interval
        )
        if not evaluated:
            # Some legacy-selected profiles retain only the presentation clip.
            # It remains eligible only after the independent landmark/topology
            # gate above; detector input is never changed or replaced.
            evaluated = self._clip_segments_to_u_interval(
                self.profile.actual_segments, interval
            )
        return (
            evaluated,
            self._measurement_context_geometry(),
        )

    def _reliable_actual_landmarks(self):
        landmarks = getattr(self.measurement, "actual_landmarks", None)
        output = []
        for label, name in (
            (tr("Actual upper berm start"), "upper_berm_start"),
            (tr("Actual upper crest"), "upper_crest"),
            (tr("Actual lower toe"), "lower_toe"),
        ):
            landmark = getattr(landmarks, name, None)
            if landmark is None or not landmark.detection.reliable:
                continue
            if (
                name == "upper_crest"
                and getattr(landmark, "source", "physical_breakpoint")
                == "boundary_face_run_onset"
            ):
                label = tr("Actual upper crest · face-run onset")
            elif (
                name == "upper_crest"
                and getattr(landmark, "source", "physical_breakpoint")
                == "boundary_design_elevation_fallback"
            ):
                label = tr("Actual upper crest · elevation fallback")
            output.append((label, landmark.point))
        return tuple(output)

    @staticmethod
    def _measurement_context_pen(color):
        pen = QPen(color, 1.25)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        return pen

    @staticmethod
    def _legend_rows(profile):
        design = [(tr("Face"), "face"), (tr("Berm"), "berm"), (tr("Road"), "road")]
        if any(
            getattr(segment, "semantic_role", None) == "unknown"
            for segment in profile.design_segments
        ):
            design.append((tr("Unknown"), "unknown"))
        return tuple(design), ((tr("Survey"), "actual"),)

    def plot_rect(self) -> QRectF:
        """Drawing-grid bounds used by both painting and annotation placement."""
        left, top, right, bottom = 62, 34, 22, 46
        return QRectF(
            left,
            top,
            max(1, self.width() - left - right),
            max(1, self.height() - top - bottom),
        )

    def _plot_data_bounds(self):
        """Return the current display bounds without including ruler overlays."""
        points = self._points()
        if not points:
            return None
        plot = self.plot_rect()
        u_values = [point.u for point in points]
        z_values = [point.z for point in points]
        u_min, u_max = min(u_values), max(u_values)
        z_min, z_max = min(z_values), max(z_values)
        if abs(u_max - u_min) < 1e-9:
            u_min -= 1.0
            u_max += 1.0
        if abs(z_max - z_min) < 1e-9:
            z_min -= 1.0
            z_max += 1.0
        u_pad = (u_max - u_min) * 0.025
        z_pad = (z_max - z_min) * 0.04
        return self._equal_aspect_bounds(
            plot,
            u_min - u_pad,
            u_max + u_pad,
            z_min - z_pad,
            z_max + z_pad,
        )

    def _map_data_to_widget(self, u: float, z: float, bounds=None) -> QPointF:
        """Map engineering U/Z coordinates using the active paint transform."""
        bounds = self._plot_data_bounds() if bounds is None else bounds
        if bounds is None:
            return QPointF()
        u_min, u_max, z_min, z_max = bounds
        plot = self.plot_rect()
        return QPointF(
            plot.left() + (u - u_min) / (u_max - u_min) * plot.width(),
            plot.bottom() - (z - z_min) / (z_max - z_min) * plot.height(),
        )

    def _map_widget_to_data(self, point: QPointF, bounds=None):
        """Invert the active paint transform for presentation-only clicks."""
        bounds = self._plot_data_bounds() if bounds is None else bounds
        if bounds is None:
            return None
        u_min, u_max, z_min, z_max = bounds
        plot = self.plot_rect()
        return (
            u_min + (point.x() - plot.left()) / plot.width() * (u_max - u_min),
            z_min + (plot.bottom() - point.y()) / plot.height() * (z_max - z_min),
        )

    @staticmethod
    def _manual_measurement_values(point_a, point_b):
        delta_u = point_b[0] - point_a[0]
        delta_z = point_b[1] - point_a[1]
        return (
            delta_u,
            delta_z,
            hypot(delta_u, delta_z),
            degrees(atan2(abs(delta_z), abs(delta_u))),
        )

    def set_measure_mode(self, enabled: bool) -> None:
        enabled = bool(enabled) and self.mode in ("selected", "overview")
        if self.measure_mode == enabled:
            return
        self.measure_mode = enabled
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()
        self.setToolTip(tr("Click two points to measure") if enabled else "")
        self.measurement_state_changed.emit()
        self.update()

    def clear_measurement(self) -> None:
        if self.measure_point_a is None and self.measure_point_b is None:
            return
        self.measure_point_a = None
        self.measure_point_b = None
        self.measurement_state_changed.emit()
        self.update()

    def _manual_measurement_annotation_lines(self):
        if self.measure_point_a is None or self.measure_point_b is None:
            return ()
        delta_u, delta_z, distance, angle = self._manual_measurement_values(
            self.measure_point_a, self.measure_point_b
        )
        vertical_delta_label = "ΔdZ" if self.mode == "overview" else "ΔZ"
        return (
            tr("Manual measure"),
            f"L  {distance:.2f} m",
            f"ΔU {delta_u:+.2f} m",
            f"{vertical_delta_label} {delta_z:+.2f} m",
            f"A  {angle:.1f}°",
        )

    def _draw_manual_measurement(self, painter, colors, bounds) -> None:
        if self.measure_point_a is None:
            return
        point_a = self._map_data_to_widget(*self.measure_point_a, bounds)
        marker_pen = QPen(colors["design"], 1.5)
        marker_pen.setCosmetic(True)
        painter.setPen(marker_pen)
        painter.setBrush(QBrush(colors["background"]))
        painter.drawEllipse(point_a, 4.0, 4.0)
        if self.measure_point_b is None:
            return
        point_b = self._map_data_to_widget(*self.measure_point_b, bounds)
        painter.drawLine(point_a, point_b)
        painter.drawEllipse(point_b, 4.0, 4.0)

        lines = self._manual_measurement_annotation_lines()
        metrics = QFontMetrics(painter.font())
        padding = 7
        width = max(metrics.horizontalAdvance(line) for line in lines) + padding * 2
        height = metrics.lineSpacing() * len(lines) + padding * 2
        plot = self.plot_rect()
        annotation = QRectF(
            max(plot.left() + 4, plot.right() - width - 4),
            plot.top() + 4,
            min(width, max(1.0, plot.width() - 8)),
            min(height, max(1.0, plot.height() - 8)),
        )
        palette = _canvas_palette()
        painter.setPen(QPen(palette["annotation_border"], 1))
        painter.setBrush(QBrush(palette["annotation_background"]))
        painter.drawRoundedRect(annotation, 3, 3)
        painter.setPen(colors["text"])
        for index, line in enumerate(lines):
            painter.drawText(
                QRectF(
                    annotation.left() + padding,
                    annotation.top() + padding + index * metrics.lineSpacing(),
                    annotation.width() - padding * 2,
                    metrics.height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = self._colors()
        painter.fillRect(self.rect(), colors["background"])

        bounds = self._plot_data_bounds()
        if bounds is None:
            self._actual_landmark_hit_targets = ()
            painter.setPen(colors["text"])
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("No profile selected"))
            return

        left = 62
        plot = self.plot_rect()
        u_min, u_max, z_min, z_max = bounds

        def map_point(point):
            return self._map_data_to_widget(point.u, point.z, bounds)

        painter.setPen(QPen(colors["grid"], 1))
        metrics = QFontMetrics(painter.font())
        for step in range(6):
            fraction = step / 5
            x = plot.left() + plot.width() * fraction
            y = plot.top() + plot.height() * fraction
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(colors["text"])
            u_value = u_min + fraction * (u_max - u_min)
            z_value = z_max - fraction * (z_max - z_min)
            painter.drawText(
                QRectF(x - 36, plot.bottom() + 5, 72, metrics.height() + 2),
                Qt.AlignmentFlag.AlignHCenter,
                f"{u_value:.1f}",
            )
            painter.drawText(
                QRectF(2, y - metrics.height() / 2, left - 10, metrics.height() + 2),
                Qt.AlignmentFlag.AlignRight,
                f"{z_value:.1f}",
            )
            painter.setPen(QPen(colors["grid"], 1))

        painter.setPen(QPen(colors["border"], 1))
        painter.drawRect(plot)

        painter.setPen(colors["text"])
        painter.drawText(QRectF(plot.left(), plot.bottom() + 8, plot.width(), 24), Qt.AlignmentFlag.AlignCenter, tr("U (m, + toward wall/toe)"))
        painter.save()
        painter.translate(16, plot.center().y())
        painter.rotate(-90)
        vertical_axis = tr("dZ (m, local Design crest = 0)") if self.mode == "overview" else "Z (m)"
        painter.drawText(QRectF(-plot.height() / 2, -12, plot.height(), 24), Qt.AlignmentFlag.AlignCenter, vertical_axis)
        painter.restore()

        design, _ = self._geometry()
        evaluated_actual, context_actual = self._actual_render_layers()
        def draw_segments(segments, color, base_width, semantic=False, *, dashed=False):
            for segment in segments:
                width = base_width
                role = str(getattr(segment, "semantic_role", "") or "").lower()
                segment_color = colors.get(role, color) if semantic else color
                pen = (
                    self._measurement_context_pen(segment_color)
                    if dashed else QPen(segment_color, width)
                )
                painter.setPen(pen)
                painter.drawLine(map_point(segment.start), map_point(segment.end))

        draw_segments(design, colors["design"], 3.0 if self.mode == "overview" else 2.3, True)
        actual_color = QColor(colors["actual"])
        if self.mode == "overview":
            actual_color.setAlpha(90)
        draw_segments(evaluated_actual, actual_color, 1.0 if self.mode == "overview" else 2.2)
        if self.mode == "selected":
            context_color = QColor(colors["actual"])
            context_color.setAlpha(120)
            draw_segments(
                context_actual, context_color, 1.25, dashed=True
            )
        marker_targets = []
        if self.mode == "selected":
            marker_pen = QPen(colors["actual"], 1.25)
            marker_pen.setCosmetic(True)
            painter.setPen(marker_pen)
            painter.setBrush(QBrush(colors["background"]))
            for label, point in self._reliable_actual_landmarks():
                location = map_point(point)
                painter.drawEllipse(location, 3.5, 3.5)
                marker_targets.append((location, label))
        self._actual_landmark_hit_targets = tuple(marker_targets)
        self._draw_manual_measurement(painter, colors, bounds)

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.mode in ("selected", "overview")
            and self.measure_mode
            and self.plot_rect().contains(event.position())
        ):
            point = self._map_widget_to_data(event.position())
            if point is not None:
                if self.measure_point_a is None or self.measure_point_b is not None:
                    self.measure_point_a = point
                    self.measure_point_b = None
                else:
                    self.measure_point_b = point
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.measurement_state_changed.emit()
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        for point, label in self._actual_landmark_hit_targets:
            if hypot(event.position().x() - point.x(), event.position().y() - point.y()) <= 8:
                QToolTip.showText(event.globalPosition().toPoint(), label, self)
                event.accept()
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)


class WallConformanceTab(QWidget):
    """Read-only diagnostic view for current Project design vs actual surfaces."""

    wall_conformance_state_changed = Signal()

    def __init__(self, context, site_id: int, assessment_polygon: PlanPolygon, parent=None,
                 *, area=None, geometry_revision=None, controller=None,
                 read_only: bool = False):
        super().__init__(parent)
        self.context = context
        self.site_id = site_id
        self.assessment_polygon = assessment_polygon
        self.area = area
        self.geometry_revision = geometry_revision
        self.controller = controller
        active_revision_id = getattr(area, "active_geometry_revision_id", None)
        revision_id = getattr(geometry_revision, "id", None)
        self.read_only = bool(
            read_only
            or (active_revision_id is not None and revision_id != active_revision_id)
        )
        self._alignment_before_drawing = None
        self._alignment_load_error: str | None = None
        self.service = WallConformanceDiagnosticService(
            create_project_surface_dataset_service(context)
        )
        self.result = None

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        setup = QFrame()
        setup.setObjectName("CriterionCard")
        setup.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        setup_layout = QVBoxLayout(setup)
        setup_layout.setContentsMargins(10, 8, 10, 8)
        setup_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel(tr("Wall conformance"))
        title.setObjectName("EngineeringSectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.calculate_button = QPushButton(tr("Calculate profiles"))
        self.calculate_button.setProperty("role", "primary")
        self.calculate_button.clicked.connect(self.calculate)
        title_row.addWidget(self.calculate_button)
        setup_layout.addLayout(title_row)

        datasets = QGridLayout()
        datasets.setHorizontalSpacing(12)
        datasets.setVerticalSpacing(2)
        self.design_title = QLabel(tr("DESIGN"))
        self.actual_title = QLabel(tr("ACTUAL"))
        self.design_title.setObjectName("EngineeringSectionTitle")
        self.actual_title.setObjectName("EngineeringSectionTitle")
        self.design_metadata = QLabel()
        self.actual_metadata = QLabel()
        for label in (self.design_metadata, self.actual_metadata):
            label.setObjectName("MutedText")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        datasets.addWidget(self.design_title, 0, 0)
        datasets.addWidget(self.actual_title, 0, 1)
        datasets.addWidget(self.design_metadata, 1, 0)
        datasets.addWidget(self.actual_metadata, 1, 1)
        datasets.setColumnStretch(0, 1)
        datasets.setColumnStretch(1, 1)
        setup_layout.addLayout(datasets)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(QLabel(tr("Spacing")))
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.5, 50.0)
        self.spacing.setDecimals(1)
        self.spacing.setSingleStep(0.5)
        self.spacing.setValue(3.0)
        self.spacing.setSuffix(" m")
        self.spacing.setMaximumWidth(110)
        controls.addWidget(self.spacing)
        self.alignment_metadata = QLabel(tr("Wall Alignment · not set"))
        self.alignment_metadata.setObjectName("MutedText")
        self.alignment_metadata.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        controls.addSpacing(10)
        controls.addWidget(self.alignment_metadata, 1)
        self.set_alignment_button = QPushButton(tr("Set Wall Alignment"))
        self.set_alignment_button.clicked.connect(self._begin_alignment_drawing)
        controls.addWidget(self.set_alignment_button)
        self.clear_alignment_button = QPushButton(tr("Clear"))
        self.clear_alignment_button.clicked.connect(self._clear_wall_alignment)
        self.clear_alignment_button.setEnabled(False)
        controls.addWidget(self.clear_alignment_button)
        setup_layout.addLayout(controls)

        mapping_row = QHBoxLayout()
        self.semantic_mapping = QLabel()
        self.semantic_mapping.setObjectName("MutedText")
        self.semantic_mapping.setToolTip(
            tr("Diagnostic mapping read from the active Design surface attributes.")
        )
        mapping_row.addWidget(self.semantic_mapping, 1)
        self.edit_semantics = QPushButton(tr("Edit design semantics…"))
        self.edit_semantics.clicked.connect(self._edit_design_semantics)
        mapping_row.addWidget(self.edit_semantics)
        setup_layout.addLayout(mapping_row)

        self.status = QLabel(tr("Ready to calculate."))
        self.status.setWordWrap(True)
        set_status_role(self.status, "info")
        setup_layout.addWidget(self.status)
        root.addWidget(setup)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.plan = WallConformancePlanWidget()
        self.plan.setMinimumWidth(480)
        self.plan.setMaximumWidth(720)
        self.plan.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self.plan.set_assessment_polygon(self.assessment_polygon)
        self.plan.profile_selected.connect(lambda index: self._select_profile(index + 1))
        self.plan.alignment_completed.connect(self._wall_alignment_completed)
        self.plan.alignment_drawing_cancelled.connect(self._wall_alignment_drawing_cancelled)
        splitter.addWidget(self.plan)

        profile_host = QWidget()
        profile_layout = QVBoxLayout(profile_host)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(0)
        self.profile_header = QWidget()
        self.profile_header.setFixedHeight(58)
        profile_header_layout = QVBoxLayout(self.profile_header)
        profile_header_layout.setContentsMargins(8, 5, 8, 4)
        profile_header_layout.setSpacing(1)
        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        profile_title = QLabel(tr("Transverse section"))
        profile_title.setObjectName("EngineeringSectionTitle")
        selector_row.addWidget(profile_title)
        selector_row.addSpacing(10)
        selector_row.addWidget(QLabel(tr("Profile")))
        self.profile_selector = QComboBox()
        self.profile_selector.setMinimumWidth(150)
        self.profile_selector.setMaximumWidth(220)
        self.profile_selector.currentIndexChanged.connect(self._select_profile)
        selector_row.addWidget(self.profile_selector)
        self.variant_label = QLabel(tr("Variant"))
        self.variant_label.setVisible(False)
        selector_row.addWidget(self.variant_label)
        self.variant_selector = QComboBox()
        self.variant_selector.setMinimumWidth(185)
        self.variant_selector.setMaximumWidth(270)
        self.variant_selector.currentIndexChanged.connect(self._select_variant)
        self.variant_selector.setVisible(False)
        selector_row.addWidget(self.variant_selector)
        self.profile_summary = QLabel("—")
        self.profile_summary.setObjectName("SummaryValue")
        selector_row.addWidget(self.profile_summary)
        self.measure_button = QPushButton(tr("Measure"))
        self.measure_button.setCheckable(True)
        self.measure_button.setMinimumWidth(self.measure_button.sizeHint().width())
        self.measure_button.setToolTip(tr("Click two points to measure"))
        self.measure_button.setEnabled(False)
        selector_row.addWidget(self.measure_button)
        self.clear_measure_button = QPushButton(tr("Clear"))
        self.clear_measure_button.setFixedWidth(52)
        self.clear_measure_button.setEnabled(False)
        selector_row.addWidget(self.clear_measure_button)
        selector_row.addStretch()
        profile_header_layout.addLayout(selector_row)
        self.profile_plot = WallProfilePlot()
        self.measure_button.toggled.connect(self.profile_plot.set_measure_mode)
        self.clear_measure_button.clicked.connect(self.profile_plot.clear_measurement)
        self.profile_plot.measurement_state_changed.connect(self._sync_measure_controls)
        self.profile_legend = QLabel()
        self.profile_legend.setObjectName("MutedText")
        self.profile_legend.setWordWrap(True)
        self.profile_legend.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.profile_legend.hide()
        profile_header_layout.addWidget(self.profile_legend)
        self.details_schedule = WallProfileSchedule()
        self.details_schedule.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        details_layout = QVBoxLayout(self.details_schedule)
        details_layout.setContentsMargins(10, 8, 10, 8)
        details_layout.setSpacing(3)
        self.details_title = QLabel(tr("Representative Design"))
        self.details_title.setObjectName("SummaryValue")
        details_layout.addWidget(self.details_title)
        self.details_metadata = QLabel()
        self.details_metadata.setObjectName("MutedText")
        self.details_metadata.setWordWrap(True)
        self.details_metadata.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        details_layout.addWidget(self.details_metadata)
        self.details_scroll = QScrollArea()
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.details_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.details_scroll.setAutoFillBackground(False)
        self.details_scroll.viewport().setAutoFillBackground(False)
        self.details_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: 0; }"
            "QScrollArea::viewport { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self.details_content = QWidget()
        self.details_content.setAutoFillBackground(False)
        self.details_content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.details_rows = QGridLayout(self.details_content)
        self.details_rows.setContentsMargins(0, 0, 0, 0)
        self.details_rows.setHorizontalSpacing(6)
        self.details_rows.setVerticalSpacing(3)
        self.details_rows.setColumnStretch(0, 1)
        self.details_rows.setColumnStretch(1, 1)
        self.details_rows.setColumnStretch(2, 0)
        self.details_rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail_stretch_row = None
        self.details_scroll.setWidget(self.details_content)
        details_layout.addWidget(self.details_scroll, 1)
        self.profile_canvas = WallProfileDrawingHost(
            self.profile_plot, self.profile_header, self.details_schedule
        )
        self.profile_canvas.setMinimumWidth(self.profile_plot.minimumWidth())
        profile_layout.addWidget(self.profile_canvas, 1)
        self.details_schedule.hide()
        for escape_source in (
            self,
            self.plan.view,
            self.profile_selector,
            self.profile_plot,
        ):
            escape_source.installEventFilter(self)
        splitter.addWidget(profile_host)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([600, 810])
        self.splitter = splitter
        self._splitter_initialised = False
        root.addWidget(splitter, 1)
        self._load_saved_wall_alignment()
        self._refresh_dataset_metadata()
        self._refresh_calculation_availability()

    def showEvent(self, event):
        super().showEvent(event)
        if self._splitter_initialised or self.splitter.width() <= 0:
            return
        target_width = min(600, max(480, self.splitter.width() - 340))
        self.splitter.setSizes([target_width, max(1, self.splitter.width() - target_width)])
        self._splitter_initialised = True

    @staticmethod
    def _dataset_text(dataset) -> str:
        if dataset is None:
            return tr("No active dataset")
        revision = int(getattr(dataset, "revision_number", 0) or 0)
        source_format = str(getattr(dataset, "source_format", "") or tr("Unknown")).upper()
        triangles = int(getattr(dataset, "triangle_count", 0) or 0)
        return (
            tr("R%1 · %2 · %3 triangles")
            .replace("%1", str(revision))
            .replace("%2", source_format)
            .replace("%3", f"{triangles:,}")
        )

    @staticmethod
    def _variant_context_label(variant) -> str:
        context = variant.upstream_context
        if context is None:
            return tr("No upstream context")
        return tr("%1 context").replace("%1", tr(context.role.title()))

    def _refresh_dataset_metadata(self) -> None:
        try:
            design, actual = self.service.current_datasets(self.site_id)
        except Exception as exc:
            self.design_metadata.setText(str(exc))
            self.actual_metadata.setText(str(exc))
            return
        self.design_metadata.setText(self._dataset_text(design))
        self.actual_metadata.setText(self._dataset_text(actual))
        if design is None:
            self.semantic_mapping.setText(tr("Design semantics: unavailable"))
            self.edit_semantics.setEnabled(False)
            return
        mapping, fallback = self.service.mapping_for_dataset(design)
        assignments = {
            role: sorted(
                (str(value) for value, assigned in mapping.assignments if assigned == role),
                key=str.casefold,
            )
            for role in ("face", "berm", "road")
        }
        detail = " · ".join(
            f"{tr(role.title())}={','.join(assignments[role])}"
            for role in ("face", "berm", "road")
            if assignments[role]
        )
        prefix = (
            tr("Design semantics · default mapping")
            if fallback
            else tr("Design semantics · %1").replace("%1", mapping.attribute_name)
        )
        self.semantic_mapping.setText(f"{prefix} · {detail}")
        self.edit_semantics.setEnabled(bool(getattr(self.service.surface_service, "storage_available", True)))

    def _refresh_calculation_availability(self) -> None:
        if self._alignment_load_error is not None:
            self.calculate_button.setEnabled(False)
            self.status.setText(self._alignment_load_error)
            set_status_role(self.status, "error")
            return
        try:
            design, actual = self.service.current_datasets(self.site_id)
        except Exception as exc:
            self.calculate_button.setEnabled(False)
            self.status.setText(str(exc))
            set_status_role(self.status, "error")
            return
        storage_available = bool(
            getattr(self.service.surface_service, "storage_available", True)
        )
        if not storage_available:
            self.calculate_button.setEnabled(False)
            self.status.setText(tr("Shared file storage is unavailable for this connection."))
            set_status_role(self.status, "info")
            return
        if design is None or actual is None:
            self.calculate_button.setEnabled(False)
            missing = tr("Design surface") if design is None else tr("Actual survey")
            self.status.setText(tr("%1 is not configured for this Project.").replace("%1", missing))
            set_status_role(self.status, "info")
            return
        if self.plan.wall_alignment is None:
            self.calculate_button.setEnabled(False)
            self.status.setText(tr("Define a Wall Alignment to calculate profiles."))
            set_status_role(self.status, "info")
            return
        self.calculate_button.setEnabled(True)

    def _set_wall_alignment_state(self, alignment: WallAlignment | None) -> None:
        self.plan.set_wall_alignment(alignment)
        has_alignment = alignment is not None
        self.clear_alignment_button.setEnabled(has_alignment and not self.read_only)
        self.set_alignment_button.setEnabled(not self.read_only)
        self.set_alignment_button.setText(
            tr("Edit Wall Alignment") if has_alignment else tr("Set Wall Alignment")
        )
        if alignment is None:
            self.alignment_metadata.setText(tr("Wall Alignment · not set"))
            self.wall_conformance_state_changed.emit()
            return
        self.alignment_metadata.setText(
            tr("Wall Alignment · %1 vertices · %2 m")
            .replace("%1", str(len(alignment.points)))
            .replace("%2", f"{alignment.length_m:.1f}")
        )
        self.wall_conformance_state_changed.emit()

    def _load_saved_wall_alignment(self) -> None:
        if self.controller is None or self.area is None or self.geometry_revision is None:
            self._set_wall_alignment_state(None)
            return
        try:
            alignment = self.controller.load_wall_alignment(
                self.area, self.geometry_revision
            )
        except Exception as exc:
            self._alignment_load_error = str(exc)
            self._set_wall_alignment_state(None)
            self.status.setText(self._alignment_load_error)
            set_status_role(self.status, "error")
            return
        self._set_wall_alignment_state(alignment)
        if alignment is not None:
            self.status.setText(self.alignment_metadata.text())
            set_status_role(self.status, "info")

    def _sync_measure_controls(self) -> None:
        available = self.profile_plot.mode in ("selected", "overview")
        self.measure_button.setEnabled(available)
        self.measure_button.blockSignals(True)
        self.measure_button.setChecked(self.profile_plot.measure_mode)
        self.measure_button.blockSignals(False)
        self.clear_measure_button.setEnabled(
            self.profile_plot.measure_point_a is not None
            or self.profile_plot.measure_point_b is not None
        )

    def _clear_calculated_result(self) -> None:
        self.result = None
        self.profile_selector.clear()
        self.variant_selector.clear()
        self.variant_selector.setVisible(False)
        self.variant_label.setVisible(False)
        self.profile_plot.set_profile(None)
        self._update_profile_legend()
        self.plan.clear_result()
        self.profile_summary.setText("—")
        self._clear_details()

    def _begin_alignment_drawing(self) -> None:
        if self.read_only:
            return
        self._alignment_before_drawing = self.plan.wall_alignment
        self.plan.begin_alignment_drawing()
        self.status.setText(tr("Draw Wall Alignment: click vertices, then press Enter or double-click to finish. Esc cancels."))
        set_status_role(self.status, "info")

    def _wall_alignment_completed(self, alignment: WallAlignment) -> None:
        previous = self._alignment_before_drawing
        try:
            if self.controller is not None and self.area is not None:
                self.controller.save_wall_alignment(self.area, alignment)
        except Exception as exc:
            self._set_wall_alignment_state(previous)
            self.status.setText(str(exc))
            set_status_role(self.status, "error")
            return
        self._clear_calculated_result()
        self._alignment_load_error = None
        self._set_wall_alignment_state(alignment)
        self.status.setText(self.alignment_metadata.text())
        set_status_role(self.status, "success")
        self._refresh_calculation_availability()

    def _wall_alignment_drawing_cancelled(self) -> None:
        if self.plan.wall_alignment is None:
            self._refresh_calculation_availability()
        else:
            self.alignment_metadata.setText(
                tr("Wall Alignment · %1 vertices · %2 m")
                .replace("%1", str(len(self.plan.wall_alignment.points)))
                .replace("%2", f"{self.plan.wall_alignment.length_m:.1f}")
            )
            self.status.setText(self.alignment_metadata.text())
            set_status_role(self.status, "info")

    def _clear_wall_alignment(self) -> None:
        if self.read_only:
            return
        try:
            if self.controller is not None and self.area is not None:
                self.controller.clear_wall_alignment(self.area)
        except Exception as exc:
            self.status.setText(str(exc))
            set_status_role(self.status, "error")
            return
        self._set_wall_alignment_state(None)
        self._clear_calculated_result()
        self._alignment_load_error = None
        self._refresh_calculation_availability()

    def _edit_design_semantics(self) -> None:
        from ui.dialogs.design_surface_semantics_dialog import DesignSurfaceSemanticsDialog

        try:
            dialog = DesignSurfaceSemanticsDialog(self.service, self.site_id, self)
            if dialog.exec():
                self._clear_calculated_result()
                self._refresh_dataset_metadata()
                self.status.setText(tr("Design surface semantics saved. Calculate profiles again."))
                set_status_role(self.status, "success")
                self._refresh_calculation_availability()
        except Exception as exc:
            self.status.setText(str(exc))
            set_status_role(self.status, "error")

    def _settings(self) -> WallConformanceDiagnosticSettings:
        return WallConformanceDiagnosticSettings(
            spacing_m=self.spacing.value(),
        )

    def calculate(self) -> None:
        self.calculate_button.setEnabled(False)
        self.status.setText(tr("Calculating transverse profiles…"))
        set_status_role(self.status, "info")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.result = self.service.calculate_current(
                self.site_id,
                self.assessment_polygon,
                self.plan.wall_alignment,
                self._settings(),
            )
        except Exception as exc:
            self.result = None
            self.profile_selector.clear()
            self.profile_summary.setText("—")
            self.profile_plot.set_profile(None)
            self._update_profile_legend()
            self._clear_details()
            self.status.setText(str(exc))
            set_status_role(self.status, "error")
            self._refresh_dataset_metadata()
            return
        finally:
            QApplication.restoreOverrideCursor()
            self._refresh_calculation_availability()

        self._refresh_dataset_metadata()
        self.plan.set_result(self.result)
        self.profile_selector.blockSignals(True)
        self.profile_selector.clear()
        self.profile_selector.addItem(tr("Overview · All actual profiles"))
        for index, profile in enumerate(self.result.profile_sections.profiles, start=1):
            self.profile_selector.addItem(
                tr("Profile %1 · Ch. %2 m")
                .replace("%1", str(index))
                .replace("%2", f"{profile.alignment.chainage_m:.1f}")
            )
        self.profile_selector.blockSignals(False)
        self.variant_selector.blockSignals(True)
        self.variant_selector.clear()
        for index, variant in enumerate(self.result.profile_sections.design_variants, start=1):
            self.variant_selector.addItem(
                tr("%1 · %2 · %3 profiles")
                .replace("%1", str(index))
                .replace("%2", self._variant_context_label(variant))
                .replace("%3", str(len(variant.profile_indices)))
            )
            self.variant_selector.setItemData(
                index - 1, variant.signature, Qt.ItemDataRole.ToolTipRole
            )
        self.variant_selector.blockSignals(False)
        has_variants = self.variant_selector.count() > 0
        self.variant_selector.setVisible(has_variants)
        self.variant_label.setVisible(has_variants)
        count = len(self.result.profile_sections.profiles)
        self.status.setText(self._result_status_text())
        set_status_role(self.status, "success")
        if count:
            self.profile_selector.setCurrentIndex(0)
            self._select_profile(0)
        else:
            self.profile_summary.setText(tr("No profiles"))
            self.profile_plot.set_profile(None)
            self._update_profile_legend()
            self.plan.set_selected_profile(-1)
            self._clear_details()

    def _select_profile(self, index: int) -> None:
        if self.result is None:
            self.plan.set_selected_profile(-1)
            self.profile_plot.set_profile(None)
            self._update_profile_legend()
            self.profile_summary.setText("—")
            self._clear_details()
            return
        if index == 0:
            self.profile_selector.blockSignals(True)
            self.profile_selector.setCurrentIndex(0)
            self.profile_selector.blockSignals(False)
            self.plan.set_selected_profile(-1)
            self.profile_plot.set_overview(
                self.result.profile_sections,
                max(0, self.variant_selector.currentIndex()),
                self.result.measurements,
            )
            self._update_profile_legend()
            variant = self.result.profile_sections.design_variants[
                max(0, self.variant_selector.currentIndex())
            ]
            coverage = sum(
                self.profile_plot._profile_has_overview_actual_display(
                    self.result.profile_sections.profiles[i],
                    self.result.measurements[i],
                )
                for i in variant.profile_indices
                if i < len(self.result.measurements)
            )
            self.profile_summary.setText(
                tr("Actual coverage: %1 / %2 profiles · Select a profile to inspect")
                .replace("%1", str(coverage))
                .replace("%2", str(len(variant.profile_indices)))
            )
            self._show_representative_details()
            return
        profile_index = index - 1
        if not 0 <= profile_index < len(self.result.profile_sections.profiles):
            return
        if self.profile_selector.currentIndex() != index:
            self.profile_selector.blockSignals(True)
            self.profile_selector.setCurrentIndex(index)
            self.profile_selector.blockSignals(False)
        profile = self.result.profile_sections.profiles[profile_index]
        self.plan.set_selected_profile(profile_index)
        measurement = (
            self.result.measurements[profile_index]
            if profile_index < len(self.result.measurements) else None
        )
        self.profile_plot.set_profile(profile, measurement)
        self._update_profile_legend()
        self.profile_summary.setText(
            tr("Profile %1").replace("%1", str(profile_index + 1))
        )
        evaluated_actual, _ = self.profile_plot._actual_render_layers()
        if self.profile_plot._profile_actual_measurement_geometry(profile) and not evaluated_actual:
            self.profile_summary.setText(
                self.profile_summary.text()
                + " · "
                + tr("No compatible Actual wall section")
            )
        elif not profile.actual_segments:
            self.profile_summary.setText(
                self.profile_summary.text()
                + " · "
                + tr("No survey data in Design elevation range")
            )
        self._show_profile_details(profile)

    def _select_variant(self, index: int) -> None:
        if self.result is not None and self.profile_selector.currentIndex() == 0:
            self._select_profile(0)

    def _update_profile_legend(self) -> None:
        design, _ = self.profile_plot._geometry()
        actual, _ = self.profile_plot._actual_render_layers()
        context = self.profile_plot._measurement_context_geometry()
        if self.profile_plot.mode == "empty" or not design and not actual and not context:
            self.profile_legend.clear()
            self.profile_legend.hide()
            return
        colors = WallProfilePlot._colors()

        def swatch(key):
            return f'<span style="color:{colors[key].name()}">&#9632;</span>'

        design_roles = [
            role for role in ("face", "berm", "road", "unknown")
            if any(getattr(segment, "semantic_role", None) == role for segment in design)
        ]
        parts = []
        if design_roles:
            parts.append(
                "<b>" + tr("DESIGN") + "</b>"
                + " "
                + "  ".join(
                    f"{swatch(role)} {tr(role.title())}" for role in design_roles
                )
            )
        if actual or context:
            if self.profile_plot.mode == "overview":
                parts.append(f"<b>{tr('ACTUAL')}</b> {swatch('actual')} {tr('All profiles')}")
            elif not actual:
                parts.append(f"<b>{tr('ACTUAL')}</b> {tr('Context')}")
            else:
                context_swatch = (
                    f'<span style="color:{colors["actual"].name()}; opacity:0.5">╌</span>'
                )
                parts.append(
                    f"<b>{tr('ACTUAL')}</b> {swatch('actual')} {tr('Evaluated')}"
                    f"  {context_swatch} {tr('Context')}"
                )
        self.profile_legend.setText(" · ".join(parts))
        self.profile_legend.setVisible(bool(parts))

    def _result_status_text(self) -> str:
        sections = self.result.profile_sections
        profile_count = len(sections.profiles)
        skipped = self._skipped_diagnostics()
        total = len(sections.placement_result.station_chainages_m)
        parts = [tr("%1 profiles").replace("%1", str(profile_count))]
        if skipped:
            parts.append(tr("%1 skipped").replace("%1", str(len(skipped))))
        if total:
            parts.append(
                tr("%1% coverage").replace(
                    "%1", str(round(100 * profile_count / total))
                )
            )
        return " · ".join(parts)

    def _skipped_diagnostics(self):
        seen = set()
        skipped = []
        for diagnostic in getattr(self.result, "diagnostics", ()):
            chainage = getattr(diagnostic, "chainage_m", None)
            station = getattr(diagnostic, "station_index", None)
            key = (station, chainage)
            if (station is None and chainage is None) or key in seen:
                continue
            seen.add(key)
            skipped.append(diagnostic)
        return tuple(skipped)

    def _clear_details(self) -> None:
        self.details_schedule.hide()
        self.details_title.setText(tr("Representative Design"))
        self.details_metadata.clear()
        self._clear_detail_rows()
        self.profile_canvas.refresh_layout()

    def _clear_detail_rows(self) -> None:
        for row in range(self.details_rows.rowCount()):
            self.details_rows.setRowStretch(row, 0)
            self.details_rows.setRowMinimumHeight(row, 0)
        while self.details_rows.count():
            item = self.details_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._detail_stretch_row = None

    def _add_detail_section(self, row: int, title: str) -> int:
        if row:
            self.details_rows.setRowMinimumHeight(row, 6)
            row += 1
        label = QLabel(title)
        label.setObjectName("SummaryValue")
        self.details_rows.addWidget(label, row, 0, 1, 3)
        return row + 1

    def _add_detail_metric(
        self, row: int, label_text: str, value: str, range_text: str = "", tooltip: str = ""
    ) -> int:
        label = QLabel(label_text)
        label.setObjectName("MutedText")
        value_label = QLabel(value)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        range_label = QLabel(range_text)
        range_label.setObjectName("MutedText")
        range_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for widget in (label, value_label, range_label):
            widget.setWordWrap(False)
            widget.setMinimumWidth(0)
            widget.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
        if tooltip:
            for widget in (label, value_label, range_label):
                widget.setToolTip(tooltip)
        self.details_rows.addWidget(label, row, 0)
        self.details_rows.addWidget(value_label, row, 1)
        self.details_rows.addWidget(range_label, row, 2)
        return row + 1

    @staticmethod
    def _measurement_value(value, unit: str, *, signed: bool = False) -> str:
        if value is None:
            return tr("N/A")
        prefix = "+" if signed and value >= 0 else ""
        return f"{prefix}{value:.1f}{unit}"

    @classmethod
    def _summary_range_text(cls, aggregate, unit: str) -> str:
        if aggregate is None or aggregate.minimum is None or aggregate.maximum is None:
            return tr("Insufficient data")
        return (
            tr("Range %1 … %2")
            .replace("%1", cls._measurement_value(aggregate.minimum, unit, signed=True))
            .replace("%2", cls._measurement_value(aggregate.maximum, unit, signed=True))
        )

    def _add_measurement_summary(self, row: int) -> int:
        summary = getattr(self.result, "measurement_summary", None)
        if summary is None:
            return row
        row = self._add_detail_section(row, tr("Measurements"))
        for label, aggregate, unit in (
            (tr("Overall angle"), getattr(summary, "angle_deviation_deg", None), "°"),
            (tr("Upper berm"), getattr(summary, "upper_berm_width_deviation_m", None), " m"),
            (tr("Lower toe"), getattr(summary, "toe_signed_offset_u_m", None), " m"),
        ):
            median = None if aggregate is None else aggregate.median
            mean = None if aggregate is None else aggregate.mean
            tooltip = (
                (
                    tr("Mean %1 · %2")
                    .replace("%1", self._measurement_value(mean, unit, signed=True))
                    .replace("%2", self._summary_range_text(aggregate, unit))
                )
                if mean is not None and aggregate.minimum is not None
                else tr("Insufficient data")
            )
            row = self._add_detail_metric(
                row,
                label,
                tr("Q2 %1").replace(
                    "%1", self._measurement_value(median, unit, signed=True)
                ),
                (
                    tr("%1/%2").replace("%1", str(aggregate.valid_count)).replace(
                        "%2", str(aggregate.total_count)
                    )
                    if median is not None else ""
                ),
                tooltip,
            )
        return row

    def _refresh_detail_schedule(self) -> None:
        if self._detail_stretch_row is not None:
            self.details_rows.setRowStretch(self._detail_stretch_row, 1)
        self.details_content.adjustSize()
        self.details_scroll.widget().updateGeometry()
        self.profile_canvas.refresh_layout()

    def _show_representative_details(self) -> None:
        variants = self.result.profile_sections.design_variants if self.result else ()
        if not variants:
            self._clear_details()
            return
        variant = variants[max(0, self.variant_selector.currentIndex())]
        self.details_schedule.show()
        self.details_title.setText(tr("Representative Design"))
        context = variant.upstream_context
        context_text = (
            tr(context.role.title()) + " " + tr("context")
            if context is not None
            else tr("No context")
        )
        self.details_metadata.setText(
            tr("Variant %1 · %2 profiles · %3")
            .replace("%1", str(max(0, self.variant_selector.currentIndex()) + 1))
            .replace("%2", str(len(variant.profile_indices)))
            .replace("%3", context_text)
        )
        self.details_metadata.setToolTip(variant.signature)
        self._clear_detail_rows()
        row = 0
        row = self._add_measurement_summary(row)
        if context is not None:
            row = self._add_detail_section(row, tr("Upstream %1").replace("%1", tr(context.role.title())))
            row = self._add_detail_metric(
                row,
                tr("W"),
                f"W {context.width_median:.1f} m",
                tooltip=(
                    tr("Range %1–%2 m")
                    .replace("%1", f"{context.width_range[0]:.1f}")
                    .replace("%2", f"{context.width_range[1]:.1f}")
                ),
            )
        counters = {}
        for element in variant.elements:
            counters[element.role] = counters.get(element.role, 0) + 1
            name = f"{tr(element.role.title())} {counters[element.role]}"
            row = self._add_detail_section(row, name)
            if element.role == "face" and element.angle_median is not None:
                row = self._add_detail_metric(
                    row,
                    tr("H / A"),
                    f"{element.height_median:.1f} m · {element.angle_median:.1f}°",
                    tooltip=(
                        f"H {element.height_range[0]:.1f}–{element.height_range[1]:.1f} m"
                        f" · A {element.angle_range[0]:.1f}–{element.angle_range[1]:.1f}°"
                    ),
                )
            else:
                row = self._add_detail_metric(
                    row, tr("W"),
                    f"W {element.width_median:.1f} m",
                    tooltip=(
                        tr("Range %1–%2 m")
                        .replace("%1", f"{element.width_range[0]:.1f}")
                        .replace("%2", f"{element.width_range[1]:.1f}")
                    ),
                )
        if variant.elements:
            terminal = variant.elements[-1]
            row = self._add_detail_section(row, tr("Lower toe"))
            row = self._add_detail_metric(
                row,
                tr("U / dZ (m)"),
                f"{terminal.end_u:.1f} / {terminal.end_dz:.1f}",
            )
        self._detail_stretch_row = row
        self._refresh_detail_schedule()

    def _show_profile_details(self, profile) -> None:
        self.details_schedule.show()
        profile_number = self.profile_selector.currentIndex()
        self.details_title.setText(tr("Profile %1").replace("%1", str(profile_number)))
        design_section = profile.design_section
        signature = getattr(design_section, "topology_signature", "—")
        context = getattr(design_section, "upstream_context", None)
        context_text = (
            tr(context.role.title()) + " " + tr("context")
            if context is not None
            else tr("No context")
        )
        self.details_metadata.setText(
            tr("Ch. %1 m · %2 · %3")
            .replace("%1", f"{profile.alignment.chainage_m:.1f}")
            .replace("%2", signature)
            .replace("%3", context_text)
        )
        self.details_metadata.setToolTip(signature)
        self._clear_detail_rows()
        row = 0
        measurement_index = profile_number - 1
        measurement = (
            self.result.measurements[measurement_index]
            if self.result is not None
            and 0 <= measurement_index < len(self.result.measurements)
            else None
        )
        row = self._add_detail_section(row, tr("Design"))
        if measurement is None:
            # Retain the Design-only diagnostic schedule for incomplete or
            # legacy results that do not expose a measurement record.
            if context is not None:
                row = self._add_detail_section(
                    row, tr("Upstream %1").replace("%1", tr(context.role.title()))
                )
                row = self._add_detail_metric(
                    row, tr("W"), f"W {context.horizontal_width:.1f} m"
                )
            counters = {}
            elements = getattr(design_section, "elements", ())
            for element in elements:
                counters[element.role] = counters.get(element.role, 0) + 1
                row = self._add_detail_section(
                    row, f"{tr(element.role.title())} {counters[element.role]}"
                )
                if element.role == "face" and element.angle_degrees is not None:
                    row = self._add_detail_metric(
                        row,
                        tr("H / A"),
                        f"H {element.vertical_height:.1f} m · A {element.angle_degrees:.1f}°",
                    )
                else:
                    row = self._add_detail_metric(
                        row, tr("W"), f"W {element.horizontal_width:.1f} m"
                    )
            if elements:
                terminal = elements[-1].end
                row = self._add_detail_section(row, tr("Lower toe"))
                row = self._add_detail_metric(
                    row, tr("U / Z"), f"U {terminal.u:.1f} m · Z {terminal.z:.1f} m"
                )
        else:
            design_toe = measurement.design_landmarks.lower_toe.point
            actual_toe = measurement.actual_landmarks.lower_toe.point
            berm_boundary = (
                measurement.actual_landmarks.upper_berm_start.detection.reason_code
                == "boundary_truncated"
            )
            row = self._add_detail_metric(
                row, tr("Overall angle"),
                self._measurement_value(measurement.design_overall_angle_deg, "°"),
            )
            row = self._add_detail_metric(
                row, tr("Upper berm"),
                self._measurement_value(measurement.design_upper_berm_width_m, " m"),
            )
            row = self._add_detail_metric(
                row, tr("Toe U"),
                self._measurement_value(getattr(design_toe, "u", None), " m"),
            )
            row = self._add_detail_section(row, tr("Actual"))
            row = self._add_detail_metric(
                row, tr("Overall angle"),
                self._measurement_value(measurement.actual_overall_angle_deg, "°"),
            )
            row = self._add_detail_metric(
                row, tr("Upper berm"),
                (tr("N/A · Pit boundary") if berm_boundary else
                 self._measurement_value(measurement.actual_upper_berm_width_m, " m")),
            )
            row = self._add_detail_metric(
                row, tr("Toe U"),
                self._measurement_value(getattr(actual_toe, "u", None), " m"),
            )
            row = self._add_detail_section(row, tr("Deviation"))
            row = self._add_detail_metric(
                row, tr("Angle"),
                self._measurement_value(measurement.angle_deviation_deg, "°", signed=True),
            )
            row = self._add_detail_metric(
                row, tr("Berm"),
                (tr("N/A · Pit boundary") if berm_boundary else
                 self._measurement_value(
                     measurement.upper_berm_width_deviation_m, " m", signed=True
                 )),
            )
            row = self._add_detail_metric(
                row, tr("Toe"),
                self._measurement_value(
                    measurement.toe_signed_offset_u_m, " m", signed=True
                ),
            )
        self._detail_stretch_row = row
        self._refresh_detail_schedule()

    def _return_to_overview(self) -> None:
        if self.result is not None and self.profile_selector.currentIndex() > 0:
            self._select_profile(0)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self.profile_plot.mode in ("selected", "overview")
            and self.profile_plot.measure_mode
        ):
            if (
                self.profile_plot.measure_point_a is not None
                and self.profile_plot.measure_point_b is None
            ):
                self.profile_plot.clear_measurement()
            else:
                self.profile_plot.set_measure_mode(False)
            event.accept()
            return True
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self.result is not None
            and self.profile_selector.currentIndex() > 0
        ):
            self._return_to_overview()
            event.accept()
            return True
        return super().eventFilter(watched, event)
