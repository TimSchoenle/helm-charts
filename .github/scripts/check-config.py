#!/usr/bin/env python3
"""Validate every chart's rendered configuration against the contracts of the images it pins.

The rendered configuration is the one artifact in this repository that no other gate can see
into. `values.schema.json` describes the chart's *values*, not the application's settings.
kubeconform describes Kubernetes objects, and a ConfigMap holding a stale `config.toml` is a
perfectly valid ConfigMap. `helm lint`, kube-linter, `helm unittest` and the install test all
agree, and `serde` ignores an unknown key by design — so on the day the application renames
`isr.ttl_secs`, every gate passes, the pod starts, reports healthy, and runs on a compiled
default nobody chose. The same hole covers the `PORTFOLIO_*` variables a chart emits and the
key-named files it mounts.

This is the entry point: the loop, the wiring and the exit status. Everything it composes lives
next to it, one concern per module, so each piece is testable by calling it:

  config_contract.py         the published contract — envelope, union, classification, values
  config_declaration.py      `config-contract.yaml`, image resolution, the staleness interlock
  config_manifests.py        navigating what `just render` produced
  config_gate_document.py    gate 1, against the union of every contract reading the document
  config_gate_container.py   gates 2 and 3, against the one image each container runs
  config_coverage.py         the chart-level check that nothing opted out by forgetting
  config_report.py           collecting findings and rendering them

**The scopes of the gates differ, and the difference is the point.** Gate 1 validates a file
every binary reads, so it runs against the union of their contracts — a key belonging to one
would otherwise be "unknown" to the schema of another, and a correct deployment rejected. Gates 2
and 3 are about one container, which runs exactly one image, so they run against that image's own
contract. A variable set on a sidecar that only the main image reads passes against the union and
is exactly the defect gate 2 exists to catch.

Which contract belongs to which container is derived, not declared: every declared image resolves
to a digest, every rendered container names one, and the two are matched. A hand-written mapping
would duplicate a fact the manifest already states, and could drift from it.

Every gate runs before anything exits, matching what `just render` and kubeconform already do:
one broken chart must not hide the state of the rest.

Gate 4 — running the pinned image with `--check-config` — is deliberately not here. It costs a
container pull per component and belongs beside the e2e install rather than in the hot path.

Usage: python3 .github/scripts/check-config.py rendered
       python3 .github/scripts/check-config.py --coverage-only
       JV_BIN=/usr/local/bin/jv python3 .github/scripts/check-config.py rendered
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc  # noqa: E402
from config_coverage import check_coverage  # noqa: E402
from config_declaration import (  # noqa: E402
    Binding,
    Consumer,
    Declaration,
    DeclarationError,
    Document,
    bind,
    load_declaration,
)
from config_gate_container import ServiceLinkGate, check_container  # noqa: E402
from config_gate_document import DocumentGate  # noqa: E402
from config_manifests import (  # noqa: E402
    containers_of,
    digest_of,
    load_manifests,
    pod_spec,
    select,
)
from config_report import Report  # noqa: E402

CHARTS_DIR = Path("charts")
FIRST_PARTY = Path(".github/configs/first-party-images.txt")


class Runner:
    """Walk the charts, and hand each rendered pair to the gates."""

    def __init__(self, charts: Path, rendered: Path, jv: str, report: Report):
        self.charts = charts
        self.rendered = rendered
        self.report = report
        self.document_gate = DocumentGate(jv)
        self.service_links = ServiceLinkGate()

    def run(self) -> int:
        """Check every chart that declares a contract; returns how many were checked."""
        checked = 0
        for chart_dir in sorted(self.charts.iterdir()):
            if not (chart_dir / "Chart.yaml").is_file():
                continue
            declaration = load_declaration(chart_dir)
            if declaration is None or not declaration.documents:
                continue
            self.check_chart(chart_dir, declaration)
            checked += 1
        return checked

    def check_chart(self, chart_dir: Path, declaration: Declaration) -> None:
        values = _read_yaml(chart_dir / "values.yaml")
        app_version = _read_yaml(chart_dir / "Chart.yaml").get("appVersion")

        for document in declaration.documents:
            where = f"{declaration.chart}: {document.name}"

            binding, problems = bind(chart_dir, document, values, app_version)
            for problem in problems:
                self.report.fail(where, problem)
            if binding is None:
                continue

            for rendered in sorted(self.rendered.glob(f"{declaration.chart}--*.yaml")):
                self.check_pair(rendered, document, binding)

    def check_pair(self, rendered: Path, document: Document, binding: Binding) -> None:
        where = f"{rendered.name}: {document.name}"
        relaxed = document.relaxed(rendered.name.split("--", 1)[1])
        manifests = load_manifests(rendered)

        self.report.extend(
            where, self.document_gate.check(manifests, document.source, binding.union, relaxed)
        )

        for consumer in document.consumers:
            matched = select(manifests, consumer.kind, consumer.selector)
            if len(matched) != 1:
                self.report.fail(
                    where,
                    f"the consumer selector {json.dumps(consumer.selector)} matches "
                    f"{len(matched)} {consumer.kind}s, and a consumer must name exactly one",
                )
                continue

            spec = pod_spec(matched[0])
            if "env" not in relaxed:
                self.report.extend(where, self.service_links.check(spec, binding.union))
            self.check_containers(where, manifests, spec, consumer, binding, relaxed)

    def check_containers(
        self,
        where: str,
        manifests: list[dict[str, Any]],
        spec: dict[str, Any],
        consumer: Consumer,
        binding: Binding,
        relaxed: set[str],
    ) -> None:
        present = containers_of(spec)
        for name in consumer.containers:
            container = present.get(name)
            if container is None:
                self.report.fail(
                    where,
                    f"the {consumer.kind} has no container {name!r} "
                    f"(it has {', '.join(sorted(present)) or 'none'})",
                )
                continue

            # The container's own image decides which contract gates 2 and 3 read, so a values
            # file that overrode the image would otherwise have them validated against a contract
            # describing a different binary — silently.
            digest = digest_of(str(container.get("image") or ""))
            mine = binding.by_digest.get(digest or "")
            if mine is None:
                self.report.fail(
                    where,
                    f"container {name!r} runs {container.get('image')!r}, which is not one of the "
                    "images this document declares, so no contract describes what it reads",
                )
                continue

            self.report.extend(where, check_container(manifests, spec, container, mine, relaxed))


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def find_jv() -> str:
    """The pinned JSON Schema binary, or a message saying how to get one.

    Installed by pinned release URL exactly as `kubeconform` already is. A recipe that needs an
    external binary and cannot find it fails saying so rather than skipping the check.
    """
    candidate = os.environ.get("JV_BIN") or shutil.which("jv")
    if not candidate:
        raise DeclarationError(
            "jv is not on PATH. It is the pinned JSON Schema engine gate 1 delegates to; install "
            "it from https://github.com/santhosh-tekuri/jsonschema/releases or set JV_BIN to its "
            "path."
        )
    return candidate


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Validate rendered configuration against image contracts"
    )
    parser.add_argument(
        "rendered", nargs="?", default="rendered", help="directory `just render` wrote into"
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="only check that every chart pinning a first-party image declares a contract",
    )
    args = parser.parse_args(argv)

    charts = Path(args.charts)
    report = Report()

    try:
        if args.coverage_only:
            check_coverage(charts, FIRST_PARTY, report)
        else:
            rendered = Path(args.rendered)
            if not rendered.is_dir():
                raise DeclarationError(
                    f"{rendered}: no rendered manifests; run `just render {rendered}` first"
                )
            if Runner(charts, rendered, find_jv(), report).run() == 0:
                print("==> no chart declares a configuration contract; nothing to validate")
    except (DeclarationError, cc.ContractError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    report.print(sys.stdout, sys.stderr)
    report.summary()

    if report.errors:
        print(f"\n{len(report.errors)} configuration contract violation(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
