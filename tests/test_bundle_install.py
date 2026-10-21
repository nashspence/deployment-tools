from __future__ import annotations

import json
import platform
import sys

import pytest

from scripts.bundle_install import install, sha256, verify

CHECK_PURPOSE = {
    "purpose": "validation-tooling",
    "authority": ["scripts/bundle_install.py"],
    "scope": "local scaffold installer",
    "relationship": "no-product-claim",
    "reason": "Synthetic bundles test hash, path and offline installation guards; no native activation promise.",
    "claims": [],
}


def bundle_fixture(root):
    (root / "wheels").mkdir()
    (root / "locks").mkdir()
    wheel = root / "wheels/fixture.whl"
    wheel.write_bytes(b"synthetic artifact")
    lock = root / "locks/fixture.txt"
    lock.write_text("fixture==1.0 --hash=sha256:" + sha256(wheel) + "\n")
    manifest = {
        "format": "fishhat-scaffold-bundle/v1",
        "production_qualified": False,
        "profile": {
            "platform": sys.platform,
            "machine": platform.machine(),
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        },
        "components": {
            "fixture": {
                "distribution": "fixture",
                "lock": "locks/fixture.txt",
                "dependencies": ["fixture"],
            }
        },
        "files": {
            path.relative_to(root).as_posix(): {"sha256": sha256(path), "size": path.stat().st_size}
            for path in (wheel, lock)
        },
    }
    return seal(root, manifest)


def seal(root, manifest):
    (root / "manifest.json").write_text(json.dumps(manifest))
    expected = sha256(root / "manifest.json")
    values = {
        **{key: value["sha256"] for key, value in manifest["files"].items()},
        "manifest.json": expected,
    }
    (root / "SHA256SUMS").write_text(
        "".join(f"{value}  {key}\n" for key, value in sorted(values.items()))
    )
    return expected


def test_verified_bundle_detects_changed_artifact(tmp_path):
    expected = bundle_fixture(tmp_path)
    assert verify(tmp_path, expected)["production_qualified"] is False
    (tmp_path / "wheels/fixture.whl").write_bytes(b"changed artifact")
    with pytest.raises(ValueError, match="artifact changed"):
        verify(tmp_path, expected)


def test_untrusted_manifest_never_creates_installation(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundle_fixture(bundle)
    destination = tmp_path / "destination"
    with pytest.raises(ValueError, match="manifest"):
        install(bundle, destination, "fixture", "0" * 64)
    assert not destination.exists()


def test_existing_installation_is_preserved(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    expected = bundle_fixture(bundle)
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "keep").write_text("preserved")
    with pytest.raises(ValueError, match="existing installation"):
        install(bundle, destination, "fixture", expected)
    assert (destination / "keep").read_text() == "preserved"


def test_even_trusted_lock_cannot_select_network_requirements(tmp_path):
    expected = bundle_fixture(tmp_path)
    manifest = verify(tmp_path, expected)
    lock = tmp_path / "locks/fixture.txt"
    lock.write_text("fixture @ " + "https://" + "example.invalid/fixture.whl\n")
    manifest["files"]["locks/fixture.txt"] = {"sha256": sha256(lock), "size": lock.stat().st_size}
    expected = seal(tmp_path, manifest)
    with pytest.raises(ValueError, match="hashed package pins"):
        verify(tmp_path, expected)


def test_unclassified_member_or_wrong_profile_is_rejected(tmp_path):
    expected = bundle_fixture(tmp_path)
    manifest = verify(tmp_path, expected)
    manifest["profile"]["python"] = "0.0"
    expected = seal(tmp_path, manifest)
    with pytest.raises(ValueError, match="platform or Python"):
        verify(tmp_path, expected)
    manifest["profile"]["python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    expected = seal(tmp_path, manifest)
    (tmp_path / "unclassified").write_text("unclassified")
    with pytest.raises(ValueError, match="unclassified"):
        verify(tmp_path, expected)
