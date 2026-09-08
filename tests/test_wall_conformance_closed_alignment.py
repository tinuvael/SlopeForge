import math

import pytest

from domain.geometry.surfaces import SurfaceVertex
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import WallTransitionLine, sample_wall_alignment


def test_closed_crest_tangent_wraps_across_storage_seam() -> None:
    crest = WallTransitionLine(
        "crest",
        (
            SurfaceVertex(0, 0, 10),
            SurfaceVertex(10, 0, 10),
            SurfaceVertex(10, 10, 10),
            SurfaceVertex(0, 10, 10),
            SurfaceVertex(0, 0, 10),
        ),
    )
    toe = WallTransitionLine(
        "toe",
        (
            SurfaceVertex(1, 1, 0),
            SurfaceVertex(9, 1, 0),
            SurfaceVertex(9, 9, 0),
            SurfaceVertex(1, 9, 0),
            SurfaceVertex(1, 1, 0),
        ),
    )
    area = PlanPolygon(
        (
            PlanPoint(-1, -1),
            PlanPoint(3, -1),
            PlanPoint(3, 3),
            PlanPoint(-1, 3),
            PlanPoint(-1, -1),
        )
    )

    samples = sample_wall_alignment(
        crest,
        (toe,),
        area,
        spacing_m=10.0,
        tangent_window_m=2.0,
    )

    assert len(samples) == 1
    sample = samples[0]
    root_half = math.sqrt(0.5)
    assert sample.chainage_m == 0
    assert sample.tangent_xy == pytest.approx((root_half, -root_half))
    assert sample.normal_xy == pytest.approx((root_half, root_half))
