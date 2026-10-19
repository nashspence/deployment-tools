from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import sys
from importlib.metadata import entry_points
from unittest.mock import patch

import pytest

from scripts.operation_qualification import REGISTRY, build_map, check, report
from scripts.project_policy import ROOT
from tests.support import FIXTURES

TOOLS = {
    entry.name: entry.load()
    for entry in entry_points(group="console_scripts")
    if entry.name in {"domain-ops", "host-ops", "app-ops"}
}
assert set(TOOLS) == {"domain-ops", "host-ops", "app-ops"}


def invoke(main, arguments):
    with patch.object(sys, "argv", ["tool", *arguments]):
        return main()


def test_info_success_for_every_tool():
    for tool, main in TOOLS.items():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert invoke(main, ["info", "--json"]) == 0
        value = json.loads(output.getvalue())
        assert value["tool"] == tool
        assert value["capabilities"] == ["local-contract-validation"]
        assert value["native_mutations"] is False and value["production_qualified"] is False


def test_info_failure_for_every_tool():
    for main in TOOLS.values():
        with contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as error:
            invoke(main, ["info", "--unsupported-option"])
        assert error.value.code == 2


def test_validate_success_for_every_tool():
    for main in TOOLS.values():
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert invoke(main, ["validate", str(FIXTURES / "domain/domain.yaml"), "--json"]) == 0
        assert json.loads(output.getvalue())["status"] == "valid"


def test_validate_failure_for_every_tool(tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"SYNTHETIC_PRIVATE_SENTINEL":true}')
    original = source.read_bytes()
    for main in TOOLS.values():
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            assert invoke(main, ["validate", str(source), "--json"]) == 2
        assert json.loads(output.getvalue())["error"] == "INPUT_REJECTED"
        assert "SYNTHETIC_PRIVATE_SENTINEL" not in output.getvalue()
        assert str(source) not in output.getvalue()
    assert source.read_bytes() == original


def copy_sources(tmp_path):
    for name in ("packages", "policy", "tests"):
        shutil.copytree(
            ROOT / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build"),
        )
    shutil.copyfile(ROOT / "release.toml", tmp_path / "release.toml")
    return tmp_path


def test_map_exhaustively_tracks_current_surfaces_and_native_gaps():
    coverage = check()
    rows = {row["id"]: row for row in coverage["operations"]}
    assert len([key for key in rows if key.startswith("action:")]) == 34
    assert len([key for key in rows if key.startswith("rpc:")]) == 19
    assert all(
        row["status"] == "unimplemented" and row["implementation"] is None
        for key, row in rows.items()
        if key.startswith(("action:", "rpc:"))
    )
    assert all(
        row["implementation"]["stage"] == "scaffold"
        for row in rows.values()
        if row["implementation"]
    )


def test_new_catalogue_operation_requires_map_review(tmp_path):
    root = copy_sources(tmp_path)
    path = root / "packages/contracts/src/deployment_tools_contracts/contracts/actions.json"
    catalogue = json.loads(path.read_text())
    item = copy.deepcopy(catalogue["actions"][0])
    item["name"] = "app.future"
    catalogue["actions"].append(item)
    path.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError, match="coverage changed"):
        check(root)


def test_missing_test_binding_and_incomplete_recovery_fail(tmp_path):
    root = copy_sources(tmp_path)
    registry = root / REGISTRY
    original = registry.read_text()
    registry.write_text(original.replace("read_only = true", "read_only = false", 1))
    with pytest.raises(ValueError, match="proof requirements"):
        build_map(root)
    registry.write_text(original.replace("::test_info_success_for_every_tool", "::test_missing", 1))
    with pytest.raises(ValueError, match="definition"):
        build_map(root)


def test_unknown_implementation_binding_fails(tmp_path):
    root = copy_sources(tmp_path)
    registry = root / REGISTRY
    registry.write_text(
        registry.read_text().replace("cli:domain-ops:info", "cli:domain-ops:missing", 1)
    )
    with pytest.raises(ValueError, match="unknown operation"):
        build_map(root)


def test_report_requires_current_complete_executed_proofs(monkeypatch, tmp_path):
    import scripts.operation_qualification as operations
    from scripts.validation import CANONICAL, FORMAT, digest

    coverage = build_map()
    source = {"commit": "a" * 40, "clean": True, "fingerprint": "b" * 64}
    monkeypatch.setattr(operations, "check", lambda root: coverage)
    monkeypatch.setattr(operations, "source_identity", lambda root: source)
    folder = tmp_path / "artifacts/validation"
    folder.mkdir(parents=True)
    nodes = {
        node
        for row in coverage["operations"]
        for values in row["tests"].values()
        for node in values
    }
    receipt = {
        "format": FORMAT,
        "id": "c" * 32,
        "target": "unit",
        "state": "completed",
        "exit_status": 0,
        "source_before": source,
        "source_after": source,
        "selection": {"canonical": True, "invocation_sha256": digest(CANONICAL["unit"])},
        "tests": {"exit_status": 0, "outcomes": dict.fromkeys(nodes, "passed")},
        "full_check_passed": True,
        "production_qualified": False,
    }
    path = folder / (receipt["id"] + ".json")
    path.write_text(json.dumps(receipt))
    result = report(tmp_path)
    assert sum(row["required_proofs_passed"] for row in result["operations"]) == 7
    assert all(not row["native_qualified"] for row in result["operations"])
    receipt["selection"]["canonical"] = False
    path.write_text(json.dumps(receipt))
    assert not any(row["required_proofs_passed"] for row in report(tmp_path)["operations"])
    receipt["selection"]["canonical"] = True
    receipt["source_after"] = {**source, "commit": "d" * 40}
    path.write_text(json.dumps(receipt))
    assert not any(row["required_proofs_passed"] for row in report(tmp_path)["operations"])
    receipt["source_after"] = source
    receipt["tests"]["outcomes"] = dict.fromkeys(nodes, "skipped")
    path.write_text(json.dumps(receipt))
    assert not any(row["required_proofs_passed"] for row in report(tmp_path)["operations"])
