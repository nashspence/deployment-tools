from __future__ import annotations

import copy
import json

import pytest
import yaml

from scripts.check_repository import validate_controls
from scripts.project_policy import ROOT, read_toml

CHECK_PURPOSE = {
    "purpose": "repository-policy",
    "authority": ["release.toml#/governance"],
    "scope": "GitHub governance policy",
    "relationship": "no-product-claim",
    "reason": "Synthetic API receipts and workflow checks protect collaborator-only submissions, required gates and publication controls.",
    "claims": [],
}


@pytest.fixture
def controls():
    return json.loads((ROOT / "tests/fixtures/repository-controls.json").read_text())


def test_repository_control_receipt_matches_declared_policy(controls):
    assert validate_controls(read_toml(ROOT / "release.toml"), controls, "full") == []


@pytest.mark.parametrize(
    "change",
    [
        "bypass",
        "missing-check",
        "weakened-codeql",
        "public-prs",
        "mutable-releases",
        "security-paused",
        "missing-reviewer",
        "unexpected-branch",
    ],
)
def test_governance_drift_blocks_verification(controls, change):
    observed = copy.deepcopy(controls)
    ruleset = observed["rulesets"][0]
    rules = {rule["type"]: rule.get("parameters", {}) for rule in ruleset["rules"]}
    if change == "bypass":
        ruleset["bypass_actors"] = [
            {"actor_id": 1, "actor_type": "Integration", "bypass_mode": "always"}
        ]
    elif change == "missing-check":
        rules["required_status_checks"]["required_status_checks"].pop()
    elif change == "weakened-codeql":
        rules["code_scanning"]["code_scanning_tools"][0]["security_alerts_threshold"] = "critical"
    elif change == "public-prs":
        observed["creation"]["pullRequestCreationPolicy"] = "ALL"
    elif change == "mutable-releases":
        observed["immutable"]["enabled"] = False
    elif change == "security-paused":
        observed["fixes"]["paused"] = True
    elif change == "missing-reviewer":
        observed["environment"]["protection_rules"] = []
    elif change == "unexpected-branch":
        observed["environment_branches"]["branch_policies"].append({"name": "*", "type": "branch"})
    assert validate_controls(read_toml(ROOT / "release.toml"), observed, "full")


def test_public_ci_audit_does_not_require_admin_credentials(controls):
    public = {key: controls[key] for key in ("repository", "creation", "rulesets")}
    for key in (
        "security_and_analysis",
        "allow_auto_merge",
        "allow_merge_commit",
        "allow_rebase_merge",
        "allow_squash_merge",
        "delete_branch_on_merge",
    ):
        public["repository"].pop(key)
    assert validate_controls(read_toml(ROOT / "release.toml"), public, "public") == []
    assert validate_controls(read_toml(ROOT / "release.toml"), public, "full")


def test_candidate_workflow_requires_main_and_protected_environment():
    workflow = yaml.load(
        (ROOT / ".github/workflows/release-candidate.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    candidate = workflow["jobs"]["candidate"]
    assert candidate["environment"] == "release-publication"
    assert candidate["if"] == "${{ github.ref == 'refs/heads/main' }}"
    assert candidate["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }
    assert (
        candidate["strategy"]["matrix"]["os"]
        == read_toml(ROOT / "release.toml")["scaffold_install_tests"]
    )
    for step in candidate["steps"]:
        if "uses" in step:
            assert len(step["uses"].split("@")[-1]) == 40
    commands = "\n".join(step.get("run", "") for step in candidate["steps"])
    assert "gh release create" not in commands
    assert "git push" not in commands
    assert "distributions smoke" in commands


def test_dependabot_owns_the_uv_workspace_and_separate_security_groups():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {item["package-ecosystem"] for item in config["updates"]} == {"uv", "github-actions"}
    for update in config["updates"]:
        assert update["directory"] == "/"
        assert {group["applies-to"] for group in update["groups"].values()} == {
            "version-updates",
            "security-updates",
        }
