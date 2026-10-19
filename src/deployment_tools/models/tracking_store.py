"""Optional SQLite metadata-index model; NOT an HTTP/auth server or dispatcher.

Uses real SQLite on private temporary local stores in tests. No SSH, Forgejo,
subprocess, socket, URL dereferencing or owner-library imports. Production must
qualify path custody, tokens, disk quotas, actual deadlines and shutdown.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import ROOT, ContractError, canonical, digest
from .tracking import validate_event


class EventStore:
    def __init__(
        self,
        path: Path,
        domain_id: str,
        service_id: str,
        *,
        initialize: bool = False,
        max_events: int = 100000,
        max_workflows: int = 10000,
    ) -> None:
        new = not path.exists()
        if new and not initialize:
            raise ContractError("missing index; explicit initialization required")
        self.db = sqlite3.connect(path, timeout=0.25)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if new:
                self.db.executescript((ROOT / "contracts/receipts-store.sql").read_text())
                with self.db:
                    self.db.executemany(
                        "INSERT INTO meta VALUES (?,?)",
                        [
                            ("domain", domain_id),
                            ("service", service_id),
                            ("schema", "1"),
                            ("epoch", str(uuid.uuid4())),
                        ],
                    )
            meta = dict(self.db.execute("SELECT key,value FROM meta"))
            if (
                meta.get("domain") != domain_id
                or meta.get("service") != service_id
                or meta.get("schema") != "1"
            ):
                raise ContractError("index identity/schema mismatch")
            self.epoch = meta["epoch"]
        except Exception:
            self.db.close()
            raise
        self.domain, self.service = domain_id, service_id
        self.max_events, self.max_workflows = max_events, max_workflows

    def _auth(self, principal: str, scopes: set[str], action: str, domain: str | None) -> None:
        # Trusted server auth output in tests, never read from event payload.
        if not principal or action not in scopes or domain != self.domain:
            raise ContractError("report permission denied")

    def _envelope(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "format": "deployment-ops/index-record/v1",
            "service_id": self.service,
            "store_epoch": self.epoch,
            "sequence": row[0],
            "received_at": row[7],
            "reporter": row[6],
            "content_digest": row[4],
            "authority": "reported",
            "event": json.loads(row[5]),
        }

    def put(
        self,
        event: dict[str, Any],
        *,
        principal: str,
        scopes: set[str],
        workflow_id: str | None = None,
        event_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._auth(principal, scopes, "append", event.get("domain_id"))
        validate_event(event)
        if workflow_id is not None and workflow_id != event["workflow_id"]:
            raise ContractError("path/workflow mismatch")
        if event_id is not None and event_id != event["id"]:
            raise ContractError("path/event mismatch")
        key = (self.domain, event["workflow_id"], event["id"])
        data = canonical(event)
        hash_ = digest(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM events WHERE domain_id=? AND workflow_id=? AND event_id=?", key
            ).fetchone()
            if row:
                if row[5] != data:
                    raise ContractError("immutable event conflict")
                self.db.commit()
                return 200, self._envelope(row)
            count = self.db.execute("SELECT count(*) FROM events").fetchone()[0]
            workflows = self.db.execute(
                "SELECT count(DISTINCT workflow_id) FROM events"
            ).fetchone()[0]
            known = self.db.execute(
                "SELECT 1 FROM events WHERE workflow_id=? LIMIT 1", (event["workflow_id"],)
            ).fetchone()
            if count >= self.max_events or (not known and workflows >= self.max_workflows):
                raise ContractError("index quota")
            # Corrections may reference reports that arrived later; cycles are not valid.
            successor = event["supersedes"]
            seen = {event["id"]}
            while successor:
                if successor in seen:
                    raise ContractError("correction cycle")
                seen.add(successor)
                parent = self.db.execute(
                    "SELECT canonical_event FROM events WHERE domain_id=? AND workflow_id=? AND event_id=?",
                    (self.domain, event["workflow_id"], successor),
                ).fetchone()
                successor = json.loads(parent[0])["supersedes"] if parent else None
            now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            self.db.execute(
                "INSERT INTO events(domain_id,workflow_id,event_id,content_digest,canonical_event,reporter,received_at) VALUES(?,?,?,?,?,?,?)",
                (*key, hash_, data, principal, now),
            )
            row = self.db.execute(
                "SELECT * FROM events WHERE domain_id=? AND workflow_id=? AND event_id=?", key
            ).fetchone()
            self.db.commit()
            return 201, self._envelope(row)
        except Exception:
            self.db.rollback()
            raise

    def get(
        self, workflow_id: str, event_id: str, *, principal: str, scopes: set[str]
    ) -> dict[str, Any] | None:
        self._auth(principal, scopes, "read", self.domain)
        row = self.db.execute(
            "SELECT * FROM events WHERE domain_id=? AND workflow_id=? AND event_id=?",
            (self.domain, workflow_id, event_id),
        ).fetchone()
        return self._envelope(row) if row else None

    def events(
        self,
        workflow_id: str,
        *,
        principal: str,
        scopes: set[str],
        after: int = 0,
        limit: int = 100,
        expected_store_epoch: str | None = None,
    ) -> dict[str, Any]:
        self._auth(principal, scopes, "read", self.domain)
        self._page_args(after, limit, expected_store_epoch)
        rows = self.db.execute(
            "SELECT * FROM events WHERE domain_id=? AND workflow_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (self.domain, workflow_id, after, limit + 1),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows[:limit]:
            candidate = [*items, self._envelope(row)]
            if len(canonical(candidate)) > 1000000:
                break
            items = candidate
        more = len(rows) > len(items)
        return {
            "format": "deployment-ops/index-page/v1",
            "service_id": self.service,
            "store_epoch": self.epoch,
            "authority": "reported",
            "items": items,
            "next_after": items[-1]["sequence"] if more and items else None,
        }

    def workflows(
        self,
        *,
        principal: str,
        scopes: set[str],
        after: int = 0,
        limit: int = 100,
        expected_store_epoch: str | None = None,
    ) -> dict[str, Any]:
        self._auth(principal, scopes, "read", self.domain)
        self._page_args(after, limit, expected_store_epoch)
        rows = self.db.execute(
            """SELECT e.workflow_id,e.sequence,e.received_at FROM events e
            JOIN (SELECT workflow_id,min(sequence) AS first_sequence FROM events
                  WHERE domain_id=? GROUP BY workflow_id) f ON e.sequence=f.first_sequence
            WHERE e.sequence>? ORDER BY e.sequence LIMIT ?""",
            (self.domain, after, limit + 1),
        ).fetchall()
        items = [
            {"workflow_id": r[0], "first_sequence": r[1], "first_received_at": r[2]}
            for r in rows[:limit]
        ]
        return {
            "format": "deployment-ops/workflow-index-page/v1",
            "service_id": self.service,
            "store_epoch": self.epoch,
            "authority": "reported",
            "items": items,
            "next_after": items[-1]["first_sequence"] if len(rows) > limit else None,
        }

    def _page_args(self, after: int, limit: int, epoch: str | None) -> None:
        if (
            type(after) is not int
            or type(limit) is not int
            or not 0 <= after <= 9007199254740991
            or not 1 <= limit <= 100
        ):
            raise ContractError("bounded integer pagination required")
        if (after and epoch is None) or (epoch is not None and epoch != self.epoch):
            raise ContractError("index pagination epoch changed/missing")

    def restore_epoch(self) -> None:
        """Explicit offline recovery mechanic only; never run as an API endpoint."""
        self.epoch = str(uuid.uuid4())
        with self.db:
            self.db.execute("UPDATE meta SET value=? WHERE key='epoch'", (self.epoch,))

    def close(self) -> None:
        self.db.close()
