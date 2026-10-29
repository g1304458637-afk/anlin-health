"""Track the highest consumed TOTP counter so a shown code cannot be replayed."""
from alembic import op
import sqlalchemy as sa

revision = "d10a00000004"
down_revision = "d10a00000003"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("totp_last_used_counter", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("totp_last_used_counter")
