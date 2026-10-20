"""Owner dispatch reference with an injected authoritative commit boundary.

This models persistence ordering, not native supervision or a separate journal.
Native observations must be authenticated and qualified by the installed owner.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, cast

from deployment_tools_adapters import ROLES, Registry, check_request, check_result
from deployment_tools_contracts.validation import ContractError, canonical, digest, validate


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


class AdapterCoordinator:
    def __init__(
        self,
        owner: dict[str, Any],
        registry: Registry,
        commit: Callable[[dict[str, Any]], None],
    ) -> None:
        validate("common.schema.json", owner, "Owner")
        self.registry = registry
        self.commit = commit
        self._poisoned = False
        self.state: dict[str, Any] = {
            "owner": copy.deepcopy(owner),
            "bindings": {},
            "admission": {},
            "attempts": {},
            "quiescent": {},
        }

    def _publish(self, candidate: dict[str, Any]) -> None:
        require(not self._poisoned, "OWNER_COMMIT_UNCERTAIN")
        canonical(candidate)
        try:
            self.commit(copy.deepcopy(candidate))
        except Exception:
            # A failed acknowledgment can follow a successful commit. Stop all dispatch.
            self._poisoned = True
            raise
        self.state = candidate

    def bind(self, binding: dict[str, Any], *, owner_approved: bool) -> None:
        require(owner_approved, "BINDING_NOT_AUTHORIZED")
        validate("adapter.schema.json", binding, "RuntimeBinding")
        subject = binding["subject"]
        require(
            binding["scope"]["kind"] == "application"
            and binding["scope"]["handle"] == subject
            and binding["scope"]["authority"] == self.state["owner"]["id"],
            "SUBJECT_SCOPE",
        )
        require(subject not in self.state["bindings"], "EXPLICIT_REBIND_REQUIRED")
        self._instance_matches(binding)
        candidate = copy.deepcopy(self.state)
        candidate["bindings"][subject] = copy.deepcopy(binding)
        candidate["admission"][subject] = False
        candidate["quiescent"][subject] = False
        self._publish(candidate)

    def _instance_matches(self, binding: dict[str, Any]) -> None:
        instance = self.registry.inspect_instance(binding["instance"])
        require(
            instance["role"] == "runtime"
            and instance["installation_digest"] == binding["installation_digest"]
            and instance["generation"] == binding["instance_generation"]
            and digest(instance["configuration"]) == binding["configuration_digest"],
            "STALE_BINDING",
        )
        require(
            any(g["scope"] == binding["scope"] for g in instance["grants"]),
            "SUBJECT_SCOPE",
        )

    def open_admission(self, subject: str, *, owner_approved: bool) -> None:
        require(owner_approved, "ADMISSION_NOT_AUTHORIZED")
        require(self.state["quiescent"].get(subject) is True, "QUIESCENCE_REQUIRED")
        self._instance_matches(self.state["bindings"][subject])
        candidate = copy.deepcopy(self.state)
        candidate["admission"][subject] = True
        candidate["quiescent"][subject] = False
        self._publish(candidate)

    def close_admission(self, subject: str) -> None:
        require(subject in self.state["bindings"], "UNKNOWN_BINDING")
        candidate = copy.deepcopy(self.state)
        candidate["admission"][subject] = False
        candidate["quiescent"][subject] = False
        candidate["bindings"][subject]["admission_generation"] += 1
        validate("adapter.schema.json", candidate["bindings"][subject], "RuntimeBinding")
        self._publish(candidate)

    def prepare(
        self,
        subject: str,
        request: dict[str, Any],
        *,
        operation_id: str,
        effect_id: str,
        attempt_id: str,
        owner_approved: bool,
    ) -> dict[str, Any]:
        require(not self._poisoned, "OWNER_COMMIT_UNCERTAIN")
        check_request("runtime", request)
        binding = self.state["bindings"].get(subject)
        require(binding is not None, "UNKNOWN_BINDING")
        assert binding is not None
        require(ROLES["runtime"][request["method"]]["effect"], "REQUEST_IS_NOT_EFFECT")
        require(request["method"] != "reconcile", "RECONCILE_ORIGINAL_ATTEMPT")
        require(request["input"].get("scope") == binding["scope"], "REQUEST_SCOPE")
        self._instance_matches(binding)
        self.registry.authorize(
            binding["instance"],
            "runtime",
            request["method"],
            binding["scope"],
            owner_approved=owner_approved,
        )
        attempt = {
            "owner": copy.deepcopy(self.state["owner"]),
            "operation_id": operation_id,
            "effect_id": effect_id,
            "attempt_id": attempt_id,
            "instance": binding["instance"],
            "installation_digest": binding["installation_digest"],
            "binding_generation": binding["generation"],
            "native_incarnation": binding["native_incarnation"],
            "contract": request["contract"],
            "method": request["method"],
            "scope": copy.deepcopy(binding["scope"]),
            "input_digest": digest(request),
            "state": "prepared",
            "certainty": "none",
            "outcome": "pending",
            "native_task": None,
        }
        validate("adapter.schema.json", attempt, "EffectAttempt")
        previous = self.state["attempts"].get(attempt_id)
        if previous is not None:
            immutable = set(attempt) - {"state", "certainty", "outcome", "native_task"}
            require(all(previous[k] == attempt[k] for k in immutable), "ATTEMPT_IDENTITY_CONFLICT")
            return cast(dict[str, Any], copy.deepcopy(previous))
        for old in self.state["attempts"].values():
            if old["effect_id"] == effect_id and old["operation_id"] == operation_id:
                require(old["state"] == "resolved", "UNCERTAIN_EFFECT_RETRY")
                raise ContractError("EFFECT_RETRY_REQUIRES_NEW_PLAN")
        candidate = copy.deepcopy(self.state)
        candidate["attempts"][attempt_id] = attempt
        self._publish(candidate)
        return copy.deepcopy(attempt)

    def cancel_prepared(self, attempt_id: str, *, owner_approved: bool) -> None:
        require(owner_approved, "CANCELLATION_NOT_AUTHORIZED")
        attempt = self.state["attempts"].get(attempt_id)
        require(attempt is not None, "UNKNOWN_ATTEMPT")
        assert attempt is not None
        require(attempt["state"] == "prepared", "CANNOT_CANCEL_DISPATCHED_EFFECT")
        candidate = copy.deepcopy(self.state)
        candidate["attempts"][attempt_id].update(state="resolved", outcome="cancelled")
        self._publish(candidate)

    def dispatch(self, subject: str, attempt_id: str) -> dict[str, Any]:
        require(not self._poisoned, "OWNER_COMMIT_UNCERTAIN")
        binding = self.state["bindings"].get(subject)
        attempt = self.state["attempts"].get(attempt_id)
        require(binding is not None and attempt is not None, "UNKNOWN_ATTEMPT")
        assert binding is not None and attempt is not None
        gate = ROLES["runtime"][attempt["method"]]["admission"]
        if gate == "open":
            require(self.state["admission"][subject], "ADMISSION_CLOSED")
        elif gate == "closed":
            require(not self.state["admission"][subject], "ADMISSION_NOT_CLOSED")
        self._instance_matches(binding)
        require(
            attempt["scope"] == binding["scope"]
            and attempt["instance"] == binding["instance"]
            and attempt["installation_digest"] == binding["installation_digest"]
            and attempt["binding_generation"] == binding["generation"]
            and attempt["native_incarnation"] == binding["native_incarnation"],
            "STALE_DISPATCH",
        )
        require(attempt["state"] == "prepared", "RECONCILE_REQUIRED")
        candidate = copy.deepcopy(self.state)
        candidate["attempts"][attempt_id].update(state="dispatched", certainty="unknown")
        candidate["quiescent"][subject] = False
        self._publish(candidate)
        # The native call can occur only after this owner commit has returned.
        return cast(dict[str, Any], copy.deepcopy(self.state["attempts"][attempt_id]))

    def settle(self, attempt_id: str, result: dict[str, Any]) -> None:
        attempt = self.state["attempts"].get(attempt_id)
        require(attempt is not None, "UNKNOWN_ATTEMPT")
        assert attempt is not None
        require(attempt["state"] in {"dispatched", "reconciling"}, "INVALID_SETTLEMENT")
        check_result(attempt, result)
        known_terminal = (
            result["task_state"] == "terminal" and result["effect_certainty"] != "unknown"
        )
        outcome = "pending"
        if known_terminal and result["postcondition"] != "unknown":
            outcome = "succeeded" if result["postcondition"] == "satisfied" else "failed"
        candidate = copy.deepcopy(self.state)
        candidate["attempts"][attempt_id].update(
            state="resolved" if outcome != "pending" else "reconciling",
            certainty=result["effect_certainty"],
            native_task=result["native_task"],
            outcome=outcome,
        )
        self._publish(candidate)

    def prove_quiescent(self, subject: str, observation: dict[str, Any]) -> None:
        validate("adapter.schema.json", observation, "Observation")
        binding = self.state["bindings"].get(subject)
        require(
            binding is not None and not self.state["admission"][subject], "ADMISSION_NOT_CLOSED"
        )
        assert binding is not None
        require(
            observation["scope"] == binding["scope"]
            and observation["generation"] == binding["generation"]
            and observation["instance"] == binding["instance"]
            and observation["installation_digest"] == binding["installation_digest"]
            and observation["native_incarnation"] == binding["native_incarnation"]
            and observation["admission_generation"] == binding["admission_generation"],
            "OBSERVATION_IDENTITY",
        )
        require(
            observation["coverage"] == "complete"
            and observation["continuation"] is None
            and observation["activity"] == "inactive"
            and observation["quiescence"] == "verified",
            "INCOMPLETE_QUIESCENCE",
        )
        require(
            not any(
                a["scope"] == binding["scope"] and a["state"] != "resolved"
                for a in self.state["attempts"].values()
            ),
            "PENDING_NATIVE_WORK",
        )
        candidate = copy.deepcopy(self.state)
        candidate["quiescent"][subject] = True
        self._publish(candidate)

    def snapshot(self) -> dict[str, Any]:
        require(not self._poisoned, "OWNER_COMMIT_UNCERTAIN")
        return copy.deepcopy(self.state)

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Load owner-verified state; cached observations never restore start authority."""
        canonical(snapshot)
        require(
            set(snapshot) == set(self.state) and snapshot["owner"] == self.state["owner"],
            "RESTORE_OWNER_IDENTITY",
        )
        scopes = {}
        for key, binding in snapshot["bindings"].items():
            validate("adapter.schema.json", binding, "RuntimeBinding")
            require(
                key == binding["subject"]
                and binding["scope"]["kind"] == "application"
                and binding["scope"]["handle"] == key
                and binding["scope"]["authority"] == snapshot["owner"]["id"],
                "RESTORE_BINDING_IDENTITY",
            )
            scopes[digest(binding["scope"])] = binding
        for key, attempt in snapshot["attempts"].items():
            validate("adapter.schema.json", attempt, "EffectAttempt")
            require(
                key == attempt["attempt_id"]
                and attempt["owner"] == snapshot["owner"]
                and digest(attempt["scope"]) in scopes,
                "RESTORE_ATTEMPT_IDENTITY",
            )
        candidate = copy.deepcopy(snapshot)
        candidate["admission"] = {subject: False for subject in candidate["bindings"]}
        candidate["quiescent"] = {subject: False for subject in candidate["bindings"]}
        for binding in candidate["bindings"].values():
            binding["admission_generation"] += 1
            validate("adapter.schema.json", binding, "RuntimeBinding")
        self._publish(candidate)
