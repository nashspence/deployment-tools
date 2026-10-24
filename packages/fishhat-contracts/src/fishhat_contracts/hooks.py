"""Pure application-hook handoff checks; no materialization, grants or native execution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .validation import ContractError, canonical, validate


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _path(value: str) -> PurePosixPath:
    _require(
        value.startswith("/") and all(part not in ("", ".", "..") for part in value[1:].split("/")),
        "noncanonical hook materialization path",
    )
    return PurePosixPath(value)


def validate_hook_request(value: dict[str, Any]) -> None:
    """Validate the closed request and exact snapshot/materialization joins.

    These lexical checks do not establish filesystem identity or authority. The
    receiving owner independently verifies accepted procedure grants, selected
    input custody, actual artifact bytes, native aliases and read-only isolation.
    """
    validate("hook-request.schema.json", value)
    selected = value["restore_input"]
    if selected is None:
        return
    _require(
        set(selected["datasets"]) == set(value["dataset_handles"]),
        "restore input must cover every dataset target exactly",
    )
    targets = [_path(value["output_directory"])] + [
        _path(target["path"]) for target in value["dataset_handles"].values()
    ]
    paths: dict[PurePosixPath, bytes] = {}
    for dataset, item in selected["datasets"].items():
        snapshot = item["snapshot"]
        _require(
            snapshot["dataset"] == dataset and snapshot["producer"]["role"] == "capture",
            "restore snapshot dataset or producer mismatch",
        )
        expected = {canonical(artifact) for artifact in snapshot["artifacts"]}
        supplied = [canonical(entry["artifact"]) for entry in item["materializations"]]
        _require(
            len(supplied) == len(expected) and set(supplied) == expected,
            "restore materialization differs from complete snapshot artifacts",
        )
        for materialization in item["materializations"]:
            path = _path(materialization["path"])
            identity = canonical(materialization["artifact"])
            _require(
                path not in paths or paths[path] == identity,
                "restore materialization path selects different artifacts",
            )
            _require(
                all(path != p and path not in p.parents and p not in path.parents for p in targets),
                "restore materialization overlaps dataset target or output",
            )
            paths[path] = identity


def require_selected_restore_input(
    value: dict[str, Any],
    *,
    checkpoint_digest: str,
    snapshots: Mapping[str, dict[str, Any]],
) -> None:
    """Compare with independently retained owner inputs, never request-derived pins.

    The owner supplies the checkpoint and snapshot closure selected by accepted
    work, after authenticating their custody. A matching result supplies neither
    continuing dispatch permission nor evidence of native materialization.
    """
    validate_hook_request(value)
    validate("common.schema.json", checkpoint_digest, "Digest")
    for snapshot in snapshots.values():
        validate("adapter.schema.json", snapshot, "Snapshot")
    selected = value["restore_input"]
    _require(selected is not None, "selected restore input requires a restore hook")
    _require(selected["checkpoint_digest"] == checkpoint_digest, "selected checkpoint changed")
    _require(
        {dataset: item["snapshot"] for dataset, item in selected["datasets"].items()}
        == dict(snapshots),
        "selected snapshot closure changed",
    )
