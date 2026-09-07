# Wall Conformance measurement context

`TransverseProfile.assessment_u_interval`, `design_segments`, `design_section`
and `actual_segments` retain their evaluation/display meanings. In particular,
Actual display geometry still receives the existing Assessment U clip and
Design elevation clip.

`TransverseProfile.measurement_context` is an optional derived
`ProfileMeasurementContext`: physical Design section elements, bounded Design
and Actual section segments, and `u_intervals`. It contains no surface or
persistence objects. An explicitly empty context does not fall back to display
geometry; older profile constructors without context retain the original
measurement input behavior.

The explicit Alignment assembly creates context only after the profile passes
its existing acceptance/invariant checks. It reuses the selected local Design
run and intersects Actual only once on the already fixed plane. No placement,
stationing, variant, clipping, or skipped-profile decisions use context.

## Physical Design support

The existing semantic section reducer is applied in a temporary positive-U
frame, then coordinates are restored. Source triangle provenance and positive
overlap identify the full counterparts of the first and last assessed Faces.
Their physical crest/toe may be outside the mask. Only immediate platforms and
the preceding Face provide additional semantic boundary evidence. The next
Face is not another evaluated Face or KPI.

A Berm/Road-to-Face transition supports the crest independently of whether the
upper platform's start is supported. The start requires preceding Face evidence;
a platform ending at the dataset boundary is not proof of a physical berm start.
A last-Face-to-Berm/Road transition supports the lower toe. No terminal clip edge
is promoted to a landmark.

## Bounded Actual input

The candidate half-window is computed from `WallMeasurementTolerances`:

`search_half_window_m + side_fit_window_m + actual_connection_tolerance_m`

Defaults give `5 + 2.5 + 0.15 = 7.65 m`. Candidate existence is not gated by
the nominal 5 m Design locality inside that physical-fit envelope. The Actual
context adds another complete side-fit window and connection tolerance, giving
a default support radius of `7.65 + 2.5 + 0.15 = 10.30 m`. It is one combined U
interval from the smallest supported Design landmark minus this support radius
to the largest plus the radius. This separation prevents a valid candidate at
the candidate-envelope edge from losing its right or left fitted run at the
context edge. Context has no Assessment Z clip.
The existing locality, orientation, residual, ambiguity and connected-support
checks continue to apply. No lines are extrapolated over dataset ends or gaps.

## Real-data check, 2026-09-05

Reproduce with `python -m tools.validate_wall_measurement_context`, which uses
the existing local validator's saved connection, Assessment AA-92E59B43 and
explicit development Alignment. It prints coordinates, tolerance-aware outside
flags, statuses and KPI values. It performs no writes.

52 profiles have Actual context; all 52 have empty display Actual segments.
At the three representative stations there are already Actual intersections
inside the Assessment U mask, but the existing elevation clip removes them.
The new context retains these observations and diagnoses incompatible geometry.

| Chainage m | Assessment U | Context U | Design crest U/Z | Design toe U/Z | Actual context Z |
| ---: | --- | --- | --- | --- | --- |
| 0.000 | 45.750–53.174 | 36.463–60.824 | 47.113 / 640.000 | 53.174 / 624.523 | 666.041–670.177 |
| 77.553 | 55.396–72.447 | 37.743–80.097 | 55.396 / 644.700 | 72.447 / 615.412 | 672.519–674.673 |
| 152.123 | 67.625–84.816 | 49.977–92.466 | 67.625 / 638.616 | 84.816 / 607.293 | 665.668–676.315 |

Design upper berm starts lie outside the mask at U/Z 44.113/640.000,
45.393/644.736 and 57.627/638.678 respectively. Crests/toes are inside or on
the boundary within the named connection tolerance. Design overall angles are
68.614°, 59.793° and 61.241°; upper widths are 3.000, 10.003 and 9.998 m.

All Actual landmarks at these stations are `not_detected/incompatible_geometry`;
their U/Z and outside flags are null, and paired KPIs are null. Actual elevations
are tens of metres above the expected Design landmarks, outside the existing
3 m locality tolerance. This pass removes artificial clipping loss but does not
establish successful real-data detection or tune the detector to this dataset.

Boundary-specific synthetic tests cover valid out-of-mask landmarks, dataset
ends, gaps, remote components, immutable display clipping and placement, empty
context behavior, named radius configuration and reversed Alignment results.
