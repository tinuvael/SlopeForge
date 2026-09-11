"""Focused static coverage checks for active user-interface localization."""
from __future__ import annotations

import ast
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
# SlopeForge Updater is a separate English-only administrative executable and
# updater_main.py deliberately does not install the engineering UI translator.
UPDATER_UI = ROOT / "ui" / "updater_window.py"
LOCALIZED_UI = [
    path for path in sorted((ROOT / "ui").rglob("*.py")) if path != UPDATER_UI
]
ACTIVE = [
    ROOT / "main.py",
    *sorted((ROOT / "app").rglob("*.py")),
    *LOCALIZED_UI,
    *sorted((ROOT / "widgets").rglob("*.py")),
]
INVARIANTS = {
    "SlopeForge", "DAI", "FCI", "UCS", "RQD", "GSI", "FF", "Jn", "Jr", "Ja", "Jw", "Q′",
    "MPa", "m", "mm", "kg", "ms", "m²", "m³", "%", "—",
    # QFileDialog filter syntax and format/product names are technical strings;
    # keeping them invariant avoids translating extension masks differently
    # between locales.
    "Project Lines (*.dxf *.dm *.dmx);;AutoCAD DXF (*.dxf);;Datamine files (*.dm *.dmx)",
    "Geometry files (*.dxf *.dm *.dmx);;AutoCAD DXF (*.dxf);;Datamine files (*.dm *.dmx)",
}
UI_CALLS = {"QLabel", "QPushButton", "QCheckBox", "QGroupBox", "setText", "setWindowTitle", "setToolTip", "setPlaceholderText", "addAction", "addTab", "addRow", "setHorizontalHeaderLabels", "addItem"}
MESSAGE_BOX_CALLS = {"critical", "information", "question", "warning"}
SELF_READABLE_LANGUAGE_ITEMS = {"English", "Русский", "en", "ru"}


