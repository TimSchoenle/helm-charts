#!/usr/bin/env python3
"""Create a new chart: the chassis every chart here shares, and the configuration surface its
image's contract describes.

A chart in this repository is two things stacked. Underneath is a chassis — around thirty values
blocks, a handful of templates, a podspec wired to `charts/common` — which is the same in every
chart because the library owns the logic and these files only name it. On top is the part that is
about *this* image: the settings it reads, what each one accepts, which of them are credentials,
and how a chart value reaches each one.

Both halves are hand-copied from a neighbouring chart today, and both go wrong doing it. The
chassis drifts silently: 2,069 lines of the eight charts' `values.yaml` files are byte-identical to
another chart's and the rest of each block is not, which is what repeatedly copy-editing one
neighbour looks like. The configuration half goes wrong differently — it is written by reading a
400 KB JSON document by hand, so a key gets missed, and a key nothing surfaces is exactly the
defect `just check-config-bindings` exists to catch after the fact.

This copies the first half and, where there is a contract to read, generates the second.

--------------------------------------------------------------------------------------------
Two forms
--------------------------------------------------------------------------------------------

**Contracted** — the default, and the only networked path. The image publishes a contract, so the
chart gets a values block per setting with the `# @config` marker that binds it, a `_helpers.tpl`
that projects them into the document, a Secret for every credential, a `config-contract.yaml`, a
`contract-tests.yaml` and a vendored contract. It passes `just check-config`,
`just check-config-bindings` and `just check-contract-tests` on its first run.

**Plain** — `--no-contract`. The image publishes nothing to read, so the scaffold asserts nothing
about its configuration: no `config` escape hatch, no `configMount`, no ConfigMap and no Secret.
What is left is the chassis and the workload, which is two thirds of every chart here and all of
what `teamspeak` and `paperless-ngx` are made of. A `config-contract.yaml` is written only when
the image is *first-party* — the same question `just check-contract-coverage` asks, decided from
the same list — because a chart pinning `paperless-ngx` owes no declaration and a scaffold that
gave it one would introduce a file this repository deliberately does not have.

The seam between the two is `config_scaffold.Surface`, and every renderer branches on it rather
than on a flag threaded through from here.

--------------------------------------------------------------------------------------------
The one networked step
--------------------------------------------------------------------------------------------

Generating the configuration surface needs the contract, and the contract lives in the registry
next to the image. So the contracted form is the second networked recipe in the repository, and it
uses the first one to do it: the vendoring is `refresh-contracts.py`, unchanged and called as a
module, so a contract this scaffold writes has been through the same signature verification, the
same label agreement check and the same `source` envelope as every contract `just contracts` has
ever written. There is no second path into `charts/*/contracts/`, which is the property worth
having — a contract that arrived some other way would be trusted by every gate downstream.

`--no-contract` is not a way to skip the network for an image that does publish a contract. It
produces a chart with no configuration surface at all, which for such an image is strictly less
than what could have been generated.

--------------------------------------------------------------------------------------------
What "finished" means
--------------------------------------------------------------------------------------------

The chart this writes passes `just check` — every offline gate, including the ones that read a
contract. That is the bar, and it is checked by running them rather than asserted here.

What it is not is *done*, and what is left is printed as an ordered list at the end rather than
left to be discovered: the chart description and the README prose, which a generator cannot write
and helm-docs will happily publish as placeholders; whatever the workload needs beyond a
Deployment, none of which is in a contract; the `unbound` reasons, each generated with a
defensible sentence and a defensible sentence is not a considered one; and, where the image
publishes no default for a key it requires, the value this scaffold had to invent.

It does not commit, add a `ci/` fixture beyond the one, or touch the root README — the chart index
is derived from `Chart.yaml`, so the table picks the chart up on its own.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc  # noqa: E402
import config_scaffold as sc  # noqa: E402
from config_coverage import first_party_patterns  # noqa: E402
from config_declaration import DeclarationError, load_declaration  # noqa: E402

# `FIRST_PARTY` is the list `just check-contract-coverage` decides from, and it is imported rather
# than spelt here for the reason the constant is shared at all: whether a chart owes a declaration
# is that gate's rule, and a scaffold with its own opinion about it would write charts the gate
# then rejects.
from config_paths import CHARTS_DIR, FIRST_PARTY  # noqa: E402

TEMPLATES = Path(".github/templates/chart")

# The library chart every chart here depends on. Read from its own `Chart.yaml` rather than
# written down, so a library release is not a second edit somebody has to remember.
COMMON = "common"


class ScaffoldAborted(Exception):
    """Something the caller has to fix before a chart can be written."""


# --------------------------------------------------------------------------------------------
# Substitution
# --------------------------------------------------------------------------------------------


def substitute(text: str, values: dict[str, str]) -> str:
    """Replace every `%%NAME%%` placeholder, and refuse a file that still holds one.

    `%%NAME%%` rather than `{{ NAME }}` because these files are Go templates whose braces have to
    survive into the chart untouched. Refusing a leftover is the point of doing it here rather
    than with `str.format`: a placeholder nobody substituted would otherwise reach the chart as
    literal text and render as itself.
    """
    for name, value in values.items():
        text = text.replace(f"%%{name}%%", value)
    if "%%" in text:
        marker = text[text.index("%%"):][:40].splitlines()[0]
        raise ScaffoldAborted(f"a template placeholder was not substituted: {marker}")
    return text


# The template subtrees that are not shared: one per configuration form, selected by `Surface`.
# Everything outside them is copied into every chart whatever its form.
FORMS = ("contract", "plain")

# Files under the template root that are not chart files. `values.chassis.yaml` is a fragment the
# generated `values.yaml` ends with rather than a document of its own; `README.md` documents the
# directory to whoever edits it.
NOT_CHART_FILES = frozenset({"values.chassis.yaml", "README.md"})


def copy_chassis(chart_dir: Path, values: dict[str, str], templates: Path, form: str) -> None:
    """Copy the shared files and the form's own, substituting placeholders on the way.

    Two passes over one directory rather than two directories, because the split is between the
    files a form *replaces* and the files every form keeps — and a reader of the template tree
    should be able to see at a glance which is which. `templates/serviceaccount.yaml` is shared;
    `contract/templates/deployment.yaml` and `plain/templates/deployment.yaml` are the same file
    written two ways, and the second has no ConfigMap to mount.
    """
    if form not in FORMS:
        raise ScaffoldAborted(f"{form!r} is not a configuration form; expected one of {FORMS}")

    excluded = tuple(templates / other for other in FORMS)
    for root in (templates, templates / form):
        if not root.is_dir():
            raise ScaffoldAborted(f"{root}: the scaffold templates are missing")
        for source in sorted(root.rglob("*")):
            if source.is_dir() or source.name in NOT_CHART_FILES:
                continue
            if root is templates and any(source.is_relative_to(other) for other in excluded):
                continue
            _write(
                chart_dir / source.relative_to(root),
                substitute(source.read_text(encoding="utf-8"), values),
            )


# --------------------------------------------------------------------------------------------
# The two forms
# --------------------------------------------------------------------------------------------


def scaffold(
    charts: Path,
    templates: Path,
    name: str,
    repository: str,
    app_version: str,
    description: str,
    source: str,
    document: str,
    document_key: str,
    contracted: bool,
) -> Path:
    """Write the chart, in whichever form was asked for. Returns the chart directory.

    Everything here is what the two forms share plus the form's own templates. On the contracted
    path two of these files are provisional and `generate` rewrites them, and the order is forced
    rather than chosen: `refresh-contracts.py` reads the declaration to learn which image to ask,
    and reads `values.yaml` to resolve it. So the chart has to name its image and its document
    *before* it can fetch the description of that document.

    On the plain path nothing here is provisional. The chart is finished when this returns.
    """
    sc.check_chart_name(name)

    chart_dir = charts / name
    if chart_dir.exists():
        raise ScaffoldAborted(f"{chart_dir} already exists; pick another name or remove it")

    library = charts / COMMON / "Chart.yaml"
    if not library.is_file():
        raise ScaffoldAborted(f"{library}: cannot read the `common` library's version")
    common_version = str((yaml.safe_load(library.read_text(encoding="utf-8")) or {})["version"])

    # Before the fetch there is no dialect to read the loader's spellings from, so the chart name
    # stands in for them. Every use that survives into the rendered chart is rewritten by
    # `generate` from what the contract actually declares; on the plain path there is no loader to
    # describe and these reach only comment prose.
    chart_dir.mkdir(parents=True)
    copy_chassis(
        chart_dir,
        {
            "CHART": name,
            "DESCRIPTION": description,
            "APP_VERSION": app_version,
            "SOURCE": source,
            "COMMON_VERSION": common_version,
            "DOCUMENT_KEY": document_key,
            "PREFIX": _shout(name) + "_",
            "ENV_PREFIX": _shout(name),
            "SEPARATOR": "__",
        },
        templates,
        "contract" if contracted else "plain",
    )

    provisional = (
        sc.Surface(
            union=cc.Union(dialect={"prefix": _shout(name) + "_", "nesting_separator": "__"})
        )
        if contracted
        else sc.plain()
    )
    _write(
        chart_dir / "values.yaml",
        sc.render_values(
            name,
            provisional,
            repository,
            app_version,
            (templates / "values.chassis.yaml").read_text(encoding="utf-8"),
            document_key,
        ),
    )
    _write(chart_dir / "templates" / "_helpers.tpl", sc.render_helpers(name, provisional))
    _write(chart_dir / "ci" / "test-values.yaml", sc.render_ci_values(name, provisional))

    if contracted:
        # The least a declaration can say and still tell the refresh which image to ask.
        # Rewritten in full by `generate`, once there are keys to write off.
        _write(
            chart_dir / sc.DECLARATION,
            sc.render_declaration(
                name, document, f"{sc.CONTRACTS}/{document}.json", document_key, sc.Plan()
            ),
        )

    return chart_dir


def _shout(name: str) -> str:
    """`netcup-offer-bot` as `NETCUP_OFFER_BOT` — the environment prefix a chart name implies.

    A guess, and only ever used by `--no-contract`, where there is no dialect to read the real one
    from. Every contract states its own prefix and the contract form uses that instead.
    """
    return name.replace("-", "_").upper()


def vendor(chart_dir: Path, signer: str) -> Path:
    """Fetch and verify the contract for the image this chart pins. The one networked step.

    Delegated to `refresh-contracts.py` rather than reimplemented, so a contract written by this
    scaffold is one `just contracts` would have written: same signature verification, same label
    agreement check, same envelope. Imported as a module because the file name is hyphenated and
    a `subprocess` call would hide its exceptions behind an exit code.
    """
    module = _refresh_module()
    client = module.RegistryClient()
    try:
        # The library's own check, rather than a second one here: it names both binaries, says
        # which recipe needs them and names the override variables, and a message this script
        # wrote instead would drift from it.
        client.require()
        module.refresh_chart(chart_dir, client, signer)
    except module.RefreshError as failure:
        raise ScaffoldAborted(
            f"{failure}\n       Scaffold with `--no-contract --reason ...` if this image "
            "publishes none."
        ) from failure

    written = sorted((chart_dir / sc.CONTRACTS).glob("*.json"))
    if len(written) != 1:
        raise ScaffoldAborted(
            f"expected one vendored contract under {chart_dir / sc.CONTRACTS}, found "
            f"{len(written)}"
        )
    return written[0]


def _refresh_module():
    """`refresh-contracts.py` as a module.

    Loaded by path because the file name is hyphenated and a plain `import` cannot name it. A
    `subprocess` call would work too and is worse: it would hide `RefreshError` — which carries
    the whole signature-verification story — behind an exit code, and this command has to be able
    to remove the partial chart it just wrote.
    """
    import importlib.util

    if "refresh_contracts" in sys.modules:
        return sys.modules["refresh_contracts"]

    path = Path(__file__).resolve().parent / "refresh-contracts.py"
    spec = importlib.util.spec_from_file_location("refresh_contracts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_contracts"] = module
    spec.loader.exec_module(module)
    return module


def generate(
    chart_dir: Path, templates: Path, document: str, document_key: str, contract: Path
) -> sc.Surface:
    """Write the configuration surface: values, helpers, declaration, enrolment and fixtures.

    Everything here overwrites what `scaffold` wrote provisionally. The chart's own image pin is
    read back out of the file rather than passed in again, so there is one place it is written and
    a rewrite cannot silently disagree with the contract that was just fetched for it.
    """
    vendored = cc.load_vendored(contract)
    union = cc.union_contracts([(str(contract), vendored.contract)])

    if union.dialect.get("nesting_separator") is None:
        raise ScaffoldAborted(f"{contract}: the contract declares no nesting separator")

    surface = sc.from_contract(union)
    plan = surface.plan
    name = chart_dir.name
    chassis = (templates / "values.chassis.yaml").read_text(encoding="utf-8")

    values = chart_dir / "values.yaml"
    repository, tag = _pinned(values)

    _write(values, sc.render_values(name, surface, repository, tag, chassis, document_key))
    _write(chart_dir / "templates" / "_helpers.tpl", sc.render_helpers(name, surface))
    _write(
        chart_dir / sc.DECLARATION,
        sc.render_declaration(
            name, document, f"{sc.CONTRACTS}/{contract.name}", document_key, plan
        ),
    )
    _write(chart_dir / sc.ENROLMENT, sc.render_enrolment(name, document, plan))
    _write(chart_dir / "ci" / "test-values.yaml", sc.render_ci_values(name, surface))
    _write(
        chart_dir / "tests" / "configmap_test.yaml",
        sc.render_unit_test(name, document_key, plan),
    )

    # The two placeholders the offline pass could only guess at. Rewritten from the dialect the
    # contract actually declares, which is the whole reason the contract form exists.
    for template in ("deployment.yaml", "secret.yaml"):
        path = chart_dir / "templates" / template
        text = path.read_text(encoding="utf-8")
        text = text.replace(_shout(name) + "_", union.prefix)
        text = text.replace(f'"prefix" "{_shout(name)}"', f'"prefix" "{sc.env_prefix(union)}"')
        text = text.replace("`__` for nesting", f"`{union.dialect['nesting_separator']}` for nesting")
        _write(path, text)

    return surface


def _pinned(values: Path) -> tuple[str, str]:
    """The repository and tag the scaffolded `values.yaml` already carries."""
    image = (yaml.safe_load(values.read_text(encoding="utf-8")) or {}).get("image") or {}
    return str(image.get("repository") or ""), str(image.get("tag") or "")


def owes_a_declaration(first_party: Path, repository: str) -> bool:
    """Whether `just check-contract-coverage` will demand a declaration from this chart.

    The same question the gate asks, asked with the gate's own list and the gate's own pattern
    matcher rather than a second copy of either. A chart pinning `timschoenle/foo` owes a
    declaration — a real one, or an explicit opt-out with a reason — and a chart pinning
    `paperless-ngx` owes nothing, because nobody publishes a contract for it and a file that could
    only say "no" is a file nobody should be asked to write.

    That distinction is why the plain form does not always write an opt-out. `teamspeak` and
    `paperless-ngx` carry no `config-contract.yaml` at all, and a scaffold that gave every plain
    chart one would have introduced a file this repository deliberately does not have.
    """
    if not first_party.is_file():
        raise ScaffoldAborted(
            f"{first_party}: missing, so whether this chart owes a declaration cannot be decided"
        )
    return any(
        cc.matches_ignore(pattern, repository)
        for pattern in first_party_patterns(first_party)
    )


def opt_out(chart_dir: Path, reason: str, repository: str) -> None:
    """The `--no-contract` declaration: an explicit opt-out with a written reason.

    Not the same thing as having no declaration at all. `just check-contract-coverage` demands a
    file from any chart pinning a first-party image, and this is the form that satisfies it while
    saying, in a sentence a reviewer reads, that the image publishes nothing to check against.
    """
    _write(
        chart_dir / sc.DECLARATION,
        "# vim: set ft=yaml:\n"
        "\n"
        f"# This chart pins {repository}, which publishes no configuration contract. It says so\n"
        "# here rather than simply having no file: an empty `documents` is an explicit opt-out,\n"
        "# and `just check-contract-coverage` reports a first-party image that is accounted for\n"
        "# by neither a document nor a written reason.\n"
        "#\n"
        "# `unconfigured` is deliberately absent. It would be the field for naming an image this\n"
        "# chart pins and does not describe — but nothing reads it on a chart with no documents,\n"
        "# and on a chart that has them `config_coverage` compares it against *values paths*\n"
        "# while `just explain` prints it as a list of images. Adding the first usage of a field\n"
        "# whose two readers disagree is not this file's job; the reason below names the image.\n"
        "\n"
        "documents: []\n"
        "\n"
        "reason: >-\n"
        + "\n".join(sc.wrap(reason, "  ", sc.WIDTH))
        + "\n",
    )


def _write(path: Path, text: str) -> None:
    """Write one generated file with LF endings, whatever the platform prefers.

    The same reason `generate-contract-tests.py` gives: this repository is developed from a Git
    Bash shell on Windows, and Python's text mode would translate every newline to CRLF — leaving
    a committed file that differs from the one CI writes on every line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------------
