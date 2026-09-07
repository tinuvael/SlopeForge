from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

import application.services.wall_conformance as wall_conformance_service_module
from application.services.wall_conformance import (
    WallConformanceDiagnosticService,
    WallConformanceDiagnosticSettings,
    WallConformanceUnavailableError,
)
from domain.geometry.surfaces import SurfaceTriangle, SurfaceVertex, TriangleSurface
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    WallAlignment,
    build_alignment_profile_sections,
)


def _bench(dx: float = 0.0) -> TriangleSurface:
    vertices = (
        SurfaceVertex(-5 + dx, 0, 10),
        SurfaceVertex(0 + dx, 0, 10),
        SurfaceVertex(-5 + dx, 20, 10),
        SurfaceVertex(0 + dx, 20, 10),
        SurfaceVertex(5 + dx, 0, 0),
        SurfaceVertex(5 + dx, 20, 0),
        SurfaceVertex(10 + dx, 0, 0),
        SurfaceVertex(10 + dx, 20, 0),
    )
    specifications = (
        ((0, 1, 2), 5),
        ((1, 3, 2), 5),
        ((1, 4, 3), 2),
        ((4, 5, 3), 2),
        ((4, 6, 5), 3),
        ((6, 7, 5), 3),
    )
    return TriangleSurface(
        vertices,
        tuple(
            SurfaceTriangle(indices, source_attributes={"COLOUR": colour})
            for indices, colour in specifications
        ),
    )


def _area() -> PlanPolygon:
    return PlanPolygon(
        (
            PlanPoint(-2, 4),
            PlanPoint(8, 4),
            PlanPoint(8, 16),
            PlanPoint(-2, 16),
            PlanPoint(-2, 4),
        )
    )


class FakeSurfaceService:
    def __init__(
        self,
        *,
        storage_available=True,
        design=True,
        actual=True,
        actual_dx=1.0,
        semantic_mapping=None,
    ):
        self.storage_available = storage_available
        self.actual_dx = actual_dx
        self.design = (
            SimpleNamespace(
                logical_id="DESIGN-1",
                revision_number=2,
                source_format="datamine",
                triangle_count=6,
                semantic_mapping_json=semantic_mapping,
            )
            if design
            else None
        )
        self.actual = (
            SimpleNamespace(
                logical_id="ACTUAL-1",
                revision_number=7,
                source_format="datamine",
                triangle_count=6,
            )
            if actual
            else None
        )

    def current(self, _site_id, dataset_kind):
        return self.design if dataset_kind == "design" else self.actual

    def load_dataset(self, _site_id, logical_id):
        if logical_id == "DESIGN-1":
            return self.design, SimpleNamespace(surface=_bench())
        if logical_id == "ACTUAL-1":
            return self.actual, SimpleNamespace(surface=_bench(dx=self.actual_dx))
        raise AssertionError(logical_id)


def _alignment() -> WallAlignment:
    return WallAlignment((PlanPoint(0.0, 4.0), PlanPoint(0.0, 16.0)))


def test_diagnostic_service_loads_active_project_surfaces_and_builds_profiles() -> None:
    surface_service = FakeSurfaceService()
    service = WallConformanceDiagnosticService(surface_service)
    expected = build_alignment_profile_sections(
        alignment=_alignment(), design_surface=_bench(),
        actual_surface=_bench(dx=surface_service.actual_dx),
        assessment_polygon=_area(),
        role_mapping=service.mapping_for_dataset(surface_service.design)[0],
        spacing_m=5.0,
    )
    result = service.calculate_current(
        1, _area(), _alignment(), WallConformanceDiagnosticSettings(spacing_m=5.0)
    )
    assert result.design_dataset.logical_id == "DESIGN-1"
    assert result.actual_dataset.logical_id == "ACTUAL-1"
    assert len(result.profile_sections.profiles) == len(expected.profiles) == 4
    assert result.diagnostics == expected.diagnostics


def test_diagnostic_service_uses_alignment_contract_and_retains_diagnostics(monkeypatch) -> None:
    mapping = SurfaceRoleMapping("COLOUR", ((2, "face"), (5, "berm"), (3, "road")))
    direct = build_alignment_profile_sections(
        alignment=_alignment(), design_surface=_bench(), actual_surface=_bench(dx=1.0),
        assessment_polygon=_area(), role_mapping=mapping, spacing_m=7.25,
    )
    captured = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return direct

    monkeypatch.setattr(wall_conformance_service_module, "build_alignment_profile_sections", fake_builder)
    result = WallConformanceDiagnosticService(
        FakeSurfaceService(semantic_mapping=mapping.to_dict())
    ).calculate_current(1, _area(), _alignment(), WallConformanceDiagnosticSettings(spacing_m=7.25))
    assert captured["design_surface"] is not captured["actual_surface"]
    assert captured["role_mapping"] == mapping
    assert captured["alignment"] == _alignment()
    assert captured["spacing_m"] == 7.25
    assert result.profile_sections is direct


def test_changing_actual_changes_sections_but_not_alignment_design_placement(monkeypatch) -> None:
    first = WallConformanceDiagnosticService(FakeSurfaceService(actual_dx=1.0)).calculate_current(
        1, _area(), _alignment(), WallConformanceDiagnosticSettings(5.0)
    )
    second = WallConformanceDiagnosticService(FakeSurfaceService(actual_dx=2.0)).calculate_current(
        1, _area(), _alignment(), WallConformanceDiagnosticSettings(5.0)
    )
    placement = lambda result: tuple(
        (profile.alignment.chainage_m, profile.alignment.origin, profile.alignment.normal_xy)
        for profile in result.profile_sections.profiles
    )
    actual = lambda result: tuple(
        tuple((segment.start, segment.end) for segment in profile.actual_segments)
        for profile in result.profile_sections.profiles
    )
    assert placement(first) == placement(second)
    assert actual(first) != actual(second)


