# Wall Conformance engineering measurements

## Additional geometry diagnostics

Wall Conformance also derives diagnostic-only backbreak and face-conformity
metrics from its accepted transverse profiles. They are not Assessment scoring
inputs and never affect DAI, FCI, or any matrix criterion.

- **Backbreak** is `max(0, U_design_crest - U_actual_crest)`. With `+U`
  directed toward the wall/lower toe, only an Actual crest upstream of Design
  is positive. Mean and maximum backbreak use reliable crest profiles only.
- **Face residuals** compare only semantic Design `face` segments with the
  compatible continuous Actual wall component at common elevations. The signed
  Design-normal residual is `(U_design - U_actual) * sin(abs(face angle))`:
  positive is overbreak and negative is underbreak.
- **Mean overbreak** and **mean underbreak** are the non-negative means of
  their respective signed residual supports. **Contour RMS** is
  `sqrt(mean(residual²))` over all valid Face support.

Both Design and Actual piecewise-linear breakpoints partition the common-Z
intervals. Integrals are weighted by Design-face arc length at each uniformly
spaced alignment station, so raw TIN vertex density and collinear subdivision
do not change the engineering result. Berms, roads, topography, floors,
disconnected fragments, and ambiguous Actual intersections are excluded.

These are derived measurements, not Assessment scores. The Wall Conformance
result itself is ephemeral; an Assessment revision receives a compatibility
snapshot only after the user explicitly applies the current result.
The entry points in `domain/wall_conformance/measurements.py` are
`extract_design_landmarks`, `detect_actual_landmarks`, `measure_profile`,
`measure_profiles`, and `aggregate_measurements`. They consume already generated
`TransverseProfile` objects. The existing application service returns
`measurements` and `measurement_summary` on `WallConformanceDiagnosticResult`.

Placement, profile spacing, Design-derived azimuth/+U, Assessment clipping,
local-run selection, variants, Actual intersections, and skipped-profile rules
are upstream inputs. Measurement failure never requests another placement.
The existing optional `ProfileMeasurementContext` supplies bounded support from
the accepted local run. An explicitly empty context remains authoritative;
profiles without that field use their existing section geometry.

## Physical landmark definitions

* Upper crest: the Berm/Road-to-first-assessed-Face transition. This can be
  supported even when the upper platform's start cannot be measured.
* Upper berm start: the upstream end of that same connected Berm/Road, with a
  preceding Face ending there in the supplied local Design geometry. A
  presentation `upstream_context` endpoint alone is insufficient. Neither a
  dataset end nor an Assessment clip edge proves a physical boundary.
* Lower toe: the end of the last assessed Face, contiguous with the following
  semantic Berm/Road. A clipped terminal or a bare terminal marker without
  downstream geometry is unsupported; no horizontal platform is invented.

The existing context builder can provide physical Face endpoints outside the
Assessment display mask. Measurements do not reconstruct topology or select a
different wall run. Internal Faces/Berms are never additional KPI rows.

## Actual detection

Design supplies transition identity, bounded local context, and a soft
candidate preference. Actual semantic labels are ignored. Every transition
vertex in the connected Actual measurement context is eligible; normally the
output point is an Actual vertex, not a projection of Design or an
extrapolated intersection of fitted lines.

At a Design-confirmed outer boundary with no upstream Berm/Road context, the
upper berm start is explicitly `boundary_truncated`. The Design upper-crest
elevation defines a local vertical search band equal to one assessed Design
Face height above and below that elevation. Within that band, a connected
Actual breakpoint is accepted only when its downstream local fit is a
supported Face; the upstream topography may descend, be flat, or rise to a
local crest. Its slope sign never defines crest existence. The selected point
is always an Actual point, not an intersection with the Design elevation. A
genuine measurement boundary which begins directly on the Face remains a
secondary one-sided case. The historical `boundary_face_run_onset` and
`boundary_design_elevation_fallback` provenance values remain readable, but
neither is the primary outer-boundary path. `ProfileLandmark.source` records
how a crest was obtained.

