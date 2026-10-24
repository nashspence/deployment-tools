"""Synthetic resource identity handoffs, without native enforcement claims."""

import json
from copy import deepcopy

import pytest
from fishhat_contracts.resources import (
    ReservationModel,
    check_capacity,
    resource_bindings_digest,
    validate_host_resources,
    validate_snapshot,
)
from fishhat_contracts.validation import ContractError, strict_yaml

from tests.support import fixture_path

CHECK_PURPOSE = {
    "purpose": "implementation",
    "authority": [
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/resources.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/resource-records.schema.json",
    ],
    "scope": "portable resource selection and exact capacity binding joins",
    "relationship": "candidate",
    "reason": "Synthetic provider selections, alias collisions and replaced identities exercise pure reference validators, not native quota enforcement or admission authority.",
    "claims": [],
}

NOW = "2026-10-19T12:00:00Z"


def snapshot():
    return json.loads(fixture_path("examples/resources/capacity-snapshot.json").read_text())


def request():
    return json.loads(fixture_path("examples/resources/capacity-request.json").read_text())


def host():
    return strict_yaml(fixture_path("examples/host-node-a/host.yaml").read_text())


@pytest.mark.parametrize("implementation", ["fixture.project-quota", "fixture.dataset-quota"])
def test_generic_source_accepts_distinct_selected_storage_implementations(implementation):
    value = host()
    value["adapter_instances"]["workload-storage"]["implementation"] = implementation
    validate_host_resources(value)


@pytest.mark.parametrize("field", ["instance", "allocation"])
def test_compute_selection_requires_declared_role_and_scope(field):
    value = host()
    value["resources"]["accounts"]["app-demo-app"]["compute"][field] = "quadlet"
    with pytest.raises(ContractError):
        validate_host_resources(value)


def test_two_buckets_cannot_claim_the_same_opaque_allocation():
    value = host()
    storage = value["resources"]["accounts"]["app-demo-app"]["storage"]
    storage["scratch"]["allocation"] = storage["runtime"]["allocation"]
    with pytest.raises(ContractError, match="duplicate storage allocation"):
        validate_host_resources(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance", "other-storage"),
        ("installation_digest", "sha256:" + "b" * 64),
        ("configuration_digest", "sha256:" + "b" * 64),
        ("instance_generation", 2),
        ("instance_digest", "sha256:" + "b" * 64),
        (
            "scope",
            {
                "authority": "00000000-0000-4000-8000-000000000009",
                "kind": "allocation",
                "handle": "workload-pool",
            },
        ),
    ],
    ids=["instance", "installation", "configuration", "generation", "enrollment", "scope"],
)
def test_quota_cannot_substitute_any_part_of_pool_executor(field, value):
    observed = snapshot()
    observed["quotas"][0]["storage"]["pool"]["executor"][field] = value
    with pytest.raises(ContractError, match="pool binding mismatch"):
        validate_snapshot(observed)


def test_distinct_adapter_names_do_not_prove_independent_pool_capacity():
    observed = snapshot()
    other = deepcopy(observed["pools"][0])
    other["pool"] = "alias-pool"
    other["storage"]["executor"]["instance"] = "other-storage"
    other["storage"]["handle"] = "alias-handle"
    observed["pools"].append(other)
    with pytest.raises(ContractError, match="duplicate pool capacity domain"):
        validate_snapshot(observed)


def test_control_pool_alias_cannot_share_workload_capacity():
    observed = snapshot()
    observed["control_pool"]["capacity_domain"] = observed["pools"][0]["storage"]["capacity_domain"]
    with pytest.raises(ContractError, match="control and workload capacity domain overlap"):
        validate_snapshot(observed)


@pytest.mark.parametrize("change", ["pool", "allocation", "instance", "owner-epoch"])
def test_internally_consistent_replacement_cannot_reuse_approved_capacity_request(change):
    approved = request()
    observed = snapshot()
    if change == "pool":
        observed["pools"][0]["storage"]["incarnation"] = "pool-recreated"
        for quota in observed["quotas"]:
            quota["storage"]["pool"] = deepcopy(observed["pools"][0]["storage"])
    elif change == "allocation":
        observed["quotas"][0]["storage"]["incarnation"] = "allocation-recreated"
    elif change == "instance":
        observed["pools"][0]["storage"]["executor"]["instance_generation"] += 1
        for quota in observed["quotas"]:
            quota["storage"]["pool"] = deepcopy(observed["pools"][0]["storage"])
    else:
        observed["owner"]["epoch"] = "00000000-0000-4000-8000-000000000009"
    validate_snapshot(observed)
    with pytest.raises(ContractError, match="approved resource bindings"):
        check_capacity(
            observed, approved, approved_bindings_digest=approved["bindings_digest"], now=NOW
        )


def test_caller_cannot_recompute_the_expected_owner_approval():
    observed = snapshot()
    proposed = request()
    approved_digest = proposed["bindings_digest"]
    observed["quotas"][0]["storage"]["incarnation"] = "replacement-allocation"
    proposed["bindings_digest"] = resource_bindings_digest(observed)
    with pytest.raises(ContractError, match="approved resource bindings"):
        check_capacity(observed, proposed, approved_bindings_digest=approved_digest, now=NOW)


@pytest.mark.parametrize("field", ["max_bytes", "headroom_bytes", "reserve_bytes", "root"])
def test_accounting_cannot_rewrite_approved_capacity_policy(field):
    observed, proposed = snapshot(), request()
    if field == "reserve_bytes":
        observed["pools"][0][field] = 0
    elif field == "root":
        observed["quotas"][0][field] = "/srv/other/runtime"
    elif field == "headroom_bytes":
        observed["quotas"][0][field] = 0
    else:
        observed["quotas"][0][field] *= 2
    with pytest.raises(ContractError, match="approved resource bindings"):
        check_capacity(
            observed, proposed, approved_bindings_digest=proposed["bindings_digest"], now=NOW
        )


def test_writer_cessation_cannot_release_a_claim_on_a_replacement_allocation():
    proposed = request()
    book = ReservationModel(snapshot(), approved_bindings_digest=proposed["bindings_digest"])
    book.reserve(proposed, now=NOW)
    book.timeout(proposed["operation_id"])
    book.snapshot["quotas"][0]["storage"]["incarnation"] = "replacement-allocation"
    before = deepcopy(book.snapshot)
    with pytest.raises(ContractError, match="approved resource bindings"):
        book.release(proposed["operation_id"], writer_stopped=True)
    assert book.snapshot == before
    assert book.requests[proposed["operation_id"]]["phase"] == "reconciling"


def test_accounting_updates_and_order_do_not_change_approved_binding_identity():
    observed = snapshot()
    expected = request()["bindings_digest"]
    observed["quotas"].reverse()
    observed["quotas"][0]["used_bytes"] += 1
    observed["pools"][0]["available_bytes"] -= 1
    observed["observed_at"] = "2026-10-19T12:00:01Z"
    assert resource_bindings_digest(observed) == expected


def test_resource_bindings_cannot_hide_a_native_provider_field():
    observed = snapshot()
    observed["quotas"][0]["storage"]["project_id"] = 10001
    with pytest.raises(ContractError):
        validate_snapshot(observed)
