from __future__ import annotations

import copy

import pytest
from fishhat_adapters import ROLES, Registry, check_request, check_response
from fishhat_contracts.validation import ContractError, digest
from fishhat_owner.adapter_state import AdapterCoordinator

from tests.adapter_support import (
    OWNER,
    binding,
    identifier,
    observation,
    request,
    runtime_registry,
    runtime_sources,
)

CHECK_PURPOSE = {
    "purpose": "public-contract",
    "authority": [
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/adapter.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/capture-adapter.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/secret-source-adapter.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/credential-issuer-adapter.schema.json",
    ],
    "scope": "exact adapter input custody, receiver enrollment joins and reconciliation identities",
    "relationship": "candidate",
    "reason": "Independent old/new input and lost-reply cases; no native execution or qualification.",
    "claims": [],
}


def artifact(letter):
    return {
        "digest": "sha256:" + letter * 64,
        "media_type": "application/octet-stream",
        "size_bytes": 64,
    }


def installed(role, *, observe_only=False):
    """An independent authority-model double, not a supplied native adapter."""
    scope = {"authority": OWNER["id"], "kind": "dataset", "handle": "data"}
    if role in {"secret-source", "credential-issuer"}:
        scope["kind"] = role
    elif role == "runtime":
        scope["kind"] = "application"
    configuration_schema = {
        "type": "object",
        "properties": {"endpoint": {"type": "string"}},
        "required": ["endpoint"],
        "additionalProperties": False,
    }
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    descriptor = {
        "format": "deployment-ops/adapter/v1",
        "id": "independent.example-" + role,
        "roles": [role],
        "entrypoint": "bin/adapter",
        "configuration_schema": configuration_schema,
        "component_schema": empty,
        "source_schema": empty,
        "implemented": [role + "." + method for method in ROLES[role]],
        "native_qualified": False,
    }
    instance = {
        "id": "selected",
        "implementation": descriptor["id"],
        "installation_digest": artifact("a")["digest"],
        "role": role,
        "schema_digest": digest(configuration_schema),
        "configuration": {"endpoint": "protected-target"},
        "grants": [
            {
                "scope": scope,
                "access": ["observe"] if observe_only else ["observe", "change"],
                "mutation": None
                if observe_only
                else {
                    "controller": "selected",
                    "namespaces": [
                        {"authority": OWNER["id"], "resource": "data", "facet": "content"}
                    ],
                },
            }
        ],
        "generation": 3,
    }
    registry = Registry()
    registry.install(
        {
            "implementation": descriptor["id"],
            "artifact": artifact("a"),
            "descriptor_digest": digest(descriptor),
            "enabled": True,
        },
        descriptor,
        owner_approved=True,
    )
    registry.enroll(instance, owner_approved=True)
    reference = {
        "instance": instance["id"],
        "role": role,
        "installation_digest": instance["installation_digest"],
        "configuration_digest": digest(instance["configuration"]),
        "instance_generation": instance["generation"],
        "instance_digest": digest(instance),
        "scope": scope,
    }
    return registry, reference


def handoff(role, method):
    registry, reference = installed(role)
    inputs = {"scope": copy.deepcopy(reference["scope"])}
    if role == "capture":
        inputs.update(
            {
                "binding": reference,
                "dataset": "data",
                "destination": "target:data",
                "procedure_digest": artifact("b")["digest"],
                "snapshot": {
                    "handle": "snapshot:old",
                    "dataset": "data",
                    "format": "independent.snapshot",
                    "consistency": "quiesced",
                    "artifacts": [artifact("c")],
                    "restore_requirements": ["independent.reader"],
                    "producer": copy.deepcopy(reference),
                },
            }
        )
    elif role == "secret-source":
        inputs.update(
            {
                "binding": reference,
                "entry": "database",
                "version": "version:new",
                "consumer": "demo",
                "candidate": artifact("c"),
                "expected_previous": {
                    "source": copy.deepcopy(reference),
                    "entry": "database",
                    "version": "version:old",
                    "artifact_digest": artifact("b")["digest"],
                },
            }
        )
    else:
        inputs["lease"] = {
            "issuer": reference,
            "identity": "database-user",
            "consumer": "demo",
            "lease": "lease:old",
        }
        if method == "renew":
            inputs["lifetime_seconds"] = 3600
    return registry, {"contract": "fishhat." + role, "method": method, "input": inputs}


