#!/usr/bin/env python3
"""The coverage report — which charts are covered by a configuration contract, and which are not.

**Adopting a contract is opt-in.** A chart is covered when it carries a `config-contract.yaml`,
and a chart without one is simply not covered: this reports it and does not fail. The images in
this repository adopt the contract format on their own release schedules, and a gate that failed
every chart whose image had not caught up yet would be a gate that is red for reasons nobody in
this repository can fix — so it would end up disabled, which is worse than absent.

Two things are still hard errors, because both are a chart contradicting *itself* rather than
waiting on someone else:

- a chart that declares documents but leaves one of its own first-party images unaccounted for.
  A new service added to a multi-image chart would otherwise be validated by nothing while the
  chart still looked covered — the failure this file exists to prevent, and the one case where
  the chart's author has everything they need to fix it.
- a `config-contract.yaml` that cannot be read as one, which `load_declaration` refuses.

An explicit `documents: []` with a written `reason` remains supported and is worth writing when
the decision not to adopt is deliberate rather than pending — it is the difference between a
choice and an oversight, for a reader who cannot tell them apart from silence.

Runs without a render, so `just check-contract-coverage` is cheap enough to sit in `just check`
next to the gates that need one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import config_contract as cc
from config_declaration import DECLARATION, DeclarationError, chart_dirs, load_declaration
from config_report import Report, warning


def first_party_patterns(path: Path) -> list[str]:
    """The repositories this organisation builds, from `.github/configs/first-party-images.txt`."""
    if not path.is_file():
        raise DeclarationError(f"{path}: missing; coverage cannot be decided without it")
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def pinned_images(values: Any, path: str = "") -> list[tuple[str, str]]:
    """Every `(values path, repository)` a chart's values.yaml pins.

    Discovered by shape rather than by a list of known paths: anything with a `repository` is an
    image block, which is what `common.image` itself assumes. A chart that grows a new service is
    therefore seen the moment its values are added, with nothing here to update.
    """
    found: list[tuple[str, str]] = []
    if isinstance(values, dict):
        if isinstance(values.get("repository"), str) and values["repository"]:
            found.append((path, values["repository"]))
        for name, value in values.items():
            found.extend(pinned_images(value, f"{path}.{name}" if path else name))
    elif isinstance(values, list):
        for index, value in enumerate(values):
            found.extend(pinned_images(value, f"{path}[{index}]"))
    return found


def check_coverage(charts: Path, first_party: Path, report: Report) -> None:
    patterns = first_party_patterns(first_party)

    covered: list[str] = []
    uncovered: list[tuple[str, list[str]]] = []

    for chart_dir in chart_dirs(charts):
        # A chart with no values.yaml pins no image, so there is nothing here to be covered or
        # uncovered. Checked here rather than in the shared walk because every other caller
        # legitimately reads a chart without one.
        values_path = chart_dir / "values.yaml"
        if not values_path.is_file():
            continue

        values = yaml.safe_load(values_path.read_text(encoding="utf-8")) or {}
        ours = [
            (path, repository)
            for path, repository in pinned_images(values)
            if any(cc.matches_ignore(pattern, repository) for pattern in patterns)
        ]
        if not ours:
            continue

        declaration = load_declaration(chart_dir)

        if declaration is None or not declaration.documents:
            uncovered.append((chart_dir.name, [repository for _, repository in ours]))
            continue

        covered.append(chart_dir.name)

        # From here on the chart has opted in, so an image it does not account for is its own
        # inconsistency rather than a producer that has not shipped yet.
        declared = {reference.values for doc in declaration.documents for reference in doc.images}
        declared.update(declaration.unconfigured)
        for path, repository in ours:
            if path not in declared:
                report.fail(
                    f"{chart_dir.name}: {DECLARATION}",
                    f"the values path {path!r} pins the first-party image {repository} and no "
                    "document reads it; list it under a document's `images`, or under "
                    "`unconfigured` if it reads no contract-described configuration",
                )

    _report_coverage(covered, uncovered, report)


def _report_coverage(
    covered: list[str], uncovered: list[tuple[str, list[str]]], report: Report
) -> None:
    """Say what is covered and what is not, without failing on the latter.

    Printed rather than silent because opt-in without visibility is just absence: the point of
    naming the uncovered charts on every run is that adopting one becomes an obvious next step
    rather than something nobody remembers is possible.
    """
    for name in covered:
        print(f"covered: {name}")

    for name, repositories in uncovered:
        report.add(
            name,
            warning(
                "pins the first-party image(s) "
                f"{', '.join(repositories)} and declares no configuration contract, so its "
                f"rendered configuration is checked by nothing. Add a {DECLARATION} once the "
                "image publishes one."
            ),
        )

    if not covered and not uncovered:
        print("==> no chart pins a first-party image")
