#!/usr/bin/env python3
"""Generating a chart's configuration surface from the contract its image publishes.

Every other script in this group *reads* a chart and holds it against a contract. This one runs in
the other direction: given a contract, it writes the chart values, the template helpers, the
declaration and the round-trip enrolment that satisfy it — so a new chart starts life bound to
its image rather than acquiring the binding one gate failure at a time.

`Surface` is the seam that makes this usable for a chart with no contract at all. Every renderer
below takes one and branches on whether it carries a `Union`, so an image that publishes nothing
gets the chassis and the workload and no assertion about its configuration — which is what
`teamspeak` and `paperless-ngx` are. See that class for what the plain form deliberately omits.

That direction is available because the contract already carries everything the mapping needs.
Per key it states the configuration path, a JSON Schema constraint, the documentation the
producer wrote, the default the binary compiles in, whether the key is required, whether it is a
credential and whether a file may supply it. What a person adds by hand is the *judgement* — that
a particular setting deserves a first-class value rather than living in the `config` escape
hatch, that a credential belongs in a file rather than in the ConfigMap — and this module states
each of those judgements as a rule rather than leaving them to be made once per chart.

--------------------------------------------------------------------------------------------
Why generating `# @config` markers here is not the thing `config_bindings.py` refuses
--------------------------------------------------------------------------------------------

`config_bindings.py` says, in bold, that nothing generates a marker: they are hand-written,
because a generator would have to land in the same change as the format it depends on, and a
generated marker on an existing chart asserts a mapping the reviewer did not write and cannot
check against anything.

Neither objection reaches this module, and the difference is which artefact is derived. On an
existing chart the *value* is the fact and the marker is a claim about it — a generator would be
guessing which of `telemetry.logLevel` and `logging.level` feeds `telemetry.log_level`, and a
wrong guess reads exactly like a right one. Here the value does not exist yet: the key is the
fact, and the value and its marker are emitted together from that one key by one rule. There is
no prior mapping for the marker to be wrong about, because the marker *is* the mapping and the
value was named to match it.

The gate is unchanged either way. `just check-config-bindings` holds what comes out of here
against the same contract, so a scaffold whose rules are wrong fails on the chart's first run and
not in production.

--------------------------------------------------------------------------------------------
Where each contract key goes
--------------------------------------------------------------------------------------------

One rule per row, applied in order; the first that matches wins.

| The key is...                     | Destination                        | Why                     |
|-----------------------------------|------------------------------------|-------------------------|
| `reserved`                        | nothing, plus an `unbound` entry   | the loader owns it      |
| `secret`, and file-supplyable     | `secretData`, plus `unbound`       | never a ConfigMap       |
| `secret`, not file-supplyable     | nothing, plus an `unbound` entry   | no safe channel exists  |
| `structured`                      | a chart value typed by its own     | the tree is the value   |
|                                   | constraint, or an open `object`    |                         |
| anything else                     | a chart value, typed and defaulted | the ordinary case       |

The credential rule is the one worth arguing with, so it is written down rather than assumed. A
`secret: true` key is delivered as a *file* and never written into the rendered document, because
a ConfigMap is readable by anything that can read the namespace and a bearer credential in one is
a credential that has leaked. This repository's own charts do not all follow that rule —
`netcup-offer-bot` projects `telemetry.sentry_dsn` into `config.toml` while keeping
`discord.webhook_url` in a file, which is a judgement about how much a particular credential is
worth. A generator has no way to make that judgement and the conservative direction is the only
defensible default: moving one key from the Secret into the ConfigMap afterwards is a deliberate
edit somebody signs, where the opposite mistake is silent.

A key that is `secret` and *not* file-supplyable has no channel at all — `file_supplyable` is
false for everything but `text`, so a secret integer could only be delivered through the document
or the environment, and both are refused for a credential. It is written off with a reason saying
exactly that, rather than quietly projected.

--------------------------------------------------------------------------------------------
What is deliberately not generated
--------------------------------------------------------------------------------------------

**A Service, an Ingress, a HTTPRoute, a PodMonitor or a PersistentVolumeClaim.** Whether the
workload listens, on what, and whether anything should reach it is not in the contract — a
`server.port` key says the binary can bind a port, not that this deployment wants a Service in
front of it. Scaffolding one would put a template in the chart that nobody chose and that the
first `just render` would have to be read to discover.

**Prose.** The generated `README.md.gotmpl` carries section headings and a comment saying so. A
chart description written by a generator is a description nobody has read, and helm-docs will
happily render it into the published README.

**`ci/` fixtures beyond the one.** `ct` needs a values file that installs; the interesting ones —
a fixture with the network policy on, one with the ingress off — are per chart and per decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import config_bindings as cb
import config_contract as cc
import config_shapes as cs
import config_testgen as tg

# The marker vocabulary, taken from the parser rather than spelt again. A scaffold writing
# `@config` while the gate looked for something else would produce a chart that fails the moment
# it is created, which is the one failure mode a scaffold cannot be allowed to have.
MARKER = cb.MARKER

# The only document format this scaffold can write. `common` ships a TOML renderer and nothing
# else, so a chart whose image reads YAML or JSON would need a helper the library does not have —
# and inventing one inside a generated `_helpers.tpl` puts an untested renderer in every chart
# that uses it. Refused with a message naming the gap instead.
FORMAT = "toml"

# Where a generated file goes, relative to the chart directory.
DECLARATION = "config-contract.yaml"
ENROLMENT = "contract-tests.yaml"
CONTRACTS = "contracts"

# The `values.yaml` subtree the raw escape hatch lives under, matching what `config_testgen`
# writes its probes into. Held as one name so the two cannot disagree about it.
CONFIG_ROOT = tg.VALUES_ROOT

# Column the `values.yaml` comments wrap at, matching the existing charts.
WIDTH = 96


class ScaffoldError(Exception):
    """A contract, or a chart name, this scaffold cannot honestly turn into a chart."""


# --------------------------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------------------------

_CHART_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def check_chart_name(name: str) -> None:
    """Refuse a name Helm, Kubernetes or this repository's own tooling would reject later.

    Checked here rather than left to `helm lint`, because everything this module writes embeds
    the name — template helper names, the release selector in the declaration, the suite file
    names — and a name that fails at `helm lint` fails after all of it is on disk.
    """
    if not _CHART_NAME.match(name):
        raise ScaffoldError(
            f"{name!r} is not a valid chart name: lower-case alphanumerics and hyphens, starting "
            "and ending with an alphanumeric"
        )


def camel(segment: str) -> str:
    """`check_interval_secs` as `checkIntervalSecs` — one contract path segment as a chart value.

    The convention every chart here already follows, verified against all five that map values
    onto a contract: `feed.check_interval_secs` is `feed.checkIntervalSecs`, `discord.webhook_url`
    is `discord.webhookUrl`, and a segment with no underscore — `metrics.ip`, `metrics.port` — is
    left exactly as it is.
    """
    head, *rest = segment.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def values_path_for(path: str) -> str:
    """The dotted chart value that carries one contract key."""
    return ".".join(camel(segment) for segment in path.split("."))


def env_prefix(union: cc.Union) -> str:
    """The dialect prefix as `common.fileConfig.env` wants it: without the trailing separator.

    The contract states `NETCUP_OFFER_BOT_` because that is what it prepends to a variable name;
    the library partial appends the separator itself, so passing the contract's spelling through
    would produce `NETCUP_OFFER_BOT__CONFIG`.
    """
    return union.prefix.rstrip("_")


# --------------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------------

PROJECTED = "projected"
SECRET_FILE = "secret-file"
WRITTEN_OFF = "written-off"


@dataclass(frozen=True)
class Placement:
    """One contract key, and what the scaffold does with it.

    `values_path` is set for everything but a write-off — a credential still gets a chart value,
    it simply carries into the Secret rather than into the document.
    """

    key: dict[str, Any]
    where: str
    values_path: str
    reason: str = ""

    @property
    def path(self) -> str:
        return str(self.key["path"])

    @property
    def marker_class(self) -> str:
        return cb.STRUCTURED if self.key.get("text_form") == "structured" else cb.PROJECTION

    @property
    def optional(self) -> bool:
        return not self.key.get("required")


@dataclass
class Plan:
    """Every key of one document, sorted into where the generated chart puts it."""

    projected: list[Placement] = field(default_factory=list)
    secrets: list[Placement] = field(default_factory=list)
    written_off: list[Placement] = field(default_factory=list)

    @property
    def all(self) -> list[Placement]:
        return self.projected + self.secrets + self.written_off

    @property
    def required_projected(self) -> list[Placement]:
        """Keys the image demands and the chart must therefore render a value for."""
        return [item for item in self.projected if not item.optional]


@dataclass(frozen=True)
class Surface:
    """A chart's configuration surface, and where the scaffold learnt it.

    The seam that lets this module scaffold a chart for an image that publishes no contract. Two
    shapes exist and every renderer below branches on exactly one question — whether `union` is
    set — rather than on a flag threaded through from the caller:

    **Contracted.** `union` is the merged contract and `plan` sorts its keys, so `values.yaml`
    carries a block per setting with the marker that binds it, `_helpers.tpl` projects them into
    the document, and the chart is enrolled in `check-config`, `check-config-bindings` and the
    generated round trip.

    **Plain.** No contract exists to read, so the scaffold asserts nothing about the image's
    configuration: no `config` escape hatch, no `configMount`, no ConfigMap and no Secret. What is
    left is everything that was never about the contract — the chassis, the workload, the network
    policy, the service account — which is two thirds of every chart here and all of what
    `teamspeak` and `paperless-ngx` are made of.

    The plain form is deliberately *less* than the contracted one rather than the same thing with
    empty parts. A `configMount` in a chart whose image does not read a mounted file is a value an
    operator can set and nothing honours; an empty `derivedConfig` is a helper that looks like a
    mapping and maps nothing. Both would be scaffolding somebody has to notice and delete.
    """

    union: cc.Union | None = None
    plan: Plan = field(default_factory=Plan)

    @property
    def contracted(self) -> bool:
        return self.union is not None

    @property
    def form(self) -> str:
        """The template subdirectory this surface's chart is built from."""
        return "contract" if self.contracted else "plain"

    @property
    def prefix(self) -> str:
        """The environment prefix the image's loader owns, or an empty string when unknown."""
        return self.union.prefix if self.union is not None else ""


