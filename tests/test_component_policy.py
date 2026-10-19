from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import tomllib
from pathlib import Path

import pytest
from deployment_tools_contracts import ContractError
from deployment_tools_owner.journal import Journal
from deployment_tools_reporting.store import EventStore

from scripts.project_policy import ROOT, boundary_errors, components, snapshot, state_errors
from tests.support import FIXTURES


def test_components_and_public_contracts_are_current():
    assert not boundary_errors()
    assert snapshot() == json.loads((ROOT / "policy/interfaces.json").read_text())


def test_component_import_violation_is_detected(tmp_path):
    shutil.copytree(
        ROOT / "packages",
        tmp_path / "packages",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "build"),
    )
    shutil.copytree(ROOT / "policy", tmp_path / "policy")
    shutil.copyfile(ROOT / "release.toml", tmp_path / "release.toml")
    module = tmp_path / "packages/client/src/deployment_tools_client/invalid.py"
    module.write_text("import deployment_tools_reporting.store\n")
    assert any("undeclared cross-component import" in error for error in boundary_errors(tmp_path))


def restore_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / (name + ".sqlite")
    with sqlite3.connect(target) as connection:
        connection.executescript((ROOT / "state/fixtures/v1" / (name + ".sql")).read_text())
    return target


def test_preserved_owner_journal_replays_and_retains_epoch(tmp_path):
    path = restore_fixture(tmp_path, "owner")
    owner = "00000000-0000-4000-8000-000000000001"
    epoch = "00000000-0000-4000-8000-000000000002"
    request = "00000000-0000-4000-8000-000000000003"
    subject = json.loads((FIXTURES / "owner-request.json").read_text())["params"]["subject"]
    journal = Journal(path, owner, epoch)
    try:
        receipt = journal.submit(
            request,
            subject,
            principal="another-reader",
            authorized=True,
            expected_epoch=epoch,
            expected_generation=0,
            expires=200,
            now=1000,
        )
        assert receipt["principal"] == "fixture-maintainer"
        assert receipt["state"] == "accepted"
        assert journal.generation == 0
        with pytest.raises(ContractError):
            journal.submit(
                request,
                {"changed": True},
                principal="fixture-maintainer",
                authorized=True,
                expected_epoch=epoch,
                expected_generation=0,
                expires=200,
                now=1000,
            )
        journal.transition(request, "running", "none")
        journal.transition(request, "succeeded", "known")
        assert journal.generation == 1
    finally:
        journal.close()
    reopened = Journal(path, owner, epoch)
    assert reopened.generation == 1
    reopened.close()


def test_unknown_journal_version_refuses_reinitialization(tmp_path):
    path = restore_fixture(tmp_path, "owner")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(ContractError, match="unsupported journal schema"):
        Journal(path, "owner", "epoch", initialize=True)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 1


def test_preserved_reporting_store_retains_immutable_events(tmp_path):
    event = json.loads((FIXTURES / "tracking/event.json").read_text())
    store = EventStore(
        restore_fixture(tmp_path, "reporting"),
        event["domain_id"],
        "00000000-0000-4000-8000-000000000004",
    )
    try:
        assert store.epoch == "00000000-0000-4000-8000-000000000005"
        status, envelope = store.put(event, principal="fixture-reporter", scopes={"append"})
        assert status == 200
        assert envelope["event"] == event
        changed = copy.deepcopy(event)
        changed["reported_at"] = "2026-01-02T00:00:00Z"
        with pytest.raises(ContractError, match="immutable event conflict"):
            store.put(changed, principal="fixture-reporter", scopes={"append"})
    finally:
        store.close()


def test_all_persistence_is_owned_and_baseline_fixtures_match():
    assert not state_errors()
    inventory = tomllib.loads((ROOT / "state/inventory.toml").read_text())
    assert {owner["id"] for owner in inventory["owners"]} == {"owner-journal", "reported-events"}
    assert all(owner["qualification"] == "reference-model" for owner in inventory["owners"])
    assert len(components()) == 8


@pytest.mark.parametrize(
    "relative",
    [
        "state/fixtures/v1/example.sql",
        "packages/example/src/example/migrations/versions/v1_0001.py",
    ],
    ids=["state-fixture", "state-migration"],
)
def test_retired_state_fixtures_and_migrations_remain_immutable(tmp_path, relative):
    import subprocess

    from scripts.project_policy import fixture_history_errors

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init", "--quiet")
    fixture = tmp_path / relative
    fixture.parent.mkdir(parents=True)
    fixture.write_text("PRAGMA user_version=1;\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture Maintainer",
        "-c",
        "user.email=maintainer@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Introduce state fixture",
    )
    assert not fixture_history_errors(tmp_path)
    fixture.write_text("PRAGMA user_version=2;\n")
    assert fixture_history_errors(tmp_path)
    fixture.unlink()
    assert fixture_history_errors(tmp_path)
