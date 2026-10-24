"""Declared source choices and observation authority, without native secret or hook execution."""

from __future__ import annotations

import copy
import json

import pytest
from fishhat_adapters import Registry, check_configuration
from fishhat_contracts.validation import (
    REGISTRY,
    SCHEMAS,
    ContractError,
    digest,
    strict_yaml,
    validate,
    validate_app,
)
from jsonschema import Draft202012Validator

from tests.adapter_support import OWNER, D, descriptors
from tests.support import FIXTURES

CHECK_PURPOSE = {
    "purpose": "public-contract",
    "authority": [
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/source.schema.json",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/common.schema.json#/$defs/Time",
        "packages/fishhat-contracts/src/fishhat_contracts/contracts/adapter.schema.json#/$defs/Selection",
    ],
    "scope": "source extent, maintenance schedule policy, observation roles, provider selection and timestamp syntax",
    "relationship": "candidate",
    "reason": "Synthetic adverse selections and timestamp inputs exercise declared validation boundaries; no provider retrieval, secret publication, native hook or installed-owner authorization is executed.",
    "claims": [],
}


def application():
    root = FIXTURES / "demo-app"
    return (
        strict_yaml((root / "app.yaml").read_text()),
        strict_yaml((root / "secrets.yaml").read_text()),
    )


@pytest.mark.parametrize("hook", ["restore", "capture", "verify-restore"])
def test_interface_readiness_cannot_substitute_a_recovery_procedure(hook):
    app, catalogue = application()
    app["interfaces"]["http"]["readiness_hook"] = hook
    with pytest.raises(ContractError, match="read-only health hook"):
        validate_app(app, catalogue)


@pytest.mark.parametrize(
    "field,role",
    [
        ("health", "health"),
        ("verify_config", "verify-config"),
        ("transition_assess", "transition-assess"),
        ("transition_verify", "transition-verify"),
    ],
)
def test_lifecycle_observation_cannot_request_write_access(field, role):
    app, catalogue = application()
    app["hooks"][role] = {**copy.deepcopy(app["hooks"]["health"]), "role": role}
    app["lifecycle"][field] = [role] if field == "health" else role
    validate_app(app, catalogue)
    app["hooks"][role]["mode"] = "write"
    with pytest.raises(ContractError, match=r"schema source\.schema\.json"):
        validate_app(app, catalogue)


@pytest.mark.parametrize(
    "field,role",
    [("configure", "configure"), ("quiesce", "quiesce"), ("transition_apply", "transition-apply")],
)
def test_lifecycle_changes_cannot_claim_observation_access(field, role):
    app, catalogue = application()
    app["hooks"][role] = {
        **copy.deepcopy(app["hooks"]["health"]),
        "role": role,
        "mode": "write",
    }
    app["lifecycle"][field] = role
    validate_app(app, catalogue)
    app["hooks"][role]["mode"] = "read"
    with pytest.raises(ContractError, match=r"schema source\.schema\.json"):
        validate_app(app, catalogue)


def test_readiness_uses_its_explicit_executor_without_expanding_secret_grants():
    app, catalogue = application()
    app["hooks"]["health"]["component"] = "maintenance"
    validate_app(app, catalogue)
    app["hooks"]["health"]["secrets"] = ["database-password"]
    with pytest.raises(ContractError, match="borrows another components secret"):
        validate_app(app, catalogue)


def test_secret_catalogue_has_no_implicit_provider_or_legacy_locator():
    _, catalogue = application()
    validate("source.schema.json", catalogue, "RepoSecrets")
    del catalogue["source"]
    with pytest.raises(ContractError):
        validate("source.schema.json", catalogue, "RepoSecrets")
    catalogue["locator"] = {
        "repository": catalogue.pop("repository"),
        "tag": "secrets-bundle",
        "asset": "secrets.tar.age",
    }
    with pytest.raises(ContractError):
        validate("source.schema.json", catalogue, "RepoSecrets")


@pytest.mark.parametrize("field", ["configuration", "credential_handle", "owner_approved"])
def test_catalogue_selection_cannot_reconfigure_or_approve_its_provider(field):
    _, catalogue = application()
    catalogue["source"][field] = {} if field == "configuration" else "unapproved"
    with pytest.raises(ContractError):
        validate("source.schema.json", catalogue, "RepoSecrets")


