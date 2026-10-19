"""Audit the package artifact allowlist, independently of the source tree."""

import sys
import zipfile
from pathlib import Path

from check_public import check_text

for filename in sys.argv[1:]:
    with zipfile.ZipFile(filename) as archive:
        for name in archive.namelist():
            p = Path(name)
            if (
                p.is_absolute()
                or ".." in p.parts
                or not (name.startswith("deployment_tools/") or ".dist-info/" in name)
            ):
                raise SystemExit("Unexpected wheel entry")
            if p.suffix.lower() in {".md", ".age", ".pem", ".key", ".sqlite", ".db", ".bundle"}:
                raise SystemExit("Forbidden wheel entry")
            text = archive.read(name).decode("utf-8")
            if check_text(name, text):
                raise SystemExit("Wheel content rejected")
    print("Wheel contents passed:", Path(filename).name)
