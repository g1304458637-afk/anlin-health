"""Stage MFA secrets and allow encrypted sensitive columns.

Revision ID: 6b3a2d87c4e1
Revises: ead91a9144f3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.crypto import EncryptedString


revision: str = "6b3a2d87c4e1"
down_revision: Union[str, None] = "ead91a9144f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wider columns accommodate AES-GCM envelope expansion. Legacy plaintext
    # remains readable by EncryptedString and is encrypted on a later write.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("totp_secret", existing_type=sa.String(64), type_=EncryptedString(200), existing_nullable=True)
        batch.add_column(sa.Column("totp_pending_secret", EncryptedString(200), nullable=True))
        batch.add_column(sa.Column("totp_pending_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("totp_pending_failures", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("claim_requests") as batch:
        batch.alter_column("stated_phone", existing_type=sa.String(30), type_=EncryptedString(200), existing_nullable=True)


def downgrade() -> None:
    connection = op.get_bind()
    encrypted_active = connection.scalar(sa.text("SELECT COUNT(*) FROM users WHERE totp_secret LIKE 'enc:v1:%'"))
    encrypted_claim = connection.scalar(sa.text("SELECT COUNT(*) FROM claim_requests WHERE stated_phone LIKE 'enc:v1:%'"))
    pending = connection.scalar(sa.text("SELECT COUNT(*) FROM users WHERE totp_pending_secret IS NOT NULL"))
    if encrypted_active or encrypted_claim or pending:
        raise RuntimeError("Cannot downgrade while encrypted MFA/claim data or staged MFA secrets remain")
    with op.batch_alter_table("claim_requests") as batch:
        batch.alter_column("stated_phone", existing_type=EncryptedString(200), type_=sa.String(30), existing_nullable=True)
    with op.batch_alter_table("users") as batch:
        batch.drop_column("totp_pending_failures")
        batch.drop_column("totp_pending_expires_at")
        batch.drop_column("totp_pending_secret")
        batch.alter_column("totp_secret", existing_type=EncryptedString(200), type_=sa.String(64), existing_nullable=True)