def secret_registry(provider, *, source_schema=None):
    descriptor = json.loads(next(p for p in descriptors() if provider in p.parts).read_text())
    if source_schema is not None:
        descriptor["source_schema"] = source_schema
    configuration = (
        {
            "repository_handle": "approved-repository",
            "asset": "secrets.tar.age",
            "identity_handle": "approved-identity",
        }
        if provider == "forgejo-age"
        else {
            "endpoint_handle": "approved-endpoint",
            "mount": "approved-mount",
            "credential_handle": "approved-credential",
        }
    )
    scope = {"authority": OWNER["id"], "kind": "secret-source", "handle": "repository-secrets"}
    instance = {
        "id": "demo-secrets",
        "implementation": descriptor["id"],
        "installation_digest": D,
        "role": "secret-source",
        "schema_digest": digest(descriptor["configuration_schema"]),
        "configuration": configuration,
        "grants": [{"scope": scope, "access": ["observe"], "mutation": None}],
        "generation": 1,
    }
    registry = Registry()
    registry.install(
        {
            "implementation": descriptor["id"],
            "artifact": {"digest": D, "media_type": "application/zip", "size_bytes": 100},
            "descriptor_digest": digest(descriptor),
            "enabled": True,
        },
        descriptor,
        owner_approved=True,
    )
    registry.enroll(instance, owner_approved=True)
    return registry, instance, scope


@pytest.mark.parametrize("provider", ["forgejo-age", "openbao-kv"])
def test_catalogue_selects_either_enrolled_secret_provider_without_storage_assumptions(provider):
    app, catalogue = application()
    registry, instance, scope = secret_registry(provider)
    validate_app(app, catalogue)
    resolved = registry.resolve_source(catalogue["source"], "secret-source", scope)
    assert resolved == instance
    assert catalogue["repository"]["name"] == "app/demo-app"
    assert "locator" not in catalogue
    resolved["configuration"].clear()
    assert registry.inspect_instance(instance["id"])["configuration"] == instance["configuration"]
    with pytest.raises(ContractError, match="SCOPE_NOT_GRANTED"):
        registry.resolve_source(
            catalogue["source"], "secret-source", {**scope, "handle": "another"}
        )
    with pytest.raises(ContractError):
        registry.resolve_source(catalogue["source"], "runtime", scope)


@pytest.mark.parametrize("change", ["instance", "schema", "source"])
def test_catalogue_cannot_substitute_unenrolled_or_unreviewed_provider_inputs(change):
    _, catalogue = application()
    registry, _, scope = secret_registry("openbao-kv")
    selection = catalogue["source"]
    if change == "instance":
        selection["instance"] = "unapproved"
    elif change == "schema":
        selection["source_schema_digest"] = D
    else:
        selection["source"]["endpoint_handle"] = "unapproved-endpoint"
    with pytest.raises(ContractError):
        registry.resolve_source(selection, "secret-source", scope)


def test_provider_owns_locator_grammar_under_its_pinned_source_schema():
    _, catalogue = application()
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "enum": ["reviewed/application"]}},
        "required": ["path"],
        "additionalProperties": False,
    }
    registry, instance, scope = secret_registry("openbao-kv", source_schema=schema)
    catalogue["source"].update(
        source_schema_digest=digest(schema), source={"path": "reviewed/application"}
    )
    validate("source.schema.json", catalogue, "RepoSecrets")
    assert registry.resolve_source(catalogue["source"], "secret-source", scope) == instance
    catalogue["source"]["source"]["path"] = "another/application"
    with pytest.raises(ContractError, match="CONFIGURATION_REJECTED"):
        registry.resolve_source(catalogue["source"], "secret-source", scope)


@pytest.mark.parametrize(
    "value,provider_valid",
    [
        ("not-a-timeZ", False),
        ("2026-02-29T00:00:00Z", False),
        ("2026-04-31T00:00:00Z", False),
        ("2026-10-24T24:00:00Z", False),
        ("2026-10-24T00:60:00Z", False),
        ("2026-10-24T00:00:60Z", False),
        ("0000-01-01T00:00:00Z", True),
        ("2026-10-24 00:00:00Z", False),
        ("2026-10-24t00:00:00z", True),
        ("2026-10-24T00:00:00+00:00", True),
        ("2026-10-24T00:00:00.1234567Z", True),
        ("2026-10-24T00:00:00Z\n", False),
    ],
    ids=[
        "invalid",
        "nonleap-day",
        "month-length",
        "hour",
        "minute",
        "leap-second",
        "year-zero",
        "space",
        "lowercase",
        "offset",
        "excess-precision",
        "trailing-newline",
    ],
)
def test_date_time_validation_is_strict_for_shared_and_provider_schemas(value, provider_valid):
    with pytest.raises(ContractError):
        validate("common.schema.json", value, "Time")
    schema = {
        "type": "object",
        "properties": {"expires_at": {"type": "string", "format": "date-time"}},
        "required": ["expires_at"],
        "additionalProperties": False,
    }
    supplied = {"expires_at": value}
    if provider_valid:
        check_configuration(schema, supplied, digest(schema))
        assert supplied == {"expires_at": value}
    else:
        with pytest.raises(ContractError, match="CONFIGURATION_REJECTED"):
            check_configuration(schema, supplied, digest(schema))


@pytest.mark.parametrize(
    "value",
    ["2026-10-24T00:00:00Z", "2024-02-29T23:59:59.1Z", "2024-02-29T23:59:59.123456Z"],
    ids=["seconds", "leap-day-fraction", "microseconds"],
)
def test_utc_seconds_and_microseconds_are_valid_for_every_schema_owner(value):
    validate("common.schema.json", value, "Time")
    schema = {"type": "string", "format": "date-time"}
    check_configuration(schema, value, digest(schema))