def test_alignment_builder_failures_are_translated_with_cause(monkeypatch) -> None:
    original = ValueError("no profiles")
    monkeypatch.setattr(
        wall_conformance_service_module,
        "build_alignment_profile_sections",
        lambda **_kwargs: (_ for _ in ()).throw(original),
    )
    with pytest.raises(WallConformanceUnavailableError, match="Wall Alignment") as caught:
        WallConformanceDiagnosticService(FakeSurfaceService()).calculate_current(
            1, _area(), _alignment()
        )
    assert caught.value.__cause__ is original


def test_active_settings_and_service_source_exclude_legacy_contract() -> None:
    assert [field.name for field in fields(WallConformanceDiagnosticSettings)] == [
        "spacing_m"
    ]
    source = Path(wall_conformance_service_module.__file__).read_text(encoding="utf-8")
    for obsolete_name in (
        "build_v2_profile_sections",
        "build_transverse_profiles",
        "sample_wall_alignment",
        "select_primary_crest_line",
        "select_design_alignment",
        "tangent_window_m",
    ):
        assert obsolete_name not in source

    ui_source = (
        Path(__file__).parents[1] / "ui" / "pages" / "wall_conformance_tab.py"
    ).read_text(encoding="utf-8")
    assert "Strike smoothing radius" not in ui_source
    assert "tangent_window" not in ui_source


def test_diagnostic_service_refuses_database_only_storage_mode() -> None:
    service = WallConformanceDiagnosticService(
        FakeSurfaceService(storage_available=False)
    )

    with pytest.raises(WallConformanceUnavailableError, match="Shared file storage"):
        service.calculate_current(1, _area(), _alignment())


def test_diagnostic_service_reports_missing_active_surface() -> None:
    service = WallConformanceDiagnosticService(FakeSurfaceService(actual=False))

    with pytest.raises(WallConformanceUnavailableError, match="Actual survey"):
        service.calculate_current(1, _area(), _alignment())


def test_diagnostic_service_reports_missing_design_surface() -> None:
    service = WallConformanceDiagnosticService(FakeSurfaceService(design=False))

    with pytest.raises(WallConformanceUnavailableError, match="Design surface"):
        service.calculate_current(1, _area(), _alignment())


class FakeButton:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def isEnabled(self):
        return self.enabled


class FakeLabel:
    def __init__(self, text=""):
        self._text = text

    def setText(self, text):
        self._text = str(text)

    def text(self):
        return self._text


class FakeTabs:
    def __init__(self, entries):
        self.entries = list(entries)

    def indexOf(self, widget):
        try:
            return self.entries.index(widget)
        except ValueError:
            return -1

    def count(self):
        return len(self.entries)

    def insertTab(self, index, widget, label):
        self.entries.insert(index, widget)
        return index

    def tabText(self, index):
        return getattr(self.entries[index], "_tab_label", None)


def _fake_tab_class(surface_service):
    class FakeWallConformanceTab:
        def __init__(self, context, site_id, polygon, parent=None, **_kwargs):
            self.context = context
            self.site_id = site_id
            self.polygon = polygon
            self.parent = parent
            self.service = WallConformanceDiagnosticService(surface_service)
            self.calculate_button = FakeButton()
            self.status = FakeLabel("Ready to calculate.")

    return FakeWallConformanceTab


def _fake_assessment_page():
    assessment_tab = object()
    overview = SimpleNamespace(_tab_label="Overview")
    assessment = assessment_tab
    linked = SimpleNamespace(_tab_label="Linked events")
    page = SimpleNamespace(
        context=object(),
        controller=SimpleNamespace(site_id=42),
        area=SimpleNamespace(
            active_geometry_revision=lambda: SimpleNamespace(
                final_geometry_frozen=_area()
            )
        ),
        assessment_tab=assessment_tab,
        tabs=FakeTabs([overview, assessment, linked]),
        read_only=False,
    )
    return page


def test_installer_places_wall_conformance_after_assessment(monkeypatch) -> None:
    import ui.pages.wall_conformance_install as installer

    monkeypatch.setattr(installer, "set_status_role", lambda label, role: label)
    monkeypatch.setattr(
        installer,
        "WallConformanceTab",
        _fake_tab_class(FakeSurfaceService()),
    )
    page = _fake_assessment_page()

    tab = installer.install_wall_conformance_tab(page)
    tab._tab_label = "Wall conformance"

    assert page.tabs.indexOf(tab) == 2
    assert page.tabs.tabText(2) == "Wall conformance"
    assert page.wall_conformance_tab is tab
    assert tab.site_id == 42
    assert tab.calculate_button.isEnabled()


def test_installer_disables_calculation_when_actual_surface_is_missing(monkeypatch) -> None:
    import ui.pages.wall_conformance_install as installer

    monkeypatch.setattr(installer, "set_status_role", lambda label, role: label)
    monkeypatch.setattr(
        installer,
        "WallConformanceTab",
        _fake_tab_class(FakeSurfaceService(actual=False)),
    )
    page = _fake_assessment_page()

    tab = installer.install_wall_conformance_tab(page)

    assert not tab.calculate_button.isEnabled()
    assert "Actual survey" in tab.status.text()