def from_contract(union: cc.Union) -> Surface:
    """The contracted surface: every key of one document, sorted."""
    return Surface(union=union, plan=plan_keys(union))


def plain() -> Surface:
    """The surface of a chart whose image publishes no contract."""
    return Surface()


def plan_keys(union: cc.Union) -> Plan:
    """Sort one document's keys by the table in this module's docstring."""
    plan = Plan()
    for path in sorted(union.keys):
        key = union.keys[path]
        values_path = values_path_for(path)

        if key.get("reserved"):
            plan.written_off.append(
                Placement(
                    key,
                    WRITTEN_OFF,
                    "",
                    "Reserved by the loader: the image sets it itself, so a chart value feeding "
                    "it would be overwritten at boot and would read to an operator as a setting "
                    "that does nothing.",
                )
            )
            continue

        if key.get("secret"):
            if cc.file_supplyable(key):
                plan.secrets.append(Placement(key, SECRET_FILE, values_path))
            else:
                plan.written_off.append(
                    Placement(
                        key,
                        WRITTEN_OFF,
                        "",
                        f"A credential whose `text_form` is {key.get('text_form')!r}, so no file "
                        "can supply it — only the configuration document or the environment can, "
                        "and a bearer credential in a ConfigMap is readable by anything that can "
                        "read the namespace. Surfacing it would mean choosing one of those two, "
                        "which is a decision for whoever knows what this credential is worth.",
                    )
                )
            continue

        plan.projected.append(Placement(key, PROJECTED, values_path))

    return plan


# --------------------------------------------------------------------------------------------
# Rendering `values.yaml`
# --------------------------------------------------------------------------------------------


def wrap(text: str, prefix: str, width: int = WIDTH) -> list[str]:
    """Comment lines, wrapped, each carrying `prefix`. Paragraph breaks are collapsed.

    A `docs` block is Rust doc-comment prose and routinely runs to several paragraphs; a values
    description is one paragraph by helm-docs' convention, so the paragraphs are joined rather
    than reproduced. The full text stays available through `just explain`, which is the command
    for reading a contract.
    """
    words = " ".join(text.split()).split()
    lines: list[str] = []
    current = prefix.rstrip()
    for word in words:
        candidate = f"{current} {word}" if current.strip() != prefix.strip() else f"{prefix}{word}"
        if len(candidate) > width and current.strip() != prefix.strip():
            lines.append(current)
            current = f"{prefix}{word}"
        else:
            current = candidate
    if current.strip() != prefix.strip():
        lines.append(current)
    return lines


