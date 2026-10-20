from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from deployment_tools_adapters import check_configuration, check_descriptor
from deployment_tools_contracts.validation import ContractError, digest, strict_json


def main(package_file: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Installed adapter scaffold; no native dispatch")
    parser.add_argument("command", choices=("info", "validate", "qualify"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result: dict[str, Any]
    try:
        descriptor = strict_json(Path(package_file).with_name("descriptor.json").read_bytes())
        check_descriptor(descriptor)
        if args.command == "validate":
            data = strict_json(sys.stdin.buffer.read(1048577))
            check_configuration(
                descriptor["configuration_schema"], data, digest(descriptor["configuration_schema"])
            )
            result = {"status": "valid", "scope": "local-configuration-only"}
        elif args.command == "qualify":
            print(
                json.dumps(
                    {
                        "status": "unqualified",
                        "error": "NATIVE_EXECUTOR_UNIMPLEMENTED",
                        "native_qualified": False,
                    }
                )
            )
            return 2
        else:
            result = {
                "implementation": descriptor["id"],
                "roles": descriptor["roles"],
                "descriptor_digest": digest(descriptor),
                "implemented_operations": descriptor["implemented"],
                "capabilities": ["local-configuration-validation"],
                "native_mutations": False,
                "native_qualified": False,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, ContractError, KeyError, TypeError):
        print(json.dumps({"error": "INPUT_REJECTED"}), file=sys.stderr)
        return 2