def attempt_for(query):
    return {
        "owner": OWNER,
        "operation_id": identifier(8),
        "effect_id": identifier(9),
        "attempt_id": identifier(10),
        "instance": "selected",
        "installation_digest": artifact("a")["digest"],
        "binding_generation": 3,
        "native_incarnation": identifier(11),
        "contract": query["contract"],
        "method": query["method"],
        "scope": query["input"]["scope"],
        "input_digest": digest(query),
        "state": "dispatched",
        "certainty": "unknown",
        "outcome": "pending",
        "native_task": None,
    }


def response_for(query, attempt, output):
    return {
        "contract": query["contract"],
        "method": query["method"],
        "request_digest": digest(query),
        "scope": query["input"]["scope"],
        "coverage": "complete",
        "continuation": None,
        "proposal": None,
        "output": output,
        "effect": {
            "attempt_id": attempt["attempt_id"],
            "input_digest": attempt["input_digest"],
            "acknowledgment": "accepted",
            "task_state": "terminal",
            "effect_certainty": "known",
            "postcondition": "satisfied",
            "native_task": None,
        },
    }


def reconciliation():
    original = request()
    attempt = attempt_for(original)
    query = {
        "contract": original["contract"],
        "method": "reconcile",
        "input": {"scope": original["input"]["scope"], "attempt": copy.deepcopy(attempt)},
    }
    response = response_for(
        query, attempt, {"deployment": None, "observation": None, "objects": []}
    )
    return query, attempt, response


def test_lost_activation_reply_is_settled_without_a_second_dispatch():
    registry, _, _ = runtime_registry()
    commits = []
    owner = AdapterCoordinator(OWNER, registry, lambda value: commits.append(copy.deepcopy(value)))
    owner.bind(binding(), **runtime_sources(registry), owner_approved=True)
    owner.prove_quiescent("demo", observation())
    owner.open_admission("demo", owner_approved=True)
    prepared = owner.prepare(
        "demo",
        request(),
        **runtime_sources(registry),
        operation_id=identifier(8),
        effect_id=identifier(9),
        attempt_id=identifier(10),
        owner_approved=True,
    )
    original = owner.dispatch("demo", prepared["attempt_id"])
    query = {
        "contract": original["contract"],
        "method": "reconcile",
        "input": {"scope": original["scope"], "attempt": original},
    }
    response = response_for(
        query, original, {"deployment": None, "observation": None, "objects": []}
    )
    assert response["request_digest"] != response["effect"]["input_digest"]
    check_response("runtime", query, response, attempt=original)
    owner.settle(original["attempt_id"], response["effect"])
    assert owner.snapshot()["attempts"][original["attempt_id"]]["outcome"] == "succeeded"
    count = len(commits)
    with pytest.raises(ContractError, match="RECONCILE_REQUIRED"):
        owner.dispatch("demo", original["attempt_id"])
    assert len(commits) == count


@pytest.mark.parametrize(
    "substitution",
    [
        "rpc-digest",
        "effect-digest",
        "attempt-id",
        "scope",
        "role",
        "read-method",
        "trusted-attempt",
    ],
)
def test_reconciliation_refuses_identity_substitution(substitution):
    query, original, response = reconciliation()
    if substitution == "rpc-digest":
        response["request_digest"] = original["input_digest"]
    elif substitution == "effect-digest":
        response["effect"]["input_digest"] = digest(query)
    elif substitution == "attempt-id":
        response["effect"]["attempt_id"] = identifier(99)
    elif substitution == "trusted-attempt":
        original["installation_digest"] = artifact("f")["digest"]
    else:
        changed = query["input"]["attempt"]
        if substitution == "scope":
            changed["scope"] = {**changed["scope"], "handle": "other"}
        elif substitution == "role":
            changed["contract"] = "fishhat.service"
        else:
            changed["method"] = "observe"
        response["request_digest"] = digest(query)
    with pytest.raises(ContractError):
        check_response("runtime", query, response, attempt=original)


def test_observational_reconciliation_does_not_require_mutation_authority():
    registry, reference = installed("runtime", observe_only=True)
    assert registry.authorize(
        "selected", "runtime", "reconcile", reference["scope"], owner_approved=True
    )
    with pytest.raises(ContractError, match="SCOPE_NOT_GRANTED"):
        registry.authorize(
            "selected", "runtime", "activate", reference["scope"], owner_approved=True
        )


