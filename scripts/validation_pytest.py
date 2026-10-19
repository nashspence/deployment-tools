from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPORT_VARIABLE = "DEPLOYMENT_TOOLS_TEST_REPORT"


class TestResults:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.collected: list[str] = []
        self.outcomes: dict[str, str] = {}

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        previous = self.outcomes.get(report.nodeid)
        if report.failed:
            self.outcomes[report.nodeid] = "failed"
        elif previous != "failed" and report.skipped:
            self.outcomes[report.nodeid] = "skipped"
        elif previous not in {"failed", "skipped"} and report.when == "call":
            self.outcomes[report.nodeid] = "passed"

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self.destination.write_text(
            json.dumps(
                {
                    "collected": self.collected,
                    "outcomes": self.outcomes,
                    "exit_status": int(exitstatus),
                }
            ),
            encoding="utf-8",
        )


def pytest_configure(config: pytest.Config) -> None:
    destination = os.environ.get(REPORT_VARIABLE)
    if destination:
        config.pluginmanager.register(TestResults(Path(destination)), "deployment-tools-results")
