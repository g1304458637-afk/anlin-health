"""Measurement context and request deduplication for retry-safe manual entry."""
from alembic import op
import sqlalchemy as sa

revision = "d10a00000003"
down_revision = "d10a00000002"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("vitals") as batch:
        batch.add_column(sa.Column("measurement_context", sa.String(30), nullable=True))
        batch.add_column(sa.Column("client_request_id", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_vital_client_request", ["resident_id", "actor_id", "client_request_id"])


def downgrade():
    with op.batch_alter_table("vitals") as batch:
        batch.drop_constraint("uq_vital_client_request", type_="unique")
        batch.drop_column("client_request_id")
        batch.drop_column("measurement_context")
