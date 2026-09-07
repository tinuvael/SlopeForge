"""Persist Wall Conformance alignments per Assessment geometry revision."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "3"
down_revision = "2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_area_wall_alignments",
        sa.Column("assessment_area_geometry_revision_id", sa.Integer(), nullable=False),
        sa.Column(
            "points_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(points_json) = 'array'",
            name="ck_assessment_area_wall_alignments_points_array",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_area_geometry_revision_id"],
            ["assessment_area_geometry_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assessment_area_geometry_revision_id"),
    )


def downgrade() -> None:
    op.drop_table("assessment_area_wall_alignments")
