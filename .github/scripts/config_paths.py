#!/usr/bin/env python3
"""The chart tree's location, shared by the scripts that walk it.

One declaration rather than one per script, so pointing the toolchain at another tree is one edit
and not several that can drift apart. Deliberately not a home for anything that decides
something: the contract rules live in `terrace-contract`, and what is left here is the answer to
"where is `charts/`".
"""

from __future__ import annotations

from pathlib import Path

# The chart tree, relative to the repository root. Every entry point takes `--charts` and defaults
# to this, so a caller can point the whole toolchain at a fixture directory.
CHARTS_DIR = Path("charts")
