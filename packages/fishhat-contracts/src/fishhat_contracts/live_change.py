"""Offline host.apply comparisons; native observation and custody belong to executors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .validation import ContractError, validate_plan


def validate_host_preimages(plan: dict[str, Any]) -> None:
    """Check joins after Plan schema validation, in linear work over its effects."""
    if plan["subject"]["action"] != "host.apply":
        return
    preimages = {item["effect_step"]: item for item in plan["host_preimages"]}
    if len(preimages) != len(plan["host_preimages"]):
        raise ContractError("duplicate host preimage step")
    if set(preimages) != {effect["step"] for effect in plan["effects"]}:
        raise ContractError("host preimages must cover every effect exactly")
    for item in preimages.values():
        if item["disposition"] == "preserve" and item["proposed_digest"] != item["live_digest"]:
            raise ContractError("preserved live state differs from proposed state")


def require_current_host_preimages(
    plan: dict[str, Any],
    current_by_step: Mapping[str, str],
    verified_captures: Mapping[str, tuple[str, str]] | None = None,
) -> None:
    """Compare owner-supplied observations and verified custody with a reviewed plan.

    Each capture value is (artifact digest, captured live digest), verified by the
    executor for that exact effect's scope, independently of the plan. Neither
    caller-supplied hashes nor a successful comparison establish native facts.
    The executor must hold native exclusion or supply an equivalent atomic check
    through each mutation, and recheck after interruption. This pure batch check
    performs no reads, captures, writes, admission or source adoption.
    """
    validate_plan(plan)
    if plan["subject"]["action"] != "host.apply":
        raise ContractError("host preimage check requires a host.apply plan")
    preimages = {item["effect_step"]: item for item in plan["host_preimages"]}
    if set(current_by_step) != set(preimages):
        raise ContractError("current host preimages must cover every effect exactly")
    captures = {} if verified_captures is None else verified_captures
    capture_steps = {step for step, item in preimages.items() if item["disposition"] == "capture"}
    if set(captures) != capture_steps:
        raise ContractError("verified captures must cover capture dispositions exactly")
    for step, item in preimages.items():
        if current_by_step[step] != item["live_digest"]:
            raise ContractError("live host state changed after review")
        if item["disposition"] == "capture" and captures[step] != (
            item["capture_digest"],
            item["live_digest"],
        ):
            raise ContractError("capture does not preserve reviewed live state")