def summary_of(key: dict[str, Any]) -> str:
    """The first paragraph of a key's documentation, or a sentence naming the key.

    Never empty: helm-docs renders `# --` descriptions into the published values table, and a
    blank row there is worse than a plain one — it reads as a value nobody thought about.
    """
    docs = key.get("docs")
    if isinstance(docs, str) and docs.strip():
        return " ".join(docs.split("\n\n")[0].split())
    return f"The `{key['path']}` setting."


def schema_lines(key: dict[str, Any], indent: str) -> list[str]:
    """The `@schema` block for one contract key, from its constraint.

    A thin call into `config_shapes`, which is where the rules and the measurements behind them
    live. Two generators write these blocks — this one for a chart that does not exist yet, and
    `config_shapes.py` for one that does — and a scaffold whose idea of a block differed from the
    gate's would produce a chart that fails the moment it is created.
    """
    return cs.render(
        cs.expected(
            key.get("constraint"),
            optional=not key.get("required"),
            structured=key.get("text_form") == "structured",
        ),
        indent,
    )


def default_for(key: dict[str, Any]) -> Any:
    """The value the generated chart writes for one key.

    Three cases, and only the first is a real answer:

    **The image publishes a default.** `default_value` is that default already parsed, which is
    exactly what a values file wants. Written through, so the chart's default *is* the binary's
    and the two cannot drift.

    **Optional, with no default.** `null`, and the derived helper wraps the key in `with` — so
    nothing is written into the document and the binary falls back to its compiled default. That
    is what "unset" has to mean; writing an empty string instead would *supply* the key.

    **Required, with no default.** The image demands a value and names none, so the chart has to
    invent one and every invented value is wrong. What it must not be is invalid: a required
    integer bounded at `minimum: 1` cannot be written as `0`, because the chart would then fail
    `just check-config` on its first run for a reason that reads like a bug in the gate. So the
    value is drawn from `config_testgen`'s candidate walk — the same constraint-satisfying search
    the round-trip probes use — and every key that needed one is listed in the scaffolder's
    closing output as something to replace with a value somebody chose.
    """
    if key.get("default_value") is not None:
        return key["default_value"]

    if not key.get("required"):
        return None

    if key.get("text_form") == "structured":
        # Empty, in the shape the constraint names. An empty table where the constraint says
        # `array` is the same defect `schema_lines` used to have from the other side: the chart
        # would be written with a default its own `values.schema.json` rejects.
        declared = (key.get("constraint") or {}).get("type")
        if isinstance(declared, list):
            declared = declared[0] if declared else None
        return [] if str(declared) == "array" else {}

    candidate = tg.satisfying(key)
    if candidate is not None:
        return candidate

    constraint = key.get("constraint") or {}
    declared = constraint.get("type")
    if isinstance(declared, list):
        declared = declared[0] if declared else None
    return {"integer": 0, "number": 0, "boolean": False, "array": [], "object": {}}.get(
        str(declared), ""
    )


def invented(plan: Plan) -> list[Placement]:
    """The surfaced keys whose value the scaffold chose rather than read from the contract.

    Reported rather than hidden. A required key with no published default is the one place this
    generator has to guess, and a guess nobody was told about is the same defect as a missing key.
    """
    return [
        item
        for item in plan.projected
        if not item.optional and item.key.get("default_value") is None
    ]


def undescribed(plan: Plan) -> list[str]:
    """The grouping blocks whose `# --` description is a placeholder, as values paths.

    The companion to `invented`, and reported for the same reason: `branch_placeholder` writes a
    `TODO` because a contract says nothing about the blocks a chart groups its keys into, and a
    placeholder nobody was told about is one helm-docs publishes into the README table.
    """
    surfaced = plan.projected + plan.secrets
    if not surfaced:
        return []

    found: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name in sorted(node):
            child = node[name]
            if isinstance(child, Placement):
                continue
            path = f"{prefix}.{name}" if prefix else name
            found.append(path)
            walk(child, path)

    walk(tree_of(surfaced), "")
    return found


def values_scalar(value: Any) -> str:
    """One default as it is written into `values.yaml`."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[]" if not value else "[" + ", ".join(values_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{}"
    return cs.quoted(str(value))


def marker_line(placement: Placement, indent: str) -> str:
    """The one line that binds a chart value to a contract key.

    Written inside the `@schema` block, which is why it reads as a comment within a comment: the
    text between the delimiters is the schema and is parsed as YAML, so a YAML comment there is
    discarded by the only parser that reads it. `config_bindings.py` records why that is the one
    position both generators ignore.
    """
    suffix = " optional" if placement.optional else ""
    return f"{indent}# # {MARKER} {placement.marker_class} {placement.path}{suffix}"


def described(text: str, indent: str) -> list[str]:
    """One helm-docs description: `# --` on the first line, `#` on the rest.

    The marker belongs to the description as a whole and helm-docs reads it that way — repeating
    it on every wrapped line puts a literal `--` in the middle of the rendered sentence.
    """
    wrapped = wrap(text, "", WIDTH - len(indent) - 5)
    return [f"{indent}# -- {wrapped[0]}"] + [f"{indent}# {line}" for line in wrapped[1:]]


def description_lines(placement: Placement, indent: str) -> list[str]:
    """The helm-docs description for one contract key's value."""
    key = placement.key
    summary = summary_of(key).rstrip(".")
    text = f"{summary} (`{key['path']}`)."
    if placement.where == SECRET_FILE:
        text += f" Delivered as the secrets-directory file `{_file_name(placement)}`."

    return described(text, indent)


def value_block(placement: Placement, indent: str = "") -> list[str]:
    """One contract key as the `values.yaml` block that feeds it.

    No `@config-shape` marker is written, and none is needed: a block belonging to a value that
    carries a `# @config` marker is generated from the contract by definition, so the block this
    function emits is already owned by `just config-shapes` and stays in step with the image on
    its own. The marker that used to enrol it was retired when generation became the default —
    see `config_shapes.py`.

    A credential gets no `# @config` marker either, for the reason below.
    """
    leaf = placement.values_path.split(".")[-1]

    lines: list[str] = [f"{indent}# @schema"]
    # A credential gets no marker, and the two facts that make that right are worth keeping
    # together: every marker class names a way a value reaches the *configuration document*, and a
    # credential delivered as a file never reaches it. `check-config-bindings` says the same thing
    # from the other side — it refuses a key that is both bound by a marker and written off in
    # `unbound`, which a marker here would make every credential.
    if placement.where != SECRET_FILE:
        lines.append(marker_line(placement, indent))
    lines.extend(schema_lines(placement.key, indent))
    lines.append(f"{indent}# @schema")
    lines.extend(description_lines(placement, indent))

    value = default_for(placement.key) if placement.where != SECRET_FILE else ""
    if isinstance(value, dict) and not value:
        lines.append(f"{indent}{leaf}: {{}}")
    else:
        lines.append(f"{indent}{leaf}: {values_scalar(value)}")
    return lines


