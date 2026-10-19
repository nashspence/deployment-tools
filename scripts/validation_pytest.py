"""Mandatory observational pytest instrumentation, aligned with Riverhog's timing lane."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock

from scripts.run_stats import RunStats, comparison

REPORT_VARIABLE = "DEPLOYMENT_TOOLS_TEST_REPORT"
SAFE_NODE = re.compile(r"tests/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+)+(?:\[[A-Za-z0-9_.-]+\])?")


def safe_node(node: str) -> str:
    return (
        node
        if SAFE_NODE.fullmatch(node)
        else "redacted:" + hashlib.sha256(node.encode()).hexdigest()
    )


def write_stats(root: Path, value: dict[str, Any]) -> Path:
    directory = root / "artifacts/test-stats"
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("test stats must stay inside the checkout")
    directory.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, sort_keys=True) + "\n"
    if len(content.encode()) > 2_000_000:
        raise ValueError("test stats record exceeds capacity")
    path = directory / (uuid.uuid4().hex + ".json")
    with FileLock(str(directory / ".lock"), timeout=10):
        existing = [
            p
            for p in directory.glob("*.json")
            if re.fullmatch(r"[a-f0-9]{32}\.json", p.name) and not p.is_symlink()
        ]
        size = sum(p.stat().st_size for p in existing) + len(content.encode())
        count = len(existing) + 1
        for old in sorted(existing, key=lambda p: p.stat().st_mtime_ns):
            if count <= 32 and size <= 8_000_000:
                break
            size -= old.stat().st_size
            count -= 1
            old.unlink()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    return path


class TestResults:
    def __init__(self, root: Path, destination: Path | None) -> None:
        self.root, self.destination = root, destination
        self.stats = RunStats()
        self.collected: list[str] = []
        self.outcomes: dict[str, str] = {}
        self.phases: dict[str, dict[str, Any]] = {}
        self.fixtures: dict[str, dict[str, int | float]] = {}
        self.output: Path | None = None
        self.target: dict[str, Any] = {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = [safe_node(item.nodeid) for item in session.items]

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: pytest.FixtureDef[Any], request: pytest.FixtureRequest
    ) -> Generator[None, Any, Any]:
        started = time.perf_counter()
        try:
            return (yield)
        finally:
            entry = self.fixtures.setdefault(fixturedef.argname, {"count": 0, "seconds": 0.0})
            entry["count"] += 1
            entry["seconds"] += time.perf_counter() - started

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        node = safe_node(report.nodeid)
        self.phases.setdefault(node, {})[report.when] = {
            "seconds": report.duration,
            "outcome": report.outcome,
            "rss_sample_bytes": self.stats.sample(),
            "rss_scope": "process-snapshot-not-test-allocation",
            "expected_failure": bool(getattr(report, "wasxfail", False)),
        }
        previous = self.outcomes.get(node)
        if report.failed:
            self.outcomes[node] = "failed"
        elif previous != "failed" and report.skipped:
            self.outcomes[node] = "skipped"
        elif previous not in {"failed", "skipped"} and report.when == "call":
            self.outcomes[node] = "passed"

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        stats = self.stats.finish()
        filtered = any(
            (
                session.config.option.keyword,
                session.config.option.markexpr,
                session.config.option.collectonly,
                session.config.option.deselect,
                session.config.option.lf,
                session.config.option.failedfirst,
            )
        )
        target = comparison(
            self.root,
            list(session.config.args),
            stats,
            success=int(exitstatus) == 0,
            filtered=filtered,
        )
        self.target = target
        value = {
            "profile": {
                "platform": sys.platform,
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "profiling_target": target,
            "format": "deployment-tools-pytest-timing/v1",
            "qualification_eligible": False,
            "collected": self.collected,
            "outcomes": self.outcomes,
            "exit_status": int(exitstatus),
            "stats": stats,
            "phases": self.phases,
            "fixtures": self.fixtures,
        }
        self.output = write_stats(self.root, value)
        if self.destination:
            self.destination.write_text(json.dumps(value), encoding="utf-8")

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.output:
            terminalreporter.write_line(
                f"Non-contractual test stats: {self.output.relative_to(self.root)}"
            )
            if self.target.get("metrics"):
                metrics = self.target["metrics"]
                details = "; ".join(
                    f"{key}: {m['observed']:.3f}/{m['target']:g} {m['status']}"
                    for key, m in metrics.items()
                )
                terminalreporter.write_line("Profiling targets (report only): " + details)


def pytest_configure(config: pytest.Config) -> None:
    if not config.pluginmanager.hasplugin("deployment-tools-results"):
        destination = os.environ.get(REPORT_VARIABLE)
        config.pluginmanager.register(
            TestResults(config.rootpath, Path(destination) if destination else None),
            "deployment-tools-results",
        )
