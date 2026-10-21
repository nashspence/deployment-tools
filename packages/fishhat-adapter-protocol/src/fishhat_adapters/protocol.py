"""Bounded data validation, with installation trust supplied by the receiving owner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from fishhat_contracts.validation import ContractError, canonical, digest, validate
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable

ROLES: dict[str, dict[str, dict[str, Any]]] = json.loads(
    Path(__file__).with_name("roles.json").read_text(encoding="utf-8")
)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


def check_schema(schema: dict[str, Any]) -> None:
    """No remote resolution; every object boundary must explicitly close its fields."""
    require(len(canonical(schema)) <= 262144, "SCHEMA_CAPACITY")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError("INVALID_CONFIGURATION_SCHEMA") from exc

    def walk(node: Any, depth: int = 0) -> None:
        require(depth <= 32, "SCHEMA_DEPTH")
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in node:
                    require(
                        isinstance(node[key], str) and node[key].startswith("#/"),
                        "NONLOCAL_SCHEMA_REFERENCE",
                    )
            node_type = node.get("type")
            if (
                node_type == "object"
                or (isinstance(node_type, list) and "object" in node_type)
                or "properties" in node
            ):
                require(node.get("additionalProperties") is False, "OPEN_OBJECT_SCHEMA")
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(schema)


def check_descriptor(value: dict[str, Any]) -> None:
    validate("adapter.schema.json", value)
    check_schema(value["configuration_schema"])
    check_schema(value["component_schema"])
    check_schema(value["source_schema"])
    allowed = {role + "." + method for role in value["roles"] for method in ROLES[role]}
    require(set(value["implemented"]) <= allowed, "UNKNOWN_OPERATION")


def check_configuration(schema: dict[str, Any], value: Any, expected_digest: str) -> None:
    check_schema(schema)
    require(digest(schema) == expected_digest, "SCHEMA_IDENTITY")
    require(len(canonical(value)) <= 1048576, "CONFIGURATION_CAPACITY")
    try:
        errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value)
        require(next(errors, None) is None, "CONFIGURATION_REJECTED")
    except (RecursionError, Unresolvable):
        raise ContractError("CONFIGURATION_REJECTED") from None


def check_request(role: str, value: dict[str, Any]) -> None:
    require(role in ROLES, "UNKNOWN_CONTRACT")
    validate(role + "-adapter.schema.json", value)


def check_response(
    role: str,
    request: dict[str, Any],
    response: dict[str, Any],
    *,
    attempt: dict[str, Any] | None = None,
) -> None:
    check_request(role, request)
    validate(role + "-adapter.schema.json", response, "Response")
    require(
        response["contract"] == request["contract"]
        and response["method"] == request["method"]
        and response["request_digest"] == digest(request)
        and response["scope"] == request["input"]["scope"],
        "RESPONSE_IDENTITY",
    )
    require(
        response["coverage"] != "complete" or response["continuation"] is None,
        "INCOMPLETE_RESPONSE_COVERAGE",
    )
    if ROLES[role][request["method"]]["effect"]:
        require(attempt is not None, "EFFECT_ATTEMPT_REQUIRED")
        assert attempt is not None
        require(
            attempt["input_digest"] == response["request_digest"]
            and attempt["scope"] == response["scope"],
            "RESPONSE_EFFECT_IDENTITY",
        )
        check_result(attempt, response["effect"])


def check_result(attempt: dict[str, Any], result: dict[str, Any]) -> None:
    validate("adapter.schema.json", attempt, "EffectAttempt")
    validate("adapter.schema.json", result, "EffectResult")
    require(
        result["attempt_id"] == attempt["attempt_id"]
        and result["input_digest"] == attempt["input_digest"],
        "RESULT_IDENTITY",
    )
    if result["acknowledgment"] == "rejected":
        require(
            result["effect_certainty"] == "none" and result["task_state"] == "terminal",
            "CONTRADICTORY_REJECTION",
        )
    if result["postcondition"] == "satisfied":
        require(result["effect_certainty"] == "known", "UNKNOWN_EFFECT_CANNOT_SUCCEED")
    if result["task_state"] == "running":
        require(result["native_task"] is not None, "MISSING_NATIVE_TASK")


class Registry:
    """A receiving owner's pinned data registry, never a package loader or permission engine."""

    def __init__(self) -> None:
        self._installations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self._instances: dict[str, dict[str, Any]] = {}

    def install(
        self, installation: dict[str, Any], descriptor: dict[str, Any], *, owner_approved: bool
    ) -> None:
        require(owner_approved, "INSTALLATION_NOT_AUTHORIZED")
        validate("adapter.schema.json", installation, "Installation")
        check_descriptor(descriptor)
        require(
            installation["implementation"] == descriptor["id"]
            and installation["descriptor_digest"] == digest(descriptor),
            "INSTALLATION_IDENTITY",
        )
        key = installation["artifact"]["digest"]
        pair = (copy.deepcopy(installation), copy.deepcopy(descriptor))
        require(
            key not in self._installations or self._installations[key] == pair,
            "IMMUTABLE_INSTALLATION_CONFLICT",
        )
        self._installations[key] = pair

    def enroll(self, instance: dict[str, Any], *, owner_approved: bool) -> None:
        require(owner_approved, "INSTANCE_NOT_AUTHORIZED")
        validate("adapter.schema.json", instance, "Instance")
        pair = self._installations.get(instance["installation_digest"])
        require(pair is not None, "UNINSTALLED_ADAPTER")
        assert pair is not None
        installation, descriptor = pair
        require(
            installation["enabled"]
            and instance["implementation"] == descriptor["id"]
            and instance["role"] in descriptor["roles"],
            "INSTANCE_BINDING",
        )
        check_configuration(
            descriptor["configuration_schema"], instance["configuration"], instance["schema_digest"]
        )
        scopes = [digest(x["scope"]) for x in instance["grants"]]
        require(len(scopes) == len(set(scopes)), "DUPLICATE_SCOPE")
        previous = self._instances.get(instance["id"])
        require(previous is None or previous == instance, "EXPLICIT_REBIND_REQUIRED")
        self._instances[instance["id"]] = copy.deepcopy(instance)

    def inspect_instance(self, instance_id: str) -> dict[str, Any]:
        instance = self._instances.get(instance_id)
        require(instance is not None, "UNENROLLED_ADAPTER")
        assert instance is not None
        return copy.deepcopy(instance)

    def resolve(self, selection: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
        validate("adapter.schema.json", selection, "Selection")
        instance = self._instances.get(selection["instance"])
        require(instance is not None, "UNENROLLED_ADAPTER")
        assert instance is not None
        _, descriptor = self._installations[instance["installation_digest"]]
        require(instance["role"] == "runtime", "INSTANCE_ROLE")
        check_configuration(
            descriptor["source_schema"], selection["source"], selection["source_schema_digest"]
        )
        for component in components.values():
            check_configuration(
                descriptor["component_schema"],
                component["native"],
                digest(descriptor["component_schema"]),
            )
        selectors = [canonical(x["native"]) for x in components.values()]
        require(len(set(selectors)) == len(selectors), "DUPLICATE_COMPONENT")
        return copy.deepcopy(instance)

    def authorize(
        self,
        instance_id: str,
        role: str,
        method: str,
        scope: dict[str, Any],
        *,
        owner_approved: bool,
    ) -> dict[str, Any]:
        require(owner_approved, "OPERATION_NOT_AUTHORIZED")
        require(role in ROLES and method in ROLES[role], "UNKNOWN_OPERATION")
        instance = self._instances.get(instance_id)
        require(instance is not None and instance["role"] == role, "INSTANCE_ROLE")
        assert instance is not None
        access = "change" if ROLES[role][method]["effect"] else "observe"
        require(
            any(g["scope"] == scope and access in g["access"] for g in instance["grants"]),
            "SCOPE_NOT_GRANTED",
        )
        _, descriptor = self._installations[instance["installation_digest"]]
        require(role + "." + method in descriptor["implemented"], "NATIVE_EXECUTOR_UNIMPLEMENTED")
        return copy.deepcopy(instance)


def require_qualification(
    value: dict[str, Any],
    *,
    expected: dict[str, str],
    required_cases: set[str],
    trusted_runner: str,
) -> None:
    """Validate evidence already authenticated by the owner; data cannot authenticate itself."""
    validate("adapter.schema.json", value, "Qualification")
    require(
        set(expected)
        == {"installation_digest", "contract_digest", "configuration_digest", "environment_digest"},
        "QUALIFICATION_BINDING",
    )
    require(
        all(value[k] == v for k, v in expected.items()) and value["runner"] == trusted_runner,
        "QUALIFICATION_IDENTITY",
    )
    cases = {case["id"]: case["outcome"] for case in value["cases"]}
    require(len(cases) == len(value["cases"]), "DUPLICATE_QUALIFICATION_CASE")
    require(bool(required_cases) and required_cases <= cases.keys(), "MISSING_QUALIFICATION_CASE")
    require(all(cases[k] == "passed" for k in required_cases), "UNQUALIFIED_NATIVE_BEHAVIOR")
