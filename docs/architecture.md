# SlopeForge architecture

SlopeForge is a Python 3.14 / PySide6 modular monolith backed by PostgreSQL,
SQLAlchemy 2.x, psycopg 3, and Alembic. Current code, schema, migrations, and
tests take precedence over this overview.

## Dependency direction

```text
ui/ (PySide6 presentation)
    -> application/ (commands, queries, ports, transactions)
        -> domain/ (business and engineering models/policies)

infrastructure/ implements application ports and external I/O.
app/ + main.py provide configuration, localization, and dependency wiring.
```

`domain/` has no Qt, SQLAlchemy, database, desktop, or report dependencies.
`application/` has no Qt dependencies. New concrete adapters belong in
`infrastructure/`. The active, coupled ORM graph remains in `database/` and
`repositories/`; moving it would create import churn without improving an MVP
workflow.

## Product and ownership model

```text
Project / Quarry (internal Site)
└── Domain
    ├── Blast events
    │   └── Horizon [virtual]
    │       ├── Production Block = BlastEvent(type="production")
    │       └── Contour Blast = BlastEvent(type="contour")
    └── Assessment areas
        └── Elevation Interval [virtual]
            └── Assessment Area
```

Horizon and Interval are presentation groups, not database entities. Project
Lines belong to the Project/Site and are shared across its Domains. The removed
Mine, Assessment Workspace, and BlastBlock persistence models are not part of
the current architecture.

A Production Block is the production BlastEvent itself and opens the Block page.
A Contour Blast opens the Contour page and has no Geomechanics tab. The
revisioned Technical Card is the single active engineering record for either
event type.

## Assessment and geometry

Assessment Area identity is stable and its boundary is revisioned. The boundary
is one continuous operation which traces/snaps to real Project Lines and uses
explicit straight connectors where needed. Revisions freeze source-line
provenance and retain a derived polygon.

Assessment scoring is intentionally two-dimensional:

- DAI is the Design Achievement Index;
- FCI is the Face Condition Index;
- matrix X is FCI and Y is DAI;
- dashboards use stored completed results rather than recalculating history.

## Persistence, revisions, and concurrency

PostgreSQL is the only application database. Normal editing uses focused writes;
`Domain.version` is the optimistic-concurrency token, including transactions
which protect more than one Domain. The Assessment state repository is a reader,
not a whole-state save API.

The immutable SlopeForge 1.0 production Alembic baseline is revision `1`. The
former disposable pre-release migration chain was consolidated before the 1.0
release. Future physical schema changes require appended normal revisions after
the current head. Never edit revision `1` after release and never use
`alembic stamp` to conceal an incompatible physical schema.

Blast geometry, Assessment geometry, Technical Card, and evaluation revisions
preserve the inputs and results needed to reproduce history. Event/Assessment
links preserve exact geometry revision provenance. Planned and actual facts
remain distinct.

## Attachments and reports

Every physical attachment has one owner:

- Production or Contour BlastEvent → Photos/Documents;
- Assessment evaluation → Photos/Documents.

File operations go through the application/storage workflow so database and
filesystem rollback behavior stays coordinated.

The Project-level Excel report is an active application use case with an
OpenPyXL writer in `infrastructure/reports/`. The Analysis page is an intentional
MVP placeholder; it is not a second report implementation.

## Composition and guardrails

`app/use_case_factory.py` constructs application use cases and their concrete
adapters. `MainWindow`, `EntityPageController`, and `ProjectTree` own navigation
and transient Qt page lifetimes. UI code still has a small number of deliberate
direct repository/service dependencies; replacing these is not part of final
MVP cleanup.

`tools/architecture_audit.py` and `tests/test_architecture_boundaries.py` enforce
the important dependency and removed-package rules. Do not add microservices,
ceremonial interfaces, speculative analytics infrastructure, or package moves
without a concrete boundary or product need.
