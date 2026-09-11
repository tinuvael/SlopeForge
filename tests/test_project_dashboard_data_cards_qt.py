from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

try:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QWidget

    from ui.pages.dashboards.project_geometry_card import ProjectGeometryCard
    from ui.pages.dashboards.widgets import (
        CompactSummaryList,
        ProjectLinesCard,
        SummaryRow,
    )
except ImportError as exc:
    pytest.skip(f"Qt runtime unavailable: {exc}", allow_module_level=True)


CARD_HEIGHT = 192
ROW_HEIGHT = 44
GEOMETRY_GAP = 22
FIRST_ROW_BASELINE_TOLERANCE = 6


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _surface(revision: int, name: str):
    return SimpleNamespace(
        revision_number=revision,
        source_format="datamine",
        vertex_count=124027,
        triangle_count=248042,
        imported_at=datetime(2026, 8, 23, 18, 6),
        source_files_json=[{"original_filename": name}],
    )


def _domain_row():
    return SummaryRow(
        "1",
        "North-east domain with a deliberately long name",
        "Blast events: 123 · Production: 100 · Contour: 23",
        "Assessment areas: 99/100 · DAI 0.75 · FCI 0.62",
    )


def _project_lines_dataset():
    return SimpleNamespace(
        name="var1cor_st_with_a_deliberately_long_dataset_name",
        imported_at=datetime(2026, 8, 23, 16, 10),
        source_file_name="var1cor_st_with_a_very_long_source_filename.dmx",
        is_active=True,
    )


def test_project_data_cards_share_header_and_first_row_baselines_without_overflow(app):
    host = QWidget()
    host.resize(1200, CARD_HEIGHT)
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(9)

    domain = CompactSummaryList(
        "Domain summary",
        visible_rows=3,
        show_go_to=True,
        row_height=ROW_HEIGHT,
        row_spacing=3,
    )
    domain.set_rows([_domain_row()])

    lines = ProjectLinesCard()
    lines.add_header_action("Add")
    lines.set_datasets([_project_lines_dataset()])

    geometry = ProjectGeometryCard()
    geometry.set_datasets(
        _surface(1, "design_surface_with_a_long_name_tr.dmx"),
        _surface(2, "actual_surface_with_a_long_name_tr.dmx"),
    )

    cards = (domain, lines, geometry)
    for card in cards:
        card.setMinimumWidth(0)
        card.setFixedHeight(CARD_HEIGHT)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(card)

    host.show()
    app.processEvents()

    header_y = [card.heading.mapTo(host, QPoint(0, 0)).y() for card in cards]
    assert max(header_y) - min(header_y) <= 1

    domain_row = domain.list.itemWidget(domain.list.item(0))
    lines_row = lines.list.itemWidget(lines.list.item(0))
    domain_title = domain_row.findChild(QLabel, "RelatedEntityTitle")
    lines_title = lines_row.findChild(QLabel, "RelatedEntityTitle")
    design_title = geometry._rows["design"][0]
    actual_title = geometry._rows["actual"][0]
    assert domain_title is not None and lines_title is not None

    # QListWidget item geometry and label metrics vary by a few pixels between
    # Qt platform styles. Keep this guard tight enough to catch a visibly
    # displaced first row without making normal Windows style rounding fail.
    first_title_y = [
        domain_title.mapTo(host, QPoint(0, 0)).y(),
        lines_title.mapTo(host, QPoint(0, 0)).y(),
        design_title.mapTo(host, QPoint(0, 0)).y(),
    ]
    assert max(first_title_y) - min(first_title_y) <= FIRST_ROW_BASELINE_TOLERANCE

    widths = [card.width() for card in cards]
    assert max(widths) - min(widths) <= 2
    assert geometry.geometry().right() <= host.contentsRect().right()
    assert domain_row.geometry().right() <= domain.list.viewport().rect().right()
    assert lines_row.geometry().right() <= lines.list.viewport().rect().right()

    design_y = design_title.mapTo(geometry, QPoint(0, 0)).y()
    actual_y = actual_title.mapTo(geometry, QPoint(0, 0)).y()
    expected_offset = ROW_HEIGHT + GEOMETRY_GAP
    assert expected_offset - 3 <= actual_y - design_y <= expected_offset + 3

    host.close()
    app.processEvents()


def test_persistent_dashboard_rows_rebind_after_pre_layout_wide_geometry(app):
    """Rows populated before final dashboard sizing must not keep stale width."""
    domain = CompactSummaryList(
        "Domain summary",
        visible_rows=3,
        show_go_to=True,
        row_height=ROW_HEIGHT,
        row_spacing=3,
    )
    domain.resize(760, CARD_HEIGHT)
    domain.show()
    domain.set_rows([_domain_row()])

    lines = ProjectLinesCard()
    lines.add_header_action("Add")
    lines.resize(760, CARD_HEIGHT)
    lines.show()
    lines.set_datasets([_project_lines_dataset()])
    app.processEvents()

    domain.resize(330, CARD_HEIGHT)
    lines.resize(330, CARD_HEIGHT)
    app.processEvents()

    domain_row = domain.list.itemWidget(domain.list.item(0))
    lines_row = lines.list.itemWidget(lines.list.item(0))
    assert domain_row.geometry().right() <= domain.list.viewport().rect().right()
    assert lines_row.geometry().right() <= lines.list.viewport().rect().right()
    assert domain_row.width() <= domain.list.viewport().width()
    assert lines_row.width() <= lines.list.viewport().width()

    domain.close()
    lines.close()
    app.processEvents()


def test_compact_summary_list_uses_extra_height_and_scrolls_when_constrained(app):
    rows = [
        SummaryRow(str(index), f"Area {index}", "Elevation interval", "DAI — · FCI —")
        for index in range(6)
    ]
    card = CompactSummaryList("Areas", visible_rows=3, row_height=ROW_HEIGHT)
    card.set_rows(rows)

    card.resize(420, 430)
    card.show()
    app.processEvents()
    compact_baseline = ROW_HEIGHT * 3 + 4
    assert card.list.height() > compact_baseline
    assert card.list.maximumHeight() == 16777215
    assert not card.list.verticalScrollBar().isVisible()
    first = card.list.itemWidget(card.list.item(0))
    assert first.geometry().width() <= card.list.viewport().width()

    card.setFixedHeight(210)
    app.processEvents()
    assert card.list.height() < ROW_HEIGHT * len(rows)
    assert card.list.verticalScrollBar().isVisible()
    assert card.geometry().height() == 210
    card.close()
    app.processEvents()
