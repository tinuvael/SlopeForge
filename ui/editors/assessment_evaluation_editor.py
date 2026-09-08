"""Usable, revision-safe Qt editor for Assessment Area wall assessments."""
from __future__ import annotations

from app.localization import tr

from copy import deepcopy
from PySide6.QtCore import QDate, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from domain.assessment.evaluation import (
    CONDITION, DESIGN, REQUIRE_MANUAL_SCORE_REASON, AssessmentCriterionResult, AssessmentMatrixTemplate,
    calculate_revision,
)
from application.services.wall_conformance import (
    AssessmentGeometryMeasurementInputs,
    assessment_geometry_inputs_from_measurement_summary,
    compatible_actual_profile_count,
)
from ui.presentation_labels import (
    CRITERION_HELP, compact_criterion_label, criterion_label, domain_message, matrix_label, option_label, result_label,
)
from ui.widgets.design_system import SplitSaveButton

DAMAGE_WARNING = "The matrix has no automatic score for the range of 1–5 features/m²."


class NullableDoubleSpinBox(QDoubleSpinBox):
    """A spin box whose initial/sentinel state is None; explicit zero stays zero."""
    nullableValueChanged = Signal(object)

    def __init__(self, maximum=999.0, minimum=0.0, parent=None):
        super().__init__(parent)
        self._sentinel = float(minimum) - 0.01
        self.setRange(self._sentinel, maximum)
        self.setDecimals(2)
        self.setSpecialValueText("—")
        self.setValue(self._sentinel)
        self.valueChanged.connect(lambda _value: self.nullableValueChanged.emit(self.nullable_value()))
        self.setToolTip(tr("Required for completion. Clear resets the value to —."))

    def nullable_value(self):
        return None if self.value() == self.minimum() else float(self.value())

    def set_nullable_value(self, value):
        self.setValue(self.minimum() if value is None else float(value))

    def clear_value(self):
        self.set_nullable_value(None)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.clear_value(); self.editingFinished.emit(); event.accept(); return
        super().keyPressEvent(event)


class NullableScoreSpinBox(QSpinBox):
    """Integer score editor whose sentinel is displayed as an em dash."""
    def __init__(self, maximum, parent=None):
        super().__init__(parent); self.setRange(-1,int(maximum)); self.setSingleStep(1); self.setSpecialValueText("—"); self.setValue(-1)
    def nullable_value(self): return None if self.value()<0 else int(self.value())
    def set_nullable_value(self,value): self.setValue(-1 if value is None else int(round(value)))
    def clear_value(self): self.set_nullable_value(None)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace): self.clear_value(); self.editingFinished.emit(); event.accept(); return
        super().keyPressEvent(event)
    def stepBy(self, steps):
        if self.value()==0 and steps<0:return
        super().stepBy(steps)


class QuadrantPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.template = self.design = self.condition = None
        self.setMinimumSize(300, 230)

    def set_result(self, template, design, condition):
        self.template, self.design, self.condition = template, design, condition
        self.setToolTip("" if design is None or condition is None else
                        f"DAI: {design:.3f}\nFCI: {condition:.3f}")
        self.update()

    def _axis_foreground(self):
        return self.palette().color(QPalette.ColorRole.Text)

    def _axis_border(self):
        return self.palette().color(QPalette.ColorRole.Mid)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(55, 20, max(10, self.width() - 75), max(10, self.height() - 65))
        if not self.template:
            return
        x = self.template.face_condition_threshold
        y = self.template.design_achievement_threshold
        px, py = rect.left() + x * rect.width(), rect.bottom() - y * rect.height()
        regions = (
            (QRectF(rect.left(), rect.top(), px-rect.left(), py-rect.top()), "#f6df72", tr("Geometry achieved, condition insufficient")),
            (QRectF(px, rect.top(), rect.right()-px, py-rect.top()), "#8bd17c", tr("Good results")),
            (QRectF(rect.left(), py, px-rect.left(), rect.bottom()-py), "#ef7770", tr("Unacceptable results")),
            (QRectF(px, py, rect.right()-px, rect.bottom()-py), "#f2b764", tr("Condition good, geometry unacceptable")),
        )
        for region, colour, label in regions:
            painter.fillRect(region, QColor(colour))
            painter.drawText(region.adjusted(5, 5, -5, -5), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
        painter.setPen(QPen(self._axis_border(), 1))
        painter.drawRect(rect); painter.drawLine(px, rect.top(), px, rect.bottom()); painter.drawLine(rect.left(), py, rect.right(), py)
        painter.setPen(QPen(self._axis_foreground(), 1))
        painter.drawText(rect.left(), self.height()-10, tr("Face condition (FCI) →"))
        painter.save(); painter.translate(15, rect.bottom()); painter.rotate(-90); painter.drawText(0, 0, tr("Design achievement (DAI) →")); painter.restore()
        if self.design is not None and self.condition is not None:
            cx, cy = rect.left()+self.condition*rect.width(), rect.bottom()-self.design*rect.height()
            painter.setBrush(QColor("#1261a0")); painter.setPen(QPen(Qt.GlobalColor.white, 2)); painter.drawEllipse(QRectF(cx-7, cy-7, 14, 14))


class CriterionEditor(QWidget):
    changed = Signal()

    def __init__(self, criterion, parent=None):
        super().__init__(parent)
        self.criterion = criterion
        self.setObjectName("CriterionCard")
        self._restoring = False; self._setting_score = False; self._manual_value = None; self._accepted_value = None; self.override_reason = None; self.notes = ""
        root = QVBoxLayout(self); root.setContentsMargins(7, 5, 7, 5); root.setSpacing(2)
        top = QHBoxLayout(); self.title = QLabel(f"<b>{compact_criterion_label(criterion.id, criterion.name)}</b>"); self.title.setWordWrap(True); top.addWidget(self.title, 1)
        self.help_button = QToolButton(); self.help_button.setText("?"); self.help_button.setAutoRaise(True); top.addWidget(self.help_button)
        if criterion.kind in ("numeric", "damage"):
            self.input = NullableDoubleSpinBox(100 if criterion.id == "visible_drillhole_traces" else 999)
            self.input.setFixedWidth(120)
            self.input.nullableValueChanged.connect(self.changed)
            top.addWidget(self.input)
        else:
            self.input = QComboBox(); self.input.setMaximumWidth(380); self.input.addItem(tr("— select observation —"), None)
            for option in criterion.options:
                self.input.addItem(option_label(option.id, option.label), option.id)
            self.input.currentIndexChanged.connect(self.changed); top.addWidget(self.input, 1)
        help_text = criterion_label(criterion.id,criterion.name) + "\n" + CRITERION_HELP.get(criterion.id, criterion.help_text)
        if criterion.options: help_text += "\n" + "\n".join(f"{option_label(o.id,o.label)}: {o.score:g}" for o in criterion.options)
        self.title.setToolTip(help_text); self.help_button.setToolTip(help_text)
        self.manual_score = NullableScoreSpinBox(criterion.maximum_score); self.manual_score.setFixedWidth(105); self.manual_score.setSuffix(f" / {criterion.maximum_score:g}")
        score_editor=self.manual_score.lineEdit(); score_editor.setAutoFillBackground(True); score_editor.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True); self._native_score_editor_palette=QPalette(score_editor.palette())
        self.score_state_frame=QFrame(); self.score_state_frame.setObjectName("ScoreStateFrame"); score_box=QHBoxLayout(self.score_state_frame); score_box.setContentsMargins(2,2,2,2); score_box.addWidget(self.manual_score)
        self.manual_score.setToolTip(tr("Edit to override the automatic score. Delete or Backspace restores automatic scoring."))
        self.manual_score.editingFinished.connect(self._score_committed); top.addWidget(self.score_state_frame)
        self.manual_score.valueChanged.connect(self._score_value_changed)
        root.addLayout(top)
        self.validation = QLabel(); self.validation.setWordWrap(True); self.validation.setStyleSheet("color:#a33"); self.validation.hide(); root.addWidget(self.validation)
        self.auto_score = QLabel(tr("—")); self.accepted = QLabel(tr("—"))

        self.primary_input = self.input

    def set_primary_input(self, control):
        self.primary_input = control; self._sync_primary()

    def set_help(self, text): self.title.setToolTip(text); self.help_button.setToolTip(text)

    def _sync_primary(self):
        self.primary_input.setEnabled(self._manual_value is None)

    def _set_score_value(self, value):
        self._setting_score=True; self.manual_score.set_nullable_value(value); self._setting_score=False

    def _score_committed(self):
        if self._restoring or self._setting_score: return
        value=self.manual_score.nullable_value(); previous=self._manual_value
        if value is None:
            self._manual_value=None; self.override_reason=None; self._sync_primary(); self.changed.emit(); return
        if previous is None and self._accepted_value is not None and value == self._accepted_value:
            self._set_score_value(self._accepted_value); return
        if previous is not None and value == previous: return
        reason=self.override_reason
        if REQUIRE_MANUAL_SCORE_REASON:
            reason, ok = QInputDialog.getText(self, tr("Override score"), tr("Reason for manual score"), QLineEdit.EchoMode.Normal, reason or "")
            if not ok or not reason.strip(): self._set_score_value(previous if previous is not None else self._accepted_value); self._sync_primary(); return
        self._manual_value=int(value); self.override_reason=(reason or "").strip() or None; self._sync_primary(); self.changed.emit()

    def _score_value_changed(self, _value):
        if not REQUIRE_MANUAL_SCORE_REASON and not self._restoring and not self._setting_score:self._score_committed()

    def set_score(self, result):
        self._accepted_value=result.accepted_score
        self._set_score_value(result.accepted_score)
        if result.manual_score is not None:
            self.manual_score.setObjectName("ManualScore"); self._apply_score_state("#fff4cc")
            self.manual_score.setToolTip(f"{tr('Manual override')}\n{tr('Reason')}: {result.override_reason or '—'}")
        elif result.accepted_score is None:
            self.manual_score.setObjectName("MissingScore"); self._apply_score_state("#fff0f0")
            self.manual_score.setToolTip(tr("Required for completion"))
        else:
            self.manual_score.setObjectName("AutomaticScore"); self._apply_score_state(None)
            self.manual_score.setToolTip(tr("Edit to override the automatic score. Delete or Backspace restores automatic scoring."))

    def _apply_score_state(self, colour):
        editor=self.manual_score.lineEdit()
        if colour:
            palette=QPalette(self._native_score_editor_palette); palette.setColor(QPalette.ColorRole.Base,QColor(colour)); editor.setPalette(palette)
            editor.setStyleSheet(f"background-color:{colour};"); self.score_state_frame.setStyleSheet(f"#ScoreStateFrame{{background:{colour};border:1px solid {colour};border-radius:3px;}}")
        else:
            editor.setPalette(self._native_score_editor_palette); editor.setStyleSheet(""); self.score_state_frame.setStyleSheet("")
        editor.style().unpolish(editor); editor.style().polish(editor); editor.update(); self.score_state_frame.update()

    def restore(self, result):
        if result:
            self._restoring = True
            if isinstance(self.input, NullableDoubleSpinBox): self.input.set_nullable_value(result.raw_numeric_value)
            else:
                index = self.input.findData(result.selected_option_id); self.input.setCurrentIndex(max(index, 0))
            self._manual_value=result.manual_score; self.override_reason=result.override_reason; self.notes=result.notes or ""; self._restoring=False; self._sync_primary()

    def result(self):
        numeric = self.input.nullable_value() if isinstance(self.input, NullableDoubleSpinBox) else None
        selected = self.input.currentData() if isinstance(self.input, QComboBox) else None
        manual = self._manual_value
        return AssessmentCriterionResult(
            self.criterion.id, self.criterion.name, self.criterion.section,
            raw_numeric_value=numeric, selected_option_id=selected,
            manual_score=manual, maximum_score=self.criterion.maximum_score,
            override_reason=self.override_reason if manual is not None else None, notes=self.notes,
        )


