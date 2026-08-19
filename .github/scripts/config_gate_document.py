#!/usr/bin/env python3
"""Gate 1 — the rendered document, against the union of every contract that reads it.

Select the object by kind and label selector, take `data[key]`, parse it, and validate the result
against the merged `json_schema` with `additionalProperties: false`. That catches an unknown key,
a wrong type, a missing required key, a value outside an enum, and a table where a scalar belongs
— the whole class of defect that renders cleanly, passes `values.schema.json`, passes kubeconform
and then boots on a compiled default nobody chose.

The union, not one image's contract: `tankovault` renders one `config.toml` read by eight
binaries, each of whose contracts covers only the keys it consumes, so validating against one
with `additionalProperties: false` would call the other seven binaries' keys unknown. Gates 2 and
3 are the opposite case and live in `config_gate_container.py`.

Pure JSON Schema is delegated to a pinned binary rather than reimplemented. `jv` is a single Go
binary installed exactly the way `kubeconform` already is, which keeps the scripts in this
directory stdlib + PyYAML and keeps a `pip install` out of a recipe — the difference, on a Git
Bash shell on Windows, between a gate that runs locally and one that does not.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml

import config_contract as cc
from config_declaration import Source
from config_manifests import names_of, select
from config_report import Finding, error

# `additional properties 'a', 'b' not allowed`, as `jv` phrases it.
JV_ADDITIONAL = re.compile(r"additional propert(?:y|ies) (.+) not allowed")
JV_QUOTED = re.compile(r"'([^']*)'")


class DocumentGate:
    """Validate one rendered configuration document against the union of its contracts."""

    def __init__(self, jv: str):
        self.jv = jv

    def check(
        self,
        manifests: list[dict[str, Any]],
        source: Source,
        union: cc.Union,
        relaxed: set[str],
    ) -> list[Finding]:
        if "document" in relaxed:
            return []

        matched = select(manifests, source.kind, source.selector)
        if len(matched) != 1:
            found = names_of(matched)
            return [
                error(
                    f"the selector {json.dumps(source.selector)} matches {len(matched)} "
                    f"{source.kind}s{f' ({found})' if found else ''}, and a document must name "
                    "exactly one"
                )
            ]

        data = matched[0].get("data") or {}
        if source.key not in data:
            name = (matched[0].get("metadata") or {}).get("name")
            return [
                error(
                    f"{source.kind} {name!r} has no key {source.key!r}"
                    + cc.suggest(source.key, data)
                )
            ]

        try:
            instance = parse_document(data[source.key], source.format)
        except Exception as failure:  # noqa: BLE001 — every parser raises its own type
            return [error(f"{source.key}: is not valid {source.format}: {failure}")]

        schema = cc.strip_internals(union.json_schema)
        if "closed" in relaxed:
            schema = open_schema(schema)

        findings = []
        for location, message in self._validate(schema, instance):
            findings.extend(self._describe(source, union, location, message))
        return findings

    def _describe(
        self, source: Source, union: cc.Union, location: str, message: str
    ) -> list[Finding]:
        path = dotted(location)
        additional = JV_ADDITIONAL.search(message)
        if not additional:
            return [error(f"{source.key}: {path or '(root)'}: {message}")]

        # An unknown key is the rename case, and the union already holds every key path — so the
        # answer to "what was it called instead" costs a Levenshtein pass and turns the most
        # common failure here from a puzzle into a one-line answer.
        findings = []
        for name in JV_QUOTED.findall(additional.group(1)):
            full = f"{path}.{name}" if path else name
            findings.append(
                error(f"{source.key}: {full}: no such key" + cc.suggest(full, union.keys))
            )
        return findings

    def _validate(self, schema: dict[str, Any], instance: Any) -> list[tuple[str, str]]:
        """Run `jv` over one instance, as `(instance path, message)` pairs."""
        with tempfile.TemporaryDirectory() as work:
            schema_path = Path(work) / "schema.json"
            instance_path = Path(work) / "instance.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            instance_path.write_text(json.dumps(instance), encoding="utf-8")

            result = subprocess.run(
                [
                    self.jv,
                    "--output",
                    "detailed",
                    "--draft",
                    "7",
                    str(schema_path),
                    str(instance_path),
                ],
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            return []

        start = result.stdout.find("{")
        if start < 0:
            detail = (result.stderr or result.stdout).strip()
            return [("", f"the validator could not run: {detail}")]

        try:
            output = json.loads(result.stdout[start:])
        except json.JSONDecodeError:
            return [("", f"the validator produced unreadable output: {result.stdout.strip()}")]

        return flatten(output)


def parse_document(text: str, form: str) -> Any:
    if form == "toml":
        return tomllib.loads(text)
    if form == "json":
        return json.loads(text)
    return yaml.safe_load(text)


def open_schema(schema: Any) -> Any:
    """Drop every `additionalProperties: false` the union added, for a `closed` exemption.

    The one case that needs it is a chart whose values append verbatim text to the rendered
    document — `configExtraToml` and its equivalents — which the chart never parses, so keys it
    introduces are invisible to the renderer that would have to declare them.
    """
    if isinstance(schema, dict):
        return {
            name: open_schema(value)
            for name, value in schema.items()
            if not (name == "additionalProperties" and value is False)
        }
    if isinstance(schema, list):
        return [open_schema(value) for value in schema]
    return schema


def flatten(node: Any) -> list[tuple[str, str]]:
    """Take the leaves of `jv`'s error tree; the branches only restate their children."""
    if not isinstance(node, dict):
        return []
    children: list[tuple[str, str]] = []
    for child in node.get("errors") or []:
        children.extend(flatten(child))
    if children:
        return children
    message = node.get("error")
    if not message:
        return []
    return [(str(node.get("instanceLocation") or ""), str(message))]


def dotted(location: str) -> str:
    return ".".join(part for part in location.split("/") if part)
