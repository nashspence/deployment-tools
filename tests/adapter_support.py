"""Synthetic owner identities and installed-descriptor test inputs, never native evidence."""

import copy
import json
from pathlib import Path

from deployment_tools_adapters import Registry
from deployment_tools_contracts.validation import digest

from tests.support import ROOT

D = "sha256:" + "a" * 64
OWNER = {
    "tool": "app-ops",
    "id": "00000000-0000-4000-8000-000000000001",
    "epoch": "00000000-0000-4000-8000-000000000002",
}
SCOPE = {"authority": OWNER["id"], "kind": "application", "handle": "demo"}
INCARNATION = "00000000-0000-4000-8000-000000000003"


def identifier(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def descriptors() -> list[Path]:
    return sorted(
        (ROOT / "some-implementations/deployment-tools").glob("*/*/src/*/descriptor.json")
    )


def runtime_registry() -> tuple[Registry, dict, dict]:
    # This is an authority-model test double, not the second supplied/native adapter.
    path = next(p for p in descriptors() if "podman-quadlet" in p.parts)
    descriptor = json.loads(path.read_text())
    descriptor["id"] = "independent-author.example-runtime"
    descriptor["implemented"] = ["runtime.activate", "runtime.deactivate"]
    installation = {
        "implementation": descriptor["id"],
        "artifact": {"digest": D, "media_type": "application/zip", "size_bytes": 100},
        "descriptor_digest": digest(descriptor),
        "enabled": True,
    }
    instance = {
        "id": "native-demo",
        "implementation": descriptor["id"],
        "installation_digest": D,
        "role": "runtime",
        "schema_digest": digest(descriptor["configuration_schema"]),
        "configuration": {"account": "app-demo"},
        "grants": [{"scope": SCOPE, "access": ["observe", "change"]}],
        "generation": 1,
    }
    registry = Registry()
    registry.install(installation, descriptor, owner_approved=True)
    registry.enroll(instance, owner_approved=True)
    return registry, copy.deepcopy(descriptor), copy.deepcopy(instance)


def binding() -> dict:
    return {
        "subject": "demo",
        "instance": "native-demo",
        "generation": 1,
        "installation_digest": D,
        "configuration_digest": digest({"account": "app-demo"}),
        "scope": copy.deepcopy(SCOPE),
        "native_incarnation": INCARNATION,
        "admission_generation": 0,
        "instance_generation": 1,
    }


def request(method: str = "activate") -> dict:
    return {
        "contract": "deployment-tools.runtime",
        "method": method,
        "input": {
            "scope": copy.deepcopy(SCOPE),
            "deployment": {
                "handle": "deployment:demo",
                "release_digest": D,
                "binding_digest": D,
                "projection_digest": D,
                "components": ["web"],
            },
        },
    }


def observation(admission_generation: int = 0) -> dict:
    return {
        "scope": copy.deepcopy(SCOPE),
        "generation": 1,
        "observed_at": "2026-01-01T00:00:00Z",
        "native_incarnation": INCARNATION,
        "coverage": "complete",
        "activity": "inactive",
        "health": "unknown",
        "quiescence": "verified",
        "continuation": None,
        "instance": "native-demo",
        "installation_digest": D,
        "admission_generation": admission_generation,
    }
