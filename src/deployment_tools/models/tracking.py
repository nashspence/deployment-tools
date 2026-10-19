"""Bounded report validation/projection model, NOT a live Forgejo or HTTP client.

Adapters passed by tests are metadata-only fakes. Native hard deadlines, auth,
HTTP handling and isolation remain unimplemented qualification requirements.
"""

from __future__ import annotations

import copy
from typing import Any

from .contracts import ContractError, canonical, digest, validate

MAX_EVENT_BYTES = 16384


def validate_event(event: dict[str, Any]) -> dict[str, Any]:
    validate("tracking-records.schema.json", event, "Event")
    if len(canonical(event)) > MAX_EVENT_BYTES:
        raise ContractError("event too large")
    if event["id"] == event["supersedes"]:
        raise ContractError("self correction")
    if (event["kind"] == "correction") != (event["supersedes"] is not None):
        raise ContractError("only corrections name superseded events")
    if event["kind"] == "intent" and event["operations"]:
        raise ContractError("intent-only report must not assert accepted work")
    if event["coordination_copies"] in ("issue-only", "issue-and-index") and event["issue"] is None:
        raise ContractError("issue disposition needs exact issue reference")
    if event.get("index_reference") is not None:
        if event["index_reference"]["workflow_id"] != event["workflow_id"]:
            raise ContractError("index link/workflow mismatch")
        if event["coordination_copies"] not in ("issue-and-index", "index-only"):
            raise ContractError("index link needs reported index-copy disposition")
    seen = set()
    for op in event["operations"]:
        key = (op["owner"]["id"], op["owner"]["epoch"], op["request_id"])
        if key in seen:
            raise ContractError("duplicate owner identity in report")
        seen.add(key)
        if op["owner"] not in event["participants"]:
            raise ContractError("unknown reported participant")
    return event


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    validate("tracking-profile.schema.json", profile)
    if profile["issue"] is None and profile["index"] is None:
        raise ContractError("no reporting target configured")
    if profile["index"]:
        port = int(profile["index"]["base_url"].rsplit(":", 1)[1])
        if not 1 <= port <= 65535:
            raise ContractError("invalid loopback port")
    return profile


def report(event: dict[str, Any], adapters: dict[str, Any]) -> dict[str, Any]:
    """Explicit best-effort reporter only. No management outcome is accepted as input.

    Fakes must be bounded by the caller. Real implementation must enforce the
    overall five-second watchdog including DNS, credential I/O and all requests.
    """
    out: dict[str, Any] = {
        "format": "deployment-ops/reporting-result/v1",
        "event_id": None,
        "workflow_id": None,
        "recorded": False,
        "complete": False,
        "sinks": [],
        "execution_affected": False,
    }
    if any(k not in ("issue", "index") for k in adapters):
        raise ContractError("unknown reporting adapter")
    try:
        validate_event(event)
    except (ContractError, ValueError, TypeError):
        out["sinks"] = [
            {"sink": k, "status": "invalid", "code": "invalid-event", "reference": None}
            for k in adapters
        ]
        return out
    out["event_id"], out["workflow_id"] = event["id"], event["workflow_id"]
    for name, adapter in adapters.items():
        if adapter is None:
            result = {
                "sink": name,
                "status": "not-configured",
                "code": "disabled",
                "reference": None,
            }
        else:
            try:
                # One best-effort call, no retry loop or arbitrary URL lookup.
                result = adapter(copy.deepcopy(event))
                validate("tracking-records.schema.json", result, "SinkResult")
                if result["sink"] != name:
                    raise ContractError("wrong adapter result")
            except (TimeoutError, ConnectionError):
                # The remote report may have been committed. No false negative.
                result = {
                    "sink": name,
                    "status": "unknown",
                    "code": "transport-unknown",
                    "reference": None,
                }
            except PermissionError:
                result = {
                    "sink": name,
                    "status": "failed",
                    "code": "permission-denied",
                    "reference": None,
                }
            except Exception:
                # An unexpected adapter failure may follow a successful write.
                # Do not include raw exceptions: they may contain credentials.
                result = {
                    "sink": name,
                    "status": "unknown",
                    "code": "report-unknown",
                    "reference": None,
                }
        out["sinks"].append(result)
    successes = [r["status"] in ("recorded", "duplicate") for r in out["sinks"]]
    out["recorded"] = any(successes)
    out["complete"] = bool(successes) and all(successes)
    validate("tracking-records.schema.json", out, "ReportingResult")
    return out


def render_issue_comment(event: dict[str, Any]) -> str:
    """Render only validated metadata; no execution instructions or raw outputs."""
    validate_event(event)
    # JSON text in a fenced block prevents summary markup from adding links/actions.
    # Backticks escaped in serialized JSON so supplied summary cannot close block.
    body = (
        canonical(event)
        .decode("utf-8")
        .replace("`", "\\u0060")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return (
        f"<!-- deployment-ops-report:v1 workflow={event['workflow_id']} event={event['id']} digest={digest(event)} -->\n"
        "Reported coordination metadata only; verify execution at the actual owners.\n\n"
        "```json\n" + body + "\n```\n"
    )


def comment_match(comments: list[str], event: dict[str, Any], *, complete_scan: bool) -> str:
    """Read-only duplicate hint; never permits blind POST retry or owner replay."""
    validate_event(event)
    prefix = (
        f"<!-- deployment-ops-report:v1 workflow={event['workflow_id']} event={event['id']} digest="
    )
    hits = [c for c in comments if c.startswith(prefix)]
    if any(not c.startswith(prefix + digest(event) + " -->") for c in hits):
        return "conflict"
    if hits:
        return "duplicate"
    return "not-observed" if complete_scan else "unknown"


def validate_index_auth(config: dict[str, Any]) -> dict[str, Any]:
    """Static enrollment checks only; no actual bearer authentication implemented."""
    validate("receipts-service.schema.json", config, "Auth")
    ids = set()
    hashes = set()
    for principal in config["principals"]:
        if principal["id"] in ids or principal["token_sha256"] in hashes:
            raise ContractError("ambiguous index principal/token binding")
        ids.add(principal["id"])
        hashes.add(principal["token_sha256"])
    return config