def tree_of(placements: list[Placement]) -> dict[str, Any]:
    """Group placements into the nested shape their chart values have.

    A leaf is a `Placement`; a branch is a dict. Sorted at every level, so the generated file is a
    property of the contract rather than of the order the keys happened to arrive in.
    """
    tree: dict[str, Any] = {}
    for placement in placements:
        segments = placement.values_path.split(".")
        node = tree
        for segment in segments[:-1]:
            existing = node.get(segment)
            if isinstance(existing, Placement):
                raise ScaffoldError(
                    f"the contract key {placement.path!r} needs the chart value "
                    f"{'.'.join(segments)!r}, but {segment!r} is already a leaf value on the way "
                    "there; the two cannot both exist and this mapping has to be written by hand"
                )
            node = node.setdefault(segment, {})
        node[segments[-1]] = placement
    return tree


def render_tree(tree: dict[str, Any], indent: str = "", depth: int = 0) -> list[str]:
    """The values blocks for one level of the tree, deepest structure last."""
    lines: list[str] = []
    for name in sorted(tree):
        node = tree[name]
        if lines:
            lines.append("")
        if isinstance(node, Placement):
            lines.extend(value_block(node, indent))
        else:
            lines.append(f"{indent}# @schema")
            lines.append(f"{indent}# additionalProperties: true")
            lines.append(f"{indent}# @schema")
            lines.extend(described(branch_placeholder(node, depth + 1), indent))
            lines.append(f"{indent}{name}:")
            lines.extend(render_tree(node, indent + "  ", depth + 1))
    return lines


def branch_placeholder(node: dict[str, Any], depth: int) -> str:
    """The description a grouping block is scaffolded with, naming the prefix it holds.

    A contract describes keys, not the blocks a chart groups them into, so there is nothing here
    to derive a sentence from and no honest way to invent one. `just check-values-docs` requires
    one all the same — helm-docs gives a documented group its own README row, and helm-schema
    copies the text into the property an editor shows on hover — so the scaffold writes a `TODO`
    in the same spelling `Chart.yaml`'s description placeholder uses, and `new-chart.py` lists
    every one of them in its closing output rather than leaving them to a gate.

    Without this, a freshly scaffolded chart failed `just check-values-docs` with one entry per
    grouping block — sixteen for `discord-alertmanager`, fourteen from its contract plus `image`
    and `configMount` — which is what the gate reported before anybody had read a line of it.
    """
    return (
        f"TODO: what the `{branch_prefix(node, depth)}` settings have in common, in one sentence."
    )


def branch_prefix(node: dict[str, Any], depth: int) -> str:
    """The contract path prefix one grouping block holds.

    Read off a key underneath it rather than off the values path: `values_path_for` camel-cases
    each segment independently, so the two paths have the same segment count and the contract
    spelling — which is what the description should name — is only recoverable from a key.
    """
    placement = _any_placement(node)
    if placement is None:
        # `tree_of` only ever creates a branch on the way to a leaf, so this is unreachable — but
        # a branch holding nothing would render a mapping with no values and no prefix to name.
        raise ScaffoldError("a values branch was built with no key underneath it")
    return ".".join(placement.path.split(".")[:depth])


def _any_placement(node: dict[str, Any]) -> Placement | None:
    """Any placement under this branch. They all share its prefix, so any of them will do."""
    for name in sorted(node):
        child = node[name]
        if isinstance(child, Placement):
            return child
        found = _any_placement(child)
        if found is not None:
            return found
    return None


