"""Frozen v1 reference schema; restore a verified backup to reverse an upgrade."""

from alembic import op

revision = "v1_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    op.execute(
        "CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,domain_id TEXT NOT NULL,workflow_id TEXT NOT NULL,event_id TEXT NOT NULL,content_digest TEXT NOT NULL,canonical_event BLOB NOT NULL,reporter TEXT NOT NULL,received_at TEXT NOT NULL,UNIQUE(domain_id,workflow_id,event_id))"
    )
    op.execute("CREATE INDEX events_by_workflow ON events(domain_id,workflow_id,sequence)")


def downgrade() -> None:
    raise RuntimeError("state migrations are forward-only")
