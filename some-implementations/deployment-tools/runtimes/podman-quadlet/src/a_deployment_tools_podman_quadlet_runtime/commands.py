"""Reviewed native argument rendering, without execution."""

import re
from typing import Any

from deployment_tools_contracts.validation import ContractError


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


def podman_delete_argv(candidate: dict[str, Any]) -> list[str]:
    """Render two reviewed native commands only, never execute them."""
    require(
        type(candidate.get("native_id")) is str
        and re.fullmatch("[0-9a-f]{64}", candidate["native_id"]) is not None,
        "native object must have a full immutable ID",
    )
    if candidate["kind"] in ("image", "build-cache"):
        return ["podman", "rmi", "--no-prune", candidate["native_id"]]
    if candidate["kind"] == "container":
        return ["podman", "rm", candidate["native_id"]]
    raise ContractError("file cleanup requires installed no-follow handle executor")
