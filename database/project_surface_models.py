"""Metadata-only persistence for revisioned Project design/actual surface files."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ProjectSurfaceDataset(Base):
    __tablename__ = "project_surface_datasets"
    __table_args__ = (
        UniqueConstraint(
            "site_id", "logical_id", name="uq_project_surface_datasets_site_logical_id"
        ),
        UniqueConstraint(
            "site_id",
            "dataset_kind",
            "revision_number",
            name="uq_project_surface_datasets_site_kind_revision",
        ),
        CheckConstraint(
            "dataset_kind IN ('design', 'actual')",
            name="ck_project_surface_datasets_kind",
        ),
        CheckConstraint(
            "source_format IN ('dxf', 'datamine')",
            name="ck_project_surface_datasets_format",
        ),
        CheckConstraint(
            "revision_number > 0", name="ck_project_surface_datasets_revision_positive"
        ),
        CheckConstraint(
            "vertex_count >= 3", name="ck_project_surface_datasets_vertex_count"
        ),
        CheckConstraint(
            "triangle_count > 0", name="ck_project_surface_datasets_triangle_count"
        ),
        CheckConstraint(
            "jsonb_typeof(source_files_json) = 'array'",
            name="ck_project_surface_datasets_source_files_array",
        ),
        Index(
            "ix_project_surface_datasets_site_kind_revision",
            "site_id",
            "dataset_kind",
            "revision_number",
        ),
        Index("ix_project_surface_datasets_imported_at", "imported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False
    )
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_format: Mapped[str] = mapped_column(String(20), nullable=False)
    source_files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    semantic_mapping_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )
    vertex_count: Mapped[int] = mapped_column(Integer, nullable=False)
    triangle_count: Mapped[int] = mapped_column(Integer, nullable=False)
