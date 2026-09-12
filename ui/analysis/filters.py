"""Metadata-driven controls for compact Analysis filtering."""

from __future__ import annotations

from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.localization import tr
from application.analysis.models import (
    AnalysisDataset,
    AnalysisField,
    AnalysisFieldType,
    FilterChoice,
    FilterCondition,
    FilterOperator,
    FilterSpec,
)
from ui.assessment_result_presentation import assessment_result_presentation
from ui.widgets.design_system import set_button_role


def _choice_label(field: AnalysisField, value: object) -> str:
    if field.format_hint == "assessment_result":
        return assessment_result_presentation(str(value)).label
    return str(value)


class FilterControl(QWidget):
    """One typed filter editor generated from field metadata."""

    changed = Signal()
    remove_requested = Signal(str)

    def __init__(self, field: AnalysisField, *, removable: bool, parent=None):
        super().__init__(parent)
        self.field = field
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        label = tr(field.label)
        header.addWidget(QLabel(f"{label}, {field.unit}" if field.unit else label), 1)
        if removable:
            remove_button = QToolButton()
            remove_button.setObjectName("AnalysisRemoveFilterButton")
            remove_button.setText("×")
            remove_button.setAutoRaise(True)
            remove_button.setToolTip(
                tr("Remove %1 filter").replace("%1", tr(field.label))
            )
            remove_button.setAccessibleName(remove_button.toolTip())
            remove_button.clicked.connect(
                lambda: self.remove_requested.emit(self.field.key)
            )
            header.addWidget(remove_button)
        self._layout.addLayout(header)

    def condition(self) -> FilterCondition | None:
        raise NotImplementedError

    def set_condition(self, condition: FilterCondition | None) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        self.set_condition(None)


class CategoricalFilter(FilterControl):
    def __init__(
        self,
        field: AnalysisField,
        choices: tuple[FilterChoice, ...],
        *,
        removable: bool,
        parent=None,
    ):
        super().__init__(field, removable=removable, parent=parent)
        self.combo = QComboBox()
        self.combo.addItem(tr("All"), None)
        for choice in choices:
            self.combo.addItem(_choice_label(field, choice.label), choice.value)
        self.combo.currentIndexChanged.connect(self.changed)
        self._layout.addWidget(self.combo)

    def condition(self) -> FilterCondition | None:
        value = self.combo.currentData()
        if value is None:
            return None
        return FilterCondition(
            self.field.key, FilterOperator.CATEGORICAL_EQUALS, value
        )

    def set_condition(self, condition: FilterCondition | None) -> None:
        value = condition.value if condition is not None else None
        self.combo.setCurrentIndex(max(0, self.combo.findData(value)))