def render_values(
    chart: str,
    surface: Surface,
    repository: str,
    tag: str,
    chassis: str,
    document_key: str,
) -> str:
    """The whole `values.yaml`: the image, the configuration surface, the chassis.

    Ordered by how often it is edited rather than alphabetically. The image and the settings the
    contract declares are what an operator opens the file for; the chassis is thirty blocks that
    are the same in every chart and belongs after them, exactly where every existing chart already
    puts it.

    A plain surface stops after the image. Everything between it and the chassis — the settings,
    `existingSecret`, `config`, `configExtraToml`, `configMount` — describes a loader this chart's
    image is not known to have, and a value nothing honours is worse than an absent one.
    """
    plan = surface.plan
    prefix = surface.prefix
    lines = [
        "# vim: set ft=yaml:",
        "# yaml-language-server: $schema=values.schema.json",
        "",
        "# @schema",
        "# additionalProperties: true",
        "# @schema",
        "# -- Container image the pod runs, composed as `registry/repository:tag`.",
        "image:",
        "  # @schema",
        "  # type: string",
        "  # @schema",
        "  # -- Registry host. Empty means Docker Hub.",
        '  registry: ""',
        "",
        "  # @schema",
        "  # type: string",
        "  # @schema",
        "  # -- The container image repository.",
        f"  repository: {repository}",
        "",
        "  # @schema",
        "  # type: string",
        "  # @schema",
        "  # -- The container image tag. Defaults to the chart's `appVersion` when empty.",
        # Always quoted. A tag like `8.0` is a YAML float unquoted, and the chart's own schema
        # types this as a string — so an unquoted numeric tag fails `helm template` on the
        # scaffold's first render, for a reason that reads like a bug in the schema.
        f"  tag: {cs.quoted(tag)}",
        "",
        "  # @schema",
        '  # enum: ["", Always, IfNotPresent, Never]',
        "  # @schema",
        "  # -- The image pull policy. Empty resolves automatically from the tag/digest.",
        '  pullPolicy: ""',
        "",
        "# @schema",
        "# type: array",
        "# @schema",
        "# -- Optional image pull secrets for private registries",
        "imagePullSecrets: []",
    ]

    if not surface.contracted:
        lines += [
            "",
            "# " + "-" * (WIDTH - 2),
        ]
        lines.extend(
            wrap(
                "This image publishes no configuration contract, so nothing below was derived "
                "from one and nothing here is held against one. Add the values the workload needs "
                "in this section — with an `@schema` block each, which every gate in this "
                "repository expects and `values.schema.json` is generated from.",
                "# ",
            )
        )
        lines += [
            "# " + "-" * (WIDTH - 2),
            "",
        ]
        return "\n".join(lines) + "\n" + chassis.strip() + "\n"

    # One tree over the settings and the credentials together, rather than a section each. They
    # share a namespace — `telemetry.log_level` is ordinary and `telemetry.sentry_dsn` is a
    # credential — so two sections would emit `telemetry:` twice and YAML would silently keep only
    # the second, dropping every value in the first. Which channel carries a value is said in its
    # own description instead, where it stays true however the keys are grouped.
    surfaced = plan.projected + plan.secrets
    if surfaced:
        lines.append("")
        lines.append("# " + "-" * (WIDTH - 2))
        lines.extend(
            wrap(
                f"The settings the image reads. `just explain {chart}` describes each one in full "
                "— its accepted values, its default, and which spelling reaches it.",
                "# ",
            )
        )
        lines.append("#")
        lines.extend(
            wrap(
                "A value marked as delivered through the secrets directory is written into a "
                "Secret rather than into the ConfigMap, because a ConfigMap is readable by "
                "anything that can read the namespace. `existingSecret` below replaces all of "
                "them at once.",
                "# ",
            )
        )
        lines.append("# " + "-" * (WIDTH - 2))
        lines.append("")
        lines.extend(render_tree(tree_of(surfaced)))

    lines.append("")
    lines.append("# @schema")
    lines.append("# type: string")
    lines.append("# @schema")
    lines.extend(
        wrap(
            "Name of an existing Secret carrying the credentials, keeping them out of values.yaml "
            "and out of the Helm release object. **Its keys are configuration paths, not "
            f"free-form names**: `{_example_secret_file(plan)}`, because the file name is what "
            "the loader parses. Set, the chart renders no Secret of its own and the credential "
            "values above are ignored.",
            "# -- ",
        )
    )
    lines.append('existingSecret: ""')

    lines.extend(
        [
            "",
            "# @schema",
            "# type: object",
            "# additionalProperties: true",
            "# @schema",
        ]
    )
    lines.extend(
        wrap(
            f"Extra configuration, expressed as the {FORMAT.upper()} tree the image reads "
            f"(`{_example_key(plan)}`, ...). Merged over everything the chart derives from the "
            "values above, so it can both extend and override them. Rendered into the mounted "
            "ConfigMap — never into the environment, which the loader refuses to combine with a "
            "file.",
            "# -- ",
        )
    )
    lines.append(f"{CONFIG_ROOT}: {{}}")

    lines.extend(
        [
            "",
            "# @schema",
            "# type: string",
            "# @schema",
            "# -- Verbatim TOML appended after the rendered configuration. The escape hatch for",
            "# anything the chart's TOML renderer cannot express, notably arrays of tables.",
            'configExtraToml: ""',
            "",
            "# @schema",
            "# additionalProperties: true",
            "# @schema",
            "# -- Where the rendered configuration document and the credential files are mounted.",
            "configMount:",
            "  # @schema",
            "  # type: string",
            "  # @schema",
            f"  # -- Directory the rendered `{document_key}` is mounted at, passed as",
            f"  # `{prefix}CONFIG`.",
            f"  configDir: /etc/{chart}/config",
            "",
            "  # @schema",
            "  # type: string",
            "  # @schema",
            "  # -- Directory the credential files are mounted at, passed as",
            f"  # `{prefix}SECRETS_DIR`.",
            f"  secretsDir: /etc/{chart}/secrets",
            "",
        ]
    )

    return "\n".join(lines) + "\n" + chassis.strip() + "\n"


def _example_secret_file(plan: Plan) -> str:
    """A real secrets-file name from this contract, for the `existingSecret` documentation."""
    for placement in plan.secrets:
        name = placement.key.get("secrets_file")
        if name:
            return str(name)
    return "a__b"


def _example_key(plan: Plan) -> str:
    """A real configuration path from this contract, for the `config` documentation."""
    return plan.projected[0].path if plan.projected else "section.setting"


# --------------------------------------------------------------------------------------------
# Rendering `templates/_helpers.tpl`
# --------------------------------------------------------------------------------------------


def toml_tree(placements: list[Placement]) -> dict[str, Any]:
    """The contract paths of these placements as a nested tree, leaves being the placements."""
    tree: dict[str, Any] = {}
    for placement in placements:
        segments = placement.path.split(".")
        node = tree
        for segment in segments[:-1]:
            node = node.setdefault(segment, {})
        node[segments[-1]] = placement
    return tree


def _derived_lines(tree: dict[str, Any], indent: str = "") -> list[str]:
    """The YAML the `derivedConfig` helper emits, which `common.configToml` turns into TOML.

    An optional setting is wrapped in `with` rather than written empty, and that distinction is
    the whole reason this helper is generated rather than left to a per-chart hand. To the loader
    an empty string is a *supplied* value: writing `sentry_dsn = ""` configures Sentry with a
    blank DSN, which is not what an operator who left the value unset meant. `with` treats every
    falsey value as absent, so an unset optional key never reaches the document at all.
    """
    lines: list[str] = []
    for name in sorted(tree):
        node = tree[name]
        if not isinstance(node, Placement):
            lines.append(f"{indent}{name}:")
            lines.extend(_derived_lines(node, indent + "  "))
            continue

        reference = f".Values.{node.values_path}"
        structured = node.key.get("text_form") == "structured"
        bare = (node.key.get("constraint") or {}).get("type") in ("integer", "number", "boolean")

        if node.optional:
            lines.append(f"{indent}{{{{- with {reference} }}}}")
            if structured:
                lines.append(f"{indent}{name}:")
                lines.append(f"{indent}  {{{{- toYaml . | nindent {len(indent) + 2} }}}}")
            elif bare:
                lines.append(f"{indent}{name}: {{{{ . }}}}")
            else:
                lines.append(f"{indent}{name}: {{{{ . | quote }}}}")
            lines.append(f"{indent}{{{{- end }}}}")
        elif structured:
            lines.append(f"{indent}{name}:")
            lines.append(f"{indent}  {{{{- toYaml {reference} | nindent {len(indent) + 2} }}}}")
        elif bare:
            lines.append(f"{indent}{name}: {{{{ {reference} }}}}")
        else:
            lines.append(f"{indent}{name}: {{{{ {reference} | quote }}}}")
    return lines


def _comment(*lines: str) -> str:
    """A Go-template comment block, wrapped, in the house style."""
    body: list[str] = []
    for line in lines:
        if not line:
            body.append("")
        else:
            body.extend(wrap(line, "", WIDTH))
    return "{{/*\n" + "\n".join(body) + "\n*/}}\n"


