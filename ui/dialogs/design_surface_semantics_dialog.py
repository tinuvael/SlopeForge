"""Compact editor for semantic attributes on the active Design revision."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.localization import tr
from domain.wall_conformance import SurfaceRoleMapping
from ui.widgets.design_system import set_button_role, set_status_role


ROLE_CHOICES = (
    ("face", "Face"), ("berm", "Berm"), ("road", "Road"),
    ("ignore", "Ignore"), ("unknown", "Unknown / unmapped"),
)


class DesignSurfaceSemanticsDialog(QDialog):
    def __init__(self, service, site_id: int, parent=None):
        super().__init__(parent)
        self.service = service
        self.site_id = site_id
        self.inspection = service.inspect_design_semantics(site_id)
        self.saved_mapping = None
        self.setWindowTitle(tr("Design surface semantics"))
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        title = QLabel(tr("Design surface semantics"))
        title.setObjectName("EngineeringSectionTitle")
        root.addWidget(title)
        revision = QLabel(
            tr("Active Design revision R%1").replace(
                "%1", str(self.inspection.dataset.revision_number)
            )
        )
        revision.setObjectName("MutedText")
        root.addWidget(revision)

        attribute_row = QHBoxLayout()
        attribute_row.addWidget(QLabel(tr("Source attribute")))
        self.attribute = QComboBox()
        self.attribute.addItems(sorted(self.inspection.attribute_values))
        preferred = self.attribute.findText(self.inspection.mapping.attribute_name)
        self.attribute.setCurrentIndex(max(0, preferred))
        self.attribute.currentTextChanged.connect(self._populate)
        attribute_row.addWidget(self.attribute, 1)
        root.addLayout(attribute_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [tr("Source value"), tr("Triangles"), tr("Role")]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = set_button_role(QPushButton(tr("Save")), "primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self._populate()

    def _populate(self) -> None:
        attribute = self.attribute.currentText()
        values = self.inspection.attribute_values.get(attribute, ())
        self.table.setRowCount(len(values))
        for row, entry in enumerate(values):
            value_item = QTableWidgetItem(str(entry.value))
            value_item.setData(Qt.ItemDataRole.UserRole, entry.value)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            count_item = QTableWidgetItem(f"{entry.triangle_count:,}")
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count_item.setFlags(count_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            role = self.inspection.mapping.resolve({attribute: entry.value})
            combo = QComboBox()
            for key, label in ROLE_CHOICES:
                combo.addItem(tr(label), key)
            combo.setCurrentIndex(max(0, combo.findData(role)))
            if entry.value == "<missing>":
                combo.setCurrentIndex(combo.findData("unknown"))
                combo.setEnabled(False)
            combo.currentIndexChanged.connect(self._update_summary)
            self.table.setItem(row, 0, value_item)
            self.table.setItem(row, 1, count_item)
            self.table.setCellWidget(row, 2, combo)
        self.table.resizeColumnsToContents()
        self._update_summary()

    def _assignments(self):
        return tuple(
            (
                self.table.item(row, 0).data(Qt.ItemDataRole.UserRole),
                self.table.cellWidget(row, 2).currentData(),
                int(self.table.item(row, 1).text().replace(",", "")),
            )
            for row in range(self.table.rowCount())
        )

    def _update_summary(self) -> None:
        totals = {key: 0 for key, _ in ROLE_CHOICES}
        for _, role, count in self._assignments():
            totals[role] += count
        self.summary.setText(
            tr("Face: %1 · Berm: %2 · Road: %3 · Ignore: %4 · Unknown: %5")
            .replace("%1", f"{totals['face']:,}")
            .replace("%2", f"{totals['berm']:,}")
            .replace("%3", f"{totals['road']:,}")
            .replace("%4", f"{totals['ignore']:,}")
            .replace("%5", f"{totals['unknown']:,}")
        )
        has_face = totals["face"] > 0
        self.save_button.setEnabled(has_face)
        if not has_face:
            set_status_role(self.summary, "error")
            self.summary.setToolTip(tr("Map at least one source value to Face before saving."))
        elif totals["berm"] + totals["road"] == 0:
            set_status_role(self.summary, "warning")
            self.summary.setToolTip(tr("No Berm or Road platform geometry is mapped."))
        elif totals["unknown"]:
            set_status_role(self.summary, "warning")
            self.summary.setToolTip(tr("Some Design triangles remain unknown."))
        else:
            set_status_role(self.summary, "success")
            self.summary.setToolTip("")

    def _save(self) -> None:
        mapping = SurfaceRoleMapping(
            self.attribute.currentText(),
            tuple(
                (value, role)
                for value, role, _ in self._assignments()
                if value != "<missing>"
            ),
        )
        try:
            self.service.save_design_semantics(
                self.site_id, self.inspection.dataset.logical_id, mapping
            )
        except Exception as exc:
            self.summary.setText(str(exc))
            set_status_role(self.summary, "error")
            return
        self.saved_mapping = mapping
        self.accept()