The detector normalizes segment direction and removes exact duplicate geometry
and numerical zero-length segments. Endpoint connectivity separates gaps.
Collinear tessellation vertices are not alternate breakpoints. Both sides of a
candidate are clipped to their local fit windows before fitting.

Each side uses orthogonal least squares with exact length-weighted segment
moments. Subdividing a straight segment does not change its fit weight. Tiny
connected fragments contribute by physical length, rather than by triangle
count; isolated tiny components cannot establish support. Hard acceptance uses
connected support, no bridged gap, acceptable residuals, and an Actual physical
transition type: Face-to-platform for upper berm start/lower toe, or
platform-to-Face for upper crest. A Face materially descends in +U, a platform
is materially flatter, and the fitted runs must have a meaningful orientation
change. Rising near-vertical geometry and wrong-shape kinks remain
incompatible.

For a lower toe, the facets touching a candidate contribute a continuous onset
preference relative to the stable left/right fitted runs. They do not impose a
second absolute Face/platform validity gate. This keeps the selected point at
the onset of the downstream floor without allowing one short transitional TIN
facet to create, remove, or move the physical landmark when its angle crosses a
class threshold.

Design orientation difference and Design distance are soft, deterministic
preferences among physically valid candidates. They never veto an otherwise
valid displaced crest, berm, or toe. In particular, the nominal 5 m Design
locality does not act as a second hard candidate gate after the bounded context
has admitted connected Actual geometry. Candidate selection is lexicographic:
transition-onset coherence (for a toe), fit quality, then Design-orientation
preference, then Design distance. Similar separated candidates with
indistinguishable physical evidence remain ambiguous.
Only residual quality can produce low confidence after a candidate is
physically valid. Raw candidate points remain inspectable.

The upper transitions are also evaluated as a coherent Face-to-platform-to-Face
pair when both are available in the same connected Actual component. The pair
requires correct +U ordering, a continuous sufficiently wide platform, and an
acceptable platform fit. A lone crest can still be detected at a genuine outer
boundary; a berm start is never invented there.

The upper berm additionally requires connected Actual coverage between its
detected boundaries. Invalid endpoint ordering also prevents a paired value.
Angle and Toe remain independently usable when only Berm fails.

## Default configuration and rejection rules

`WallMeasurementTolerances` is accepted by all measurement entry points.
Settings must be finite and positive; reliable thresholds cannot exceed the
rejection thresholds, and the expected-change fraction cannot exceed one.

| Setting | Default | Meaning |
| --- | ---: | --- |
| Design endpoint connection | 0.0001 m | Semantic transition continuity |
| Actual endpoint connection | 0.15 m | Maximum tolerated endpoint separation |
| Minimum isolated component length | 0.05 m | Ignore isolated tiny geometry |
| Design locality half-window | 5 m in U | Core expanded by a full fit window before candidate evaluation |
| Side-fit window | 2.5 m in U per side | Exact clipping before fitting |
| Minimum side support | 0.60 m | Extent along each fitted line |
| Reliable / maximum fit RMS | 0.20 / 0.45 m | Low confidence / hard rejection bounds |
| Actual Face descent | 25 degrees | Minimum downward Face-like side angle in +U |
| Actual platform angle | 20 degrees | Maximum absolute platform/floor angle |
| Minimum fitted-run change | 7 degrees | Required meaningful change between adjacent stable runs |
| Transition onset fraction | 0.30 | Continuous preference for the first downstream change within a toe transition zone |
| Minimum upper platform width | 1.0 m | Coherent upper-pair evidence |
| Design distance preference | 3 m | Soft candidate ranking scale, never a rejection gate |
| Design orientation preference | 28 degrees | Soft candidate ranking scale, never a rejection gate |
| Ambiguity physical-fit margin / separation | 0.01 m / 0.30 m | Similar competing physical candidates |
| Numerical geometry / collinearity tolerance | 1e-9 m / 1e-5 degrees | Zero geometry and straight continuations |

