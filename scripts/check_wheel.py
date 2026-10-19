from __future__ import annotations

import argparse
import email
import zipfile
from pathlib import Path

from scripts.check_public import check_text
from scripts.project_policy import ROOT, components, read_toml


def check_wheels(directory: Path) -> None:
    units = components()
    expected = {item["distribution"].replace("-", "_"): item for item in units}
    found = set()
    for path in directory.glob("*.whl"):
        with zipfile.ZipFile(path) as archive:
            metadata_path = next(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )
            metadata = email.message_from_bytes(archive.read(metadata_path))
            name = str(metadata["Name"]).replace("-", "_")
            if name not in expected or name in found:
                raise ValueError("unexpected or duplicated wheel")
            found.add(name)
            unit = expected[name]
            if (
                metadata["Version"] != read_toml(ROOT / "release.toml")["version"]
                or metadata["License-Expression"] != "Apache-2.0"
            ):
                raise ValueError("wheel version or license metadata mismatch")
            dist_info = metadata_path.removesuffix("METADATA")
            if archive.read(dist_info + "licenses/LICENSE") != (ROOT / "LICENSE").read_bytes():
                raise ValueError("wheel legal license drift")
            if unit["module"] + "/py.typed" not in archive.namelist():
                raise ValueError("wheel inline type marker missing")
            for member in archive.namelist():
                entry = Path(member)
                if (
                    entry.is_absolute()
                    or ".." in entry.parts
                    or not (member.startswith(unit["module"] + "/") or member.startswith(dist_info))
                ):
                    raise ValueError("wheel crossed component boundary")
                if entry.suffix.lower() in {
                    ".md",
                    ".pyc",
                    ".pyo",
                    ".age",
                    ".pem",
                    ".key",
                    ".sqlite",
                    ".db",
                    ".bundle",
                }:
                    raise ValueError("forbidden wheel member")
                if check_text(member, archive.read(member).decode("utf-8")):
                    raise ValueError("wheel public content rejected")
    if found != set(expected):
        raise ValueError("missing workspace distribution")
    print("All workspace wheel boundaries, types, versions and licenses passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, default=ROOT / "dist", nargs="?")
    args = parser.parse_args()
    check_wheels(args.directory)


if __name__ == "__main__":
    main()
