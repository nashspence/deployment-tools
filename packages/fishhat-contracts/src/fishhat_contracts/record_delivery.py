"""Immutable record delivery reference models; no store, transport or authorization.

An owner must independently establish custody and permission before publishing or
reading a snapshot. Serialization happens once; chunks do not reprocess the record.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
from typing import Any, cast

from .bounds import Bounds, bounds
from .handoffs import schema_identity, validate_result_reference, validate_workflow
from .validation import (
    MAX_BYTES,
    SCHEMAS,
    ContractError,
    canonical,
    strict_json,
    validate,
    validate_plan,
)

CHUNK_BYTES = 196608
RECORD_KINDS = ("Plan", "Operation", "Workflow")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": record["kind"],
        "id": record["id"],
        "generation": None if record["kind"] == "Plan" else record["generation"],
    }


def _validate_record(record: dict[str, Any]) -> None:
    kind = record.get("kind")
    _require(kind in RECORD_KINDS, "unsupported record snapshot kind")
    if kind == "Plan":
        validate_plan(record)
    elif kind == "Workflow":
        validate_workflow(record)
    else:
        validate_result_reference(record)


def validate_record_reference(
    reference: dict[str, Any],
    *,
    expected_owner: dict[str, Any] | None = None,
    expected_record: dict[str, Any] | None = None,
) -> None:
    """Check exact installed schema and supplied independent owner/record expectations.

    Without independent expectations this checks shape and schema only, never origin
    or permission. A newer record generation cannot silently replace a pinned one.
    """
    validate("common.schema.json", reference, "RecordReference")
    uri = SCHEMAS["records.schema.json"]["$id"] + "#/$defs/" + reference["record"]["kind"]
    _require(reference["schema"] == schema_identity(uri), "record schema substitution")
    if expected_owner is not None:
        _require(reference["owner"] == expected_owner, "record owner epoch substitution")
    if expected_record is not None:
        _require(
            reference["record"] == expected_record, "record identity or generation substitution"
        )


class RecordSnapshot:
    """Pure immutable-byte model for an already authorized owner record.

    No object created here proves native snapshot durability, receiving permission,
    retention or signature authenticity. Native stores must provide those boundaries.
    """

    def __init__(self, record: dict[str, Any], snapshot_id: str) -> None:
        _validate_record(record)
        validate("common.schema.json", snapshot_id, "UUID")
        self._record = copy.deepcopy(record)
        self._data = canonical(self._record)
        uri = SCHEMAS["records.schema.json"]["$id"] + "#/$defs/" + record["kind"]
        self._reference = {
            "format": "deployment-ops/record-reference/v1",
            "kind": "RecordReference",
            "snapshot_id": snapshot_id,
            "owner": copy.deepcopy(record["owner"]),
            "record": _identity(record),
            "schema": schema_identity(uri),
            "digest": "sha256:" + hashlib.sha256(self._data).hexdigest(),
            "size": len(self._data),
        }
        validate("common.schema.json", self._reference, "RecordReference")

    @property
    def reference(self) -> dict[str, Any]:
        return copy.deepcopy(self._reference)

    def delivery(self, *, max_inline_bytes: int) -> dict[str, Any]:
        """Select whole inline bytes or a reference within the caller's reserved budget.

        The caller accounts for its envelope and any other page items separately;
        the framing boundary still validates the complete serialized response.
        """
        _require(
            type(max_inline_bytes) is int and 0 < max_inline_bytes <= MAX_BYTES,
            "invalid inline record budget",
        )
        if len(self._data) <= max_inline_bytes:
            return copy.deepcopy(self._record)
        _require(len(canonical(self._reference)) <= max_inline_bytes, "record reference capacity")
        return self.reference

    @bounds(
        Bounds(
            id="record-delivery.chunk",
            work="O(chunk_bytes)",
            extent="one-record-chunk",
            limits=(("chunk_bytes", CHUNK_BYTES),),
            capacity=(),
            proofs=(
                "tests/test_record_delivery.py::test_record_chunk_work_tracks_requested_range",
            ),
        )
    )
    def chunk(self, reference: dict[str, Any], *, offset: int, length: int) -> dict[str, Any]:
        """Read one immutable range after the native caller's current authorization check."""
        _require(reference == self._reference, "record snapshot reference changed")
        _require(
            type(offset) is int and 0 <= offset <= len(self._data),
            "record offset outside snapshot",
        )
        _require(type(length) is int and 1 <= length <= CHUNK_BYTES, "record chunk length")
        data = self._data[offset : offset + length]
        next_offset = offset + len(data)
        return {
            "format": "deployment-ops/record-chunk/v1",
            "kind": "RecordChunk",
            "reference": self.reference,
            "offset": offset,
            "base64": base64.b64encode(data).decode("ascii"),
            "next_offset": next_offset,
            "complete": next_offset == len(self._data),
        }


def validate_record_chunk(
    chunk: dict[str, Any],
    reference: dict[str, Any],
    *,
    offset: int,
    length: int,
) -> bytes:
    """Check a response against the independently retained exact range request."""
    validate("common.schema.json", chunk, "RecordChunk")
    _require(
        type(offset) is int
        and 0 <= offset <= reference["size"]
        and type(length) is int
        and 1 <= length <= CHUNK_BYTES,
        "record range request invalid",
    )
    _require(
        chunk["reference"] == reference and chunk["offset"] == offset,
        "record chunk identity or offset substitution",
    )
    try:
        data = base64.b64decode(chunk["base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ContractError("invalid record chunk encoding") from exc
    end = offset + len(data)
    _require(
        len(data) <= length and end <= reference["size"] and chunk["next_offset"] == end,
        "record chunk range mismatch",
    )
    _require(chunk["complete"] == (end == reference["size"]), "record completion mismatch")
    _require(bool(data) or chunk["complete"], "record chunk made no progress")
    return data


def validate_record_payload(
    reference: dict[str, Any],
    data: bytes,
    *,
    expected_owner: dict[str, Any],
    expected_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify complete transferred bytes once; a transport frame cap is not a record cap.

    A native receiver can enforce explicit local capacity while streaming and must
    preserve custody on failure. This finite in-memory model asserts no native bound.
    """
    validate_record_reference(
        reference, expected_owner=expected_owner, expected_record=expected_record
    )
    _require(
        len(data) == reference["size"]
        and "sha256:" + hashlib.sha256(data).hexdigest() == reference["digest"],
        "record content identity mismatch",
    )
    value = strict_json(data, max_bytes=reference["size"])
    _require(isinstance(value, dict), "record payload must be object")
    _validate_record(value)
    _require(
        value["owner"] == reference["owner"] and _identity(value) == reference["record"],
        "record payload binding mismatch",
    )
    _require(canonical(value) == data, "record payload encoding is not canonical")
    return cast(dict[str, Any], value)


def validate_log_page(
    page: dict[str, Any], *, expected_owner: dict[str, Any], resource: dict[str, Any]
) -> None:
    """Validate even an empty log reply against independently retained query context."""
    validate("records.schema.json", page, "EventPage")
    _require(
        page["owner"] == expected_owner and page["resource"] == resource,
        "log page owner epoch or resource changed",
    )
    positions = [event["cursor"] for event in page["events"]]
    _require(positions == sorted(set(positions)), "log event positions are not strictly ordered")