def tree(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def literal_tr_sources():
    found = set()
    for path in ACTIVE:
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr" and node.args:
                if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
    return found


def russian_catalog():
    messages = {}
    for catalog_path in sorted((ROOT / "translations").glob("slopeforge_ru*.ts")):
        root = ET.parse(catalog_path).getroot()
        for context in root.findall("context"):
            if context.findtext("name") != "SlopeForge":
                continue
            for message in context.findall("message"):
                translation = message.find("translation")
                if translation is not None and translation.get("type") not in {"unfinished", "obsolete", "vanished"}:
                    messages[message.findtext("source", "")] = "".join(translation.itertext())
    return messages


def test_every_literal_tr_call_has_finished_russian_translation():
    catalogue = russian_catalog()
    missing = sorted(source for source in literal_tr_sources()
                     if source not in INVARIANTS
                     and not catalogue.get(source))
    assert missing == []


def test_important_indirect_presentation_sources_are_in_catalogue():
    """Guard strings passed through translating helpers or runtime mappings."""
    required = {
        "Plan / assessment areas",
        "Import / Update Project Lines",
        "Assessment result distribution",
        "Domain summary",
        "Elevation intervals",
        "No completed assessment",
        "Geometry achieved, condition insufficient",
        "Good results",
        "Unacceptable results",
        "Condition good, geometry unacceptable",
        "No Project Lines",
        "Import lines",
        "Update lines",
        "Import",
        "No Domain geometry",
        "DAI / FCI over time",
        "Daily average · all completed assessments",
        "No completed data",
        "Enter a blast event name",
        "Select the blast event type: production or contour",
        "Enter the blast event horizon",
        "Geometry file contains no valid contour drillholes",
        "Geometry file contains no suitable lines",
        "Could not import geometry file: ",
        "Dataset %1 was not found",
        "Assessment Area",
        "Created",
        "Updated",
        "Assessment completed",
        "Assessment draft saved",
        "Details", "Boundary", "Review", "Area details", "Area name",
        "Assessment date", "Context", "Domain", "Project Lines", "Source",
        "Geometry", "Elevation interval", "Traced spans", "Connectors", "Links",
        "Potential events", "Production", "Contour blast", "Getting started",
        "Enter Area details.", "Verify Domain and active Project Lines.",
        "Continue to Boundary.", "Click near a Project Line to snap.",
        "Follow the line to trace the boundary.", "Move away to create a connector.",
        "Close the boundary when finished.", "Traced Project Line", "Connector",
        "Snap point", "Linked-event preview unavailable", "Boundary valid",
        "Elevation summary derived", "Linked-event preview completed", "Total",
        "Project plan", "Define Assessment boundary", "Assessment footprint",
        "Click to draw · Wheel to zoom · Middle drag to pan",
        "Boundary closed and valid.",
        "Inspect the plan, then use Draw boundary.",
        "Planned", "In preparation", "Blasted", "Assessed", "Completed", "Draft",
        "Archived", "Active", "Inactive", "Enabled", "Disabled",
        "Density", "Spacing, m", "Persistence, m", "Hole spacing, m",
        "Mean collar deviation, m", "Max collar deviation, m",
        "Mean toe deviation, m", "Max toe deviation, m",
        "Mean backbreak, m", "Maximum backbreak, m", "Mean overbreak, m",
        "Mean underbreak, m", "Contour RMS deviation, m", "Measurement method",
        "Survey", "Photogrammetry", "Laser scan", "Manual measurement", "Visual estimate",
        # Drillhole dataset cards pass these labels through translating helpers
        # rather than literal tr(...) calls, so keep them in explicit coverage.
        "Actual holes", "Matched", "Low-confidence matches", "Missing design holes",
        "Additional holes", "Mean collar deviation", "Max collar deviation",
        "Mean toe deviation", "Max toe deviation", "Total drilling",
        "Min / max depth", "Mean inclination", "Mean hole azimuth",
        "Contour length", "Mean spacing", "Spacing min / max", "Alignment azimuth",
        "Import design drillholes to calculate drilling values automatically.",
        "Optional. Import as-drilled holes to populate Execution fact automatically.",
        "Design drillholes changed after this fact was imported. Re-import the fact to refresh automatic comparison values.",
        "Auto from design holes", "Auto from as-drilled",
    }
    # Category labels are stable presentation text paired with canonical userData.
    from domain.attachments.policy import ATTACHMENT_CATEGORIES
    required.update(label for categories in ATTACHMENT_CATEGORIES.values()
                    for _key, label in categories)
    catalogue = russian_catalog()
    assert sorted(source for source in required if not catalogue.get(source)) == []


def test_technical_card_and_domain_geometry_indirect_sources_are_in_catalogue():
    required = {
        "Design line / collar offset, m", "Area, m²", "Average hole depth, m",
        "Volume, m³", "Estimated joint friction angle", "Indicative cohesion",
        "Polygons", "Selected", "Mode",
        "Select a polygon to edit its vertices.",
        "Drag vertex handles to adjust geometry.",
        "Use Add polygon to create another boundary.",
        "Click the plan to add vertices.", "Undo removes the last vertex.",
        "Finish closes and validates the polygon.",
    }
    catalogue = russian_catalog()
    assert sorted(source for source in required if not catalogue.get(source)) == []


def test_history_presentation_sources_are_in_catalogue():
    from ui.presentation_labels import HISTORY_ACTION_SOURCES

    templates = {
        "Added %1 photos", "Added %1 documents", 'Added photo "%1"',
        'Added document "%1"', 'Photo metadata updated "%1"',
        'Document metadata updated "%1"', 'Deleted photo "%1"',
        'Deleted document "%1"', "Changed field: %1",
        "Manual blast event linked · %1", "Technical Card R%1",
        "Geometry R%1", "Evaluation R%1",
    }
    catalogue = russian_catalog()
    assert sorted(source for source in HISTORY_ACTION_SOURCES | templates
                  if not catalogue.get(source)) == []


def test_obvious_widget_literals_do_not_bypass_translation():
    raw = []
    for path in ACTIVE:
        for node in ast.walk(tree(path)):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name not in UI_CALLS and name not in MESSAGE_BOX_CALLS:
                continue
            # Only display arguments are audited; later addItem arguments are stable userData.
            if name in MESSAGE_BOX_CALLS:
                arguments = node.args[1:]
            else:
                arguments = node.args if name == "setHorizontalHeaderLabels" else node.args[:1]
            for argument in arguments:
                values = argument.elts if isinstance(argument, (ast.List, ast.Tuple)) else [argument]
                for value in values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and any(ch.isalpha() for ch in value.value):
                        if value.value not in SELF_READABLE_LANGUAGE_ITEMS and value.value not in INVARIANTS:
                            raw.append(f"{path.relative_to(ROOT)}:{node.lineno}: {value.value}")
    assert raw == []


def test_representative_active_screen_labels_are_translated():
    catalogue = russian_catalog()
    expected = {
        "General information": "Общая информация", "Geomechanics": "Геомеханика",
        "Blast design": "Проект БВР", "Execution fact": "Фактическое выполнение",
        "Overview": "Обзор", "Assessment": "Оценка", "Result": "Результат",
        "Design results": "Результаты достижения проектной геометрии",
        "Criterion": "Критерий", "Entered / selected": "Введено / выбрано",
        "Domains": "Домены", "Analytics": "Аналитика", "Map": "План",
        "Projects": "Проекты", "Blast events": "Взрывные события",
        "Archive": "Архивировать", "Add project": "Добавить проект",
        "Add domain": "Добавить домен", "Add blast event": "Добавить взрывное событие",
        "Add assessment area": "Добавить участок оценки",
        "Assessment areas": "Участки оценки", "Horizon": "Горизонт",
        "Interval": "Интервал", "Block": "Блок",
        "Contour blast": "Контурный взрыв",
    }
    assert {source: catalogue[source] for source in expected} == expected
    assert catalogue["Project tree"] == "Дерево проекта"


def test_issue_136_russian_engineering_terminology():
    catalogue = russian_catalog()
    assert catalogue["Contour RMS deviation, m"] == "RMS отклонения контура, м"
    assert catalogue["Spacing, m"] == "Шаг, м"
    assert catalogue["Hole spacing, m"] == "Шаг между скважинами, м"
    assert catalogue["Mean toe deviation, m"] == "Среднее отклонение забоя скважины, м"
    assert catalogue["Max toe deviation, m"] == "Максимальное отклонение забоя скважины, м"
    assert catalogue["Mean backbreak, m"] == "Среднее разрушение бровки, м"
    assert catalogue["Maximum backbreak, m"] == "Максимальное разрушение бровки, м"
    assert catalogue["Standard deviation"] == "Стандартное отклонение"
    for source in ("RMS dependencies are unavailable or incompatible.",
                   "Unexpected RMS calculation error.", "Invalid RMS input data."):
        assert "RMS" in catalogue[source]
        assert "СКО" not in catalogue[source]


def test_active_ui_has_no_runtime_russian_fallback_bridge():
    source = (ROOT / "app" / "localization.py").read_text(encoding="utf-8")
    assert "RUSSIAN_RUNTIME_FALLBACKS" not in source


def test_analysis_metadata_and_staged_dataset_labels_are_localized():
    from application.analysis.catalog import ANALYSIS_DATASETS

    catalogue = russian_catalog()
    required = {
        dataset.label for dataset in ANALYSIS_DATASETS
    } | {
        dataset.row_semantics for dataset in ANALYSIS_DATASETS
    } | {
        field.label for dataset in ANALYSIS_DATASETS for field in dataset.fields
    }
    assert sorted(source for source in required if source not in INVARIANTS
                  and not catalogue.get(source)) == []


def test_event_type_selector_translates_display_but_keeps_canonical_user_data():
    source = (ROOT / "ui" / "dialogs" / "blast_event_dialog.py").read_text(encoding="utf-8")
    assert 'addItem(tr("Production"), "production")' in source
    assert 'addItem(tr("Contour blast"), "contour")' in source
    assert '"event_type": self.kind.currentData()' in source


def test_attachment_categories_translate_labels_but_keep_codes_as_user_data():
    source = (ROOT / "ui" / "dialogs" / "entity_attachment_dialog.py").read_text(encoding="utf-8")
    assert "addItem(tr(label), code)" in source
    assert "subtype=self.category.currentData()" in source
    assert "subtype=category.currentData()" in source


def test_internal_group_ids_are_not_rendered_in_technical_card():
    source = (ROOT / "ui" / "editors" / "technical_card_editor.py").read_text(encoding="utf-8")
    assert 'QGroupBox(f"{display_name} — {group.group_type}")' not in source
    assert 'QLabel(display_name)' in source


def test_header_tree_and_block_tabs_use_translated_presentation_labels():
    header = (ROOT / "ui" / "header.py").read_text(encoding="utf-8")
    tree_source = (ROOT / "ui" / "widgets" / "project_tree.py").read_text(encoding="utf-8")
    block = (ROOT / "ui" / "pages" / "block_page.py").read_text(encoding="utf-8")
    for label in ("Add project", "Add domain", "Add blast event", "Add assessment area", "Archive"):
        assert f'tr("{label}")' in header
    for label in ("Blast events", "Assessment areas", "Horizon", "Interval", "Block"):
        assert f"tr('{label}')" in tree_source or f'tr("{label}")' in tree_source
    for label in ("Geomechanics", "Blast design", "Execution fact"):
        assert f'tr("{label}")' in block
