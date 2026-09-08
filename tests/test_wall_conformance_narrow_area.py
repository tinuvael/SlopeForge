import pytest

from domain.geometry.surfaces import SurfaceVertex
from domain.geometry.types import PlanPoint, PlanPolygon
from domain.wall_conformance import WallTransitionLine, sample_wall_alignment


def test_narrow_area_between_regular_chainage_stations_gets_one_profile() -> None:
    crest = WallTransitionLine(
        "crest",
        (
            SurfaceVertex(0, 0, 10),
            SurfaceVertex(0, 20, 10),
        ),
    )
    toe = WallTransitionLine(
        "toe",
        (
            SurfaceVertex(5, 0, 0),
            SurfaceVertex(5, 20, 0),
        ),
    )
    area = PlanPolygon(
        (
            PlanPoint(-1, 4),
            PlanPoint(1, 4),
            PlanPoint(1, 6),
            PlanPoint(-1, 6),
            PlanPoint(-1, 4),
        )
    )

    samples = sample_wall_alignment(
        crest,
        (toe,),
        area,
        spacing_m=10.0,
        tangent_window_m=3.0,
    )

    assert len(samples) == 1
    sample = samples[0]
    assert sample.chainage_m == pytest.approx(5.0)
    assert sample.origin.x == pytest.approx(0.0)
    assert sample.origin.y == pytest.approx(5.0)
    assert sample.tangent_xy == pytest.approx((0.0, 1.0))
    assert sample.normal_xy == pytest.approx((1.0, 0.0))