def _file_name(placement: Placement) -> str:
    """The secrets-directory file name one credential is read from.

    Taken from the contract rather than derived from the path: `secrets_file` is what the producer
    published, and a chart that computed its own spelling would be right until the day the
    producer changed a separator.
    """
    name = placement.key.get("secrets_file")
    if not name:
        raise ScaffoldError(
            f"the contract key {placement.path!r} is `secret: true` and declares no "
            "`secrets_file`, so there is no file name to deliver it under"
        )
    return str(name)


def render_helpers(chart: str, surface: Surface) -> str:
    """`templates/_helpers.tpl`: the partials a chart of this shape defines.

    Contracted: `derivedConfig` renders the chart's own values as the tree the image reads,
    `effectiveConfig` merges the operator's raw `config` over it so the escape hatch can both
    extend and override, `configToml` adds the verbatim appendix, `secretData` names each
    credential by the file name the loader parses, `secretKeys` lists those names for the podspec,
    and `validateValues` refuses a render that could only produce a container which fails at boot.

    Plain: none of them. Every one describes a loader nothing here knows this image has, and an
    empty `derivedConfig` is a helper that looks like a mapping and maps nothing. What is emitted
    instead is a comment saying where a hand-written helper goes — a file rather than nothing, so
    the first one is an edit and not a decision about where partials live in this repository.
    """
    if not surface.contracted:
        return (
            _comment(
                f"Template partials for {chart}.",
                "",
                "Empty because this chart's image publishes no configuration contract, so there "
                "was nothing to derive. Anything the templates need to compute — a volume that "
                "resolves three ways, a derived configuration document, a guard that refuses an "
                "impossible render — belongs here rather than inline, so the templates stay "
                "readable and the computation is testable on its own.",
            )
        )

    union = surface.union
    plan = surface.plan
    parts: list[str] = []

    derived = _derived_lines(toml_tree(plan.projected))
    parts.append(
        _comment(
            "The configuration this chart derives from its own first-class values, as the tree "
            "the image reads.",
            "",
            "Optional settings are wrapped in `with` rather than written empty. To the loader an "
            "empty value is a *supplied* value, so writing one would configure the setting blank "
            "rather than leaving the binary on its compiled default — which is what an operator "
            "who set nothing meant.",
        )
        + f'{{{{- define "{chart}.derivedConfig" -}}}}\n'
        + ("\n".join(derived) + "\n" if derived else "")
        + "{{- end -}}\n"
    )

    parts.append(
        _comment(
            "The configuration that actually reaches the image: the derived tree with the "
            "operator's own `config` tree merged over it, so `config` can both extend and "
            "override the values above.",
            "",
            "Not included: `configExtraToml`, which is appended verbatim and never parsed.",
        )
        + f'{{{{- define "{chart}.effectiveConfig" -}}}}\n'
        + f'{{{{- $derived := include "{chart}.derivedConfig" . | fromYaml -}}}}\n'
        + f"{{{{- toYaml (mergeOverwrite $derived (deepCopy (.Values.{CONFIG_ROOT} "
        + "| default dict))) -}}\n"
        + "{{- end -}}\n"
    )

    parts.append(
        _comment(
            "The complete configuration document: the effective tree, then the verbatim escape "
            "hatch."
        )
        + f'{{{{- define "{chart}.configToml" -}}}}\n'
        + f'{{{{- $config := include "{chart}.effectiveConfig" . | fromYaml -}}}}\n'
        + '{{- include "common.configToml" (dict "ctx" . "maps" (list $config)) -}}\n'
        + "{{- end -}}\n"
    )

    separator = union.dialect.get("nesting_separator", "__")
    secret_lines = [
        f"{_file_name(item)}: {{{{ .Values.{item.values_path} | quote }}}}"
        for item in plan.secrets
    ]
    parts.append(
        _comment(
            "The credentials this chart manages, each keyed by the file name the loader reads it "
            f"from: a configuration path with `{separator}` for nesting and no dots, because a "
            "`.` in the name is refused rather than treated as a separator.",
        )
        + f'{{{{- define "{chart}.secretData" -}}}}\n'
        + ("\n".join(secret_lines) + "\n" if secret_lines else "")
        + "{{- end -}}\n"
    )

    parts.append(
        _comment(
            "The secret file names this pod projects, as a YAML list. Parse with `fromYamlArray`."
        )
        + f'{{{{- define "{chart}.secretKeys" -}}}}\n'
        + f'{{{{- $data := include "{chart}.secretData" . | fromYaml -}}}}\n'
        + '{{- include "common.fileConfig.secretKeys" (dict "ctx" . "data" $data) -}}\n'
        + "{{- end -}}\n"
    )

    parts.append(_validate_values(chart, plan))
    return "\n".join(parts)


def _validate_values(chart: str, plan: Plan) -> str:
    """A guard refusing a render that could only produce a container which fails at boot.

    Generated only for credentials, and checked against the *projected key list* rather than
    against the values: an `existingSecret` supplies the file without the chart being able to see
    inside it, and a credential the chart cannot see is not the same as one that is missing.

    Required non-secret keys are deliberately not guarded. They carry a default in `values.yaml` —
    the image's own where it publishes one, a typed empty otherwise — so a render always produces
    a value for them, and `just check-config` is what holds that value against the contract. A
    guard would only fire on a values file that had explicitly nulled one, which an operator may
    do on purpose when a sibling layer supplies it.
    """
    required = [item for item in plan.secrets if not item.optional]

    if not required:
        return (
            _comment(
                "No render is refused today: every credential this image declares is optional. "
                "Kept as a partial so the `configmap.yaml` include has something to call, and so "
                "the first required credential is one condition rather than a new file.",
            )
            + f'{{{{- define "{chart}.validateValues" -}}}}\n{{{{- end -}}}}\n'
        )

    conditions = "".join(
        f'{{{{- if not (has "{_file_name(item)}" $projected) -}}}}\n'
        f'{{{{- fail (printf "\\n\\nVALUES VALIDATION FAILED for chart %q:\\n  - '
        f"{item.values_path} is required unless existingSecret supplies "
        f'`{_file_name(item)}`\\n" .Chart.Name) -}}}}\n'
        "{{- end -}}\n"
        for item in required
    )
    return (
        _comment(
            "Refuse a render that could only produce a container which fails at boot.",
            "",
            "Checked against the projected key list rather than against the values themselves, so "
            "an `existingSecret` counts — the chart cannot see inside one, and a credential it "
            "cannot see is not the same as a credential that is missing.",
        )
        + f'{{{{- define "{chart}.validateValues" -}}}}\n'
        + f'{{{{- $projected := include "{chart}.secretKeys" . | fromYamlArray -}}}}\n'
        + conditions
        + "{{- end -}}\n"
    )


