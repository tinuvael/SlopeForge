"""Add semantic mapping metadata to Project Design surface revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2"
down_revision = "1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_surface_datasets",
        sa.Column(
            "semantic_mapping_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("project_surface_datasets", "semantic_mapping_json")
