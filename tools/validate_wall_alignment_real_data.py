"""Read-only real-data validation for explicit Wall Alignment placement.

This is a development diagnostic, not application wiring or persistence.  It
loads the last-used local SlopeForge connection, resolves the active Assessment
and Project surfaces through the existing repositories/storage adapter, and
can render a plan view used to choose the explicit validation alignment.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from math import acos, degrees, hypot
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import select

from app.connection_settings import ConnectionSettingsStore
from application.services.project_surfaces import ProjectSurfaceDatasetService
from database import assessment_models as assessment_orm
from database.connection import create_database_engine, create_session_factory
from database.models import Domain
from database.project_surface_models import ProjectSurfaceDataset
from domain.geometry.operations import (
    clip_datamine_line_by_polygon,
    point_in_polygon,
    segment_intersection,
)
from domain.geometry.surfaces import SurfaceVertex
from domain.geometry.types import PlanLineString, PlanPoint, PlanPolygon
from domain.wall_conformance import (
    SurfaceRoleMapping,
    WallAlignment,
    WallAlignmentSample,
    build_alignment_profile_sections,
    place_profiles_from_alignment,
)
from domain.wall_conformance.alignment_placement import (
    _local_design_run,
    _section_components,
    _shift_segments,
)
from domain.wall_conformance.sections import intersect_surface_with_profile
from domain.wall_conformance.semantic_sections import build_design_section
from infrastructure.files.project_geometry import ProjectGeometryFileStorage
from infrastructure.geometry_import.surfaces import import_surface_geometry
from repositories.project_surface_repository import ProjectSurfaceDatasetRepository


AREA_LOGICAL_ID = "AA-92E59B43"
SPACING_M = 3.0

# Explicit development-only input selected from the rendered real-data plan.
# It is intentionally neither persisted nor used by production domain logic.
MANUAL_ALIGNMENT_XY: tuple[tuple[float, float], ...] = (
    (10372.1, 13048.5),
    (10373.8, 13030.0),
    (10375.9, 13010.0),
    (10378.3, 12990.0),
    (10382.6, 12970.0),
    (10389.0, 12950.0),
    (10400.0, 12930.0),
    (10413.0, 12910.0),
    (10418.5, 12904.0),
)


def _bounds(points: tuple[PlanPoint, ...]) -> tuple[float, float, float, float]:
    return (
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _load_context(profile_id: str | None = None) -> dict[str, Any]:
    store = ConnectionSettingsStore()
    selected_id = profile_id or store.last_profile_id()
    if not selected_id:
        raise RuntimeError("No last-used saved SlopeForge connection exists")
    profile = store.runtime_profile(selected_id)
    settings = profile.to_settings()
    if settings.storage_root is None:
        raise RuntimeError("The selected connection has no Project file storage")
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        area_row = session.execute(
            select(
                assessment_orm.AssessmentArea.domain_id,
                Domain.site_id,
                assessment_orm.AssessmentAreaGeometryRevision.final_geometry_json,
            )
            .join(
                Domain,
                Domain.id == assessment_orm.AssessmentArea.domain_id,
            )
            .join(
                assessment_orm.AssessmentAreaGeometryRevision,
                assessment_orm.AssessmentAreaGeometryRevision.assessment_area_id
                == assessment_orm.AssessmentArea.id,
            )
            .where(
                assessment_orm.AssessmentArea.logical_id == AREA_LOGICAL_ID,
                assessment_orm.AssessmentArea.is_archived.is_(False),
                assessment_orm.AssessmentAreaGeometryRevision.is_active.is_(True),
            )
        ).one()
        datasets = tuple(session.scalars(
            select(ProjectSurfaceDataset)
            .where(ProjectSurfaceDataset.site_id == area_row.site_id)
            .order_by(
                ProjectSurfaceDataset.dataset_kind,
                ProjectSurfaceDataset.revision_number.desc(),
            )
        ))

    repository = ProjectSurfaceDatasetRepository(session_factory)
    storage = ProjectGeometryFileStorage(settings.storage_root)
    service = ProjectSurfaceDatasetService(
        repository, storage, import_surface_geometry
    )
    design_row, design_import = service.load_current(area_row.site_id, "design")
    actual_row, actual_import = service.load_current(area_row.site_id, "actual")
    if design_row.semantic_mapping_json is None:
        raise RuntimeError("The active Design dataset has no saved semantic mapping")
    mapping = SurfaceRoleMapping.from_dict(design_row.semantic_mapping_json)
    return {
        "profile": profile,
        "settings": settings,
        "domain_id": area_row.domain_id,
        "site_id": area_row.site_id,
        "assessment": PlanPolygon.from_dict(area_row.final_geometry_json),
        "datasets": datasets,
        "design_row": design_row,
        "actual_row": actual_row,
        "design": design_import.surface,
        "actual": actual_import.surface,
        "mapping": mapping,
    }


def _inspect(context: dict[str, Any]) -> None:
    assessment = context["assessment"]
    design = context["design"]
    actual = context["actual"]
    mapping = context["mapping"]
    counts = Counter(
        mapping.resolve(triangle.source_attributes)
        for triangle in design.triangles
    )
    inside_face_points = []
    for triangle in design.triangles:
        if mapping.resolve(triangle.source_attributes) != "face":
            continue
        vertices = tuple(design.vertices[index] for index in triangle.vertex_indices)
        point = PlanPoint(
            sum(vertex.x for vertex in vertices) / 3.0,
            sum(vertex.y for vertex in vertices) / 3.0,
        )
        if point_in_polygon(point, assessment):
            inside_face_points.append(point)
    payload = {
        "area": AREA_LOGICAL_ID,
        "domain_id": context["domain_id"],
        "site_id": context["site_id"],
        "assessment_vertices": [
            [point.x, point.y] for point in assessment.ring[:-1]
        ],
        "assessment_bounds": _bounds(assessment.ring[:-1]),
        "design": {
            "logical_id": context["design_row"].logical_id,
            "vertices": len(design.vertices),
            "triangles": len(design.triangles),
            "role_counts": dict(sorted(counts.items())),
            "mapping": mapping.to_dict(),
            "face_centroids_in_assessment": len(inside_face_points),
            "face_centroid_bounds": (
                _bounds(tuple(inside_face_points)) if inside_face_points else None
            ),
        },
        "actual": {
            "logical_id": context["actual_row"].logical_id,
            "vertices": len(actual.vertices),
            "triangles": len(actual.triangles),
        },
        "dataset_revisions": [
            {
                "kind": row.dataset_kind,
                "logical_id": row.logical_id,
                "revision": row.revision_number,
                "triangles": row.triangle_count,
            }
            for row in context["datasets"]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _render_plan(context: dict[str, Any], output: Path) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QColor,
        QGuiApplication,
        QImage,
        QPainter,
        QPen,
        QPolygonF,
    )

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QGuiApplication.instance() or QGuiApplication([])
    assessment = context["assessment"]
    design = context["design"]
    mapping = context["mapping"]
    min_x, min_y, max_x, max_y = _bounds(assessment.ring[:-1])
    margin = max(max_x - min_x, max_y - min_y) * 0.08
    min_x, min_y = min_x - margin, min_y - margin
    max_x, max_y = max_x + margin, max_y + margin
    width, height = 1800, 1200
    scale = min(width / (max_x - min_x), height / (max_y - min_y))
    offset_x = (width - (max_x - min_x) * scale) / 2.0
    offset_y = (height - (max_y - min_y) * scale) / 2.0

    def screen(x: float, y: float) -> QPointF:
        return QPointF(
            offset_x + (x - min_x) * scale,
            height - (offset_y + (y - min_y) * scale),
        )

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    colours = {
        "face": QColor(220, 72, 52, 120),
        "berm": QColor(135, 145, 155, 85),
        "road": QColor(225, 175, 45, 105),
        "unknown": QColor(170, 120, 190, 55),
        "ignore": QColor(220, 220, 220, 30),
    }
    for triangle in design.triangles:
        vertices = tuple(design.vertices[index] for index in triangle.vertex_indices)
        triangle_min_x = min(vertex.x for vertex in vertices)
        triangle_max_x = max(vertex.x for vertex in vertices)
        triangle_min_y = min(vertex.y for vertex in vertices)
        triangle_max_y = max(vertex.y for vertex in vertices)
        if (
            triangle_max_x < min_x
            or triangle_min_x > max_x
            or triangle_max_y < min_y
            or triangle_min_y > max_y
        ):
            continue
        role = mapping.resolve(triangle.source_attributes)
        painter.setPen(QPen(QColor(90, 90, 90, 35), 0.4))
        painter.setBrush(colours.get(role, colours["unknown"]))
        painter.drawPolygon(QPolygonF([
            screen(vertex.x, vertex.y) for vertex in vertices
        ]))

    polygon = QPolygonF([screen(point.x, point.y) for point in assessment.ring])
    painter.setBrush(QColor(50, 120, 210, 25))
    painter.setPen(QPen(QColor(20, 80, 170), 4.0))
    painter.drawPolygon(polygon)
    painter.setPen(QPen(QColor(15, 55, 110), 1.0))
    for index, point in enumerate(assessment.ring[:-1]):
        location = screen(point.x, point.y)
        painter.drawEllipse(location, 4.0, 4.0)
        painter.drawText(
            QRectF(location.x() + 6.0, location.y() - 18.0, 260.0, 34.0),
            Qt.AlignmentFlag.AlignLeft,
            f"{index}: {point.x:.1f}, {point.y:.1f}",
        )
    if MANUAL_ALIGNMENT_XY:
        alignment_polygon = QPolygonF([
            screen(x, y) for x, y in MANUAL_ALIGNMENT_XY
        ])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(20, 170, 80), 5.0))
        painter.drawPolyline(alignment_polygon)
        painter.setBrush(QColor(20, 170, 80))
        for x, y in MANUAL_ALIGNMENT_XY:
            painter.drawEllipse(screen(x, y), 5.0, 5.0)
    painter.end()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output)):
        raise RuntimeError(f"Could not save plan image: {output}")
    application.processEvents()
    print(f"rendered={output}")


def _angle_degrees(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    dot = max(-1.0, min(1.0, first[0] * second[0] + first[1] * second[1]))
    return degrees(acos(dot))


def _crosses_inside(
    first: Any, second: Any, assessment: PlanPolygon
) -> bool:
    point = segment_intersection(
        first.plan_start, first.plan_end, second.plan_start, second.plan_end
    )
    return point is not None and point_in_polygon(point, assessment)


def _run_validation(
    context: dict[str, Any], *, placement_only: bool = False,
    design_only: bool = False,
) -> None:
    if len(MANUAL_ALIGNMENT_XY) < 2:
        raise RuntimeError(
            "Select MANUAL_ALIGNMENT_XY from the rendered real-data plan first"
        )
    alignment = WallAlignment(tuple(PlanPoint(*point) for point in MANUAL_ALIGNMENT_XY))
    placement = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=context["design"],
        assessment_polygon=context["assessment"],
        role_mapping=context["mapping"],
        spacing_m=SPACING_M,
    )
    assembly = None
    if not placement_only:
        assembly = build_alignment_profile_sections(
            alignment=alignment,
            design_surface=context["design"],
            actual_surface=None if design_only else context["actual"],
            assessment_polygon=context["assessment"],
            role_mapping=context["mapping"],
            spacing_m=SPACING_M,
        )
    accepted = placement.placements
    attempted = placement.station_chainages_m
    accepted_chainages = tuple(item.chainage_m for item in accepted)
    attempted_gaps = tuple(
        second - first for first, second in zip(attempted, attempted[1:])
    )
    accepted_gaps = tuple(
        second - first
        for first, second in zip(accepted_chainages, accepted_chainages[1:])
    )
    rotations = tuple(
        _angle_degrees(first.downwall_xy, second.downwall_xy)
        for first, second in zip(accepted, accepted[1:])
    )
    crossing_pairs = tuple(
        (first.station_index, second.station_index)
        for index, first in enumerate(accepted)
        for second in accepted[index + 1 :]
        if _crosses_inside(first, second, context["assessment"])
    )
    reversed_result = place_profiles_from_alignment(
        alignment=WallAlignment(tuple(reversed(alignment.points))),
        design_surface=context["design"],
        assessment_polygon=context["assessment"],
        role_mapping=context["mapping"],
        spacing_m=SPACING_M,
    )
    forward_by_point = {
        (round(item.alignment_point.x, 5), round(item.alignment_point.y, 5)):
        item.downwall_xy
        for item in accepted
    }
    reverse_disagreements = []
    for item in reversed_result.placements:
        key = round(item.alignment_point.x, 5), round(item.alignment_point.y, 5)
        direction = forward_by_point.get(key)
        if direction is None or _angle_degrees(direction, item.downwall_xy) > 1e-5:
            reverse_disagreements.append(key)

    representative_profiles = []
    if assembly is not None and assembly.profiles:
        indices = sorted({0, len(assembly.profiles) // 2, len(assembly.profiles) - 1})
        for index in indices:
            profile = assembly.profiles[index]
            face_count = sum(
                element.role == "face" for element in profile.design_section.elements
            )
            design_points = tuple(
                point
                for element in profile.design_section.elements
                for point in (element.start, element.end)
            )
            representative_profiles.append({
                "profile_index": index,
                "chainage_m": profile.alignment.chainage_m,
                "vertical_extent_m": (
                    max(point.z for point in design_points)
                    - min(point.z for point in design_points)
                ),
                "semantic_sequence": profile.design_section.topology_signature,
                "face_tiers": face_count,
                "design_u_extent_m": max(point.u for point in design_points),
                "design_segment_count": len(profile.design_segments),
                "actual_segment_count": len(profile.actual_segments),
            })

    first_chainage = accepted_chainages[0] if accepted_chainages else None
    last_chainage = accepted_chainages[-1] if accepted_chainages else None
    accepted_span = (
        last_chainage - first_chainage if accepted_chainages else 0.0
    )
    payload = {
        "alignment": {
            "vertices": len(alignment.points),
            "length_m": alignment.length_m,
            "coordinates": MANUAL_ALIGNMENT_XY,
        },
        "placement": {
            "requested_spacing_m": SPACING_M,
            "attempted_stations": len(attempted),
            "accepted_profiles": len(accepted),
            "rejected_stations": len(attempted) - len(accepted),
            "diagnostic_counts": dict(Counter(
                diagnostic.code for diagnostic in placement.diagnostics
            )),
            "max_attempted_spacing_m": max(attempted_gaps, default=0.0),
            "max_accepted_spacing_m": max(accepted_gaps, default=0.0),
        },
        "orientation": {
            "reverse_order_disagreements": reverse_disagreements,
            "reverse_accepted_profiles": len(reversed_result.placements),
            "max_neighbour_rotation_degrees": max(rotations, default=0.0),
            "median_neighbour_rotation_degrees": median(rotations) if rotations else 0.0,
            "sign_flips": sum(rotation > 90.0 for rotation in rotations),
        },
        "coverage": {
            "first_accepted_chainage_m": first_chainage,
            "last_accepted_chainage_m": last_chainage,
            "accepted_span_m": accepted_span,
            "accepted_span_ratio": accepted_span / alignment.length_m,
            "accepted_gaps_over_1_5_spacing": [
                gap for gap in accepted_gaps if gap > SPACING_M * 1.5
            ],
        },
        "geometry": {
            "crossings_inside_assessment": crossing_pairs,
        },
        "sections": {
            "assembled_profiles": (
                None if assembly is None else len(assembly.profiles)
            ),
            "assembly_diagnostic_counts": (
                None
                if assembly is None
                else dict(Counter(
                    diagnostic.code for diagnostic in assembly.diagnostics
                ))
            ),
            "assembly_diagnostic_examples": (
                None
                if assembly is None
                else [
                    {
                        "code": diagnostic.code,
                        "message": diagnostic.message,
                        "station_index": diagnostic.station_index,
                        "chainage_m": diagnostic.chainage_m,
                    }
                    for diagnostic in assembly.diagnostics[:10]
                ]
            ),
            "representative_profiles": representative_profiles,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _probe_section_failure(context: dict[str, Any]) -> None:
    alignment = WallAlignment(tuple(PlanPoint(*point) for point in MANUAL_ALIGNMENT_XY))
    placement = place_profiles_from_alignment(
        alignment=alignment,
        design_surface=context["design"],
        assessment_polygon=context["assessment"],
        role_mapping=context["mapping"],
        spacing_m=SPACING_M,
    )
    selected_indices = sorted({
        0,
        len(placement.placements) // 2,
        len(placement.placements) - 1,
    })
    probes = []
    for placement_index in selected_indices:
        item = placement.placements[placement_index]
        provisional = WallAlignmentSample(
            item.chainage_m,
            SurfaceVertex(item.alignment_point.x, item.alignment_point.y, 0.0),
            item.wall_strike_xy,
            item.downwall_xy,
        )
        half_width = hypot(
            item.plan_end.x - item.alignment_point.x,
            item.plan_end.y - item.alignment_point.y,
        )
        all_segments = intersect_surface_with_profile(
            context["design"],
            provisional,
            role_mapping=context["mapping"],
            half_width_m=half_width,
        )
        supported = set(item.supporting_face_triangle_indices)
        candidates = tuple(
            component
            for component in _section_components(all_segments)
            if any(
                segment.semantic_role == "face"
                and segment.source_triangle_index in supported
                for segment in component
            )
        )
        assessment_fragments = clip_datamine_line_by_polygon(
            PlanLineString((item.plan_start, item.plan_end)),
            context["assessment"],
        )
        assessment_u = tuple(
            (point.x - item.alignment_point.x) * item.downwall_xy[0]
            + (point.y - item.alignment_point.y) * item.downwall_xy[1]
            for fragment in assessment_fragments
            for point in fragment.points
        )
        candidate_payload = []
        for component in candidates:
            local_run = _local_design_run(component, supported)
            faces = tuple(
                segment for segment in component if segment.semantic_role == "face"
            )
            supported_faces = tuple(
                segment
                for segment in faces
                if segment.source_triangle_index in supported
            )
            crest_point = min(
                (
                    point
                    for segment in faces
                    for point in (segment.start, segment.end)
                ),
                key=lambda point: (point.u, -point.z, point.x, point.y),
            )
            preliminary = build_design_section(
                _shift_segments(component, crest_point.u)
            )
            local_faces = tuple(
                segment
                for segment in local_run
                if segment.semantic_role == "face"
            )
            local_crest = min(
                (
                    point
                    for segment in local_faces
                    for point in (segment.start, segment.end)
                ),
                key=lambda point: (point.u, -point.z, point.x, point.y),
            )
            local_semantic = build_design_section(
                _shift_segments(local_run, local_crest.u)
            )
            local_roles = sorted({
                element.role for element in local_semantic.elements
            })
            local_faces_evaluated = tuple(
                element
                for element in local_semantic.elements
                if element.role == "face"
            )
            candidate_payload.append({
                "segment_count": len(component),
                "face_segment_count": len(faces),
                "supported_face_segment_count": len(supported_faces),
                "u_range_m": [
                    min(segment.u_min for segment in component),
                    max(segment.u_max for segment in component),
                ],
                "z_range_m": [
                    min(
                        point.z
                        for segment in component
                        for point in (segment.start, segment.end)
                    ),
                    max(
                        point.z
                        for segment in component
                        for point in (segment.start, segment.end)
                    ),
                ],
                "rising_face_segments": sum(
                    segment.end.z - segment.start.z > 1e-5
                    for segment in faces
                ),
                "descending_face_segments": sum(
                    segment.end.z - segment.start.z < -1e-5
                    for segment in faces
                ),
                "supported_rising_face_segments": sum(
                    segment.end.z - segment.start.z > 1e-5
                    for segment in supported_faces
                ),
                "supported_descending_face_segments": sum(
                    segment.end.z - segment.start.z < -1e-5
                    for segment in supported_faces
                ),
                "minimum_face_u_m": min(
                    min(segment.start.u, segment.end.u) for segment in faces
                ),
                "supported_face_u_range_m": [
                    min(segment.u_min for segment in supported_faces),
                    max(segment.u_max for segment in supported_faces),
                ],
                "preliminary_sequence": preliminary.topology_signature,
                "preliminary_face_tiers": sum(
                    element.role == "face" for element in preliminary.elements
                ),
                "preliminary_rising_face_tiers": sum(
                    element.role == "face" and element.vertical_change > 1e-5
                    for element in preliminary.elements
                ),
                "local_run": {
                    "segment_count": len(local_run),
                    "u_range_m": [
                        min(segment.u_min for segment in local_run),
                        max(segment.u_max for segment in local_run),
                    ],
                    "u_span_m": (
                        max(segment.u_max for segment in local_run)
                        - min(segment.u_min for segment in local_run)
                    ),
                    "z_range_m": [
                        min(
                            point.z
                            for segment in local_run
                            for point in (segment.start, segment.end)
                        ),
                        max(
                            point.z
                            for segment in local_run
                            for point in (segment.start, segment.end)
                        ),
                    ],
                    "semantic_sequence": local_semantic.topology_signature,
                    "face_tiers": sum(
                        element.role == "face"
                        for element in local_semantic.elements
                    ),
                    "rising_face_tiers": sum(
                        element.role == "face"
                        and element.vertical_change > 1e-5
                        for element in local_semantic.elements
                    ),
                    "max_element_width_by_role_m": {
                        role: max(
                            element.horizontal_width
                            for element in local_semantic.elements
                            if element.role == role
                        )
                        for role in local_roles
                    },
                    "face_width_range_m": [
                        min(
                            element.horizontal_width
                            for element in local_faces_evaluated
                        ),
                        max(
                            element.horizontal_width
                            for element in local_faces_evaluated
                        ),
                    ],
                    "face_height_range_m": [
                        min(
                            element.vertical_height
                            for element in local_faces_evaluated
                        ),
                        max(
                            element.vertical_height
                            for element in local_faces_evaluated
                        ),
                    ],
                },
            })
        probes.append({
            "placement_index": placement_index,
            "station_index": item.station_index,
            "chainage_m": item.chainage_m,
            "downwall_xy": item.downwall_xy,
            "face_agreement": item.face_agreement,
            "all_intersection_segments": len(all_segments),
            "connected_components": len(_section_components(all_segments)),
            "supported_components": len(candidates),
            "assessment_u_range_m": (
                [min(assessment_u), max(assessment_u)] if assessment_u else None
            ),
            "candidates": candidate_payload,
        })
    print(json.dumps({"section_failure_probes": probes}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--render", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--placement-only", action="store_true")
    parser.add_argument("--design-only", action="store_true")
    parser.add_argument("--probe-sections", action="store_true")
    args = parser.parse_args()
    context = _load_context(args.profile_id)
    if args.inspect or not (args.render or args.run or args.probe_sections):
        _inspect(context)
    if args.render:
        _render_plan(context, args.render)
    if args.run:
        _run_validation(
            context,
            placement_only=args.placement_only,
            design_only=args.design_only,
        )
    if args.probe_sections:
        _probe_section_failure(context)


if __name__ == "__main__":
    main()