# --------------------------------------------------------------------------------------------
# Rendering `config-contract.yaml`
# --------------------------------------------------------------------------------------------


def render_declaration(
    chart: str, document: str, contract_file: str, document_key: str, plan: Plan
) -> str:
    """The declaration `just check-config` and `just check-config-bindings` read.

    The selector is `app.kubernetes.io/instance`, matching every existing declaration, and the
    reason is worth repeating where a reader of the generated file will see it: that label is
    `.Release.Name`, the one identifier neither `nameOverride` nor `fullnameOverride` can move.

    `bindings: true` is written because the scaffold emits a marker for every key it surfaces and
    an `unbound` entry for every key it does not, so the chart is enrolled and complete from its
    first commit rather than joining the gate later.
    """
    lines = [
        "# vim: set ft=yaml:",
        "",
        "# What this chart's rendered configuration must satisfy, and which image decides that.",
        "#",
        "# Scaffolded by `just new-chart`. Per-chart and self-describing, so adding a chart never",
        "# edits a central file. `just check-config` reads this, unions the contracts of every",
        "# image listed, and validates the rendered ConfigMap, the container environment and the",
        "# mounted secret file names against the result.",
        "",
        "# Whether `just check-config-bindings` holds this chart's values against the keys below.",
        "#",
        "# Declared rather than inferred from the chart carrying markers: a chart that loses them",
        "# would otherwise drop out of that gate's report without a word.",
        "bindings: true",
    ]

    if plan.written_off or plan.secrets:
        lines += [
            "",
            "# Contract keys no chart value binds, each group with the reason it does not.",
            "#",
            "# Chart-level: a key nothing surfaces is unsurfaced in every document that declares",
            "# it. `documents:` would narrow an entry where that is untrue.",
            "unbound:",
        ]
        for group in _write_off_groups(plan):
            lines.append("  - keys:")
            lines.extend(f"      - {path}" for path in group[0])
            lines.append("    reason: >-")
            lines.extend(wrap(group[1], "      ", WIDTH))

    lines += [
        "",
        "documents:",
        f"  - name: {document}",
        "",
        "    # Where the rendered document is, in the output of `helm template`.",
        "    #",
        "    # Matched by label rather than by rendered name, and by `app.kubernetes.io/instance`:",
        "    # that label is `.Release.Name`, the one identifier neither `nameOverride` nor",
        "    # `fullnameOverride` can move. `just render` names the release after the chart, so",
        "    # the value below is what every rendered pair carries. A selector matching zero or",
        "    # several objects is reported rather than resolved.",
        "    source:",
        "      kind: ConfigMap",
        f"      selector: {{ app.kubernetes.io/instance: {chart} }}",
        f"      key: {document_key}",
        f"      format: {FORMAT}",
        "",
        "    # Every binary that reads this document. One image here, so the union is the",
        "    # identity.",
        "    images:",
        "      - values: image",
        f"        contract: {contract_file}",
        "",
        "    # The pods that mount it, for the environment and secret-file gates.",
        "    consumers:",
        f"      - workload: {{ kind: Deployment, selector: "
        f"{{ app.kubernetes.io/instance: {chart} }} }}",
        f"        containers: [{chart}]",
        "",
        "    # Pairs this document is not fully checked for, each with a reason.",
        "    exempt: []",
    ]
    return "\n".join(lines) + "\n"


def _write_off_groups(plan: Plan) -> list[tuple[list[str], str]]:
    """Every unsurfaced key, grouped by the reason it is unsurfaced.

    Grouped rather than listed one entry per key for the reason `config_declaration.Unbound`
    records: `tankovault` has 127 of them, and one entry each turned a handful of sentences into
    several hundred lines. The keys are still written out individually inside the group, so an
    image release that adds one turns the gate red until somebody puts it in a list on purpose.
    """
    reasons: dict[str, list[str]] = {}

    for item in plan.secrets:
        reason = (
            "Delivered as a file in the secrets directory — from the Secret this chart renders, "
            f"or from `existingSecret` under the file name `{_file_name(item)}` — and never "
            "written into the configuration document, because a credential in a ConfigMap is "
            f"readable by anything that can read the namespace. `{item.values_path}` is the value "
            "that carries it, so the binding does exist; it simply does not run through the "
            "document the markers describe. `just check-config-secrets` is what reconciles that "
            "channel, and it does it from the rendered manifests rather than from a comment."
        )
        reasons.setdefault(reason, []).append(item.path)

    for item in plan.written_off:
        reasons.setdefault(item.reason, []).append(item.path)

    return [(sorted(paths), reason) for reason, paths in reasons.items()]


# --------------------------------------------------------------------------------------------
# Rendering `contract-tests.yaml`
# --------------------------------------------------------------------------------------------


def render_enrolment(chart: str, document: str, plan: Plan) -> str:
    """The round-trip enrolment, with a prerequisite for every credential the render demands.

    A prerequisite is a chart value every generated case has to carry before the chart will render
    at all, and the only ones this scaffold knows of are the credentials `validateValues` guards.
    Nothing here may name a path under `config`: a prerequisite is never dropped for the case it
    collides with, the way a baseline is, so one written into the tree the cases probe could supply
    the very value a case exists to prove the chart delivered. The generator refuses one outright.
    """
    required = [item for item in plan.secrets if not item.optional]

    lines = [
        "# vim: set ft=yaml:",
        "",
        "# Enrols this chart in the generated configuration round-trip suites.",
        "#",
        "# `just check-config` proves the rendered document satisfies the image's contract. It",
        "# cannot prove the round trip: that a setting written into `config` arrives in that",
        "# document, at the path the image reads it from, carrying the value that was asked for.",
        "# A document missing a setting entirely satisfies the contract perfectly — every key it",
        "# does contain is legal — so a typo in a template helper passes every gate and the",
        "# container runs on a compiled default nobody chose.",
        "#",
        "# `just contract-tests` reads this file and `config-contract.yaml`, and writes one suite",
        "# per declared document under `tests/`. The presence of this file is the enrolment.",
        "documents:",
        f"  - name: {document}",
    ]

    if not required:
        lines += [
            "",
            "    # No `prerequisites` and no `baseline`: this chart renders without any value",
            "    # being set, so every generated case can stand on the chart's own defaults.",
        ]
        return "\n".join(lines) + "\n"

    lines += [
        "",
        "    # The chart values every generated case carries. This chart's `validateValues`",
        "    # fails the template before a ConfigMap exists for any case to assert against, so",
        "    # there is no case here that can do without them.",
        "    #",
        "    # Flat dotted paths, exactly as the helm-unittest `set` block they become spells",
        "    # them. Nothing here may name a path under `config`.",
        "    prerequisites:",
        "      values:",
    ]
    for item in required:
        lines.append(f"        {item.values_path}: {values_scalar(_probe_credential(item))}")

    lines += ["", "      reason: >-"]
    lines.extend(
        wrap(
            f"`{chart}.validateValues` refuses a render that projects no "
            + ", ".join(f"`{_file_name(item)}`" for item in required)
            + " file, and it runs from `configmap.yaml`, so without one there is no document for "
            "any case to assert against. The guard reads the projected key list rather than the "
            "value, so `existingSecret` would satisfy it as well; the first-class value is used "
            "instead because it keeps the enrolment to one line and leaves the chart's Secret "
            "rendering on the path every other case exercises. These occupy `secret: true` "
            "contract keys, which the generator refuses to invent a probe for on the grounds that "
            "a credential-shaped value in a test file is a credential — so they are written here "
            "rather than synthesised, and the values are placeholders that can never authenticate "
            "anywhere.",
            "        ",
            WIDTH,
        )
    )
    return "\n".join(lines) + "\n"


