from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from scripts import validation
from scripts.project_policy import ROOT


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    for key in validation.OVERRIDES:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".gitignore").write_text("artifacts/\n__pycache__/\n.pytest_cache/\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/__init__.py").touch()
    shutil.copyfile(
        ROOT / "scripts/validation_pytest.py", tmp_path / "scripts/validation_pytest.py"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_example.py").write_text("def test_pass():\n    assert True\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture Maintainer",
            "-c",
            "user.email=maintainer@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Synthetic fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def latest(root):
    path = max((root / "artifacts/validation").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    return json.loads(path.read_text())


def test_complete_unit_run_records_actual_nodes_and_source(checkout):
    assert validation.run("unit", validation.CANONICAL["unit"], checkout) == 0
    value = latest(checkout)
    assert value["full_check_passed"] is True
    assert value["source_before"] == value["source_after"]
    assert value["tests"]["outcomes"] == {"tests/test_example.py::test_pass": "passed"}
    assert value["duration_seconds"] > 0


def test_filtered_success_does_not_become_full_qualification(checkout):
    command = [*validation.CANONICAL["unit"], "-k", "pass"]
    assert validation.run("unit", command, checkout) == 0
    assert latest(checkout)["full_check_passed"] is False
    assert latest(checkout)["tests"]["outcomes"]


def test_failed_child_status_and_no_sensitive_values_are_preserved(checkout, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "SYNTHETIC_PRIVATE_SENTINEL")
    command = ["python", "-c", "import sys; sys.exit(17)", "SYNTHETIC_PRIVATE_ARGUMENT"]
    assert validation.run("ruff", command, checkout) == 17
    value = latest(checkout)
    assert value["exit_status"] == 17 and not value["full_check_passed"]
    assert "SYNTHETIC_PRIVATE" not in json.dumps(value)
    assert value["selection"]["environment_overrides"] == ["PYTEST_ADDOPTS"]


def test_changed_or_dirty_sources_cannot_qualify(checkout):
    before = validation.source_identity(checkout)
    (checkout / "tests/test_example.py").write_text("def test_pass():\n    assert 1 == 1\n")
    after = validation.source_identity(checkout)
    assert before["fingerprint"] != after["fingerprint"] and not after["clean"]
    assert validation.run("unit", validation.CANONICAL["unit"], checkout) == 0
    assert not latest(checkout)["full_check_passed"]


def test_receipts_capture_source_changes_during_execution(checkout):
    command = [
        "python",
        "-c",
        'from pathlib import Path; Path("new-source.py").write_text("changed")',
    ]
    assert validation.run("ruff", command, checkout) == 0
    value = latest(checkout)
    assert value["source_before"] != value["source_after"]
    assert not value["full_check_passed"]


def test_teardown_failure_cannot_leave_a_passing_test_proof(checkout):
    (checkout / "tests/test_example.py").write_text(
        """import pytest
@pytest.fixture
def broken():
    yield
    raise ValueError("synthetic failure")
def test_pass(broken):
    assert True
"""
    )
    assert validation.run("unit", validation.CANONICAL["unit"], checkout) == 1
    assert latest(checkout)["tests"]["outcomes"]["tests/test_example.py::test_pass"] == "failed"
    assert not latest(checkout)["full_check_passed"]


def test_receipt_retention_preserves_unrelated_files(checkout, monkeypatch):
    monkeypatch.setattr(validation, "MAX_RECEIPTS", 2)
    folder = checkout / "artifacts/validation"
    folder.mkdir(parents=True)
    unrelated = folder / "keep.json"
    unrelated.write_text('{"preserved":true}')
    for _ in range(4):
        assert validation.run("ruff", ["python", "-c", "pass"], checkout) == 0
    assert len(list(folder.glob("*.json"))) == 3
    assert unrelated.read_text() == '{"preserved":true}'


def test_reporting_options_keep_the_default_selection(monkeypatch):
    for key in validation.OVERRIDES:
        monkeypatch.delenv(key, raising=False)
    assert validation.selection(
        "unit", [*validation.CANONICAL["unit"], "--junitxml=artifacts/unit-tests.xml"]
    )["canonical"]
    assert not validation.selection("unit", [*validation.CANONICAL["unit"], "--collect-only"])[
        "canonical"
    ]