class NumericRangeFilter(FilterControl):
    def __init__(self, field: AnalysisField, *, removable: bool, parent=None):
        super().__init__(field, removable=removable, parent=parent)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.minimum = QLineEdit()
        self.maximum = QLineEdit()
        validator = QDoubleValidator(self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.minimum.setValidator(validator)
        self.maximum.setValidator(validator)
        self.minimum.setPlaceholderText(tr("Min"))
        self.maximum.setPlaceholderText(tr("Max"))
        self.minimum.editingFinished.connect(self.changed)
        self.maximum.editingFinished.connect(self.changed)
        row.addWidget(self.minimum)
        row.addWidget(self.maximum)
        self._layout.addLayout(row)

    def condition(self) -> FilterCondition | None:
        lower = self._value(self.minimum)
        upper = self._value(self.maximum)
        if lower is None and upper is None:
            return None
        return FilterCondition(
            self.field.key, FilterOperator.NUMERIC_RANGE, (lower, upper)
        )

    @staticmethod
    def _value(editor: QLineEdit) -> float | None:
        text = editor.text().strip().replace(",", ".")
        return float(text) if text else None

    def set_condition(self, condition: FilterCondition | None) -> None:
        lower, upper = condition.value if condition is not None else (None, None)
        self.minimum.setText("" if lower is None else f"{float(lower):g}")
        self.maximum.setText("" if upper is None else f"{float(upper):g}")


class DateRangeFilter(FilterControl):
    def __init__(self, field: AnalysisField, *, removable: bool, parent=None):
        super().__init__(field, removable=removable, parent=parent)
        self.from_enabled = QCheckBox(tr("From"))
        self.to_enabled = QCheckBox(tr("To"))
        self.minimum = self._date_edit()
        self.maximum = self._date_edit()
        for enabled, editor in (
            (self.from_enabled, self.minimum),
            (self.to_enabled, self.maximum),
        ):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            row.addWidget(enabled)
            row.addWidget(editor, 1)
            self._layout.addLayout(row)
            enabled.toggled.connect(editor.setEnabled)
            enabled.toggled.connect(self.changed)
            editor.dateChanged.connect(self._date_changed)

    @staticmethod
    def _date_edit() -> QDateEdit:
        editor = QDateEdit(QDate.currentDate())
        editor.setCalendarPopup(True)
        editor.setDisplayFormat(
            QLocale.system().dateFormat(QLocale.FormatType.ShortFormat)
        )
        editor.setEnabled(False)
        return editor

    def _date_changed(self) -> None:
        if self.sender().isEnabled():
            self.changed.emit()

    def condition(self) -> FilterCondition | None:
        lower = self.minimum.date().toPython() if self.from_enabled.isChecked() else None
        upper = self.maximum.date().toPython() if self.to_enabled.isChecked() else None
        if lower is None and upper is None:
            return None
        return FilterCondition(
            self.field.key, FilterOperator.DATE_RANGE, (lower, upper)
        )

    def set_condition(self, condition: FilterCondition | None) -> None:
        lower, upper = condition.value if condition is not None else (None, None)
        self.from_enabled.setChecked(lower is not None)
        self.to_enabled.setChecked(upper is not None)
        if lower is not None:
            self.minimum.setDate(QDate(lower.year, lower.month, lower.day))
        if upper is not None:
            self.maximum.setDate(QDate(upper.year, upper.month, upper.day))


def create_filter_control(
    field: AnalysisField,
    choices: tuple[FilterChoice, ...],
    *,
    removable: bool,
) -> FilterControl:
    if field.field_type is AnalysisFieldType.CATEGORICAL:
        return CategoricalFilter(field, choices, removable=removable)
    if field.field_type is AnalysisFieldType.NUMERIC:
        return NumericRangeFilter(field, removable=removable)
    if field.field_type is AnalysisFieldType.DATE:
        return DateRangeFilter(field, removable=removable)
    raise ValueError(f"Unsupported Analysis filter field type: {field.field_type}")


class AnalysisFilterPanel(QFrame):
    """Common context filters plus metadata-driven optional filters."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AnalysisFiltersPanel")
        self.setMinimumWidth(230)
        self.setMaximumWidth(300)
        self.definition: AnalysisDataset | None = None
        self.controls: dict[str, FilterControl] = {}
        self._optional_keys: list[str] = []
        self._options: dict[str, tuple[FilterChoice, ...]] = {}
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        heading = QLabel(tr("FILTERS"))
        heading.setObjectName("AnalysisSectionTitle")
        layout.addWidget(heading)
        scroll = QScrollArea()
        scroll.setObjectName("AnalysisFilterScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        self.filter_layout = QVBoxLayout(host)
        self.filter_layout.setContentsMargins(0, 0, 4, 0)
        self.filter_layout.setSpacing(9)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.add_button = QPushButton(tr("+ Add filter"))
        self.add_menu = QMenu(self.add_button)
        self.add_button.setMenu(self.add_menu)
        set_button_role(self.add_button, "secondary")
        layout.addWidget(self.add_button)
        self.reset_button = QPushButton(tr("Reset filters"))
        set_button_role(self.reset_button, "secondary")
        self.reset_button.clicked.connect(self.reset)
        layout.addWidget(self.reset_button)

    @property
    def optional_keys(self) -> tuple[str, ...]:
        return tuple(self._optional_keys)

    def conditions(self) -> tuple[FilterCondition, ...]:
        return tuple(
            condition
            for control in self.controls.values()
            if (condition := control.condition()) is not None
        )

    def set_dataset(
        self,
        definition: AnalysisDataset,
        options: dict[str, tuple[FilterChoice, ...]],
        saved_spec: FilterSpec,
        optional_keys: tuple[str, ...],
    ) -> None:
        self._updating = True
        try:
            self.definition = definition
            self._options = options
            self._clear_controls()
            saved = {condition.field_key: condition for condition in saved_spec.conditions}
            for field in definition.fields:
                if field.filterable and field.common_filter:
                    self._insert_control(field, saved.get(field.key), removable=False)
            requested = set(optional_keys) | {
                key
                for key in saved
                if not definition.field(key).common_filter
            }
            for field in definition.fields:
                if field.filterable and not field.common_filter and field.key in requested:
                    self._insert_control(field, saved.get(field.key), removable=True)
            self.filter_layout.addStretch()
            self._rebuild_add_menu()
        finally:
            self._updating = False
        self.reset_button.setEnabled(definition.available)

    def add_optional_filter(self, field_key: str) -> bool:
        if self.definition is None or field_key in self.controls:
            return False
        try:
            field = self.definition.field(field_key)
        except KeyError:
            return False
        if not field.filterable or field.common_filter:
            return False
        self._updating = True
        try:
            stretch = self.filter_layout.takeAt(self.filter_layout.count() - 1)
            self._insert_control(field, None, removable=True)
            if stretch is not None:
                self.filter_layout.addItem(stretch)
            self._rebuild_add_menu()
        finally:
            self._updating = False
        self.changed.emit()
        return True

    def remove_optional_filter(self, field_key: str) -> bool:
        if field_key not in self._optional_keys:
            return False
        control = self.controls.pop(field_key)
        self._optional_keys.remove(field_key)
        self.filter_layout.removeWidget(control)
        control.deleteLater()
        self._rebuild_add_menu()
        if not self._updating:
            self.changed.emit()
        return True

    def reset(self) -> None:
        self._updating = True
        try:
            for key, control in tuple(self.controls.items()):
                if key in self._optional_keys:
                    self.filter_layout.removeWidget(control)
                    control.deleteLater()
                    self.controls.pop(key)
                else:
                    control.reset()
            self._optional_keys.clear()
            self._rebuild_add_menu()
        finally:
            self._updating = False
        self.changed.emit()

    def _clear_controls(self) -> None:
        while self.filter_layout.count():
            item = self.filter_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.controls.clear()
        self._optional_keys.clear()

    def _insert_control(
        self,
        field: AnalysisField,
        condition: FilterCondition | None,
        *,
        removable: bool,
    ) -> None:
        control = create_filter_control(
            field, self._options.get(field.key, ()), removable=removable
        )
        control.changed.connect(self._control_changed)
        control.remove_requested.connect(self.remove_optional_filter)
        control.set_condition(condition)
        self.controls[field.key] = control
        if removable:
            self._optional_keys.append(field.key)
        self.filter_layout.addWidget(control)

    def _rebuild_add_menu(self) -> None:
        self.add_menu.clear()
        if self.definition is None:
            self.add_button.setEnabled(False)
            return
        available = [
            field
            for field in self.definition.fields
            if field.filterable
            and not field.common_filter
            and field.key not in self.controls
        ]
        for field in available:
            action = self.add_menu.addAction(tr(field.label))
            action.setData(field.key)
            action.triggered.connect(
                lambda checked=False, key=field.key: self.add_optional_filter(key)
            )
        self.add_button.setEnabled(self.definition.available and bool(available))

    def _control_changed(self) -> None:
        if not self._updating:
            self.changed.emit()
