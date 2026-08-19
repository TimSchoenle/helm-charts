#!/usr/bin/env python3
"""The coverage gate — a chart that pins a first-party image and declares no contract.

The explicit opt-out, a `config-contract.yaml` with `documents: []` and a `reason`, is the only
way out. That is what stops the whole pipeline from being escaped by forgetting: a new chart, or
a new service inside an existing one, is otherwise covered by nothing while the repository still
looks covered.

A chart that *does* declare documents must account for every first-party image it pins, either by
listing it under a document's `images` or by naming it under `unconfigured`. The second exists
because "reads no contract-described configuration" is a real answer for a bootstrap job or a
one-shot migration — it just has to be written down rather than left as an omission.

Runs without a render, so `just check-contract-coverage` is cheap enough to sit in `just check`
next to the gates that need one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import config_contract as cc
from config_declaration import DECLARATION, DeclarationError, load_declaration
from config_report import Report


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
    therefore covered the moment its values are added, with nothing here to update.
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

    for chart_dir in sorted(charts.iterdir()):
        values_path = chart_dir / "values.yaml"
        if not (chart_dir / "Chart.yaml").is_file() or not values_path.is_file():
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
        if declaration is None:
            report.fail(
                chart_dir.name,
                "pins the first-party image(s) "
                f"{', '.join(repository for _, repository in ours)} and has no {DECLARATION}; "
                "add one, or opt out explicitly with `documents: []` and a `reason`",
            )
            continue

        if not declaration.documents:
            continue

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