def _probe_credential(placement: Placement) -> str:
    """A placeholder for a required credential that cannot authenticate anywhere.

    `.invalid` is reserved by RFC 2606 and can never resolve, so a value built on it is inert
    wherever it ends up — which is the property that makes writing one into a committed test file
    defensible at all.
    """
    constraint = placement.key.get("constraint") or {}
    if "pattern" in constraint:
        raise ScaffoldError(
            f"the required credential {placement.path!r} constrains its text with a pattern, so a "
            "placeholder cannot be invented for it; write the `prerequisites` entry by hand"
        )
    return f"scaffolded-placeholder.{placement.path.replace('.', '-')}.invalid"


# --------------------------------------------------------------------------------------------
# Rendering the fixtures
# --------------------------------------------------------------------------------------------


def render_ci_values(chart: str, surface: Surface) -> str:
    """`ci/test-values.yaml`: the least a fixture has to set for `ct install` to get a running pod.

    Only the credentials the render guard demands. Everything else has a default — the image's own
    where the contract publishes one — and a fixture that restated those defaults would be a
    second place they are written, drifting the first time one of them changed.

    A plain chart has no guard and no contract to read a default out of, so the fixture is empty
    and says so. `ct` needs one file; what belongs in it is the first deliberate deviation from
    the chart's defaults, and only the chart's author knows what that is.
    """
    if not surface.contracted:
        return (
            f"# The default values install {chart} as they are.\n"
            "#\n"
            "# This chart's image publishes no configuration contract, so nothing here was\n"
            "# derived from one. `ct` needs at least one fixture, and this is the place to put\n"
            "# the first deliberate deviation from the chart's defaults.\n"
            "{}\n"
        )

    required = [item for item in surface.plan.secrets if not item.optional]
    if not required:
        return (
            f"# The default values install {chart} as they are: every setting the image requires\n"
            "# carries a default and every credential it declares is optional. This file exists\n"
            "# because `ct` needs at least one fixture, and it is the place to put the first\n"
            "# deliberate deviation from the defaults.\n"
            "{}\n"
        )

    lines = [
        "# The credentials this chart's render guard demands, and nothing else.",
        "#",
        "# Everything else has a default — the image's own where the contract publishes one — and",
        "# restating those here would be a second place they are written.",
        "#",
        "# `.invalid` is reserved by RFC 2606 and can never resolve, so these values are inert",
        "# wherever the installed pod ends up sending them.",
        "",
    ]
    tree: dict[str, Any] = {}
    for item in required:
        node = tree
        segments = item.values_path.split(".")
        for segment in segments[:-1]:
            node = node.setdefault(segment, {})
        node[segments[-1]] = _probe_credential(item)
    lines.extend(_plain_yaml(tree))
    return "\n".join(lines) + "\n"


def _plain_yaml(tree: dict[str, Any], indent: str = "") -> list[str]:
    """A nested mapping of scalars as YAML. Enough for a fixture; not a general emitter."""
    lines: list[str] = []
    for name in sorted(tree):
        node = tree[name]
        if isinstance(node, dict):
            lines.append(f"{indent}{name}:")
            lines.extend(_plain_yaml(node, indent + "  "))
        else:
            lines.append(f"{indent}{name}: {values_scalar(node)}")
    return lines


def render_unit_test(chart: str, document_key: str, plan: Plan) -> str:
    """A first hand-written suite: the ConfigMap renders, and it is the document it claims to be.

    Deliberately thin, and deliberately not overlapping the generated round-trip suite. That one
    proves every contract key arrives at the path the image reads it from, key by key, and
    regenerates when the contract moves. What it cannot assert is the shape of the object around
    the document — that a ConfigMap exists at all, that it carries the chart's labels, that the
    escape hatch reaches the file — because none of that is in the contract. This is where those
    go, and where a person adds the assertions only they know are worth making.
    """
    required = [item for item in plan.secrets if not item.optional]
    set_block = ""
    if required:
        set_block = "\n".join(
            [
                "    set:",
                *(
                    f"      {item.values_path}: {values_scalar(_probe_credential(item))}"
                    for item in required
                ),
            ]
        )

    lines = [
        f"suite: {chart} configmap",
        "templates:",
        "  - configmap.yaml",
        "",
        "# Scaffolded by `just new-chart`, and yours to extend. The generated",
        "# `contract_roundtrip_*_test.yaml` beside it proves each contract key arrives at the",
        "# path the image reads it from; this file is for everything the contract cannot say —",
        "# the shape of the object, the labels, the escape hatches.",
        "tests:",
        "  - it: renders a ConfigMap carrying the configuration document",
    ]
    if set_block:
        lines.append(set_block)
    lines += [
        "    asserts:",
        "      - hasDocuments:",
        "          count: 1",
        "      - isKind:",
        "          of: ConfigMap",
        "      - exists:",
        f"          path: data['{document_key}']",
        "",
        "  - it: appends configExtraToml verbatim, after the rendered configuration",
        "    set:",
        *([f"      {item.values_path}: {values_scalar(_probe_credential(item))}"
           for item in required]),
        "      configExtraToml: |",
        "        [scaffolded]",
        '        marker = "verbatim"',
        "    asserts:",
        "      - matchRegex:",
        f"          path: data['{document_key}']",
        '          pattern: "\\\\[scaffolded\\\\]"',
    ]
    return "\n".join(lines) + "\n"
