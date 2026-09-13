# SlopeForge Agent Guide

## Project

SlopeForge is a Python 3.14 / PySide6 desktop application for open-pit geotechnical and blasting engineering.

Persistence: PostgreSQL, SQLAlchemy 2.x, psycopg 3, Alembic.

## Source of truth

When information conflicts, use this order:

1. Current `main` code, schema, migrations, and tests.
2. Explicit GitHub issue / PR scope.
3. Product and architecture invariants in this file.
4. Maintained documents in `docs/`.

Inspect the repository before reasoning about current architecture, bugs, or PRs. Do not rely on old conversation context or historical docs when `main` differs.

## Work discipline

- One logical task -> one focused PR unless explicitly staged.
- Do not broaden scope for adjacent cleanup; report it separately.
- Do not merge unless explicitly requested.
- Preserve proven engineering algorithms unless redesign is in scope.
- Before deleting or moving legacy-looking code, classify it as `ACTIVE`, `ACTIVE_BUT_MISPLACED`, `COMPATIBILITY_ONLY`, or `DEAD` using callers, tests, migrations, and packaging evidence.
- Names such as `prototype`, `legacy`, or `old` are not evidence that code is dead.

## Architecture

Target dependency direction:

PySide6 UI  
-> Application commands / queries / ports  
-> Domain models and policies  
<- Infrastructure adapters

Responsibilities:

- `domain/`: pure business and engineering logic. No PySide6, SQLAlchemy/PostgreSQL, UI dialogs, filesystem UI, or Excel dependencies.
- `application/`: use cases, orchestration, ports/UoW, transactions, rollback semantics, read/write workflows. No PySide6.
- `infrastructure/`: PostgreSQL/SQLAlchemy, files, geometry import, report writers, authentication/session adapters, external integrations.
- `ui/`: Qt presentation, pages, dialogs, widgets, navigation.
- `app/` + `main.py`: bootstrap, configuration, localization, dependency wiring.

Do not add ceremonial abstractions, interface-per-class patterns, or microservices without a real architectural boundary.

Root `database/` and `repositories/` remain an intentionally coupled active ORM graph until a focused migration provides more value than import churn. New adapters belong under `infrastructure/`.

### Architecture invariants

- Persistence ownership: `Site -> Domain -> BlastEvent / AssessmentArea`.
- Production Block = `BlastEvent(event_type='production')`, not another entity.
- `Mine`, `BlastBlock`, and `AssessmentWorkspace` persistence are removed. Do not revive them.
- Revisioned Technical Card and Assessment persistence are canonical.
- `Domain.version` is the optimistic-concurrency token for focused writes.
- Alembic revision `1` is the immutable SlopeForge 1.0 production baseline. Future schema changes append normal revisions after the current head.

## Product model

Project / Quarry  
└── Domain  
    ├── Blast events  
    │   └── Horizon [virtual]  
    │       ├── Production Block = BlastEvent(type='production')  
    │       └── Contour Blast = BlastEvent(type='contour')  
    └── Assessment areas  
        └── Elevation Interval [virtual]  
            └── Assessment Area

- Internal `Site` = user-facing Project / Quarry.
- Horizon and Interval are virtual groups, not database entities.
- Project Lines belong to the whole Project/Site and are shared across Domains.
- Do not expose legacy `Mine` terminology in normal UI.

## Blast events / Technical Card

There is one `BlastEvent` concept: `production` or `contour`.

- Keep one `Add blast event` action.
- Production events are the persisted user-facing Blocks.
- Contour events open the Contour Blast page and currently have no Geomechanics tab.
- Do not reintroduce `BlastBlock`, `blast_blocks`, or `blast_event.blast_block_id`.
- The revisioned BlastEvent Technical Card is the canonical engineering record.
- Do not revive parallel legacy persistence such as `RockMassProfile`, `RockStructure`, `BlastDesign`, `DrillingPattern`, `ChargeSegment`, or `ExplosiveType`.
- Do not change Technical Card engineering formulas as collateral cleanup.

## Assessment

Current scoring is intentional:

- DAI = Design Achievement Index.
- FCI = Face Condition Index.
- Quadrant X = FCI; Y = DAI.
- Never replace DAI/FCI with an average.
- Dashboards/read models use stored completed results, not historical recalculation.

Assessment geometry is revisioned and preserves history.

The active geometry workflow uses one continuous boundary operation that snaps/traces Project Lines and uses explicit straight connectors where needed. Preserve frozen source-line provenance and the derived polygon. Do not restore the obsolete separate upper/lower-line workflow.

## Attachments

Each physical attachment has one owner:

- Production BlastEvent -> Photos / Documents
- Contour BlastEvent -> Photos / Documents
- Assessment Area -> Assessment evaluation -> Photos / Documents

Do not introduce duplicate/shared physical ownership unless explicitly required.

## Database / migrations

Before SlopeForge 1.0, development databases from the old revision chain may be recreated during baseline consolidation rather than disguised as compatible.

After 1.0, revision `1` is immutable production history and stored data is preservable unless explicitly working in a disposable development context.

Always:

- use Alembic for physical schema changes;
- keep one Alembic head;
- never rewrite revision `1` after 1.0;
- never use `alembic stamp` to hide incompatibility;
- never run destructive tests against normal `DATABASE_URL`;
- PostgreSQL tests use a dedicated `TEST_DATABASE_URL` whose database name clearly identifies it as a test database.