@pytest.mark.parametrize("method", ["restore", "verify"])
def test_capture_handoff_distinguishes_old_and_new_snapshot(method):
    registry, query = handoff("capture", method)
    registry.authorize_request("selected", "capture", query, owner_approved=True)
    original = attempt_for(query)
    selected = query["input"]["snapshot"]
    response = response_for(
        query, original, {"snapshots": [copy.deepcopy(selected)], "verification": "consistent"}
    )
    check_response("capture", query, response, attempt=original)
    response["output"]["snapshots"][0]["artifacts"] = [artifact("d")]
    with pytest.raises(ContractError, match="SNAPSHOT_OUTPUT_IDENTITY"):
        check_response("capture", query, response, attempt=original)
    changed = copy.deepcopy(query)
    changed["input"]["snapshot"]["handle"] = "snapshot:new"
    changed["input"]["snapshot"]["artifacts"] = [artifact("d")]
    check_request("capture", changed)
    assert digest(changed) != digest(query)


def test_cross_owner_restore_keeps_source_identity_separate_from_target_authority():
    registry, query = handoff("capture", "restore")
    query["input"]["snapshot"]["producer"]["scope"]["authority"] = identifier(55)
    registry.authorize_request("selected", "capture", query, owner_approved=True)
    query["input"]["snapshot"]["dataset"] = "different-data"
    with pytest.raises(ContractError, match="SNAPSHOT_INPUT_IDENTITY"):
        check_request("capture", query)


@pytest.mark.parametrize("method", ["renew", "revoke"])
def test_overlapping_credential_lease_is_never_an_implicit_target(method):
    registry, query = handoff("credential-issuer", method)
    registry.authorize_request("selected", "credential-issuer", query, owner_approved=True)
    original = attempt_for(query)
    lease = {
        **copy.deepcopy(query["input"]["lease"]),
        "value_handle": "protected:old",
        "validity": "revoked" if method == "revoke" else "valid",
        "expires_at": None,
        "renewable": method == "renew",
    }
    response = response_for(query, original, {"leases": [lease]})
    check_response("credential-issuer", query, response, attempt=original)
    response["output"]["leases"][0]["lease"] = "lease:new"
    with pytest.raises(ContractError, match="LEASE_RESULT_IDENTITY"):
        check_response("credential-issuer", query, response, attempt=original)
    changed = copy.deepcopy(query)
    changed["input"]["lease"]["lease"] = "lease:new"
    check_request("credential-issuer", changed)
    assert digest(changed) != digest(query)


def test_publication_requires_same_source_and_exact_predecessor():
    registry, query = handoff("secret-source", "publish")
    registry.authorize_request("selected", "secret-source", query, owner_approved=True)
    changed = copy.deepcopy(query)
    changed["input"]["expected_previous"]["artifact_digest"] = artifact("d")["digest"]
    check_request("secret-source", changed)
    assert digest(changed) != digest(query)
    changed["input"]["expected_previous"]["source"]["configuration_digest"] = artifact("e")[
        "digest"
    ]
    with pytest.raises(ContractError, match="PUBLICATION_PREDECESSOR_IDENTITY"):
        check_request("secret-source", changed)


@pytest.mark.parametrize(
    "role,method",
    [("capture", "restore"), ("secret-source", "publish"), ("credential-issuer", "revoke")],
)
def test_handoff_cannot_substitute_current_instance_configuration(role, method):
    registry, query = handoff(role, method)
    reference = (
        query["input"]["lease"]["issuer"]
        if role == "credential-issuer"
        else query["input"]["binding"]
    )
    reference["configuration_digest"] = artifact("e")["digest"]
    if role == "secret-source":
        query["input"]["expected_previous"]["source"] = copy.deepcopy(reference)
    check_request(role, query)
    with pytest.raises(ContractError, match="HANDOFF_INSTANCE_IDENTITY"):
        registry.authorize_request("selected", role, query, owner_approved=True)


@pytest.mark.parametrize(
    "role,method",
    [("capture", "restore"), ("secret-source", "publish"), ("credential-issuer", "revoke")],
)
def test_changed_exact_handoff_input_cannot_reuse_an_accepted_attempt(role, method):
    _, query = handoff(role, method)
    original = attempt_for(query)
    if role == "capture":
        query["input"]["snapshot"]["artifacts"] = [artifact("d")]
        output = {"snapshots": [], "verification": "unknown"}
    elif role == "secret-source":
        query["input"]["candidate"] = artifact("d")
        output = {"versions": []}
    else:
        query["input"]["lease"]["lease"] = "lease:new"
        output = {"leases": []}
    response = response_for(query, original, output)
    with pytest.raises(ContractError, match="RESPONSE_EFFECT_IDENTITY"):
        check_response(role, query, response, attempt=original)
