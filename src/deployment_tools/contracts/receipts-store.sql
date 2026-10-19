-- Report-only reference DDL; no dispatch queue or owner-operation authority.
PRAGMA foreign_keys=ON;
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  domain_id TEXT NOT NULL,
  workflow_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  content_digest TEXT NOT NULL,
  canonical_event BLOB NOT NULL,
  reporter TEXT NOT NULL,
  received_at TEXT NOT NULL,
  UNIQUE(domain_id, workflow_id, event_id)
);
CREATE INDEX events_by_workflow ON events(domain_id, workflow_id, sequence);
