from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from deployment_tools.models.client import (
    check_closeout,
    clean_path,
    merge_exports,
    select_suite,
    validate_bundle,
    validate_export,
    validate_recovery_set,
    validate_run,
    validate_session,
    validate_suite,
)
from deployment_tools.models.contracts import ContractError, strict_yaml, validate
from deployment_tools.models.owner_cli import FORMS, parse_command
from tests.support import fixture_path


def load(p):
    x = fixture_path(p)
    return strict_yaml(x.read_text()) if x.suffix == ".yaml" else json.loads(x.read_text())


class Portable(unittest.TestCase):
    def setUp(self):
        self.session = load("examples/session/session.json")
        self.bundle = load("examples/session/install-bundle.json")
        self.closeout = load("examples/session/closeout.json")
        self.receipts = load("examples/session/receipt-export.json")
        self.suite = load("examples/domain/domain-tests.yaml")

    def test_valid_session(self):
        validate_session(self.session)

    def test_optional_evidence_not_disposable(self):
        self.session["evidence_dir"] = self.session["root"] + "/state"
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_workspace_not_inside_optional_evidence(self):
        self.session["evidence_dir"] = "/home/example"
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_wrong_domain_path(self):
        self.session["domain_dir"] = "/elsewhere/domain"
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_no_relative_paths(self):
        self.session["root"] = "relative"
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_noncanonical_paths(self):
        for p in ["/work/../state", "/work//x", "/work/./x", "/work/", "/", "/work\x00bad"]:
            with self.subTest(p=p), self.assertRaises(ContractError):
                clean_path(p)

    def test_two_envs(self):
        self.session["test_env"] = self.session["tool_env"]
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_no_env_in_domain(self):
        self.session["tool_env"] = self.session["domain_dir"] + "/.venv"
        with self.assertRaises(ContractError):
            validate_session(self.session)

    def test_candidate_not_production_qualified(self):
        validate_bundle(self.bundle)
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle, require_qualified=True)

    def test_wrong_platform(self):
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle, platform="macos-arm64")

    def test_wrong_abi(self):
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle, python="cpython-9.9")

    def test_duplicate_artifact(self):
        self.bundle["wheels"].append(copy.deepcopy(self.bundle["wheels"][0]))
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle)

    def test_no_sdist_runtime(self):
        self.bundle["wheels"][0]["path"] = "wheels/example.tar.gz"
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle)

    def test_bundle_path_escape(self):
        self.bundle["wheels"][0]["path"] = "wheels/../../bad.whl"
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle)

    def test_qualified_requires_evidence(self):
        self.bundle["qualification"] = "qualified"
        self.bundle["evidence"] = []
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle)

    def test_no_credentials_in_bundle(self):
        self.bundle["token"] = "test-sentinel"
        with self.assertRaises(ContractError):
            validate_bundle(self.bundle)

    def test_close_never_deletes(self):
        x = check_closeout(self.closeout)
        self.assertTrue(x["safe_to_close"])
        self.assertFalse(x["deletes_files"])

    def test_close_blocks_work(self):
        self.closeout["active_jobs"] = 1
        self.assertFalse(check_closeout(self.closeout)["safe_to_close"])

    def test_close_blocks_foreign_tree(self):
        self.closeout["foreign_worktrees"] = 1
        self.assertFalse(check_closeout(self.closeout)["safe_to_close"])

    def test_close_needs_durable_evidence(self):
        self.closeout["evidence_outside_workspace"] = False
        self.assertFalse(check_closeout(self.closeout)["safe_to_close"])

    def test_close_unresolved_source(self):
        self.closeout["obligations"][0]["disposition"] = "unresolved"
        self.assertFalse(check_closeout(self.closeout)["safe_to_close"])

    def test_close_missing_source_receipt(self):
        self.closeout["obligations"][0]["evidence_digest"] = None
        self.assertFalse(check_closeout(self.closeout)["safe_to_close"])

    def test_close_pending_identity_without_export_is_warning(self):
        self.closeout["receipt_export_digest"] = None
        result = check_closeout(self.closeout)
        self.assertTrue(result["safe_to_close"])
        self.assertIn("tracking-not-retained", result["warnings"])

    def test_close_digest_changes_when_tree_changes(self):
        a = check_closeout(self.closeout)["plan_digest"]
        self.closeout["tree_digest"] = "sha256:" + "9" * 64
        self.assertNotEqual(a, check_closeout(self.closeout)["plan_digest"])

    def test_close_no_recursive_delete_option(self):
        self.closeout["would_delete"] = True
        with self.assertRaises(ContractError):
            check_closeout(self.closeout)

    def test_merge_deduplicates(self):
        self.assertEqual(len(merge_exports(self.receipts, self.receipts)["deliveries"]), 1)

    def test_merge_conflict(self):
        b = copy.deepcopy(self.receipts)
        b["deliveries"][0]["plan_digest"] = "sha256:" + "9" * 64
        with self.assertRaises(ContractError):
            merge_exports(self.receipts, b)

    def test_merge_domain_conflict(self):
        b = copy.deepcopy(self.receipts)
        b["domain_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        with self.assertRaises(ContractError):
            merge_exports(self.receipts, b)

    def test_merge_cannot_assert_new_state(self):
        b = copy.deepcopy(self.receipts)
        b["deliveries"][0]["state"] = "succeeded"
        self.assertEqual(
            merge_exports(self.receipts, b)["deliveries"][0]["state"],
            self.receipts["deliveries"][0]["state"],
        )

    def test_epoch_is_distinct_identity(self):
        b = copy.deepcopy(self.receipts)
        b["deliveries"][0]["owner"]["epoch"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertEqual(len(merge_exports(self.receipts, b)["deliveries"]), 2)

    def test_exports_no_keys(self):
        self.receipts["credential_material"] = True
        with self.assertRaises(ContractError):
            validate_export(self.receipts)

    def test_default_offline(self):
        self.assertEqual(select_suite(self.suite)["class"], "offline")

    def test_mutation_all_optins(self):
        for kw in [
            {},
            {"targets": [1]},
            {"targets": [1], "allow_mutations": True},
            {"allow_mutations": True, "confirm": True},
        ]:
            with self.subTest(kw=kw), self.assertRaises(ContractError):
                select_suite(self.suite, "mutate", **kw)

    def test_explicit_mutation(self):
        self.assertEqual(
            select_suite(
                self.suite,
                "mutate",
                targets=[{"kind": "app", "id": "demo-app"}],
                allow_mutations=True,
                confirm=True,
            )["class"],
            "mutate",
        )

    def test_missing_suite(self):
        with self.assertRaises(ContractError):
            select_suite(self.suite, "invented")

    def test_bad_test_interpreter(self):
        self.suite["suites"]["offline"]["argv"][0] = "/bin/sh"
        with self.assertRaises(ContractError):
            validate_suite(self.suite)

    def test_incomplete_pass(self):
        x = load("examples/session/test-run.json")
        x["skipped_required"] = 1
        with self.assertRaises(ContractError):
            validate_run(x)

    def test_offline_remote_effect(self):
        x = load("examples/session/test-run.json")
        x["deliveries"] = self.receipts["deliveries"]
        with self.assertRaises(ContractError):
            validate_run(x)

    def test_recovery_set_candidate(self):
        validate_recovery_set(load("examples/bootstrap/native-recovery-set.json"), self.bundle)

    def test_recovery_set_no_unproven_qualification(self):
        x = load("examples/bootstrap/native-recovery-set.json")
        x["qualified"] = True
        with self.assertRaises(ContractError):
            validate_recovery_set(x, self.bundle)

    def test_source_receipt_cannot_claim_services(self):
        x = load("examples/bootstrap/source-receipt.json")
        x["services_restored"] = True
        with self.assertRaises(ContractError):
            validate("client-records.schema.json", x, "SourceRecovery")


class DirectOwner(unittest.TestCase):
    def test_all_declared_forms_parse(self):
        tokens = {
            "{tool}": "app-ops",
            "{uuid}": "11111111-1111-4111-8111-111111111111",
            "{id}": "demo-app",
            "{digest}": "sha256:" + "a" * 64,
            "{cursor}": "abc_123",
        }
        for form in FORMS:
            command = " ".join(tokens.get(t, t) for t in form["tokens"])
            with self.subTest(form=form["name"]):
                self.assertEqual(parse_command(command, "app-ops")["form"], form["name"])

    def test_wrong_receiver(self):
        with self.assertRaises(ContractError):
            parse_command("host-ops info --json", "app-ops")

    def test_shell_injection(self):
        for tail in ["; id", " && id", " | id", "\nwhoami", " $(id)", " `id`", " >file"]:
            with self.subTest(tail=tail), self.assertRaises(ContractError):
                parse_command("app-ops info --json" + tail, "app-ops")

    def test_arbitrary_argv_rejected(self):
        for cmd in [
            "app-ops exec bash",
            "bash",
            "app-ops app restart demo-app --force",
            "app-ops info --role administrator",
            "app-ops plan --subject-stdin --json extra",
        ]:
            with self.subTest(cmd=cmd), self.assertRaises(ContractError):
                parse_command(cmd, "app-ops")

    def test_missing_apply_epoch(self):
        cmd = (
            "app-ops plan apply 11111111-1111-4111-8111-111111111111 --digest sha256:"
            + "a" * 64
            + " --request-id 22222222-2222-4222-8222-222222222222 --confirm --json"
        )
        with self.assertRaises(ContractError):
            parse_command(cmd, "app-ops")

    def test_bounded_parser(self):
        with self.assertRaises(ContractError):
            parse_command("a" * 5000, "app-ops")

    def test_host_cannot_use_app_grammar(self):
        with self.assertRaises(ContractError):
            parse_command("host-ops app restart demo-app --plan --json", "host-ops")


class NativeInstallationExperiment(unittest.TestCase):
    def test_hashed_local_wheel_install_without_index(self):
        # A synthetic benign package exercises pip, not a production domain-ops artifact.
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            w = r / "wheels"
            w.mkdir()
            name = "ops_fixture"
            di = "ops_fixture-0.0.1.dist-info"
            wheel = w / "ops_fixture-0.0.1-py3-none-any.whl"
            parts = {
                name + "/__init__.py": b"VALUE = 7\n",
                di + "/METADATA": b"Metadata-Version: 2.1\nName: ops-fixture\nVersion: 0.0.1\n",
                di
                + "/WHEEL": b"Wheel-Version: 1.0\nGenerator: spec-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            }
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            for path, b in parts.items():
                writer.writerow(
                    [
                        path,
                        "sha256="
                        + base64.urlsafe_b64encode(hashlib.sha256(b).digest())
                        .rstrip(b"=")
                        .decode(),
                        str(len(b)),
                    ]
                )
            writer.writerow([di + "/RECORD", "", ""])
            parts[di + "/RECORD"] = output.getvalue().encode()
            with zipfile.ZipFile(wheel, "w") as z:
                for p, b in parts.items():
                    z.writestr(p, b)
            lock = r / "lock.txt"
            lock.write_text(
                "ops-fixture==0.0.1 --hash=sha256:"
                + hashlib.sha256(wheel.read_bytes()).hexdigest()
                + "\n"
            )
            subprocess.run(
                [sys.executable, "-m", "venv", str(r / "venv")],
                check=True,
                capture_output=True,
                timeout=30,
            )
            py = str(r / "venv/bin/python")
            env = os.environ.copy()
            env.update(
                PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INDEX="1"
            )
            env.pop("PYTHONPATH", None)
            cmd = [
                py,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-cache-dir",
                "--find-links",
                str(w),
                "-r",
                str(lock),
            ]
            p = subprocess.run(cmd, env=env, capture_output=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr.decode())
            p = subprocess.run(
                [py, "-c", "import ops_fixture; assert ops_fixture.VALUE == 7"], env=env, timeout=10
            )
            self.assertEqual(p.returncode, 0)
            lock.write_text("ops-fixture==0.0.1 --hash=sha256:" + "0" * 64 + "\n")
            p = subprocess.run(
                [*cmd, "--force-reinstall"],
                env=env,
                capture_output=True,
                timeout=30,
            )
            self.assertNotEqual(p.returncode, 0)
