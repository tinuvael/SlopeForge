from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import atan2, degrees, hypot, isfinite
from typing import Any

from domain.geometry.surfaces import SurfaceVertex


SEMANTIC_ROLES = {"face", "berm", "road", "ignore", "unknown"}
TRANSITION_KINDS = {"crest", "toe"}


def _semantic_token(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text.casefold()
    if not number.is_finite():
        return text.casefold()
    normalized = format(number.normalize(), "f")
    return "0" if normalized in {"", "-0"} else normalized


def semantic_value_token(value: object) -> str:
    """Return the stable comparison token used for imported attribute values."""
    return _semantic_token(value)


@dataclass(frozen=True)
class SurfaceRoleMapping:
    """Map one imported triangle attribute to canonical engineering roles."""

    attribute_name: str
    assignments: tuple[tuple[object, str], ...]

    def __post_init__(self) -> None:
        if not self.attribute_name.strip():
            raise ValueError("Surface role attribute name must be non-empty")
        seen: set[str] = set()
        for source_value, role in self.assignments:
            if role not in SEMANTIC_ROLES:
                raise ValueError(f"Unsupported surface semantic role: {role!r}")
            token = _semantic_token(source_value)
            if not token:
                raise ValueError("Surface role source value must be non-empty")
            if token in seen:
                raise ValueError(f"Duplicate surface role mapping for {source_value!r}")
            seen.add(token)

    def resolve(self, attributes: dict[str, Any]) -> str:
        wanted = self.attribute_name.casefold()
        raw = next(
            (value for key, value in attributes.items() if str(key).casefold() == wanted),
            None,
        )
        token = _semantic_token(raw)
        for source_value, role in self.assignments:
            if _semantic_token(source_value) == token:
                return role
        return "unknown"

    def to_dict(self) -> dict[str, object]:
        def json_value(value: object) -> object:
            if value is None or isinstance(value, (bool, int, float, str)):
                return value
            return str(value)

        return {
            "attribute_name": self.attribute_name,
            "assignments": [
                {"value": json_value(value), "role": role}
                for value, role in self.assignments
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SurfaceRoleMapping":
        assignments = payload.get("assignments", [])
        if not isinstance(assignments, list):
            raise ValueError("Surface semantic assignments must be a list")
        return cls(
            str(payload.get("attribute_name", "")),
            tuple(
                (item["value"], str(item["role"]))
                for item in assignments
                if isinstance(item, dict) and "value" in item and "role" in item
            ),
        )


PROTOTYPE_DESIGN_ROLE_MAPPING = SurfaceRoleMapping(
    "COLOUR", ((2, "face"), (5, "berm"), (3, "road"))
)


@dataclass(frozen=True)
class WallTransitionLine:
    kind: str
    points: tuple[SurfaceVertex, ...]

    def __post_init__(self) -> None:
        if self.kind not in TRANSITION_KINDS:
            raise ValueError(f"Unsupported wall transition kind: {self.kind!r}")
        if len(self.points) < 2:
            raise ValueError("Wall transition line requires at least two points")

    @property
    def plan_length(self) -> float:
        return sum(
            hypot(b.x - a.x, b.y - a.y)
            for a, b in zip(self.points, self.points[1:])
        )


@dataclass(frozen=True)
class DesignAlignmentBoundary:
    line: WallTransitionLine
    face_patch_index: int
    interior_points: tuple[SurfaceVertex, ...]
    source: str = "face/platform"


@dataclass(frozen=True)
class DesignBoundaryEdge:
    """One semantically classified Design boundary edge with Face provenance."""

    kind: str
    first: SurfaceVertex
    second: SurfaceVertex
    face_patch_index: int
    face_interior: SurfaceVertex
    source: str

    def __post_init__(self) -> None:
        if self.kind not in TRANSITION_KINDS:
            raise ValueError(f"Unsupported boundary edge kind: {self.kind!r}")


@dataclass(frozen=True)
class ExternalWallBoundary:
    """Locally confirmed upper components of one evaluated wall region."""

    upper_components: tuple[DesignAlignmentBoundary, ...]

    def __post_init__(self) -> None:
        if not self.upper_components:
            raise ValueError("External wall boundary requires an upper component")

    @property
    def upper_lines(self) -> tuple[WallTransitionLine, ...]:
        return tuple(component.line for component in self.upper_components)


@dataclass(frozen=True)
class DesignWallTopology:
    transitions: tuple[WallTransitionLine, ...]
    alignment_boundaries: tuple[DesignAlignmentBoundary, ...]
    boundary_edges: tuple[DesignBoundaryEdge, ...] = ()


@dataclass(frozen=True)
class WallAlignmentSample:
    chainage_m: float
    origin: SurfaceVertex
    tangent_xy: tuple[float, float]
    normal_xy: tuple[float, float]
    boundary_component_index: int = 0

    def __post_init__(self) -> None:
        if not isfinite(self.chainage_m) or self.chainage_m < 0:
            raise ValueError("Wall alignment chainage must be finite and non-negative")
        tx, ty = self.tangent_xy
        nx, ny = self.normal_xy
        if not all(isfinite(value) for value in (tx, ty, nx, ny)):
            raise ValueError("Wall alignment vectors must be finite")
        if abs(hypot(tx, ty) - 1.0) > 1e-6:
            raise ValueError("Wall alignment tangent must be unit length")
        if abs(hypot(nx, ny) - 1.0) > 1e-6:
            raise ValueError("Wall alignment normal must be unit length")
        if abs(tx * nx + ty * ny) > 1e-6:
            raise ValueError("Wall alignment normal must be perpendicular to tangent")


@dataclass(frozen=True)
class UpperCrestStationEvaluation:
    """Local external-boundary decision for one sampled crest station."""

    valid: bool
    reason: str
    assessment_u_interval: tuple[float, float] | None = None
    external_toe: SectionPoint | None = None
    local_design_segments: tuple[SectionSegment, ...] = ()


@dataclass(frozen=True)
class SectionPoint:
    u: float
    z: float
    x: float
    y: float


@dataclass(frozen=True)
class SectionSegment:
    start: SectionPoint
    end: SectionPoint
    source_triangle_index: int
    semantic_role: str | None = None

    @property
    def u_min(self) -> float:
        return min(self.start.u, self.end.u)

    @property
    def u_max(self) -> float:
        return max(self.start.u, self.end.u)


@dataclass(frozen=True)
class DesignSectionElement:
    role: str
    start: SectionPoint
    end: SectionPoint
    source_triangle_indices: tuple[int, ...]

    @property
    def horizontal_width(self) -> float:
        return abs(self.end.u - self.start.u)

    @property
    def vertical_change(self) -> float:
        return self.end.z - self.start.z

    @property
    def vertical_height(self) -> float:
        return abs(self.vertical_change)

    @property
    def angle_degrees(self) -> float | None:
        if self.role != "face":
            return None
        return degrees(atan2(self.vertical_height, self.horizontal_width))


@dataclass(frozen=True)
class DesignSection:
    elements: tuple[DesignSectionElement, ...]
    upstream_context: DesignSectionElement | None = None

    @property
    def topology_signature(self) -> str:
        return "-".join(element.role.upper() for element in self.elements)


@dataclass(frozen=True)
class RepresentativeElement:
    role: str
    start_u: float
    start_dz: float
    end_u: float
    end_dz: float
    width_median: float
    width_mean: float
    width_range: tuple[float, float]
    height_median: float
    height_range: tuple[float, float]
    angle_median: float | None
    angle_range: tuple[float, float] | None


@dataclass(frozen=True)
class DesignVariant:
    signature: str
    profile_indices: tuple[int, ...]
    elements: tuple[RepresentativeElement, ...]
    upstream_context: RepresentativeElement | None = None


@dataclass(frozen=True)
class ProfileMeasurementContext:
    """Derived support on an already placed plane; never an evaluation mask.

    Design elements retain the physical endpoints of the assessed Faces only.
    Segment geometry is bounded by the supported landmark windows' U envelope.
    Actual geometry includes a full side-fit support margin and is not clipped
    to the local Design elevation envelope.
    An empty context is authoritative (it must not fall back to display data).
    """

    design_section: DesignSection
    design_segments: tuple[SectionSegment, ...]
    actual_segments: tuple[SectionSegment, ...]
    u_intervals: tuple[tuple[float, float], ...]
    design_z_interval: tuple[float, float] | None = None


@dataclass(frozen=True)
class TransverseProfile:
    alignment: WallAlignmentSample
    design_segments: tuple[SectionSegment, ...]
    actual_segments: tuple[SectionSegment, ...]
    design_section: DesignSection | None = None
    assessment_u_interval: tuple[float, float] | None = None
    external_toe: SectionPoint | None = None
    measurement_context: ProfileMeasurementContext | None = None


@dataclass(frozen=True)
class WallProfileSet:
    crest_lines: tuple[WallTransitionLine, ...]
    toe_lines: tuple[WallTransitionLine, ...]
    profiles: tuple[TransverseProfile, ...]
    design_variants: tuple[DesignVariant, ...] = ()

    def __post_init__(self) -> None:
        if not self.crest_lines or any(line.kind != "crest" for line in self.crest_lines):
            raise ValueError("Wall profile set alignment must use crest lines")
        if any(line.kind != "toe" for line in self.toe_lines):
            raise ValueError("Wall profile set toe_lines may contain only toe transitions")

    @property
    def crest_line(self) -> WallTransitionLine:
        if len(self.crest_lines) != 1:
            raise ValueError("External Design upper boundary has multiple components")
        return self.crest_lines[0]
