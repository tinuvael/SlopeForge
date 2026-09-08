"""PostgreSQL persistence model for the versioned 2D Assessment domain.

This module only declares metadata. In particular, importing it never creates an
engine or opens a database connection.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class ProjectLinesDataset(Base):
    __tablename__ = "project_lines_datasets"
    __table_args__ = (
        UniqueConstraint("site_id", "logical_id", name="uq_project_lines_datasets_site_logical_id"),
        CheckConstraint("jsonb_typeof(lines_json) = 'array'", name="ck_project_lines_datasets_lines_json_array"),
        CheckConstraint("NOT (is_archived AND is_active)", name="ck_project_lines_datasets_archived_not_active"),
        Index("ix_project_lines_datasets_one_active_per_site", "site_id", unique=True, postgresql_where=text("is_active")),
        Index("ix_project_lines_datasets_site_id", "site_id"),
        Index("ix_project_lines_datasets_imported_at", "imported_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lines_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    site: Mapped["Site"] = relationship(back_populates="project_lines_datasets")


class BlastEvent(TimestampMixin, Base):
    """Single persistence entity for both production Blocks and contour blasts."""
    __tablename__ = "blast_events"
    __table_args__ = (
        UniqueConstraint("domain_id", "logical_id", name="uq_blast_events_domain_logical_id"),
        CheckConstraint("event_type IN ('production', 'contour')", name="ck_blast_events_event_type"),
        Index("ix_blast_events_domain_id", "domain_id"), Index("ix_blast_events_event_type", "event_type"),
        Index("ix_blast_events_elevation_m", "elevation_m"), Index("ix_blast_events_event_date", "event_date"),
        Index("ix_blast_events_is_archived", "is_archived"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="RESTRICT"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    event_date: Mapped[Optional[date]] = mapped_column(Date)
    elevation_m: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    archive_reason: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped["Domain"] = relationship(back_populates="blast_events")
    created_by_user: Mapped[Optional["User"]] = relationship()
    geometry_revisions: Mapped[list["BlastEventGeometryRevision"]] = relationship(back_populates="blast_event", cascade="all, delete-orphan", passive_deletes=True, order_by="BlastEventGeometryRevision.revision_number")
    technical_card: Mapped[Optional["BlastEventTechnicalCard"]] = relationship(back_populates="blast_event", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    attachments: Mapped[list["AssessmentEntityAttachment"]] = relationship(back_populates="blast_event", cascade="all, delete-orphan", passive_deletes=True, foreign_keys="AssessmentEntityAttachment.blast_event_id")


class BlastEventGeometryRevision(Base):
    __tablename__ = "blast_event_geometry_revisions"
    __table_args__ = (
        UniqueConstraint("blast_event_id", "logical_id", name="uq_blast_event_geometry_revisions_parent_logical_id"),
        UniqueConstraint("blast_event_id", "revision_number", name="uq_blast_event_geometry_revisions_parent_number"),
        CheckConstraint("revision_number > 0", name="ck_blast_event_geometry_revisions_number_positive"),
        CheckConstraint("jsonb_typeof(source_geometry_json) = 'array'", name="ck_blast_event_geometry_revisions_source_array"),
        CheckConstraint("jsonb_typeof(plan_geometry_json) = 'object'", name="ck_blast_event_geometry_revisions_plan_object"),
        Index("ix_blast_event_geometry_revisions_one_active", "blast_event_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blast_event_id: Mapped[int] = mapped_column(ForeignKey("blast_events.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_geometry_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    plan_geometry_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    elevation_m: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blast_event: Mapped[BlastEvent] = relationship(back_populates="geometry_revisions")
    event_links: Mapped[list["AssessmentEventLink"]] = relationship(back_populates="blast_event_geometry_revision", passive_deletes=True)


class BlastEventTechnicalCard(TimestampMixin, Base):
    __tablename__ = "blast_event_technical_cards"
    __table_args__ = (UniqueConstraint("blast_event_id", name="uq_blast_event_technical_cards_event"), UniqueConstraint("logical_id", name="uq_blast_event_technical_cards_logical_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blast_event_id: Mapped[int] = mapped_column(ForeignKey("blast_events.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blast_event: Mapped[BlastEvent] = relationship(back_populates="technical_card")
    revisions: Mapped[list["BlastEventTechnicalCardRevision"]] = relationship(back_populates="technical_card", cascade="all, delete-orphan", passive_deletes=True, order_by="BlastEventTechnicalCardRevision.revision_number")


class BlastEventTechnicalCardRevision(Base):
    __tablename__ = "blast_event_technical_card_revisions"
    __table_args__ = (
        UniqueConstraint("technical_card_id", "logical_id", name="uq_technical_card_revisions_parent_logical_id"),
        UniqueConstraint("technical_card_id", "revision_number", name="uq_technical_card_revisions_parent_number"),
        CheckConstraint("revision_number > 0", name="ck_technical_card_revisions_number_positive"),
        CheckConstraint("event_type IN ('production', 'contour')", name="ck_technical_card_revisions_event_type"),
        CheckConstraint("status IN ('draft', 'completed')", name="ck_technical_card_revisions_status"),
        CheckConstraint("jsonb_typeof(payload_json) = 'object'", name="ck_technical_card_revisions_payload_object"),
        Index("ix_technical_card_revisions_one_active", "technical_card_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    technical_card_id: Mapped[int] = mapped_column(ForeignKey("blast_event_technical_cards.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blast_event_geometry_revision_id: Mapped[int] = mapped_column(ForeignKey("blast_event_geometry_revisions.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    technical_card: Mapped[BlastEventTechnicalCard] = relationship(back_populates="revisions")
    geometry_revision: Mapped[BlastEventGeometryRevision] = relationship()


class AssessmentArea(TimestampMixin, Base):
    __tablename__ = "assessment_areas"
    __table_args__ = (UniqueConstraint("domain_id", "logical_id", name="uq_assessment_areas_domain_logical_id"), Index("ix_assessment_areas_domain_id", "domain_id"), Index("ix_assessment_areas_assessment_date", "assessment_date"), Index("ix_assessment_areas_is_archived", "is_archived"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="RESTRICT"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    assessment_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    archive_reason: Mapped[Optional[str]] = mapped_column(Text)
    domain: Mapped["Domain"] = relationship(back_populates="assessment_areas")
    geometry_revisions: Mapped[list["AssessmentAreaGeometryRevision"]] = relationship(back_populates="assessment_area", cascade="all, delete-orphan", passive_deletes=True, order_by="AssessmentAreaGeometryRevision.revision_number")
    evaluation: Mapped[Optional["AssessmentAreaEvaluation"]] = relationship(back_populates="assessment_area", cascade="all, delete-orphan", passive_deletes=True, uselist=False)


class AssessmentAreaGeometryRevision(Base):
    __tablename__ = "assessment_area_geometry_revisions"
    __table_args__ = (
        UniqueConstraint("assessment_area_id", "logical_id", name="uq_assessment_area_geometry_revisions_parent_logical_id"), UniqueConstraint("assessment_area_id", "revision_number", name="uq_assessment_area_geometry_revisions_parent_number"),
        CheckConstraint("revision_number > 0", name="ck_assessment_area_geometry_revisions_number_positive"),
        CheckConstraint("jsonb_typeof(boundary_json) = 'object'", name="ck_assessment_area_geometry_revisions_boundary_object"), CheckConstraint("jsonb_typeof(final_geometry_json) = 'object'", name="ck_assessment_area_geometry_revisions_final_object"),
        Index("ix_assessment_area_geometry_revisions_one_active", "assessment_area_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_area_id: Mapped[int] = mapped_column(ForeignKey("assessment_areas.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    boundary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    final_geometry_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    min_elevation_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    max_elevation_m: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assessment_area: Mapped[AssessmentArea] = relationship(back_populates="geometry_revisions")
    event_links: Mapped[list["AssessmentEventLink"]] = relationship(back_populates="assessment_area_geometry_revision", cascade="all, delete-orphan", passive_deletes=True)
    wall_alignment: Mapped[Optional["AssessmentAreaWallAlignment"]] = relationship(
        back_populates="geometry_revision", cascade="all, delete-orphan",
        passive_deletes=True, uselist=False,
    )


class AssessmentAreaWallAlignment(TimestampMixin, Base):
    """Manual Wall Conformance stationing state owned by one frozen geometry revision."""

    __tablename__ = "assessment_area_wall_alignments"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(points_json) = 'array'",
            name="ck_assessment_area_wall_alignments_points_array",
        ),
    )
    assessment_area_geometry_revision_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_area_geometry_revisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    points_json: Mapped[list[dict[str, float]]] = mapped_column(JSONB, nullable=False)
    geometry_revision: Mapped[AssessmentAreaGeometryRevision] = relationship(
        back_populates="wall_alignment"
    )


class AssessmentEventLink(Base):
    __tablename__ = "assessment_event_links"
    __table_args__ = (
        UniqueConstraint("assessment_area_geometry_revision_id", "logical_id", name="uq_assessment_event_links_parent_logical_id"), UniqueConstraint("assessment_area_geometry_revision_id", "blast_event_geometry_revision_id", "source", name="uq_assessment_event_links_geometry_source"),
        CheckConstraint("status IN ('suggested', 'confirmed', 'excluded')", name="ck_assessment_event_links_status"), CheckConstraint("source IN ('automatic', 'manual')", name="ck_assessment_event_links_source"), CheckConstraint("frozen_intersection_geometry_json IS NULL OR jsonb_typeof(frozen_intersection_geometry_json) = 'object'", name="ck_assessment_event_links_frozen_object"),
        Index("ix_assessment_event_links_area_geometry_revision_id", "assessment_area_geometry_revision_id"), Index("ix_assessment_event_links_blast_geometry_revision_id", "blast_event_geometry_revision_id"), Index("ix_assessment_event_links_status", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_area_geometry_revision_id: Mapped[int] = mapped_column(ForeignKey("assessment_area_geometry_revisions.id", ondelete="CASCADE"), nullable=False)
    blast_event_geometry_revision_id: Mapped[int] = mapped_column(ForeignKey("blast_event_geometry_revisions.id", ondelete="RESTRICT"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    frozen_intersection_geometry_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessment_area_geometry_revision: Mapped[AssessmentAreaGeometryRevision] = relationship(back_populates="event_links")
    blast_event_geometry_revision: Mapped[BlastEventGeometryRevision] = relationship(back_populates="event_links")


class AssessmentAreaEvaluation(TimestampMixin, Base):
    __tablename__ = "assessment_area_evaluations"
    __table_args__ = (
        UniqueConstraint("assessment_area_id", name="uq_assessment_area_evaluations_area"),
        UniqueConstraint("logical_id", name="uq_assessment_area_evaluations_logical_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_area_id: Mapped[int] = mapped_column(ForeignKey("assessment_areas.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    assessment_area: Mapped[AssessmentArea] = relationship(back_populates="evaluation")
    revisions: Mapped[list["AssessmentAreaEvaluationRevision"]] = relationship(back_populates="evaluation", cascade="all, delete-orphan", passive_deletes=True, order_by="AssessmentAreaEvaluationRevision.revision_number")
    attachments: Mapped[list["AssessmentEntityAttachment"]] = relationship(back_populates="assessment_area_evaluation", cascade="all, delete-orphan", passive_deletes=True, foreign_keys="AssessmentEntityAttachment.assessment_area_evaluation_id")


class AssessmentAreaEvaluationRevision(Base):
    __tablename__ = "assessment_area_evaluation_revisions"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "logical_id", name="uq_assessment_evaluation_revisions_parent_logical_id"), UniqueConstraint("evaluation_id", "revision_number", name="uq_assessment_evaluation_revisions_parent_number"),
        CheckConstraint("revision_number > 0", name="ck_assessment_evaluation_revisions_number_positive"), CheckConstraint("matrix_template_version > 0", name="ck_assessment_evaluation_revisions_template_version_positive"), CheckConstraint("status IN ('draft', 'completed')", name="ck_assessment_evaluation_revisions_status"),
        CheckConstraint("design_achievement_index IS NULL OR design_achievement_index BETWEEN 0 AND 1", name="ck_assessment_evaluation_revisions_design_index"), CheckConstraint("face_condition_index IS NULL OR face_condition_index BETWEEN 0 AND 1", name="ck_assessment_evaluation_revisions_face_index"), CheckConstraint("jsonb_typeof(payload_json) = 'object'", name="ck_assessment_evaluation_revisions_payload_object"),
        Index("ix_assessment_evaluation_revisions_one_active", "evaluation_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("assessment_area_evaluations.id", ondelete="CASCADE"), nullable=False)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessment_area_geometry_revision_id: Mapped[int] = mapped_column(ForeignKey("assessment_area_geometry_revisions.id", ondelete="RESTRICT"), nullable=False)
    assessment_date: Mapped[Optional[date]] = mapped_column(Date)
    inspector: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    matrix_template_id: Mapped[str] = mapped_column(String(255), nullable=False)
    matrix_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    design_achievement_index: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    face_condition_index: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6))
    result_quadrant: Mapped[Optional[str]] = mapped_column(String(80))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evaluation: Mapped[AssessmentAreaEvaluation] = relationship(back_populates="revisions")
    geometry_revision: Mapped[AssessmentAreaGeometryRevision] = relationship()


class AssessmentEntityAttachment(Base):
    __tablename__ = "assessment_entity_attachments"
    __table_args__ = (
        UniqueConstraint("logical_id", name="uq_assessment_entity_attachments_logical_id"),
        CheckConstraint("(owner_type = 'blast_event' AND blast_event_id IS NOT NULL AND assessment_area_evaluation_id IS NULL) OR (owner_type = 'assessment_evaluation' AND assessment_area_evaluation_id IS NOT NULL AND blast_event_id IS NULL)", name="ck_assessment_entity_attachments_owner"),
        CheckConstraint("attachment_kind IN ('photo', 'document')", name="ck_assessment_entity_attachments_kind"), CheckConstraint("file_size_bytes IS NULL OR file_size_bytes >= 0", name="ck_assessment_entity_attachments_file_size"), CheckConstraint("length(btrim(relative_path)) > 0", name="ck_assessment_entity_attachments_relative_path"),
        Index("ix_assessment_entity_attachments_blast_event_id", "blast_event_id"), Index("ix_assessment_entity_attachments_evaluation_id", "assessment_area_evaluation_id"), Index("ix_assessment_entity_attachments_kind", "attachment_kind"), Index("ix_assessment_entity_attachments_file_date", "file_date"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logical_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_type: Mapped[str] = mapped_column(String(30), nullable=False)
    blast_event_id: Mapped[Optional[int]] = mapped_column(ForeignKey("blast_events.id", ondelete="CASCADE"))
    assessment_area_evaluation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assessment_area_evaluations.id", ondelete="CASCADE"))
    attachment_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    subtype: Mapped[str] = mapped_column(String(80), nullable=False)
    custom_subtype: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    blast_event: Mapped[Optional[BlastEvent]] = relationship(back_populates="attachments", foreign_keys=[blast_event_id])
    assessment_area_evaluation: Mapped[Optional[AssessmentAreaEvaluation]] = relationship(back_populates="attachments", foreign_keys=[assessment_area_evaluation_id])
