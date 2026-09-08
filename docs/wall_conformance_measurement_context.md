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

## Known limitation

Complex outer-boundary Actual geometry can contain more than one plausible
physical transition. The conservative detector may leave those profiles
low-confidence or N/A; raw diagnostic context must not be promoted to evaluated
Actual geometry.
