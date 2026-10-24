"""Bounded data validation, with installation trust supplied by the receiving owner."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from fishhat_contracts.validation import FORMAT_CHECKER, ContractError, canonical, digest, validate
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable

ROLES: dict[str, dict[str, dict[str, Any]]] = json.loads(
    Path(__file__).with_name("roles.json").read_text(encoding="utf-8")
)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


def runtime_binding_digest(binding: dict[str, Any]) -> str:
    """Identify an accepted runtime binding independently of its current admission fence."""
    validate("adapter.schema.json", binding, "RuntimeBinding")
    return digest({key: value for key, value in binding.items() if key != "admission_generation"})


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
        errors = Draft202012Validator(schema, format_checker=FORMAT_CHECKER).iter_errors(value)
        require(next(errors, None) is None, "CONFIGURATION_REJECTED")
    except (RecursionError, Unresolvable):
        raise ContractError("CONFIGURATION_REJECTED") from None


def check_request(role: str, value: dict[str, Any]) -> None:
    require(role in ROLES, "UNKNOWN_CONTRACT")
    validate(role + "-adapter.schema.json", value)
    method, inputs = value["method"], value["input"]
    if method == "reconcile":
        original = inputs["attempt"]
        require(
            original["contract"] == value["contract"]
            and original["scope"] == inputs["scope"]
            and original["method"] in ROLES[role]
            and ROLES[role][original["method"]]["effect"],
            "RECONCILIATION_IDENTITY",
        )
        return
    binding = _handoff_binding(role, value)
    if binding is not None:
        require(
            binding["role"] == role and binding["scope"] == inputs["scope"],
            "HANDOFF_BINDING_SCOPE",
        )
    if role == "capture" and method in {"restore", "verify"}:
        snapshot = inputs["snapshot"]
        require(
            snapshot["dataset"] == inputs["dataset"] and snapshot["producer"]["role"] == "capture",
            "SNAPSHOT_INPUT_IDENTITY",
        )
    if role == "secret-source" and method == "publish":
        previous = inputs["expected_previous"]
        require(
            previous is None
            or (previous["source"] == binding and previous["entry"] == inputs["entry"]),
            "PUBLICATION_PREDECESSOR_IDENTITY",
        )


def _handoff_binding(role: str, request: dict[str, Any]) -> dict[str, Any] | None:
    method, inputs = request["method"], request["input"]
    if (role == "capture" and method in {"assess", "capture", "restore", "verify"}) or (
        role == "secret-source" and method == "publish"
    ):
        return cast(dict[str, Any], inputs["binding"])
    if role == "credential-issuer":
        if method == "issue":
            return cast(dict[str, Any], inputs["issuer"])
        if method in {"renew", "revoke"}:
            return cast(dict[str, Any], inputs["lease"]["issuer"])
    return None


def _attempt_identity(attempt: dict[str, Any]) -> dict[str, Any]:
    mutable = {"state", "certainty", "outcome", "native_task"}
    return {key: value for key, value in attempt.items() if key not in mutable}


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
    if request["method"] == "reconcile":
        require(attempt is not None, "EFFECT_ATTEMPT_REQUIRED")
        assert attempt is not None
        validate("adapter.schema.json", attempt, "EffectAttempt")
        require(
            _attempt_identity(attempt) == _attempt_identity(request["input"]["attempt"]),
            "RECONCILIATION_IDENTITY",
        )
        check_result(attempt, response["effect"])
    elif ROLES[role][request["method"]]["effect"]:
        require(attempt is not None, "EFFECT_ATTEMPT_REQUIRED")
        assert attempt is not None
        require(
            attempt["input_digest"] == response["request_digest"]
            and attempt["scope"] == response["scope"],
            "RESPONSE_EFFECT_IDENTITY",
        )
        check_result(attempt, response["effect"])
    _check_handoff_response(role, request, response)


def _check_handoff_response(role: str, request: dict[str, Any], response: dict[str, Any]) -> None:
    method, inputs, output = request["method"], request["input"], response["output"]
    if role == "capture" and method in {"assess", "capture", "restore", "verify"}:
        for snapshot in output["snapshots"]:
            require(snapshot["dataset"] == inputs["dataset"], "SNAPSHOT_OUTPUT_IDENTITY")
            if method in {"restore", "verify"}:
                require(snapshot == inputs["snapshot"], "SNAPSHOT_OUTPUT_IDENTITY")
            else:
                require(snapshot["producer"] == inputs["binding"], "SNAPSHOT_OUTPUT_IDENTITY")
    if role == "credential-issuer" and method in {"issue", "renew", "revoke"}:
        target = inputs if method == "issue" else inputs["lease"]
        for lease in output["leases"]:
            require(
                all(lease[key] == target[key] for key in ("issuer", "identity", "consumer"))
                and (method == "issue" or lease["lease"] == target["lease"]),
                "LEASE_RESULT_IDENTITY",
            )
    if role == "secret-source" and method == "publish":
        require(
            all(
                version["source"] == inputs["binding"]["instance"]
                and version["entry"] == inputs["entry"]
                and version["version"] == inputs["version"]
                for version in output["versions"]
            ),
            "PUBLICATION_RESULT_IDENTITY",
        )


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
        self._check_mutation_authority(instance)
        self._instances[instance["id"]] = copy.deepcopy(instance)

    def _check_mutation_authority(self, instance: dict[str, Any]) -> None:
        """Scan local enrolled facts; native admission still owns alias resolution/exclusion."""
        claims = {
            digest(namespace): (grant["scope"], grant["mutation"]["controller"])
            for old in self._instances.values()
            if old["id"] != instance["id"]
            for grant in old["grants"]
            if grant["mutation"] is not None
            for namespace in grant["mutation"]["namespaces"]
        }
        for grant in instance["grants"]:
            mutation = grant["mutation"]
            if mutation is None:
                continue
            namespaces = {digest(value) for value in mutation["namespaces"]}
            controller = mutation["controller"]
            if controller != instance["id"]:
                controlling = self._instances.get(controller)
                require(controlling is not None, "UNKNOWN_MUTATION_CONTROLLER")
                assert controlling is not None
                controlling_grant = self._grant(controlling, grant["scope"], "change")
                controlling_mutation = controlling_grant["mutation"]
                require(
                    controlling_mutation["controller"] == controller
                    and namespaces <= {digest(n) for n in controlling_mutation["namespaces"]},
                    "MUTATION_DELEGATION_SCOPE",
                )
            identity = (grant["scope"], controller)
            for namespace in namespaces:
                require(
                    namespace not in claims or claims[namespace] == identity,
                    "NATIVE_MUTATION_AUTHORITY_CONFLICT",
                )
                claims[namespace] = identity

    @staticmethod
    def _grant(instance: dict[str, Any], scope: dict[str, Any], access: str) -> dict[str, Any]:
        grant = next(
            (g for g in instance["grants"] if g["scope"] == scope and access in g["access"]),
            None,
        )
        require(grant is not None, "SCOPE_NOT_GRANTED")
        assert grant is not None
        return cast(dict[str, Any], grant)

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

    def resolve_source(
        self, selection: dict[str, Any], role: str, scope: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve approved source selection without enrollment, reconfiguration or execution."""
        validate("adapter.schema.json", selection, "Selection")
        validate("adapter.schema.json", scope, "Scope")
        require(role in ROLES, "UNKNOWN_CONTRACT")
        instance = self._instances.get(selection["instance"])
        require(instance is not None and instance["role"] == role, "INSTANCE_ROLE")
        assert instance is not None
        self._grant(instance, scope, "observe")
        _, descriptor = self._installations[instance["installation_digest"]]
        check_configuration(
            descriptor["source_schema"], selection["source"], selection["source_schema_digest"]
        )
        return copy.deepcopy(instance)

    def authorize_request(
        self,
        instance_id: str,
        role: str,
        request: dict[str, Any],
        *,
        owner_approved: bool,
    ) -> dict[str, Any]:
        """Check typed input identity against current receiver-owned enrollment before dispatch."""
        check_request(role, request)
        instance = self.authorize(
            instance_id,
            role,
            request["method"],
            request["input"]["scope"],
            owner_approved=owner_approved,
        )
        if request["method"] == "reconcile":
            original = request["input"]["attempt"]
            require(
                original["instance"] == instance["id"]
                and original["installation_digest"] == instance["installation_digest"],
                "RECONCILIATION_INSTANCE_IDENTITY",
            )
        binding = _handoff_binding(role, request)
        if binding is not None:
            require(
                binding
                == {
                    "instance": instance["id"],
                    "role": instance["role"],
                    "installation_digest": instance["installation_digest"],
                    "configuration_digest": digest(instance["configuration"]),
                    "instance_generation": instance["generation"],
                    "instance_digest": digest(instance),
                    "scope": request["input"]["scope"],
                },
                "HANDOFF_INSTANCE_IDENTITY",
            )
        return instance

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
        instance = self._operation(instance_id, role, method, scope)
        if ROLES[role][method]["effect"]:
            require(
                self._grant(instance, scope, "change")["mutation"]["controller"] == instance_id,
                "MUTATION_OWNER_REQUIRED",
            )
        return copy.deepcopy(instance)

    def inspect_delegation(
        self, controller: str, instance_id: str, role: str, method: str, scope: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve enrollment data for an owning coordinator; this does not authorize dispatch."""
        instance = self._operation(instance_id, role, method, scope)
        require(ROLES[role][method]["effect"], "REQUEST_IS_NOT_EFFECT")
        mutation = self._grant(instance, scope, "change")["mutation"]
        require(
            controller != instance_id and mutation["controller"] == controller,
            "MUTATION_DELEGATION_SCOPE",
        )
        return copy.deepcopy(instance)

    def _operation(
        self, instance_id: str, role: str, method: str, scope: dict[str, Any]
    ) -> dict[str, Any]:
        require(role in ROLES and method in ROLES[role], "UNKNOWN_OPERATION")
        instance = self._instances.get(instance_id)
        require(instance is not None and instance["role"] == role, "INSTANCE_ROLE")
        assert instance is not None
        access = "change" if ROLES[role][method]["effect"] else "observe"
        self._grant(instance, scope, access)
        _, descriptor = self._installations[instance["installation_digest"]]
        require(role + "." + method in descriptor["implemented"], "NATIVE_EXECUTOR_UNIMPLEMENTED")
        return instance


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
