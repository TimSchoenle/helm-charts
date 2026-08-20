#!/usr/bin/env python3
"""The paths and the two-line helpers every script in this directory had its own copy of.

Nothing here is interesting. That is the point: each of these was declared in between two and
eight files, identically, and an identical declaration in eight files is a fact that can be
changed in one of them and stay wrong in the other seven — silently, because every copy still
parses and every test still passes.

Deliberately not a home for anything that decides something. `config_contract` owns the contract
model, `config_declaration` owns `config-contract.yaml`, `config_manifests` owns the rendered
objects, and a helper that grows a rule belongs in whichever of those the rule is about. What
lives here is the answer to "where is `charts/`" and "read this YAML file", which are not rules
and have no natural owner.

`digest_of` in `config_manifests` is a near-neighbour of `dig` below and is not the same function
— one splits an image reference, the other walks a values tree — so it stays where it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# The chart tree, relative to the repository root. Every entry point takes `--charts` and defaults
# to this, so a caller can point the whole toolchain at a fixture directory.
CHARTS_DIR = Path("charts")

# The image repositories this organisation builds, and so the ones that can be expected to publish
# a configuration contract. Read by `check-contract-coverage` to decide which charts owe a
# declaration, and by `new-chart` to decide whether to write an opt-out — the two have to agree,
# which is why neither spells the path itself.
FIRST_PARTY = Path(".github/configs/first-party-images.txt")


def read_yaml(path: Path) -> dict[str, Any]:
    """One YAML document as a mapping, with an absent or empty file reading as `{}`.

    The `or {}` is the whole reason this is a function rather than a call: `yaml.safe_load` of an
    empty file returns `None`, and every caller here goes on to `.get()` something out of it. Three
    files had this exact line; two of them had reached it independently.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def dig(values: Any, path: str) -> Any:
    """Follow a dotted values path, returning `None` at the first missing step.

    Deliberately returns `None` for "not there" rather than raising, because every caller is
    asking whether a chart happens to declare something — `image.registry`, a per-service image
    block — and absence is an ordinary answer rather than an error.
    """
    current = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
