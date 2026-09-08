"""Bounded landmark support derived after profile acceptance.

The caller supplies the already selected local Design run and the one raw
Actual intersection. No surface, stationing or wall-component search lives here.
"""
from __future__ import annotations

from dataclasses import replace

from .measurements import WallMeasurementTolerances, extract_design_landmarks
from .models import (
    DesignSection, ProfileMeasurementContext, SectionSegment, TransverseProfile,
)
from .sections import clip_section_segments_to_u_interval
from .semantic_sections import build_design_section


ACTUAL_DESIGN_Z_MARGIN_M = 1.0


def build_measurement_context(
    profile: TransverseProfile,
    local_design_run: tuple[SectionSegment, ...],
    raw_actual_segments: tuple[SectionSegment, ...],
    tolerances: WallMeasurementTolerances | None = None,
) -> ProfileMeasurementContext:
    """Recover physical endpoints only for Faces already present in the mask.

    Full semantic elements are built using the existing section reducer in a
    temporary positive-U frame, then restored to the profile's unchanged frame.
    Triangle provenance ties the first/last Face to the evaluated section.
    Only their immediate platform neighbours supply boundary evidence. Actual
    support is bounded in U by the candidate envelope plus a full side-fit
    margin. It is deliberately not cropped to Design elevations: an excavated
    lower floor may sit well above or below its Design toe and is still
    physical detector evidence.
    """
    settings = tolerances or WallMeasurementTolerances()
    empty = ProfileMeasurementContext(DesignSection(()), (), (), ())
    if not local_design_run or profile.design_section is None:
        return empty
    offset = min(segment.u_min for segment in local_design_run)
    shifted = tuple(
        replace(segment,
                start=replace(segment.start, u=segment.start.u - offset),
                end=replace(segment.end, u=segment.end.u - offset))
        for segment in local_design_run
    )
    elements = tuple(
        replace(element,
                start=replace(element.start, u=element.start.u + offset),
                end=replace(element.end, u=element.end.u + offset))
        for element in build_design_section(shifted).elements
    )
    assessed_faces = tuple(
        element for element in profile.design_section.elements if element.role == "face"
    )
    indices = [
        index for index, element in enumerate(elements)
        if element.role == "face" and any(
            set(element.source_triangle_indices).intersection(face.source_triangle_indices)
            and min(element.end.u, face.end.u) - max(element.start.u, face.start.u)
            > settings.design_connection_tolerance_m
            for face in assessed_faces
        )
    ]
    if not indices:
        return empty
    first, last = min(indices), max(indices)
    # Following platform is needed for toe evidence, never the next Face.
    stop = last + 1
    if stop < len(elements) and elements[stop].role in {"berm", "road"}:
        stop += 1
    section = DesignSection(elements[first:stop])
    # Keep immediate upper platform plus its preceding Face for berm-start
    # evidence. Do not attach presentation-only upstream_context as proof.
    start = first
    if first > 0 and elements[first - 1].role in {"berm", "road"}:
        start = first - 1
        if start > 0 and elements[start - 1].role == "face":
            start -= 1
    source_indices = {
        triangle for element in elements[start:stop]
        for triangle in element.source_triangle_indices
    }
    design_support = tuple(
        segment for segment in local_design_run
        if segment.source_triangle_index in source_indices
    )
    provisional = replace(
        profile, design_section=section, design_segments=design_support,
        measurement_context=None, external_toe=None,
    )
    landmarks = extract_design_landmarks(provisional, settings)
    points = tuple(
        landmark.point for landmark in (
            landmarks.upper_berm_start, landmarks.upper_crest, landmarks.lower_toe,
        ) if landmark.detection.reliable and landmark.point is not None
    )
    if not points:
        return ProfileMeasurementContext(section, (), (), ())
    # One combined interval keeps continuous berm evidence between landmarks;
    # detection itself still searches only each landmark's local window.
    radius = settings.context_half_window_m
    bounds = min(point.u for point in points) - radius, max(point.u for point in points) + radius
    design_z_interval = (
        min(point.z for segment in design_support for point in (segment.start, segment.end)),
        max(point.z for segment in design_support for point in (segment.start, segment.end)),
    )
    actual_context = clip_section_segments_to_u_interval(raw_actual_segments, *bounds)
    return ProfileMeasurementContext(
        section,
        clip_section_segments_to_u_interval(design_support, *bounds),
        actual_context,
        (bounds,),
        design_z_interval,
    )
