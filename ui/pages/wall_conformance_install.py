from __future__ import annotations

from app.localization import tr
from ui.pages.wall_conformance_tab import WallConformanceTab
from ui.widgets.design_system import set_status_role


def _sync_initial_availability(tab) -> None:
    """Prevent an obviously unavailable physical-file calculation upfront."""
    refresh = getattr(tab, "_refresh_calculation_availability", None)
    if refresh is not None:
        refresh()
        return
    try:
        design, actual = tab.service.current_datasets(tab.site_id)
    except Exception as exc:
        tab.calculate_button.setEnabled(False)
        tab.status.setText(str(exc))
        set_status_role(tab.status, "error")
        return

    storage_available = bool(
        getattr(tab.service.surface_service, "storage_available", True)
    )
    if not storage_available:
        tab.calculate_button.setEnabled(False)
        tab.status.setText(
            tr(
                "Shared file storage is unavailable for this connection. "
                "Surface metadata can be viewed, but wall conformance cannot be calculated."
            )
        )
        set_status_role(tab.status, "info")
        return
    if design is None or actual is None:
        tab.calculate_button.setEnabled(False)
        missing = tr("Design surface") if design is None else tr("Actual survey")
        tab.status.setText(
            tr("%1 is not configured for this Project.").replace("%1", missing)
        )
        set_status_role(tab.status, "info")
        return
    tab.calculate_button.setEnabled(True)


def install_wall_conformance_tab(assessment_page):
    """Compose the diagnostic tab into an already-built Assessment Area page."""
    revision = assessment_page.area.active_geometry_revision()
    tab = WallConformanceTab(
        assessment_page.context,
        assessment_page.controller.site_id,
        revision.final_geometry_frozen,
        assessment_page,
        area=assessment_page.area,
        geometry_revision=revision,
        controller=assessment_page.controller,
        read_only=assessment_page.read_only,
    )
    assessment_index = assessment_page.tabs.indexOf(assessment_page.assessment_tab)
    insert_index = (
        assessment_index + 1
        if assessment_index >= 0
        else assessment_page.tabs.count()
    )
    assessment_page.tabs.insertTab(insert_index, tab, tr("Wall conformance"))
    assessment_page.wall_conformance_tab = tab
    _sync_initial_availability(tab)
    return tab
