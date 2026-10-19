"""Read-only scaffold entry points. No native execution is wired up yet."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .models.contracts import (ContractError, SCHEMAS, MAX_BYTES, strict_json,
                              strict_yaml, validate, validate_domain,
                              validate_host, validate_app)

def main(tool: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=tool, description="Deployment tools scaffold (no native mutations)")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    info = commands.add_parser("info", help="Report implemented capabilities, not host readiness")
    info.add_argument("--json", action="store_true")
    check = commands.add_parser("validate", help="Validate local source data without side effects")
    check.add_argument("path", type=Path)
    check.add_argument("--schema", choices=sorted(SCHEMAS), default="source.schema.json")
    check.add_argument("--definition")
    check.add_argument("--catalogue", type=Path)
    check.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "info":
        result = {"tool": tool, "version": __version__, "implementation": "scaffold",
                  "capabilities": ["local-contract-validation"], "native_mutations": False,
                  "production_qualified": False}
    else:
        try:
            def load(path):
                with path.open("rb") as stream:
                    data = stream.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ContractError("input size limit")
                return strict_yaml(data.decode("utf-8")) if path.suffix in (".yaml", ".yml") else strict_json(data)
            data = load(args.path)
            validate(args.schema, data, args.definition)
            if args.schema == "source.schema.json" and args.definition is None:
                cat = load(args.catalogue) if args.catalogue else None
                if data.get("kind") == "Domain": validate_domain(data)
                elif data.get("kind") == "Host": validate_host(data, cat)
                elif data.get("kind") == "App": validate_app(data, cat)
            result = {"tool": tool, "status": "valid", "scope": "local-contracts-only",
                      "production_qualified": False}
        except (OSError, UnicodeError, ContractError, KeyError, ValueError):
            # Do not echo input, paths, or malformed private source in errors.
            result = {"tool": tool, "status": "invalid", "error": "INPUT_REJECTED"}
            print(json.dumps(result) if args.json else "Input rejected", file=sys.stderr)
            return 2
    print(json.dumps(result, sort_keys=True) if args.json else "\n".join(f"{k}: {v}" for k, v in result.items()))
    return 0

def domain_main() -> int: return main("domain-ops")
def host_main() -> int: return main("host-ops")
def app_main() -> int: return main("app-ops")

if __name__ == "__main__":
    raise SystemExit(domain_main())
