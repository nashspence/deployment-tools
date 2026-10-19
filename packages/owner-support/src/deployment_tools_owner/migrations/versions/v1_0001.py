"""Frozen v1 reference schema; restore a verified backup to reverse an upgrade."""

from alembic import op

revision = "v1_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE meta(owner TEXT PRIMARY KEY,epoch TEXT NOT NULL,generation INTEGER NOT NULL)"
    )
    op.execute(
        "CREATE TABLE requests(request_id TEXT PRIMARY KEY,payload_digest TEXT NOT NULL,principal TEXT NOT NULL,state TEXT NOT NULL,effect TEXT NOT NULL)"
    )
    op.execute("PRAGMA user_version=1")


def downgrade() -> None:
    raise RuntimeError("state migrations are forward-only")
