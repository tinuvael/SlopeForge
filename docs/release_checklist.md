# SlopeForge 1.0 release checklist

Use this as the consolidated manual pass before publishing SlopeForge 1.0. Completed MVP/RC issues should stay closed unless a concrete regression is found.

## Automated checks

```bash
pytest <relevant tests>
python tools/architecture_audit.py
python -m compileall app application domain infrastructure database repositories ui
git diff --check
QT_QPA_PLATFORM=offscreen pytest -q
python -m alembic heads
python -m alembic history
```

For schema/persistence validation, also run PostgreSQL integration tests with a dedicated `TEST_DATABASE_URL` and verify migrations from a clean database.

Release 1.0 version sanity:

- `app.config.APP_VERSION == "1.0.0"`;
- `app.config.APP_VERSION_DISPLAY == "1.0"`;
- Alembic has exactly one baseline revision/head: `1`;
- a clean database upgraded to head stores revision `1` and matches current ORM metadata;
- former pre-1.0 development databases are recreated rather than stamped.

## Windows / Python 3.14

- Launch on the supported Windows/Python 3.14 environment.
- Confirm login/startup succeeds without Qt lifecycle warnings/crashes.
- Repeat navigation Project → Domain → Block → Assessment Area → Contour many times and confirm no unbounded stacked-page growth or recursive navigation warnings.
- Close/restart and confirm persisted data is reconstructed correctly.

## Header and navigation

- Header toolbar actions are visually consistent and state-aware.
- Add menu exposes one `Add blast event` action rather than separate Production/Contour actions.
- Navigation panel hide/show preserves current selection/filter state.
- Search is canonical (no conflicting duplicate search box).
- Project, Domain, status, date-range, and archive filters compose correctly.
- Reset clears all filters, including Domain, and visibly restores `All domains`.
- Archived entities use a clear Restore action/icon rather than looking like Archive.

## Project / Domain

- Normal UI shows Project / Quarry and Domain terminology; no normal `Mine` or `Assessment Workspace` wording.
- Project dashboard opens and Project Lines are managed there, not as a tree branch.
- Domain dashboard opens and remains Domain-scoped.
- Project/Domain renaming preserves IDs and relationships.
- Dashboard blast counts treat a Production Block as its production BlastEvent,
  not as a second blast.
- DAI and FCI remain separate stored-result metrics.

## Project Lines / geometry

- Import a representative Project Lines dataset and confirm separate source line parts are not falsely connected.
- Verify active/history dataset behavior.
- Verify plan rendering and Fit behavior.
- Test Assessment boundary tracing on:
  - parallel horizontal lines;
  - sloping line(s);
  - interrupted lines with explicit connector;
  - triangular/wedge area;
  - curved lines;
  - multiple nearby snap candidates;
  - mixed traced/free segments;
  - invalid self-intersecting closure.
- Reimport/activate a different Project Lines dataset and confirm old Assessment geometry revisions do not mutate.

## Production Block

- Open a Block and verify General information, Geomechanics, Blast design, Execution fact, Photos/Documents, and History behavior expected by the current issues.
- Technical Card uses one Save action and each save creates/preserves revision history.
- Planned vs actual facts are edited in their intended sections.
- Workflow status is derived consistently rather than independently editable.
- Production Block metadata updates the same production BlastEvent identity.
- Photos/Documents are owned by that production BlastEvent.

## Contour Blast

- Open Contour Blast and verify General information, Blast design, Execution fact, Photos, Documents, and History.
- Confirm there is no Geomechanics tab.
- Verify the same derived workflow-status policy as Production.
- Verify contour-specific design offset/orientation and charge builder.

## Blast design / charge builder

- Derived drilling length equals hole count × average depth; no independent Production override is required.
- Empty charge editor starts as air.
- Add/move/resize continuous components in 0.1 m increments.
- Unoccupied intervals remain air.
- Components cannot overlap or extend outside the borehole.
- Bulk explosive quantity uses configured/frozen product properties.
- Cartridge products render/count as discrete cartridges and deck pitch changes count.
- Catalogue edits do not retroactively change historical Technical Card revisions.

## Assessment Area

- Creation begins with General information and uses the continuous Boundary workflow.
- Review/Save persists Area + first geometry revision atomically.
- Existing boundary editing creates a new geometry revision and does not duplicate Name/Domain metadata editing.
- Assessment page keeps DAI/FCI scoring unchanged.
- Live result matrix updates with inputs while Save draft / Complete still work.
- Linked events preserve suggested/confirmed/excluded semantics.
- Inline linked-event plan shows Assessment Area + selected event + relevant Project Lines without changing tabs.
- Assessment attachments remain owned by the evaluation.

## Archive / read-only

- Archive/Restore works for Blocks, Contour Blasts, and Assessment Areas.
- Archived entities remain readable and do not expose inappropriate mutation controls.
- Viewer role cannot mutate data, attachments, geometry, evaluations, or catalogues.

## Attachments

- Photos use the intended gallery/viewer flow.
- Documents remain scan-friendly with useful file-type presentation.
- Copy/add/delete failure paths preserve metadata/file rollback semantics.
- One physical attachment has one owner.

## Localization

### English

- Start in English and inspect login/startup, header, navigation, dialogs, Block, Contour, Assessment, dashboards, attachments, and Settings.

### Russian

- Switch to Russian through Settings and restart if required by the current implementation.
- Repeat the same active UI pass and verify no untranslated active labels/enum display values remain except intentional proper names/IDs/user content.
- Switch back to English and confirm language persistence/restart behavior.

Intentional unchanged terms include SlopeForge, DAI/FCI, repository/file names, persisted IDs/enum keys where they are not user-facing labels, and user-entered/imported content.

## Final data/revision sanity

- Restart after creating/editing representative Project, Domain, Production, Contour, Assessment, links, revisions, and attachments.
- Confirm active revisions and history are unchanged.
- Confirm completed stored DAI/FCI results are not recomputed differently on reload.
- Confirm archive state is orthogonal to operational/workflow status.
- Confirm Project Lines remain Project-wide across Domains.

## Documentation

- `AGENTS.md`, `README.md`, `docs/architecture.md`, `docs/database_setup.md`, and `docs/wall_assessment_concept.md` reflect SlopeForge 1.0.
