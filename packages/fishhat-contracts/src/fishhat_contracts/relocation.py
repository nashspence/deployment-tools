"""Pure relocation recovery-basis checks, without ownership transfer or native recovery."""

from __future__ import annotations

from typing import Any

from .validation import ContractError, validate


def validate_relocation(
    arguments: dict[str, Any],
    *,
    accepted_app: dict[str, Any],
    accepted_release_digest: str,
) -> None:
    """Join a proposal to independently loaded accepted source and release identity.

    Callers cannot supply a replacement source to reclassify preserved datasets.
    Checkpoint custody, external owners, reconstruction verification, destination
    admission and the fenced ownership handoff remain the receiving owner's work.
    """
    validate("actions.schema.json", arguments, "app.relocate")
    validate("source.schema.json", accepted_app, "App")
    validate("common.schema.json", accepted_release_digest, "Digest")
    basis = arguments["recovery_basis"]
    if basis["kind"] == "checkpoint":
        return
    if basis["release_digest"] != accepted_release_digest:
        raise ContractError("relocation release differs from accepted release")
    if any(
        dataset["custody"] == "app" and dataset["recovery"] == "preserve"
        for dataset in accepted_app["datasets"].values()
    ):
        raise ContractError("preserved application data requires a checkpoint")
