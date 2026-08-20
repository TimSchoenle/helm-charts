#!/usr/bin/env python3
"""Importing an entry point whose file name a `import` statement cannot spell.

The scripts in `.github/scripts` are hyphenated — `check-config.py`, `explain-config.py` — because
that is how they are invoked, and a hyphen is not a Python identifier. So a test that wants to
call `main()` or one of the functions around it has to load the file by path.

Four test modules had their own copy of the six lines that does this, one of them carrying a
four-line comment about a subtlety the other three did not need. This is that copy, once, with
the subtlety handled for everybody.

**The real fix is upstream and is not this.** Rename the entry points to underscores and let the
`just` recipes spell the path, and this file disappears along with the `config-secrets.py` /
`config_secrets.py` pair that currently differ by one character. That rename touches every recipe,
every workflow reference and all four of these tests, so it is worth doing on its own and worth
not doing in the middle of something else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[1]


def load(name: str, filename: str) -> ModuleType:
    """Import one hyphenated entry point under `name`, and remember it under that name.

    Registered in `sys.modules` *before* it is executed, which matters for exactly one reason and
    was measured rather than anticipated: `@dataclass` resolves its own module out of `sys.modules`
    to decide what a `ClassVar` annotation means, so a module that is not there yet fails at the
    decorator rather than at anything the test wrote. `test_contract_explain` hit that and grew
    the two extra lines; the other three did not, and so did not have them.

    Returning the module already in `sys.modules` under this name keeps a second import cheap and,
    more usefully, identical — two loads of one file produce two distinct classes, and an
    `isinstance` across them fails in a way that reads like a bug in the code under test.
    """
    existing = sys.modules.get(name)
    if existing is not None:
        return existing

    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path}: cannot be loaded as a module")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A module that failed halfway through is worse than absent: the next `load` would return
        # the broken half from `sys.modules` and the failure would surface somewhere else.
        del sys.modules[name]
        raise
    return module