class AssessmentAreaEvaluationDialog(QDialog):
    """Edits a private copy. Construction and live previews never mutate source objects."""
    def __init__(self, area, evaluation, draft, save_callback, parent=None, read_only=False,
                 attachment_service=None, unsaved=False):
        super().__init__(parent)
        self.area, self.evaluation, self.source_revision = area, evaluation, draft
        self.draft = deepcopy(draft)
        self.save_callback, self.read_only = save_callback, read_only
        self.attachment_service, self.unsaved = attachment_service, unsaved
        self.template = AssessmentMatrixTemplate.from_dict(self.draft.matrix_template_snapshot)
        self._wall_conformance_summary_provider = None
        self._wall_conformance_availability_provider = None
        self._initializing = True; self._dirty = False; self._allow_close = False; self._preview = deepcopy(self.draft)
        self.setWindowTitle(self._base_title()); self.resize(1120, 780)
        root = QVBoxLayout(self); self.tabs = QTabWidget(); root.addWidget(self.tabs)
        self._general(); self._geometry(); self._condition(); self._matrix(); self._events(); self._attachments(); self._history()
        buttons = QHBoxLayout(); buttons.addStretch()
        self.save_button = SplitSaveButton(lambda: self.save("draft"), lambda: self.save("completed"), completion_text=tr("Complete assessment"))
        self.cancel_button = QPushButton(tr("Close") if read_only else tr("Cancel")); self.cancel_button.clicked.connect(self.reject)
        self.save_button.setVisible(not read_only); buttons.addWidget(self.save_button); buttons.addWidget(self.cancel_button); root.addLayout(buttons)
        self._restore_controls()
        self._connect_general_signals()
        self._initializing = False
        if self.draft.status == "completed":
            self._preview = deepcopy(self.draft)
            self._render_preview(self._preview)
        else:
            self.refresh(mark_dirty=False)
        self._dirty = False; self._update_title()
        if read_only: self._set_read_only()

    def _base_title(self):
        suffix = f" — revision {self.draft.revision_number} ({self.draft.status})" if self.draft.revision_number else ""
        return "Face assessment" + suffix

    def take_tab(self, title: str, parent: QWidget | None = None) -> QWidget:
        """Detach a page from the dialog and give it explicit, durable ownership."""
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                page = self.tabs.widget(index)
                self.tabs.removeTab(index)
                page.setParent(parent)
                return page
        raise LookupError(f"Assessment editor tab not found: {title}")

    def _connect_general_signals(self):
        self.date.dateChanged.connect(self._changed); self.inspector.textChanged.connect(self._changed)
        self.comments.textChanged.connect(self._changed); self.recommendations.textChanged.connect(self._changed)
        self.override_reason.textChanged.connect(self._changed)

    def _general(self):
        page = QWidget(); form = QFormLayout(page)
        self.date = QDateEdit(); self.date.setCalendarPopup(True)
        self.inspector = QLineEdit(); self.override_reason = QLineEdit()
        self.detected = QLabel(tr("Contour drilling detected from a confirmed link") if self.draft.controlled_blasting_present else tr("No confirmed contour event found"))
        self.comments = QTextEdit(); self.recommendations = QTextEdit()
        self.matrix_value = QLabel(matrix_label(self.template.id, self.template.name))
        form.addRow(tr("Assessment date"), self.date); form.addRow(tr("Inspector"), self.inspector)
        form.addRow(tr("Matrix"), self.matrix_value); form.addRow(tr("Detection"), self.detected)
        form.addRow(tr("Manual matrix selection reason"), self.override_reason); form.addRow(tr("Comments"), self.comments); form.addRow(tr("Recommendations"), self.recommendations)
        self.override_reason.setVisible(self.draft.controlled_blasting_detection_source == "manual_override")
        self.tabs.addTab(page, tr("General"))

    def _nullable(self, maximum=999, minimum=0):
        control = NullableDoubleSpinBox(maximum, minimum); control.nullableValueChanged.connect(self._changed); return control

    def _geometry(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(7)
        self.shortfall = self._nullable(90); self.deficit = self._nullable(); self.toe = self._nullable(minimum=-999)
        self.wall_conformance_import_widget = QFrame()
        import_layout = QHBoxLayout(self.wall_conformance_import_widget)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.setSpacing(6)
        self.wall_conformance_import_button = QPushButton(tr("Use Wall Conformance measurements"))
        self.wall_conformance_import_button.setToolTip(
            tr("Wall Conformance Q2 measurements populate the Geometry inputs after confirmation.")
        )
        self.wall_conformance_import_button.clicked.connect(self._use_wall_conformance_measurements)
        import_layout.addWidget(self.wall_conformance_import_button)
        self.wall_conformance_import_source = QLabel(tr("Applied from Wall Conformance Q2"))
        self.wall_conformance_import_source.setObjectName("MutedText")
        self.wall_conformance_import_source.setToolTip(
            tr("The available Geometry inputs were sourced from Wall Conformance Q2 measurements.")
        )
        self.wall_conformance_import_source.hide()
        import_layout.addWidget(self.wall_conformance_import_source)
        import_layout.addStretch()
        layout.addWidget(self.wall_conformance_import_widget)
        self.geometry_editors = {}
        controls = {
            "bench_angle": (tr("Angle deviation, °"), self.shortfall),
            "berm_width": (tr("Berm deviation, m"), self.deficit),
            "toe_position": (tr("Toe deviation, m"), self.toe),
        }
        for criterion in self.template.section(DESIGN).criteria:
            editor = CriterionEditor(criterion)
            label, control = controls[criterion.id]
            editor.title.setText(f"<b>{label}</b>")
            editor.input.hide()
            top = editor.layout().itemAt(0).layout(); top.insertWidget(2,control)
            editor.set_primary_input(control); editor.set_help(self._geometry_help(criterion.id))
            editor.changed.connect(self._changed)
            self.geometry_editors[criterion.id] = editor; layout.addWidget(editor)
        self.additional_geometry_widget = QFrame()
        self.additional_geometry_widget.setProperty("assessmentSection", "additionalGeometryMetrics")
        additional_layout = QVBoxLayout(self.additional_geometry_widget)
        additional_layout.setContentsMargins(0, 8, 0, 0)
        additional_layout.setSpacing(7)
        title = QLabel(f"<b>{tr('Additional geometry metrics')}</b>")
        title.setObjectName("EngineeringSectionTitle")
        additional_layout.addWidget(title)
        self.additional_geometry_values = {}
        metrics = QGridLayout()
        metrics.setContentsMargins(7, 0, 7, 0)
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(5)
        metrics.setColumnStretch(0, 1)
        for row, (key, label) in enumerate((
            ("mean_backbreak_m", tr("Mean backbreak")),
            ("maximum_backbreak_m", tr("Maximum backbreak")),
            ("mean_overbreak_m", tr("Mean overbreak")),
            ("mean_underbreak_m", tr("Mean underbreak")),
            ("contour_rms_deviation_m", tr("Contour RMS")),
        )):
            metric_label = QLabel(label)
            metric_label.setObjectName("AssessmentMetricLabel")
            label_font = self.font()
            label_font.setBold(True)
            metric_label.setFont(label_font)
            value = QLabel(tr("N/A"))
            value.setObjectName("AssessmentMetricValue")
            value.setFont(self.font())
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.additional_geometry_values[key] = value
            metrics.addWidget(metric_label, row, 0)
            metrics.addWidget(value, row, 1)
        additional_layout.addLayout(metrics)
        layout.addWidget(self.additional_geometry_widget)
        layout.addStretch()
        scroll.setWidget(page); self.tabs.addTab(scroll, tr("Geometry"))

    def set_wall_conformance_summary_provider(self, provider, availability_provider=None):
        """Configure the page-owned current-result source after tab composition."""
        self._wall_conformance_summary_provider = provider
        self._wall_conformance_availability_provider = availability_provider
        self.refresh_wall_conformance_import_availability()

    def refresh_wall_conformance_import_availability(self):
        if self.read_only:
            available, reason = False, tr("Archived Assessment Areas and Viewer accounts are read-only.")
        elif self._wall_conformance_summary_provider is None:
            available, reason = False, tr("Wall Conformance is not available for this Assessment Area.")
        else:
            try:
                available, reason = (
                    self._wall_conformance_availability_provider()
                    if self._wall_conformance_availability_provider is not None
                    else (True, "")
                )
            except Exception as exc:
                available, reason = False, str(exc)
        self.wall_conformance_import_button.setEnabled(bool(available))
        self.wall_conformance_import_button.setToolTip(reason or tr(
            "Wall Conformance Q2 measurements populate the Geometry inputs after confirmation."
        ))

    @staticmethod
    def _wall_conformance_value(value, unit, *, signed=False):
        if value is None:
            return tr("N/A")
        prefix = "+" if signed and value >= 0 else ""
        return f"{prefix}{value:.1f}{unit}"

    def _wall_conformance_preview(self, values: AssessmentGeometryMeasurementInputs):
        fields = (
            (tr("Angle shortfall"), self.shortfall.nullable_value(), values.bench_angle_shortfall_deg, "°", False),
            (tr("Berm deficit"), self.deficit.nullable_value(), values.berm_width_deficit_m, " m", False),
            (tr("Toe deviation"), self.toe.nullable_value(), values.toe_offset_from_design_m, " m", True),
        )
        lines = []
        for label, current, proposed, unit, signed in fields:
            if proposed is None:
                lines.append(
                    f"<b>{label}</b><br>{tr('Wall Conformance')}: {tr('N/A')}<br>"
                    f"{tr('Existing value will be kept')}"
                )
            else:
                lines.append(
                    f"<b>{label}</b><br>{tr('Current')}: "
                    f"{self._wall_conformance_value(current, unit, signed=signed)}<br>"
                    f"{tr('Wall Conformance Q2')}: "
                    f"{self._wall_conformance_value(proposed, unit, signed=signed)}"
                )
        additional_fields = (
            (tr("Mean backbreak"), "mean_backbreak_m"),
            (tr("Maximum backbreak"), "maximum_backbreak_m"),
            (tr("Mean overbreak"), "mean_overbreak_m"),
            (tr("Mean underbreak"), "mean_underbreak_m"),
            (tr("Contour RMS"), "contour_rms_deviation_m"),
        )
        for label, name in additional_fields:
            current = getattr(self.draft.measured_wall_geometry, name)
            proposed = getattr(values, name)
            if proposed is None:
                lines.append(
                    f"<b>{label}</b><br>{tr('Wall Conformance')}: {tr('N/A')}<br>"
                    f"{tr('Existing value will be kept')}"
                )
            else:
                lines.append(
                    f"<b>{label}</b><br>{tr('Current')}: "
                    f"{self._wall_conformance_value(current, ' m')}<br>"
                    f"{tr('Wall Conformance')}: {self._wall_conformance_value(proposed, ' m')}"
                )
        return "<br><br>".join(lines)

    def _refresh_additional_geometry_metrics(self):
        measured = self.draft.measured_wall_geometry
        for name, label in self.additional_geometry_values.items():
            value = getattr(measured, name)
            label.setText(tr("N/A") if value is None else f"{value:.1f} m")

    @staticmethod
    def _wall_conformance_dataset_source(dataset):
        return getattr(dataset, "logical_id", None) or None

    def _apply_additional_geometry_metrics(self, values, result):
        measured = self.draft.measured_wall_geometry
        available = False
        for name in self.additional_geometry_values:
            value = getattr(values, name)
            if value is not None:
                setattr(measured, name, value)
                available = True
        if available:
            measured.calculation_method = "wall_conformance_transverse_profiles_v1"
            measured.measurement_method = "survey"
            design_source = self._wall_conformance_dataset_source(
                getattr(result, "design_dataset", None)
            )
            survey_source = self._wall_conformance_dataset_source(
                getattr(result, "actual_dataset", None)
            )
            if design_source:
                measured.design_surface_source = design_source
            if survey_source:
                measured.survey_source = survey_source
        self._refresh_additional_geometry_metrics()

    def _use_wall_conformance_measurements(self):
        if self.read_only or self._wall_conformance_summary_provider is None:
            return
        try:
            result = self._wall_conformance_summary_provider()
            if compatible_actual_profile_count(result) == 0:
                QMessageBox.warning(
                    self, tr("Wall Conformance measurements unavailable"),
                    tr("No compatible Actual wall sections were found.\n"
                       "There are no Wall Conformance measurements to apply."),
                )
                return
            values = assessment_geometry_inputs_from_measurement_summary(
                getattr(result, "measurement_summary", result)
            )
        except Exception as exc:
            QMessageBox.warning(self, tr("Wall Conformance unavailable"), domain_message(str(exc)))
            self.refresh_wall_conformance_import_availability()
            return
        if not values.has_available_value:
            QMessageBox.information(
                self, tr("Wall Conformance unavailable"),
                tr("Wall Conformance has no valid measurements for this Assessment Area."),
            )
            return
        preview = QMessageBox(self)
        preview.setIcon(QMessageBox.Icon.Question)
        preview.setWindowTitle(tr("Use Wall Conformance measurements"))
        preview.setText(tr("Use Wall Conformance measurements?"))
        preview.setInformativeText(self._wall_conformance_preview(values))
        preview.setTextFormat(Qt.TextFormat.RichText)
        apply_button = preview.addButton(tr("Apply"), QMessageBox.ButtonRole.AcceptRole)
        preview.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        preview.exec()
        if preview.clickedButton() is not apply_button:
            return
        if values.bench_angle_shortfall_deg is not None:
            self.shortfall.set_nullable_value(values.bench_angle_shortfall_deg)
        if values.berm_width_deficit_m is not None:
            self.deficit.set_nullable_value(values.berm_width_deficit_m)
        if values.toe_offset_from_design_m is not None:
            self.toe.set_nullable_value(values.toe_offset_from_design_m)
        self._apply_additional_geometry_metrics(values, result)
        self.wall_conformance_import_source.show()
        self._changed()

    def _geometry_rules(self):
        if self.template.id == "controlled_blasting_v1":
            angle = "meets design or is steeper — 50; shortfall ≤3° — 25; >3–5° — 10; >5° — 0"
            toe = "meets design — 10; <1 m — 8; 1–<2 m — 5; ≥2 m — 0"
        else:
            angle = "meets design — 40; then minus 4 points per started degree; ≥10° — 0 (2,4° is scored as 3°)"
            toe = "meets design — 20; <1 m — 15; 1–<2 m — 5; ≥2 m — 0"
        return (f"Angle: {angle}.\nBerm: meets or exceeds design — 40; deficit <1 m — 35; "
                f"1–<2 m — 25; 2–<3 m — 15; ≥3 m — 0.\nToe: {toe}.")

    def _geometry_help(self, criterion_id):
        meaning = tr("Enter 0 when design is met or exceeded; otherwise enter the deviation magnitude used by the scoring matrix.")
        rules = self._geometry_rules().splitlines()
        return criterion_label(criterion_id) + "\n" + meaning + "\n" + {"bench_angle":rules[0],"berm_width":rules[1],"toe_position":rules[2]}[criterion_id]

    def _condition(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); page = QWidget(); layout = QVBoxLayout(page); self.editors = {}
        visible_rules = ">=80%: 20; 70–<80%: 15; 60–<70%: 12; 50–<60%: 8; 30–<50%: 5; 10–<30%: 2; <10%: 0."
        crest_rules = "0 m: 15; >0–<1 m: 12; 1–<2 m: 10; 2–<3 m: 5; ≥3 m: 0."
        for criterion in self.template.section(CONDITION).criteria:
            editor = CriterionEditor(criterion); editor.changed.connect(self._changed); self.editors[criterion.id] = editor; layout.addWidget(editor)
            if criterion.id == "visible_drillhole_traces": editor.validation.setToolTip(visible_rules); editor.title.setToolTip(visible_rules)
            if criterion.id == "crest_loss": editor.validation.setToolTip(crest_rules); editor.title.setToolTip(crest_rules)
        layout.addStretch(); scroll.setWidget(page); self.tabs.addTab(scroll, tr("Face condition"))

    def _matrix(self):
        page = QWidget(); page.setObjectName("LiveResultPanel"); layout = QVBoxLayout(page); layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(7)
        self.live_title = QLabel(f"<b>{tr('Live result')} ({tr('preview')})</b>"); layout.addWidget(self.live_title)
        cards = QHBoxLayout(); self.dai_value = QLabel("—"); self.fci_value = QLabel("—"); self.result_value = QLabel(tr("Complete required inputs"))
        for title, value, caption in (("DAI", self.dai_value, tr("Design Achievement Index")), ("FCI", self.fci_value, tr("Face Condition Index")), (tr("Result"), self.result_value, "")):
            card = QFrame(); card.setObjectName("ResultCard"); box = QVBoxLayout(card); box.setContentsMargins(8, 5, 8, 5); box.addWidget(QLabel(f"<b>{title}</b>")); value.setWordWrap(True); value.setStyleSheet("font-size:17px;font-weight:600;color:#1261a0"); box.addWidget(value)
            if caption: muted = QLabel(caption); muted.setStyleSheet("color:#6b7280;font-size:10px"); muted.setWordWrap(True); box.addWidget(muted)
            cards.addWidget(card)
        layout.addLayout(cards)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.hide()
        layout.addWidget(QLabel(f"<b>{tr('Quadrant')}</b>"))
        self.plot = QuadrantPlot(); layout.addWidget(self.plot, 1)
        self.tabs.addTab(page, tr("Matrix"))

    def _events(self):
        table = QTableWidget(len(self.draft.linked_event_snapshots), 4); table.setHorizontalHeaderLabels([tr("BlastEvent"), tr("Type"), tr("Elevation"), tr("Card revision")]); table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, event in enumerate(self.draft.linked_event_snapshots):
            for column, value in enumerate((event.blast_event_name, event.event_type, f"{event.event_elevation:g}", event.technical_card_revision_id or "—")): table.setItem(row, column, QTableWidgetItem(value))
        self.tabs.addTab(table, tr("Linked events"))

    def _history(self):
        self.history = QTableWidget(0, 8); self.history.setHorizontalHeaderLabels(["№", tr("Date"), tr("Status"), tr("Geometry"), tr("Matrix"), tr("Design"), tr("Condition"), tr("Result")]); self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.refresh_history()
        self.history.cellDoubleClicked.connect(self._open_history)
        container = QWidget(); layout = QVBoxLayout(container); hint = QLabel(tr("Double-click a row to open a read-only historical revision.")); layout.addWidget(hint); layout.addWidget(self.history); self.tabs.addTab(container, tr("History"))

    def refresh_history(self):
        self.history.setRowCount(len(self.evaluation.revisions))
        for row, revision in enumerate(self.evaluation.revisions):
            values = (revision.revision_number, revision.assessment_date or "—", revision.status, revision.assessment_area_geometry_revision_id, revision.matrix_template_id, "—" if revision.design_achievement_index is None else f"{revision.design_achievement_index:.3f}", "—" if revision.face_condition_index is None else f"{revision.face_condition_index:.3f}", result_label(revision.result_label) or "—")
            for column, value in enumerate(values): self.history.setItem(row, column, QTableWidgetItem(str(value)))

    def _attachments(self):
        page = QWidget(); layout = QVBoxLayout(page)
        info = QLabel(tr("Files belong to the assessment and are shared by all revisions.")); info.setWordWrap(True); layout.addWidget(info)
        if self.attachment_service is None:
            layout.addWidget(QLabel(tr("File storage is unavailable.")))
        else:
            photos, documents = self.attachment_service.counts("assessment_evaluation", self.evaluation.id)
            self.attachment_counts = QLabel(f"{tr('Photos')}: {photos}    {tr('Documents')}: {documents}"); layout.addWidget(self.attachment_counts)
            manage = QPushButton(tr("Photos and documents")); manage.clicked.connect(self._open_attachments); layout.addWidget(manage)
        layout.addStretch(); self.tabs.addTab(page, tr("Photos and documents"))

    def _open_attachments(self):
        from ui.dialogs.entity_attachment_dialog import EntityAttachmentDialog
        EntityAttachmentDialog(self.attachment_service, "assessment_evaluation", self.evaluation.id, self,
            read_only=self.read_only or self.area.is_archived, unsaved=self.unsaved).exec()
        photos, documents = self.attachment_service.counts("assessment_evaluation", self.evaluation.id)
        self.attachment_counts.setText(f"{tr('Photos')}: {photos}    {tr('Documents')}: {documents}")

    def _open_history(self, row, _column):
        if 0 <= row < len(self.evaluation.revisions):
            AssessmentAreaEvaluationDialog(self.area, self.evaluation, self.evaluation.revisions[row], None, self,
                read_only=True, attachment_service=self.attachment_service).exec()

    def _restore_controls(self):
        d = self.draft.design_inputs or {}; results = {r.criterion_id: r for r in self.draft.criterion_results}
        if self.draft.assessment_date: self.date.setDate(QDate(self.draft.assessment_date.year, self.draft.assessment_date.month, self.draft.assessment_date.day))
        self.inspector.setText(self.draft.inspector); self.comments.setPlainText(self.draft.comments); self.recommendations.setPlainText(self.draft.recommendations); self.override_reason.setText(self.draft.change_reason or "")
        shortfall = d.get("bench_angle_shortfall_deg")
        if shortfall is None and d.get("design_bench_face_angle_deg") is not None and d.get("actual_bench_face_angle_deg") is not None:
            shortfall = max(d["design_bench_face_angle_deg"] - d["actual_bench_face_angle_deg"], 0)
        deficit = d.get("berm_width_deficit_m")
        if deficit is None and d.get("design_berm_width_m") is not None and d.get("actual_berm_width_m") is not None:
            deficit = max(d["design_berm_width_m"] - d["actual_berm_width_m"], 0)
        self.shortfall.set_nullable_value(shortfall); self.deficit.set_nullable_value(deficit); self.toe.set_nullable_value(d.get("toe_offset_from_design_m"))
        for criterion_id, editor in self.geometry_editors.items(): editor.restore(results.get(criterion_id))
        self._refresh_additional_geometry_metrics()
        face = self.draft.face_condition_inputs or {}
        for criterion_id, editor in self.editors.items():
            result = results.get(criterion_id)
            if result is None and criterion_id in face:
                result = AssessmentCriterionResult(criterion_id, editor.criterion.name, CONDITION, raw_numeric_value=face.get(criterion_id))
            editor.restore(result)

    def _changed(self, *_args):
        if self._initializing or self.read_only: return
        self.refresh(mark_dirty=True)

    def collect(self):
        """Return a new preview object; never mutate self.draft or a saved revision."""
        revision = deepcopy(self.draft)
        revision.assessment_date = self.date.date().toPython(); revision.inspector = self.inspector.text().strip(); revision.comments = self.comments.toPlainText(); revision.recommendations = self.recommendations.toPlainText(); revision.change_reason = self.override_reason.text().strip()
        shortfall, deficit, toe = self.shortfall.nullable_value(), self.deficit.nullable_value(), self.toe.nullable_value()
        revision.design_inputs = {"bench_angle_shortfall_deg": shortfall, "berm_width_deficit_m": deficit, "toe_offset_from_design_m": toe}
        values = {"bench_angle": shortfall, "berm_width": deficit, "toe_position": abs(toe) if toe is not None else None}
        results = []
        for criterion in self.template.section(DESIGN).criteria:
            override = self.geometry_editors[criterion.id].result()
            results.append(AssessmentCriterionResult(
                criterion.id, criterion.name, DESIGN, raw_numeric_value=values[criterion.id],
                manual_score=override.manual_score, maximum_score=criterion.maximum_score,
                override_reason=override.override_reason, notes=override.notes,
            ))
        face_inputs = {}
        for criterion in self.template.section(CONDITION).criteria:
            result = self.editors[criterion.id].result(); results.append(result)
            face_inputs[criterion.id] = result.raw_numeric_value if result.raw_numeric_value is not None else result.selected_option_id
        revision.face_condition_inputs = face_inputs; revision.criterion_results = results
        calculate_revision(revision)
        return revision

    def refresh(self, mark_dirty=True):
        if self._initializing: return
        preview = self.collect(); self._preview = preview
        if mark_dirty: self._dirty = True; self._update_title()
        self._render_preview(preview)

    def _render_preview(self, preview):
        """Render already-stored or live-calculated values without recalculating them."""
        by_id = {r.criterion_id: r for r in preview.criterion_results}
        for cid, editor in self.geometry_editors.items():
            result = by_id[cid]; editor.set_score(result)
            editor = self.geometry_editors[cid]
            editor.auto_score.setText(tr("—") if result.automatic_score is None else f"{result.automatic_score:g}")
            editor.accepted.setText(tr("—") if result.accepted_score is None else f"{result.accepted_score:g}")
        for cid, editor in self.editors.items():
            result = by_id[cid]; editor.set_score(result); editor.auto_score.setText(tr("—") if result.automatic_score is None else f"{result.automatic_score:g}"); editor.accepted.setText(tr("—") if result.accepted_score is None else f"{result.accepted_score:g}")
            value = result.raw_numeric_value
            if cid == "damage" and value is not None and 1 <= value <= 5:
                if DAMAGE_WARNING not in editor.help_button.toolTip(): editor.help_button.setToolTip(editor.help_button.toolTip()+"\n"+DAMAGE_WARNING)
            editor.validation.hide()
        design = "—" if preview.design_achievement_index is None else f"{preview.design_achievement_index:.3f}"
        condition = "—" if preview.face_condition_index is None else f"{preview.face_condition_index:.3f}"
        result = result_label(preview.result_label) or tr("Result available after all criteria are completed")
        self.summary.setText(f"{tr('Design')}: {preview.design_achievement_points if preview.design_achievement_points is not None else '—'} / 100; DAI: {design}\n{tr('Condition')}: {preview.face_condition_points if preview.face_condition_points is not None else '—'} / 100; FCI: {condition}\n{tr('Result')}: {result}")
        self.dai_value.setText(design); self.fci_value.setText(condition)
        self.result_value.setText(result_label(preview.result_label) or tr("Complete required inputs"))
        self.plot.set_result(self.template, preview.design_achievement_index, preview.face_condition_index)

    def _applied_rule(self, result):
        if result.raw_numeric_value is None: return tr("Required")
        if result.criterion_id == "damage" and 1 <= result.raw_numeric_value <= 5: return tr("Intermediate range 1–5 features/m²")
        return tr("Automatic matrix threshold")

    def save(self, status):
        if self.read_only:
            QMessageBox.warning(self, tr("Read only"), tr("Archived Assessment Areas and Viewer accounts cannot change the evaluation."))
            return False
        try:
            revision = self.collect()
            calculate_revision(revision, require_complete=status == "completed")
            self.save_callback(self.evaluation, revision, status)
        except Exception as exc:  # persistence errors must keep the dialog open
            QMessageBox.critical(self, tr("Assessment not saved"), f"Could not save the assessment. Changes remain in the form.\n\n{domain_message(str(exc))}")
            return False
        self._dirty = False; self._allow_close = True; self._update_title()
        message = tr("Draft saved.") if status == "draft" else tr("Assessment completed and saved.")
        QMessageBox.information(self, tr("Assessment saved"), message)
        super().accept(); return True

    def _update_title(self): self.setWindowTitle(self._base_title() + (" *" if self._dirty else ""))

    def _confirm_close(self):
        if not self._dirty or self.read_only: return "discard"
        box = QMessageBox(QMessageBox.Icon.Warning, "Unsaved changes", "The assessment has unsaved changes.", parent=self)
        save = box.addButton("Save draft", QMessageBox.ButtonRole.AcceptRole); discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole); keep = box.addButton("Continue editing", QMessageBox.ButtonRole.RejectRole)
        box.exec(); clicked = box.clickedButton()
        if clicked is save: return "saved" if self.save("draft") else "keep"
        if clicked is discard: return "discard"
        return "keep"

    def reject(self):
        action = self._confirm_close()
        if action == "discard": self._allow_close = True; super().reject()

    def closeEvent(self, event):
        if self._allow_close or self.read_only: event.accept(); return
        action = self._confirm_close()
        if action == "discard": self._allow_close = True; event.accept()
        else: event.ignore()

    def _set_read_only(self):
        for widget in self.findChildren(QWidget):
            if widget is self.cancel_button: continue
            if isinstance(widget, (QLineEdit, QTextEdit, QDateEdit, QDoubleSpinBox, QComboBox, QCheckBox, QPushButton, QToolButton)):
                widget.setEnabled(False)
