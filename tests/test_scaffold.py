from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from deployment_tools_cli.validation import main

from tests.support import FIXTURES, ROOT

CHECK_PURPOSE = {
    "purpose": "public-contract",
    "authority": ["policy/interfaces.json#/cli"],
    "scope": "in-process read-only CLI candidates",
    "relationship": "candidate",
    "reason": "Literal exit/rejection and redaction checks exercise scaffold paths; imported core helpers alone do not qualify installed entrypoints.",
    "claims": [],
}


class Scaffold(unittest.TestCase):
    def test_info_does_not_claim_native_readiness(self):
        for tool in ("domain-ops", "host-ops", "app-ops"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(tool, ["info", "--json"]), 0)
            info = json.loads(out.getvalue())
            self.assertEqual(info["tool"], tool)
            self.assertFalse(info["production_qualified"])
            self.assertFalse(info["native_mutations"])

    def test_unknown_mutations_refused(self):
        for tool in ("domain-ops", "host-ops", "app-ops"):
            with (
                self.subTest(tool=tool),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as e,
            ):
                main(tool, ["apply"])
            self.assertEqual(e.exception.code, 2)

    def test_local_domain_validation(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main("domain-ops", ["validate", str(FIXTURES / "domain/domain.yaml"), "--json"]), 0
            )

    def test_host_requires_secret_catalogue(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main("host-ops", ["validate", str(FIXTURES / "host-node-a/host.yaml"), "--json"]), 2
            )

    def test_host_with_catalogue(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(
                    "host-ops",
                    [
                        "validate",
                        str(FIXTURES / "host-node-a/host.yaml"),
                        "--catalogue",
                        str(FIXTURES / "host-node-a/secrets.yaml"),
                        "--json",
                    ],
                ),
                0,
            )

    def test_invalid_private_content_not_echoed(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "source.json"
            p.write_text('{"SYNTHETIC_PRIVATE_SENTINEL": true}')
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                self.assertEqual(main("domain-ops", ["validate", str(p), "--json"]), 2)
            self.assertNotIn("SYNTHETIC_PRIVATE_SENTINEL", out.getvalue())
            self.assertNotIn(str(p), out.getvalue())

    def test_imported_cli_does_not_dispatch_network(self):
        source = (ROOT / "packages/cli-support/src/deployment_tools_cli/validation.py").read_text()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests", source)


if __name__ == "__main__":
    unittest.main()