# Saying what is left
# --------------------------------------------------------------------------------------------


def next_steps(chart_dir: Path, surface: sc.Surface, out) -> None:
    """The ordered list of what a person still has to do, printed rather than left to be found."""
    name = chart_dir.name
    plan = surface.plan
    print(f"\n==> {chart_dir} written\n", file=out)

    if surface.contracted:
        print(
            f"    {len(plan.projected)} setting(s) surfaced as chart values, "
            f"{len(plan.secrets)} credential(s) delivered as files, "
            f"{len(plan.written_off)} key(s) written off\n",
            file=out,
        )
    else:
        print(
            "    No configuration surface: this image publishes no contract, so the chart is the "
            "chassis and the workload alone.\n",
            file=out,
        )

    steps = [
        f"Write the description in charts/{name}/Chart.yaml and the prose in README.md.gotmpl. "
        "Both are placeholders, and helm-docs publishes them.",
    ]

    if surface.contracted:
        steps.append(
            "Add whatever the workload needs beyond a Deployment — a Service, an Ingress, a "
            "PodMonitor, a volume. None of that is in the contract."
        )
        guessed = sc.invented(plan)
        if guessed:
            steps.append(
                "Replace the values this scaffold had to invent. These keys are required and "
                "their image publishes no default, so the value written is merely one the "
                f"constraint accepts: {', '.join(item.values_path for item in guessed)}."
            )
        if plan.secrets or plan.written_off:
            steps.append(
                f"Read the `unbound` reasons in charts/{name}/config-contract.yaml. Each is "
                "defensible and none is considered."
            )
        steps.append(
            "Add this chart to the enrolment census in "
            "`.github/scripts/tests/test_contract_bindings.py` "
            "(`test_every_enrolled_chart_passes_the_gate`). That test pins the exact list and "
            "each chart's key count on purpose — it is how a chart silently dropping out of "
            "`check-config-bindings` is caught — so a new enrolment is one line there."
        )
    else:
        steps.append(
            "Write the chart's own values, one `@schema` block each, and the templates that "
            "consume them — a ConfigMap, a Secret, a Service, whatever this image needs. "
            f"`templates/_helpers.tpl` is where anything computed belongs."
        )
        steps.append(
            "If this image ever publishes a configuration contract, re-run without "
            "`--no-contract` in a scratch directory and take the generated surface across."
        )

    steps += [
        "just deps && just docs        # resolve the library, generate the schema and README",
        "just check                    # every offline gate, this chart included",
    ]

    for number, step in enumerate(steps, start=1):
        wrapped = sc.wrap(step, "", sc.WIDTH - 7)
        print(f"    {number}. {wrapped[0]}", file=out)
        for line in wrapped[1:]:
            print(f"       {line}", file=out)
    print(file=out)


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="the chart's name, and the directory under charts/")
    parser.add_argument("repository", help="the image repository, as values.yaml spells it")
    parser.add_argument("version", help="the image tag, ideally `vX.Y.Z@sha256:...`")
    parser.add_argument("--charts", default=str(CHARTS_DIR), type=Path)
    parser.add_argument("--templates", default=str(TEMPLATES), type=Path)
    parser.add_argument(
        "--description", default="", help="the chart description; a placeholder when omitted"
    )
    parser.add_argument(
        "--source", default="", help="the upstream repository; derived from the image when omitted"
    )
    parser.add_argument(
        "--document",
        default="",
        help="the configuration document's name in config-contract.yaml (default: the chart's)",
    )
    parser.add_argument(
        "--document-key",
        default=f"config.{sc.FORMAT}",
        help="the ConfigMap key the document is rendered under",
    )
    parser.add_argument(
        "--no-contract",
        action="store_true",
        help="scaffold offline, for an image that publishes no configuration contract",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="why there is no contract; required with --no-contract for a first-party image",
    )
    parser.add_argument(
        "--first-party",
        default=str(FIRST_PARTY),
        type=Path,
        help="the image list check-contract-coverage decides from",
    )
    parser.add_argument(
        "--signer",
        default="",
        help="the workflow identity a contract must be signed by (default: $CONTRACT_SIGNER)",
    )
    args = parser.parse_args(argv)

    document = args.document or args.name
    description = args.description or (
        f"TODO: what {args.name} deploys, in one sentence. Written by hand — helm-docs "
        "publishes this."
    )
    source = args.source or f"https://github.com/{args.repository.split('/')[-1]}"

    chart_dir: Path | None = None
    try:
        # Asked before anything is written, so a missing reason costs nothing to correct. The
        # question is the coverage gate's, not this script's: a chart pinning a first-party image
        # owes a declaration, and an opt-out without a reason is indistinguishable from a chart
        # nobody got round to declaring. A third-party image owes neither — `teamspeak` carries no
        # `config-contract.yaml` at all — so demanding a reason there would be asking somebody to
        # justify a file this repository does not want.
        owed = args.no_contract and owes_a_declaration(args.first_party, args.repository)
        if owed and not args.reason:
            raise ScaffoldAborted(
                f"{args.repository} is a first-party image, so `just check-contract-coverage` "
                "will demand a declaration from this chart. Give --reason to write the opt-out, "
                "or drop --no-contract to vendor the contract it publishes"
            )

        chart_dir = scaffold(
            args.charts,
            args.templates,
            args.name,
            args.repository,
            args.version,
            description,
            source,
            document,
            args.document_key,
            contracted=not args.no_contract,
        )

        if args.no_contract:
            if owed:
                opt_out(chart_dir, args.reason, args.repository)
            next_steps(chart_dir, sc.plain(), sys.stdout)
            return 0

        signer = args.signer or os.environ.get("CONTRACT_SIGNER", "")
        if not signer:
            raise ScaffoldAborted(
                "no contract signer given; pass --signer or set $CONTRACT_SIGNER, as "
                "`just contracts` does"
            )

        contract = vendor(chart_dir, signer)
        surface = generate(chart_dir, args.templates, document, args.document_key, contract)

        # Read the declaration back through the loader every gate uses. A file this script wrote
        # and no parser accepted would fail on the contributor's first `just check` rather than
        # here, with a message about a chart they did not write by hand.
        if load_declaration(chart_dir) is None:
            raise ScaffoldAborted(f"{chart_dir / sc.DECLARATION} was written and cannot be read")

        next_steps(chart_dir, surface, sys.stdout)
        return 0

    except (ScaffoldAborted, sc.ScaffoldError, cc.ContractError, DeclarationError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        if chart_dir is not None and chart_dir.is_dir():
            # A half-written chart is worse than none: it renders, it lints, and it is missing the
            # one half this command exists to write. Removed rather than left for the caller to
            # notice, and named so nothing is deleted silently.
            shutil.rmtree(chart_dir)
            print(f"       removed the partial chart at {chart_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
