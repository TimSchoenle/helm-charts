#!/usr/bin/env python3
"""Collecting and rendering what the configuration gates found.

Separated from the gates themselves so a gate is a pure function of the manifests and the
contract: it returns findings and decides nothing about where they are printed, whether the run
fails, or how a GitHub step summary is laid out. That is what lets every gate be unit-tested by
calling it and reading the list back.

Every gate is run before anything exits, which is the posture the rest of this repository already
takes — `just render` attempts every pair "so one chart that fails to render does not hide the
state of the rest", and kubeconform validates every file before it exits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """One thing a gate has to say, and whether it fails the run.

    A warning is for something a gate could not check rather than something it found wrong — a
    secrets directory mounted somewhere the file names cannot be read from, or an unaccounted-for
    variable under a contract whose `external.unknown` is `warn`. Silence there would be a gate
    reporting success it has not earned.
    """

    level: str
    message: str


def error(message: str) -> Finding:
    return Finding(ERROR, message)


def warning(message: str) -> Finding:
    return Finding(WARNING, message)


@dataclass
class Report:
    """Every finding, in the order it was found, tagged with the pair that produced it.

    `where` is the chart or the rendered file, then the document — so one line names the chart,
    the values file and the key without a reader opening anything.
    """

    findings: list[tuple[str, Finding]] = field(default_factory=list)

    def add(self, where: str, found: Finding | None) -> None:
        if found is not None:
            self.findings.append((where, found))

    def extend(self, where: str, found: list[Finding]) -> None:
        for finding in found:
            self.add(where, finding)

    def fail(self, where: str, message: str) -> None:
        self.add(where, error(message))

    @property
    def errors(self) -> list[tuple[str, Finding]]:
        return [entry for entry in self.findings if entry[1].level == ERROR]

    @property
    def warnings(self) -> list[tuple[str, Finding]]:
        return [entry for entry in self.findings if entry[1].level == WARNING]

    def print(self, stream, error_stream) -> None:
        for where, finding in self.warnings:
            print(f"warning: {where}: {finding.message}", file=stream)
        for where, finding in self.errors:
            print(f"{where}: {finding.message}", file=error_stream)

    def summary(self) -> None:
        """A table for `$GITHUB_STEP_SUMMARY`, where a reviewer of a bump looks first."""
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if not target:
            return

        lines = ["## Configuration contracts", ""]
        if not self.findings:
            lines.append(
                "Every rendered document, container environment and secret mount matches the "
                "contract of the image its chart pins."
            )
        else:
            lines += ["| | Where | What |", "|---|---|---|"]
            for where, finding in self.errors + self.warnings:
                icon = "❌" if finding.level == ERROR else "⚠️"
                lines.append(f"| {icon} | `{where}` | {_cell(finding.message)} |")

        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def _cell(message: str) -> str:
    return message.replace("|", "\\|").replace("\n", " ")
