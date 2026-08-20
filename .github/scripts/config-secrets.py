#!/usr/bin/env python3
"""The credential inventory, and the report reconciling it against what the charts deliver.

Two questions nobody in this repository could answer before it existed, and one entry point for
both because they are the same subject read at two removes — what the images declare secret, and
whether anything actually supplies it.

  python3 .github/scripts/config-secrets.py                        the inventory, as a table
  python3 .github/scripts/config-secrets.py --json                 the same, for a machine
  python3 .github/scripts/config-secrets.py --reconcile rendered   the report
  python3 .github/scripts/config-secrets.py --reconcile rendered --json

**The reconciliation exits 0 whatever it finds, and that is deliberate for now.** Its three
questions are new and the first pass over an established repository is where a report earns its
triage: a finding that turns out to be a considered design decision is a line in a document, not a
red pipeline on a pull request that changed nothing. A follow-up promotes it once the findings
here are triaged, and the promotion is the last line of `main` — the findings are already
`config_report.Finding`s at the right level, so nothing else moves.

The model lives in `config_secrets.py`; this file is the loop, the layout and the exit status,
matching how `check-config.py` sits above the gates it composes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_contract as cc  # noqa: E402
from config_declaration import DeclarationError  # noqa: E402
from config_paths import CHARTS_DIR  # noqa: E402
from config_report import Report, error, warning  # noqa: E402
from config_secrets import (  # noqa: E402
    Credential,
    Mount,
    Reconciler,
    Surface,
    contracted_charts,
    credentials,
    declared_secrets,
)



# --------------------------------------------------------------------------------------------
# The inventory
# --------------------------------------------------------------------------------------------


def print_inventory(charts: Path) -> int:
    """One block per contracted chart; returns how many credentials were listed."""
    total = 0
    for chart_dir, declaration in contracted_charts(charts):
        rows = credentials(declared_secrets(chart_dir, declaration))
        total += len(rows)
        print(f"==> {declaration.chart}")
        if not rows:
            print(
                f"    no key of the {len(declaration.documents)} document(s) this chart declares "
                "is marked secret"
            )
            print()
            continue
        _print_chart(chart_dir, declaration, rows)
        print()

    if total == 0:
        print("no chart declares a configuration contract with a secret key")
    else:
        print(f"{total} credential(s) across {len(list(contracted_charts(charts)))} chart(s)")
    return total


def _print_chart(chart_dir: Path, declaration, rows: list[Credential]) -> None:
    images = sorted({image for row in rows for image in row.images})
    vendored = sum(len(document.images) for document in declaration.documents)
    print(
        f"    {len(rows)} credential(s), read by {len(images)} of the {vendored} image(s) this "
        "chart vendors a contract for"
    )

    # The environment spellings are derived from the config path by the dialect, and stating the
    # derivation once is both shorter and more useful than a column of values every one of which
    # restates it. Checked rather than assumed: a credential whose contract spells it otherwise
    # gets its literal spellings printed under its row, so the rule can never quietly become a
    # lie the table tells.
    exceptions = [row for row in rows if row.env_file != row.env + "_FILE"]
    prefix = _common_prefix([row.env for row in rows])
    if prefix:
        print(
            f"    environment: {prefix}<PATH>, with dots as `__` and upper-cased; the same "
            "spelling with `_FILE` appended names a file whose contents supply it"
        )

    multi = len(images) > 1
    widths = (
        max(len(row.path) for row in rows),
        max(len(row.secrets_file) for row in rows),
    )
    header = f"    {'config path':<{widths[0]}}  {'secrets file':<{widths[1]}}  required"
    print()
    print(header)
    print("    " + "-" * (len(header) - 4))
    for row in rows:
        required = "yes" if row.required else "no"
        print(
            f"    {row.path:<{widths[0]}}  {row.secrets_file:<{widths[1]}}  {required}"
        )
        if multi:
            print(f"        read by  {', '.join(row.images)}")
        if row in exceptions or not prefix:
            print(f"        env      {row.env}")
            print(f"        _FILE    {row.env_file}")


def _common_prefix(spellings: list[str]) -> str:
    """The dialect prefix every environment spelling shares, or nothing when they differ."""
    if not spellings:
        return ""
    head = spellings[0]
    cut = len(head)
    for spelling in spellings[1:]:
        cut = min(cut, len(spelling))
        while cut and spelling[:cut] != head[:cut]:
            cut -= 1
    return head[:cut]


def inventory_json(charts: Path) -> dict:
    payload = []
    for chart_dir, declaration in contracted_charts(charts):
        for row in credentials(declared_secrets(chart_dir, declaration)):
            payload.append(asdict(row))
    return {"credentials": payload}


# --------------------------------------------------------------------------------------------
# The reconciliation
# --------------------------------------------------------------------------------------------


def reconcile(charts: Path, rendered: Path) -> Surface:
    if not rendered.is_dir():
        raise DeclarationError(
            f"{rendered}: no rendered manifests; run `just render {rendered}` first"
        )
    return Reconciler(charts, rendered).run()


def report_of(surface: Surface) -> Report:
    """Turn one scan into findings, at the level each conclusion has earned.

    An error is reserved for a chart that has no channel for a credential at all, and for a
    credential reaching a pod that cannot read it. A warning is for everything that is a gap in
    what was *checked* rather than in what the chart does: a credential no `ci/` fixture exercises,
    a chart policy being recorded, a document that could not be reconciled. That is the same
    division `config_report.Finding` already documents, applied to a report rather than a gate.
    """
    report = Report()

    for note in surface.notes:
        report.add("not reconciled", warning(note))

    for finding in surface.undeliverable:
        row = finding.credential
        channels = (
            f"not as the file {row.secrets_file!r} in a secrets directory, not through "
            f"{row.env_file}, not as {row.env}, and not by the rendered document"
        )
        summary = f" The contract says: {row.summary}" if row.summary else ""
        if finding.named_by_chart:
            report.add(
                f"unsupplied: {row.chart}",
                warning(
                    f"{row.path} is declared secret by {', '.join(row.images)} and none of the "
                    f"{len(finding.values_files)} `ci/` values file(s) supplies it: {channels}. "
                    f"The chart does mention it, in {', '.join(finding.named_in)}, so it has "
                    "heard of the credential: this is a fixture that never sets it, or a "
                    "spelling the chart deliberately handles some other way, rather than a "
                    f"credential nothing in the chart knows about.{summary}"
                ),
            )
            continue
        report.add(
            f"undeliverable: {row.chart}",
            error(
                f"{row.path} is declared secret by {', '.join(row.images)}, no rendering of this "
                f"chart supplies it ({channels}), and no file of the chart outside its vendored "
                "contracts mentions any of those spellings. Nothing can supply this credential "
                "and nobody reading the values file would learn it exists. Checked against all "
                f"{len(finding.values_files)} `ci/` values file(s); "
                f"{'required' if row.required else 'optional'} to the image.{summary}"
            ),
        )

    for chart, workload, container, image, files, values_files in _by_container(surface.unclaimed):
        report.add(
            f"unclaimed: {chart}",
            error(
                f"{workload} container {container!r} mounts {', '.join(files)} from a Secret, and "
                "no contract this chart vendors spells any of those names, as a key or as a leaf "
                f"of a dynamic map. Under {', '.join(values_files)}. Gate 3 did not judge them: "
                "it inspects a resolved secrets directory on a declared consumer, and these are "
                "outside that reach."
            ),
        )

    for chart, workload, container, image, files, values_files in _by_container(
        surface.over_projected
    ):
        report.add(
            f"over-projected: {chart}",
            error(
                f"{workload} container {container!r} runs the {image} image and is given "
                f"{', '.join(files)}, none of which that image's own contract declares; a sibling "
                "image's does. The pod holds credentials the binary inside it never reads. Under "
                f"{', '.join(values_files)}. Gate 3 did not judge them: it inspects a resolved "
                "secrets directory on a declared consumer, and this container is outside that "
                "reach."
            ),
        )

    for elevated in surface.elevated:
        report.add(
            f"elevated: {elevated.chart}",
            warning(
                f"{elevated.path} is `secret: false` in the contract and this chart delivers it "
                f"as the Secret file {elevated.file_name!r} to {', '.join(elevated.containers)}. "
                "Chart policy electing to treat a value as more sensitive than the image does; "
                f"its text_form is {elevated.text_form!r}, so a file can supply it and gate 3 "
                "permits it. Recorded, not counted against the chart."
            ),
        )

    return report


def _by_container(
    mounts: list[Mount],
) -> list[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]]]:
    """One row per container rather than per file.

    A container over-projected six credentials has one problem, not six, and six near-identical
    lines is how a reader learns to skim a report. The values files stay in the row because which
    fixture produced it is what somebody reproducing this needs.
    """
    grouped: dict[tuple[str, str, str, str], tuple[set[str], set[str]]] = {}
    for mount in mounts:
        identity = (mount.chart, mount.workload, mount.container, mount.image)
        files, values_files = grouped.setdefault(identity, (set(), set()))
        files.add(mount.file_name)
        values_files.add(mount.values_file)

    rows = []
    for (chart, workload, container, image), (files, values_files) in sorted(grouped.items()):
        rows.append(
            (chart, workload, container, image, tuple(sorted(files)), tuple(sorted(values_files)))
        )
    return rows


def print_reconciliation(surface: Surface, report: Report) -> None:
    print(
        "==> report only: this recipe exits 0 whatever it finds, so that its first pass over an "
        "established repository can be triaged rather than merged as a red pipeline"
    )
    print(
        f"    reconciled {len(surface.charts)} chart(s) against every `ci/` values file each one "
        f"ships: {', '.join(surface.charts) or 'none'}"
    )
    print(
        "    a credential delivered under any one of them is delivered; over-projection and "
        "unclaimed names are per values file and name it"
    )
    print()
    # The findings are split across the two streams the rest of this repository splits them
    # across, so the promotion to a gate changes the exit status and nothing about the output.
    # Flushed first, or the preamble lands after them in a terminal.
    sys.stdout.flush()
    report.print(sys.stdout, sys.stderr)
    sys.stdout.flush()

    undeliverable = [entry for entry in surface.undeliverable if not entry.named_by_chart]
    unsupplied = len(surface.undeliverable) - len(undeliverable)
    print(
        f"\n{len(undeliverable)} undeliverable, {len(_by_container(surface.over_projected))} "
        f"over-projected container(s), {len(_by_container(surface.unclaimed))} container(s) with "
        f"unclaimed file(s), {unsupplied} unsupplied, {len(surface.elevated)} elevated"
    )


def surface_json(surface: Surface) -> dict:
    return {
        "charts": surface.charts,
        "values_files": surface.values_files,
        "undeliverable": [
            {"credential": asdict(entry.credential), "values_files": list(entry.values_files)}
            for entry in surface.undeliverable
        ],
        "unclaimed": [asdict(mount) for mount in surface.unclaimed],
        "over_projected": [asdict(mount) for mount in surface.over_projected],
        "elevated": [asdict(entry) for entry in surface.elevated],
        "notes": surface.notes,
    }


# --------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="The credential inventory, and what the charts deliver against it"
    )
    parser.add_argument(
        "--reconcile",
        metavar="RENDERED",
        help="reconcile against the manifests in this directory instead of listing the inventory",
    )
    parser.add_argument("--charts", default=str(CHARTS_DIR))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    charts = Path(args.charts)

    try:
        if args.reconcile:
            surface = reconcile(charts, Path(args.reconcile))
            if args.json:
                json.dump(surface_json(surface), sys.stdout, indent=2, sort_keys=True)
                print()
            else:
                print_reconciliation(surface, report_of(surface))
        elif args.json:
            json.dump(inventory_json(charts), sys.stdout, indent=2, sort_keys=True)
            print()
        else:
            print_inventory(charts)
    except (DeclarationError, cc.ContractError) as failure:
        print(f"error: {failure}", file=sys.stderr)
        return 1

    # Report only. Promoting it to a gate is this line becoming `return 1 if report.errors else 0`
    # once the findings above have been triaged, and nothing else.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
