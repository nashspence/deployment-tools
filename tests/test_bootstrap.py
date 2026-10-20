import copy
import json
import unittest
from pathlib import Path

from deployment_tools_client.bootstrap import (
    prepare_invocation,
    validate_catalogue,
    validate_selection,
)
from deployment_tools_contracts.validation import ContractError

from tests.support import fixture_path

CHECK_PURPOSE = {
    "purpose": "implementation",
    "authority": [
        "packages/deployment-tools-contracts/src/deployment_tools_contracts/contracts/bootstrap-recovery.schema.json"
    ],
    "scope": "offline bootstrap input/reference helpers",
    "relationship": "candidate",
    "reason": "Schema and fixed synthetic recovery inputs exercise selection/rejection; no backend restore or independently installed bootstrap is executed.",
    "claims": [],
}


R = Path(__file__).resolve().parents[1]


class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.s = json.loads(fixture_path("examples/bootstrap/selection.json").read_text())
        self.c = json.loads(fixture_path("examples/bootstrap/catalogue.json").read_text())

    def invoke(self, **kw):
        args = {
            "script": "/opt/recovery/restore-forgejo-main.sh",
            "tool_bin": "/opt/recovery/bin",
            "restic_repository": "s3:https://backup.example.invalid/private",
            "password_file": "/run/recovery/password",
        }
        args.update(kw)
        return prepare_invocation(self.s, **args)

    def test_selection(self):
        validate_selection(self.s)
        validate_catalogue(self.c, selection=self.s)

    def test_no_latest(self):
        self.s["snapshot_id"] = "latest"
        with self.assertRaises(ContractError):
            validate_selection(self.s)

    def test_no_wildcard_path(self):
        self.s["bare_repository_path"] = "/repositories/*/domain.git"
        with self.assertRaises(ContractError):
            validate_selection(self.s)

    def test_no_ambiguous_root(self):
        self.s["recovery_root"] = "/tmp"
        with self.assertRaises(ContractError):
            validate_selection(self.s)

    def test_flags_before_profile(self):
        x = self.invoke()
        self.assertEqual(x["env"]["INSTALL_DEPS"], "0")
        self.assertEqual(x["env"]["DOWNLOAD_BOOTSTRAP_TOOLS"], "0")
        self.assertEqual(x["env"]["VERIFY_ONLY"], "1")
        self.assertNotIn("--profile", x["argv"])

    def test_no_inherited_pythonpath(self):
        with self.assertRaises(ContractError):
            self.invoke(backend_env={"PYTHONPATH": "/malicious"})

    def test_no_password_command(self):
        with self.assertRaises(ContractError):
            self.invoke(backend_env={"RESTIC_PASSWORD_COMMAND": "sh -c bad"})

    def test_backend_url_not_credential(self):
        with self.assertRaises(ContractError):
            self.invoke(restic_repository="s3:https://key:secret@backup.example.invalid/bucket")

    def test_backend_uri_no_query(self):
        with self.assertRaises(ContractError):
            self.invoke(restic_repository="s3:https://backup.example.invalid/bucket?token=secret")

    def test_local_copy(self):
        self.invoke(restic_repository="/media/recovery/restic")

    def test_no_arbitrary_backend(self):
        with self.assertRaises(ContractError):
            self.invoke(restic_repository="rclone:arbitrary")

    def test_catalogue_commit_mismatch(self):
        self.s["expected_commit"] = "a" * 40
        with self.assertRaises(ContractError):
            validate_catalogue(self.c, selection=self.s)

    def test_catalogue_snapshot_mismatch(self):
        self.s["snapshot_id"] = "a" * 64
        with self.assertRaises(ContractError):
            validate_catalogue(self.c, selection=self.s)

    def test_no_export_duplicate(self):
        self.c["sources"].append(copy.deepcopy(self.c["sources"][0]))
        with self.assertRaises(ContractError):
            validate_catalogue(self.c)

    def test_environment_has_only_private_input_not_command(self):
        x = self.invoke()
        self.assertEqual(len(x["argv"]), 2)
        self.assertNotIn("RESTIC_PASSWORD", x["env"])
        self.assertEqual(x["env"]["GIT_TERMINAL_PROMPT"], "0")
