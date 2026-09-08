# SlopeForge

[![Release](https://img.shields.io/github/v/release/Tinuvael/SlopeForge?display_name=tag)](https://github.com/Tinuvael/SlopeForge/releases/latest)
![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/database-PostgreSQL-4169E1?logo=postgresql&logoColor=white)
[![License](https://img.shields.io/github/license/Tinuvael/SlopeForge)](LICENSE)

**SlopeForge** is an open-source desktop application for open-pit geotechnical and blasting engineering data management, final-wall assessment, and blast-quality tracking.

It organizes engineering data around Projects, Domains, Blast Events, Production Blocks, Contour Blasts, and Assessment Areas while preserving calculation, geometry, attachment, and revision history.

## Features

- Production and Contour Blast Events with blast-design and execution data
- Production Block Technical Cards with revision history
- Geomechanical inputs and blast-design charge construction
- Assessment Areas with DAI and FCI evaluation results
- Project-wide reference geometry and Assessment boundary workflows
- Project and Domain dashboards with blast and assessment summaries
- Photos and engineering documents with controlled ownership
- Project-level Excel reporting
- PostgreSQL persistence with SQLAlchemy and Alembic
- Saved multi-server PostgreSQL connection profiles with secure Windows credential storage
- Separate Windows database updater with verified PostgreSQL backup before production migrations
- English and Russian UI localization

## Download

Download the latest Windows package from **[GitHub Releases](https://github.com/Tinuvael/SlopeForge/releases/latest)**.

Available release artifacts include a portable ZIP and Windows installer. PostgreSQL is not bundled and must be configured separately. Installed Windows packages also include **SlopeForge Updater** for backup-gated production database migrations.

## Run from source

Use Python 3.14 and a PostgreSQL database. See **[Database setup](docs/database_setup.md)** for connection configuration and production upgrade guidance.

```bash
python -m pip install -r requirements.txt
alembic upgrade head
python main.py
```

The updater can be launched from a source checkout with:

```bash
python updater_main.py
```

## Engineering note

SlopeForge is an engineering data-management and decision-support tool. It does not replace professional engineering judgement, site-specific investigations, blast design, or geotechnical design. Users are responsible for verifying engineering decisions and input data.

## License

Distributed under the [GNU General Public License v3.0](LICENSE).
