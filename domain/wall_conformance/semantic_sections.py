"""Derived semantic Design sections and representative variants."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, median

from .models import (
    DesignSection, DesignSectionElement, DesignVariant, RepresentativeElement,
    SectionSegment, TransverseProfile,
)


def _clip_to_positive_u(segment: SectionSegment, tolerance: float) -> SectionSegment | None:
    if segment.u_max < -tolerance:
        return None
    if segment.start.u >= -tolerance:
        return segment
    span = segment.end.u - segment.start.u
    fraction = (0.0 - segment.start.u) / span
    start = type(segment.start)(
        0.0,
        segment.start.z + (segment.end.z - segment.start.z) * fraction,
        segment.start.x + (segment.end.x - segment.start.x) * fraction,
        segment.start.y + (segment.end.y - segment.start.y) * fraction,
    )
    return SectionSegment(
        start, segment.end, segment.source_triangle_index, segment.semantic_role
    )


def build_design_section(
    segments: tuple[SectionSegment, ...], *, tolerance: float = 1e-6
) -> DesignSection:
    """Collapse triangle fragments into ordered engineering elements."""
    upstream = [
        segment
        for segment in segments
        if segment.semantic_role in {"berm", "road"}
        and segment.u_min < -tolerance
        and segment.u_max <= tolerance
    ]
    upstream.sort(key=lambda segment: (segment.u_min, segment.u_max))
    context = None
    if upstream:
        contiguous = [upstream[-1]]
        for segment in reversed(upstream[:-1]):
            if segment.u_max < contiguous[-1].u_min - tolerance:
                break
            if segment.semantic_role != contiguous[-1].semantic_role:
                break
            contiguous.append(segment)
        contiguous.reverse()
        if contiguous[-1].u_max >= -tolerance:
            start = min(contiguous, key=lambda segment: segment.u_min).start
            last = max(contiguous, key=lambda segment: segment.u_max).end
            end = type(last)(0.0, last.z, last.x, last.y) if last.u > 0 else last
            context = DesignSectionElement(
                contiguous[-1].semantic_role,
                start,
                end,
                tuple(sorted({segment.source_triangle_index for segment in contiguous})),
            )
    usable = [
        clipped
        for segment in segments
        if segment.semantic_role != "ignore"
        if (clipped := _clip_to_positive_u(segment, tolerance)) is not None
        if clipped.u_max - clipped.u_min > tolerance
    ]
    usable.sort(key=lambda s: (s.u_min, s.u_max, s.start.z, s.end.z))
    elements: list[DesignSectionElement] = []
    for segment in usable:
        role = segment.semantic_role or "unknown"
        start, end = segment.start, segment.end
        if elements and elements[-1].role == role and start.u <= elements[-1].end.u + tolerance:
            previous = elements[-1]
            far_end = end if end.u >= previous.end.u else previous.end
            elements[-1] = DesignSectionElement(
                role, previous.start, far_end,
                tuple(sorted(set((*previous.source_triangle_indices, segment.source_triangle_index)))),
            )
        else:
            elements.append(
                DesignSectionElement(role, start, end, (segment.source_triangle_index,))
            )
    return DesignSection(tuple(elements), context)


def _range(values):
    return (min(values), max(values))


def _representative_element(
    elements: list[DesignSectionElement], origins: list[float],
) -> RepresentativeElement:
    """Summarise one consistently positioned semantic element."""
    start_u = [element.start.u for element in elements]
    end_u = [element.end.u for element in elements]
    start_dz = [element.start.z - origin for element, origin in zip(elements, origins)]
    end_dz = [element.end.z - origin for element, origin in zip(elements, origins)]
    widths = [element.horizontal_width for element in elements]
    heights = [element.vertical_height for element in elements]
    angles = [
        element.angle_degrees
        for element in elements
        if element.angle_degrees is not None
    ]
    return RepresentativeElement(
        role=elements[0].role,
        start_u=median(start_u),
        start_dz=median(start_dz),
        end_u=median(end_u),
        end_dz=median(end_dz),
        width_median=median(widths),
        width_mean=mean(widths),
        width_range=_range(widths),
        height_median=median(heights),
        height_range=_range(heights),
        angle_median=median(angles) if angles else None,
        angle_range=_range(angles) if angles else None,
    )


def build_design_variants(
    profiles: tuple[TransverseProfile, ...],
) -> tuple[DesignVariant, ...]:
    grouped: dict[
        tuple[str, str | None], list[tuple[int, TransverseProfile]]
    ] = defaultdict(list)
    for index, profile in enumerate(profiles):
        if profile.design_section and profile.design_section.elements:
            context = profile.design_section.upstream_context
            grouped[(
                profile.design_section.topology_signature,
                context.role if context is not None else None,
            )].append((index, profile))
    variants = []
    for (signature, context_role), members in sorted(
        grouped.items(), key=lambda item: (-len(item[1]), item[0][0], item[0][1] or "")
    ):
        representative = []
        element_count = len(members[0][1].design_section.elements)
        for position in range(element_count):
            elements = [profile.design_section.elements[position] for _, profile in members]
            origins = [profile.alignment.origin.z for _, profile in members]
            representative.append(_representative_element(elements, origins))
        representative_context = None
        if context_role is not None:
            contexts = [
                profile.design_section.upstream_context
                for _, profile in members
            ]
            representative_context = _representative_element(
                contexts,
                [profile.alignment.origin.z for _, profile in members],
            )
        variants.append(DesignVariant(
            signature,
            tuple(index for index, _ in members),
            tuple(representative),
            representative_context,
        ))
    return tuple(variants)
