#!/usr/bin/env python3
"""Extract the rule groups from a rendered chart, in the form promtool evaluates.

`just test-rules` checks two forms of every chart's Prometheus rules: the raw files as committed,
which still carry the scope placeholder, and the `PrometheusRule` a cluster actually receives, in
which that placeholder has been replaced by a real `namespace="..."` matcher. The second form only
exists inside a rendered manifest, so it has to be lifted back out into a bare `groups:` document
before promtool will take it.

Exactly one `PrometheusRule` is required. Zero means the chart did not render its rules at all --
almost always because the values that switch them on were not supplied -- and silently evaluating
nothing is the failure mode the whole gate exists to catch.

Usage: extract-prometheus-rules.py --manifest rendered.yaml --output rendered.rules.yml \
           [--render-values charts/<chart>/rules-tests/render-values.yaml]
"""

from __future__ import annotations

import argparse
import sys

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        required=True,
        help="rendered chart manifest to read the PrometheusRule from",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="path to write the bare `groups:` document to",
    )
    parser.add_argument(
        "--render-values",
        default="",
        help="values file the chart was rendered with, named in the error when no rule is found",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    with open(args.manifest, encoding="utf-8") as handle:
        documents = [doc for doc in yaml.safe_load_all(handle) if doc]

    rules = [doc for doc in documents if doc.get("kind") == "PrometheusRule"]
    if len(rules) != 1:
        values = args.render_values or "the chart's rules-tests/render-values.yaml"
        print(
            f"error: expected exactly one PrometheusRule in the render, found {len(rules)}. "
            f"Add or correct {values}, which supplies the values this chart needs in order to "
            f"render its rules — typically whatever switches the PrometheusRule on, plus any "
            f"credentials the chart's validator refuses to render without.",
            file=sys.stderr,
        )
        return 1

    with open(args.output, "w", encoding="utf-8") as handle:
        yaml.safe_dump(
            {"groups": rules[0]["spec"]["groups"]},
            handle,
            sort_keys=False,
            width=10**6,
            allow_unicode=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
