from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PACKAGE = ROOT / "packages/contracts/src/deployment_tools_contracts"


def fixture_path(name):
    if name == "contracts/index.json":
        return FIXTURES / "index.json"
    if name.startswith("contracts/"):
        return PACKAGE / name
    if name.startswith("examples/"):
        return FIXTURES / name[len("examples/") :]
    return FIXTURES / name