## Analytics readiness

Preserve stable identities, relations, revisions, timestamps/authors, planned-vs-actual facts, event/Assessment provenance, frozen engineering inputs, and stored completed DAI/FCI results.

Do not add an ML subsystem, feature store, warehouse, or recommendations during MVP cleanup. Future analytics should use dedicated projections, views, materialized views, or ETL over the transactional model.

## UI

Normal source/default UI is English; Russian remains localization data.

Preferred terminology: Project, Domain, Blast event, Production, Contour blast, Block, Assessment area, Project Lines, Horizon, Interval.

Avoid exposing Mine, Assessment Workspace, database implementation names, or prototype terminology.

Keep PySide6 / Qt Widgets. Do not migrate to QML/Qt Quick or web technologies unless product architecture explicitly changes.

Visual direction: compact professional engineering desktop UI, light theme, white panels/cards, subtle borders, restrained blue accent, existing SlopeForge SVG assets, minimal shadows, no decorative animation.

Prefer existing SlopeForge design tokens/helpers and SVG assets. Preserve compact Windows engineering-desktop density, readability, high-DPI behavior, contrast, and localization tolerance.

## Repository UI skills

Codex skills live under `.agents/skills/` and are vendored upstream snapshots. Do not edit their `SKILL.md` files for SlopeForge-specific rules; keep overrides here.

Use only when relevant:

- `qt-ui-design` — layout, navigation, UX, accessibility, visual consistency.
- `pyqt-widgets` — QWidget/dialog/form/table/tree/layout implementation.
- `pyqt-styling` — QSS, selectors, states, widget styling.

For broad UI redesign use all three; for narrow work load only what is needed. Current code, active issue scope, and this file override generic skill guidance.

## Testing

For every PR run:

- `pytest <relevant tests>`
- `python tools/architecture_audit.py`
- `python -m compileall app application domain infrastructure database repositories ui`
- `git diff --check`

Use `QT_QPA_PLATFORM=offscreen` when needed.

Also run the full suite for architecture, persistence/schema/Alembic, geometry-core changes, broad refactors, and before merge/release validation:

`QT_QPA_PLATFORM=offscreen pytest -q`

Before review confirm:

- scope matches the issue;
- focused regression coverage exists;
- no duplicate source of truth was introduced;
- revision/rollback/concurrency semantics remain correct;
- Windows / Python 3.14 compatibility was considered;
- unrelated failures were reported rather than opportunistically fixed.

## Local GPU subagent delegation

`local-coder` is a shell command available in the remote WSL development environment. It is backed by local `gpt-oss:20b` on the workstation GPU and is an advisory/read-only subagent. It is not the primary implementation authority.

Use it proactively when it can reduce primary-model repository exploration or provide a useful second opinion, but never treat its conclusions as authoritative without verification.

### Health check

Before relying on the local GPU worker when availability is uncertain, run:

`local-coder health --require-gpu`

If the health check fails, continue with deterministic tools and the primary model rather than blocking the task.

### Repository research

For broad repository exploration, unfamiliar subsystems, call/data-flow tracing, locating implementations/tests, or first-pass bug investigation, use:

`local-coder --verbose ask "<focused repository task>"`

Do not pre-seed file names unless the task specifically requires them; let the local worker perform the first-pass discovery. Then verify important claims directly in the repository before making decisions or edits.

Do not repeat an identical investigation unless the first result was incomplete or materially new evidence exists.

### Test triage

For targeted pytest execution and compact failure triage, use:

`local-coder test-triage <pytest-target>`

Examples:

- `local-coder test-triage tests/test_dxf_geometry_import.py`
- `local-coder test-triage tests/test_example.py::test_name`

Use the triage result to narrow investigation. The primary Codex still owns diagnosis, code changes, and final verification. Do not use local triage as a substitute for required PR test commands.

### Diff review

For a first-pass second opinion on meaningful staged/unstaged changes, use:

`local-coder review-diff`

Use its findings to identify likely regressions, broken contracts, edge cases, and missing tests. Verify significant findings yourself. Do not automatically implement every local-model suggestion.

### Ownership and safety

- `local-coder` remains advisory/read-only; the primary Codex owns all repository edits.
- The primary Codex must independently verify important local-worker claims against source, tests, or deterministic tooling.
- Use deterministic tools directly when they are cheaper or more reliable than model delegation.
- Keep architecture decisions, complex implementation, migrations, destructive operations, commits, pushes, PR creation, and final verification with the primary Codex.
- If `local-coder` is unavailable, fall back cleanly; do not change the task scope merely to restore the worker.

### External documentation

When external library behavior is version-sensitive, use Context7 for a narrow, current documentation lookup rather than guessing from model memory.

Do not retrieve large documentation sets when a focused API lookup is enough.

### GitHub

When GitHub MCP is available, use it for remote-only state such as PRs, issues, Actions/workflow runs, review comments, and remote metadata.

Prefer local Git for status, diff, history, branches, and working-tree tasks.

### Delegation priority

Use the cheapest reliable layer:

1. Deterministic tools: Git, grep, pytest, static analysis.
2. Local `gpt-oss:20b` via the `local-coder` shell command.
3. Primary model.

Keep architecture decisions, complex implementation, destructive operations, and final verification with the primary model.