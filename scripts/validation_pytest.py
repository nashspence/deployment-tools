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

from scripts.check_evidence import (
    ACTIVE,
    group_results,
    item_groups,
    reconcile,
    summary,
    validate_overrides,
)
from scripts.check_evidence import (
    FORMAT as EVIDENCE_FORMAT,
)
from scripts.run_stats import RunStats, comparison

REPORT_VARIABLE = "FISHHAT_TEST_REPORT"
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
        self.declarations: dict[str, Any] = {}
        self.observed: dict[str, set[str]] = {}
        self.inventory_hash = ""
        self.evidence: dict[str, Any] = {}
        self.subtests: dict[str, list[dict[str, Any]]] = {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = [safe_node(item.nodeid) for item in session.items]
        try:
            if len(set(self.collected)) != len(self.collected):
                raise ValueError("duplicate collected checks")
            self.declarations = {
                safe_node(i.nodeid): item_groups(i, self.root) for i in session.items
            }
            complete = list(session.config.args) == ["tests"] and not (
                session.config.option.keyword
                or session.config.option.markexpr
                or session.config.option.deselect
                or session.config.option.lf
            )
            generate = session.config.getoption("evidence_generate")
            if complete:
                validate_overrides(session.items)
            if generate and not session.config.option.collectonly:
                raise ValueError("inventory generation requires --collect-only")
            self.inventory_hash = reconcile(
                self.declarations, self.root, complete=complete, generate=generate
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise pytest.UsageError(str(error)) from error

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, Any, Any]:
        observed: set[str] = set()
        token = ACTIVE.set(observed)
        try:
            return (yield)
        finally:
            ACTIVE.reset(token)
            self.observed[safe_node(item.nodeid)] = observed

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
        if hasattr(report, "context"):
            self.subtests.setdefault(node, []).append(
                {
                    "case_sha256": hashlib.sha256(repr(report.context).encode()).hexdigest(),
                    "outcome": report.outcome,
                    "expected_failure": hasattr(report, "wasxfail"),
                }
            )
        self.phases.setdefault(node, {})[report.when] = {
            "seconds": report.duration,
            "outcome": report.outcome,
            "rss_sample_bytes": self.stats.sample(),
            "rss_scope": "process-snapshot-not-test-allocation",
            "expected_failure": hasattr(report, "wasxfail"),
        }
        previous = self.outcomes.get(node)
        if report.failed:
            self.outcomes[node] = "failed"
        elif previous != "failed" and report.skipped:
            self.outcomes[node] = "skipped"
        elif previous not in {"failed", "skipped"} and report.when == "call":
            self.outcomes[node] = "passed"

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        for node in self.collected:
            self.outcomes.setdefault(node, "not-executed")
            self.phases.setdefault(node, {})
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
        self.evidence = {
            "format": EVIDENCE_FORMAT,
            "origin": os.environ.get("FISHHAT_TEST_ORIGIN"),
            "inventory_sha256": self.inventory_hash,
            "groups": group_results(self.declarations, self.phases, self.observed, self.outcomes),
        }
        value = {
            "profile": {
                "platform": sys.platform,
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "profiling_target": target,
            "format": "fishhat-pytest-timing/v1",
            "qualification_eligible": False,
            "collected": self.collected,
            "outcomes": self.outcomes,
            "exit_status": int(exitstatus),
            "stats": stats,
            "phases": self.phases,
            "fixtures": self.fixtures,
            "evidence": self.evidence,
            "subtests": self.subtests,
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
            terminalreporter.write_line(
                "Check purposes/scopes (not native qualification): "
                + json.dumps(summary(self.evidence["groups"]), sort_keys=True)
            )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--evidence-generate",
        action="store_true",
        help="Snapshot reviewed full collection and maintained entrypoints; no execution proof",
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.pluginmanager.hasplugin("fishhat-results"):
        destination = os.environ.get(REPORT_VARIABLE)
        config.pluginmanager.register(
            TestResults(config.rootpath, Path(destination) if destination else None),
            "fishhat-results",
        )
