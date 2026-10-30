"""Alert closed-loop outcomes, per-resident thresholds, family proxy entry."""
from alembic import op
import sqlalchemy as sa

revision = "d10a00000008"
down_revision = "d10a00000007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("risk_alerts") as batch:
        batch.add_column(sa.Column("outcome", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("outcome_note", sa.String(length=300), nullable=True))
    with op.batch_alter_table("family_grants") as batch:
        batch.add_column(sa.Column("can_write_vitals", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("residents") as batch:
        batch.add_column(sa.Column("alert_overrides", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("residents") as batch:
        batch.drop_column("alert_overrides")
    with op.batch_alter_table("family_grants") as batch:
        batch.drop_column("can_write_vitals")
    with op.batch_alter_table("risk_alerts") as batch:
        batch.drop_column("outcome_note")
        batch.drop_column("outcome")