@pytest.mark.parametrize(
    "value,valid",
    [
        ("1998-12-31T23:59:60Z", True),
        ("1999-01-01T00:59:60+01:00", True),
        ("1998-12-31T15:59:60.123456789012345-08:00", True),
        ("0000-02-29T00:00:00Z", True),
        ("0400-02-29T00:00:00Z", True),
        ("2026-10-24T00:00:00+23:59", True),
        ("2026-10-24T00:00:00-00:00", True),
        ("2026-04-30T23:59:60Z", True),
        ("1998-12-31T23:58:60Z", False),
        ("1998-12-30T23:59:60Z", False),
        ("1998-12-31T15:59:60+08:00", False),
        ("1900-02-29T00:00:00Z", False),
        ("2026-10-24T00:00:00+24:00", False),
        ("2026-10-24T00:00:00+00:60", False),
    ],
    ids=[
        "utc-leap-second",
        "positive-offset-leap-second",
        "negative-offset-leap-second-with-precision",
        "year-zero-leap-day",
        "four-century-leap-day",
        "largest-offset",
        "unknown-local-offset",
        "leap-position-without-iers-schedule-claim",
        "wrong-leap-minute",
        "wrong-leap-day",
        "wrong-offset-leap-hour",
        "century-is-not-leap-year",
        "offset-hour",
        "offset-minute",
    ],
)
def test_provider_time_checks_calendar_and_offset_without_rewriting(value, valid):
    schema = {
        "type": "object",
        "properties": {"at": {"type": "string", "format": "date-time"}},
        "required": ["at"],
        "additionalProperties": False,
    }
    supplied = {"at": value}
    if valid:
        check_configuration(schema, supplied, digest(schema))
        assert supplied == {"at": value}
    else:
        with pytest.raises(ContractError, match="CONFIGURATION_REJECTED"):
            check_configuration(schema, supplied, digest(schema))


@pytest.mark.parametrize(
    "schema,pointer",
    [
        ("records.schema.json", "/$defs/Witness/properties/created_at"),
        ("resource-records.schema.json", "/$defs/CapacitySnapshot/properties/observed_at"),
        ("publisher-receipt.schema.json", "/properties/verified_at"),
    ],
    ids=["record", "resource", "publisher"],
)
def test_core_time_profile_is_expressed_without_a_custom_format_checker(schema, pointer):
    validator = Draft202012Validator(
        {"$ref": SCHEMAS[schema]["$id"] + "#" + pointer}, registry=REGISTRY
    )
    assert validator.is_valid("2026-10-24T00:00:00Z")
    assert validator.is_valid("2026-10-24T00:00:00.123456Z")
    for value in (
        "0000-01-01T00:00:00Z",
        "1998-12-31T23:59:60Z",
        "2026-10-24T24:00:00Z",
        "2026-10-24T00:00:00+00:00",
        "2026-10-24t00:00:00z",
        "2026-10-24T00:00:00.1234567Z",
        "2026-10-24T00:00:00Z\n",
    ):
        assert not validator.is_valid(value)


def test_source_extent_is_independent_of_wire_frames_and_explicit_capacity():
    from fishhat_contracts.validation import MAX_BYTES, validate_domain

    text = "value: " + "x" * (MAX_BYTES + 1)
    assert len(strict_yaml(text)["value"]) == MAX_BYTES + 1
    assert strict_yaml(text, capacity_bytes=len(text.encode())) == strict_yaml(text)
    with pytest.raises(ContractError, match="source capacity exceeded"):
        strict_yaml(text, capacity_bytes=len(text.encode()) - 1)
    domain = strict_yaml((FIXTURES / "domain/domain.yaml").read_text())
    host = domain["hosts"]["node-a"]
    source = domain["sources"][host["source"]]
    for i in range(300):
        name = f"source-{i}"
        domain["sources"][name] = {**copy.deepcopy(source), "entrypoint": f"hosts/node-{i}.yaml"}
        domain["hosts"][f"node-{i}"] = {**copy.deepcopy(host), "source": name}
    validate_domain(domain)
    domain["hosts"]["node-299"]["source"] = "missing-source"
    with pytest.raises(ContractError):
        validate_domain(domain)


@pytest.mark.parametrize("field", ["calendar_language", "dst_policy", "catch_up"])
@pytest.mark.parametrize("replacement", [None, "unsupported"])
def test_maintenance_requires_explicit_calendar_dst_and_catch_up_policy(field, replacement):
    app, catalogue = application()
    validate_app(app, catalogue)
    task = next(iter(app["maintenance"].values()))
    if replacement is None:
        del task[field]
    else:
        task[field] = replacement
    with pytest.raises(ContractError):
        validate_app(app, catalogue)