The candidate envelope defaults to 7.65 m: the 5 m Design locality plus one
2.5 m fit window and 0.15 m connection tolerance. The context support radius is
10.30 m because it adds another complete fit window and connection tolerance.
This bounds Actual detector input in U without clipping side support at the
candidate edge; the Design elevation envelope is retained as derived
presentation information and cannot remove a physical Actual floor.
Defaults are engineering detection settings, not calibrated scoring thresholds.

Stable reasons include `design_landmark_unsupported`, `no_actual_coverage`,
`insufficient_left_support`, `insufficient_right_support`, `data_gap`,
`incompatible_geometry`, `ambiguous_breakpoint`, `unstable_breakpoint`, and
`incompatible_landmark_order`. States are `detected`, `low_confidence`, and
`not_detected`, accompanied by explanatory messages.

## Read-only landmark diagnostics

`diagnose_profile_landmarks` and `diagnose_profile_landmark_set` return the
same final statuses as the detector alongside every locally considered Actual
breakpoint. Each candidate records expected/Actual U/Z, U/Z deltas, spatial
offset, both supports, fitted and expected orientations, orientation errors,
residuals, observed local gap, physical topology, hard physical gate, and the
lexicographic selection rank. The trace is derived after placement and does
not alter placement or the measurements.

`python -m tools.diagnose_wall_measurement_test_area` is a read-only report for
the existing clean `assembled()` measurement-context fixture. It prints every
generated profile, final-reason and rejected-candidate histograms, distributions
for rejected candidates, preprocessing-stage evidence, and available
representative profiles. The matching test verifies that this fixture's five
profiles have continuous Actual input and all three landmarks detected. If a
requested representative failure does not occur in that fixture, its report
entry is null rather than synthesizing a diagnosis.

## Results and aggregation

`WallProfileMeasurements` stores chainage; Design/Actual landmark groups with
U/Z/X/Y coordinates and detection status; Design/Actual overall angles and upper
berm widths; paired angle shortfall, berm deficit, signed toe offset and absolute
toe deviation; and independent `angle_status`, `berm_status`, `toe_status`.

Overall angle is `degrees(atan2(abs(toe.z - crest.z), abs(toe.u - crest.u)))`.
Upper berm width is `abs(crest.u - berm_start.u)`, not surface length.
Shortfall/deficit is `max(design - actual, 0)` within each profile. Signed toe
offset is `actual_toe.u - design_toe.u`; its absolute value is the toe KPI.
Supported Design values remain available without Actual. Unreliable paired
values are `None`.

`WallMeasurementSummary` has three `KpiAggregate` fields: `angle_shortfall_deg`,
`upper_berm_deficit_m`, and `toe_deviation_m`. Each reports `valid_count`,
`total_count`, `median`, `mean`, `minimum`, and `maximum`; `primary_value` is the
median. Only reliable paired deviations enter that KPI's aggregate. Empty
aggregates have zero valid count and `None` statistics. No subtraction of
independently aggregated Design/Actual values occurs.

The existing Assessment criteria (`bench_angle`, `berm_width`, `toe_position`)
and DAI/FCI calculations remain untouched. Wall Conformance only populates an
Assessment draft after the user explicitly previews and applies the current
measurements; the normal Assessment save workflow remains authoritative.

## Limits

This is a conservative local piecewise-linear detector. Complex outer-boundary
Actual geometry may remain low-confidence or N/A when more than one physical
transition is plausible. Rounded transitions, overhangs, exactly vertical or
branching sections, very short platforms, large
departures from Design, or insufficient survey support can be rejected. Output
coordinates follow the Actual mesh vertices; no sub-mesh precision is claimed.
Endpoint angles need supported crest/toe landmarks, rather than complete
coverage of every internal bench. The Berm width requires connected coverage.
