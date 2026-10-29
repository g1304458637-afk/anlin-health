"""Health-data lifecycle: soft-deletable vitals, audit value snapshots, hot-path indexes."""
from alembic import op
import sqlalchemy as sa


revision = "d10a00000006"
down_revision = "d10a00000005"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("vitals") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_vital_resident_type_time", ["resident_id", "type", "measured_at"])
        batch.create_index("ix_vitals_actor", ["actor_id"])
    with op.batch_alter_table("questionnaire_responses") as batch:
        batch.create_index("ix_questionnaire_responses_questionnaire", ["questionnaire_id"])
    with op.batch_alter_table("audit_logs") as batch:
        batch.add_column(sa.Column("detail_json", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_column("detail_json")
    with op.batch_alter_table("questionnaire_responses") as batch:
        batch.drop_index("ix_questionnaire_responses_questionnaire")
    with op.batch_alter_table("vitals") as batch:
        batch.drop_index("ix_vitals_actor")
        batch.drop_index("ix_vital_resident_type_time")
        batch.drop_column("deleted_at")
