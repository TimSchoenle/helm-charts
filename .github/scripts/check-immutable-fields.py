#!/usr/bin/env python3
"""Guard the fields Kubernetes refuses to update in place.

Two failure modes are checked, both of which render and lint cleanly and only surface on a
real cluster — one of them not until an upgrade of an already-running release, which is the
worst possible time to find out.

1. Version invariance of the immutable StatefulSet spec.

   The API server accepts updates to `replicas`, `ordinals`, `template`, `updateStrategy`,
   `revisionHistoryLimit`, `persistentVolumeClaimRetentionPolicy` and `minReadySeconds`, and
   rejects the entire patch if anything else moved. `volumeClaimTemplates` is the trap: it
   sits in the spec, so its *labels* are immutable too, and stamping `common.labels` onto it
   folds in `helm.sh/chart` and `app.kubernetes.io/version`. Both move on every release, so a
   chart bump that changes nothing else still produces a diff the API server refuses:

     StatefulSet.apps "..." is invalid: spec: Forbidden: updates to statefulset spec for
     fields other than ... are forbidden

   Rendering each chart at its own version and at a synthetic bumped version and diffing the
   immutable half of every StatefulSet spec catches this at PR time. Use
   `common.immutableLabels` for anything that lands in such a field.

2. Selector/template agreement.

   `spec.selector.matchLabels` must be a subset of `spec.template.metadata.labels` or the
   workload is rejected at admission with "`selector` does not match template `labels`". A
   scoped render context that resolves `common.name` differently from the root context breaks
   this, and only under `nameOverride` — which no ci/ fixture sets, so the default render
   looks perfectly healthy. Every chart is therefore rendered a second time with an explicit
   `nameOverride` purely to exercise that path.

Which StatefulSet fields are mutable is not something the Kubernetes schema records, so the
set is a constant here. `verify_against_schema` cross-checks it against the same schema
catalog kubeconform validates the rendered manifests with, which is authoritative on the
fields that exist and so catches the list drifting from the API.

Usage: python3 .github/scripts/check-immutable-fields.py
       KUBE_VERSION=1.31.0 python3 .github/scripts/check-immutable-fields.py
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

CHARTS_DIR = Path("charts")

# The API groups an offline render has to declare, shared with every other renderer in the
# repository. See the file's own header for why they are needed at all.
API_VERSIONS_FILE = Path(".github/configs/render-api-versions.txt")

# Everything the API server allows to change on an existing StatefulSet. Anything else in the
# spec is immutable and must therefore be invariant under a version bump.
#
# This list cannot be derived from the schemas the manifests are validated against, and it is
# worth being precise about why. Mutability is not part of the Kubernetes OpenAPI surface: it
# is enforced imperatively in `ValidateStatefulSetUpdate`, which copies the permitted fields
# onto the old object and rejects the request if anything else differs. The published JSON
# schema carries no `x-kubernetes-validations`, no immutability marker and no wording to parse
# — it describes shape and type only. So the set is transcribed from the API server's error
# message, which enumerates it in full, and `verify_against_schema` below pins it to the real
# schema in the one way the schema can support.
MUTABLE_STATEFULSET_SPEC_FIELDS = {
    "replicas",
    "ordinals",
    "template",
    "updateStrategy",
    "revisionHistoryLimit",
    "persistentVolumeClaimRetentionPolicy",
    "minReadySeconds",
}

# The catalog kubeconform resolves `-schema-location default` against, so the cross-check below
# reads the same schema data the manifests are already validated with in validate-manifests.sh.
SCHEMA_URL = (
    "https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/"
    "v{version}-standalone-strict/statefulset-apps-v1.json"
)

# Kept in step with the highest version in the validate-manifests matrix. Overridable so a
# toolchain bump can be tried without editing the script.
SCHEMA_KUBERNETES_VERSION = os.environ.get("KUBE_VERSION", "1.34.0")

# Workloads whose selector is immutable and must match their own pod template.
SELECTOR_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}

# Deliberately implausible as a real value, so a chart that leaks it into an unexpected place
# is easy to spot in the failure output.
PROBE_NAME_OVERRIDE = "immutability-probe"

# Bumped far beyond any real version so the synthetic render can never collide with the
# chart's own. Both fields move: `version` feeds `helm.sh/chart`, `appVersion` feeds
# `app.kubernetes.io/version`, and either one reaching an immutable field is a defect.
PROBE_VERSION = "99.99.99"


def api_version_args() -> list[str]:
    """`--api-versions` flags for every CRD group the shared list declares."""
    args: list[str] = []
    for line in API_VERSIONS_FILE.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            args += ["--api-versions", entry]
    return args


def is_library_chart(chart: Path) -> bool:
    """A library chart renders nothing on its own, so there is nothing here to check.

    Read from `Chart.yaml` rather than matched against a hard-coded name, so a second library
    chart needs no change here — matching how the shell renderers decide the same thing.
    """
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8")) or {}
    return metadata.get("type") == "library"


def statefulset_spec_properties() -> set[str] | None:
    """Every field a StatefulSet spec is allowed to contain, per the published schema."""
    url = SCHEMA_URL.format(version=SCHEMA_KUBERNETES_VERSION)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            schema = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"WARNING: could not read {url} ({error}).")
        print("WARNING: the mutable-field list was not cross-checked against the schema.")
        return None
    return set(schema["properties"]["spec"]["properties"])


def verify_against_schema(failures: list[str]) -> None:
    """Pin `MUTABLE_STATEFULSET_SPEC_FIELDS` to the schema as far as the schema allows.

    The schema cannot say which fields are mutable, but it is authoritative on which fields
    *exist*. That is enough to catch the failure mode that matters: a name in the list which is
    not a real spec field is silently inert, and the actual field it was meant to name is then
    treated as immutable — or, worse after a rename, a genuinely mutable field is diffed and
    every chart fails for the wrong reason. Either way the list has drifted from the API and
    the diff output stops meaning what it says.

    Deliberately not fatal when the schema is unreachable: this is an assertion about a
    constant, not one of the two checks the job exists to run, and a network blip should not
    fail a PR that has nothing wrong with it.
    """
    properties = statefulset_spec_properties()
    if properties is None:
        return

    unknown = MUTABLE_STATEFULSET_SPEC_FIELDS - properties
    if unknown:
        failures.append(
            f"MUTABLE_STATEFULSET_SPEC_FIELDS names {sorted(unknown)}, which the "
            f"StatefulSet schema for Kubernetes {SCHEMA_KUBERNETES_VERSION} does not define. "
            f"The list has drifted from the API. Reconcile it against "
            f"`ValidateStatefulSetUpdate` before trusting any result from this job."
        )
        return

    immutable = sorted(properties - MUTABLE_STATEFULSET_SPEC_FIELDS)
    print(
        f"Schema for Kubernetes {SCHEMA_KUBERNETES_VERSION}: treating {immutable} as "
        f"immutable.\n"
    )


def render(chart: Path, values: Path, extra: list[str] | None = None) -> list[dict]:
    """Render a chart and return its non-empty documents."""
    cmd = [
        "helm", "template", chart.name, str(chart),
        "--namespace", "default",
        *api_version_args(),
        "--values", str(values),
        *(extra or []),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"helm template failed for {chart.name} / {values.name}:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def bumped_copy(chart: Path, destination: Path) -> Path:
    """Copy a chart, vendored dependencies and all, with only its versions changed."""
    target = destination / chart.name
    shutil.copytree(chart, target)

    chart_yaml = target / "Chart.yaml"
    text = chart_yaml.read_text(encoding="utf-8")
    text = re.sub(r"^version: .*$", f"version: {PROBE_VERSION}", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^appVersion: .*$", f"appVersion: {PROBE_VERSION}", text, count=1, flags=re.MULTILINE)
    chart_yaml.write_text(text, encoding="utf-8")
    return target


def immutable_spec(statefulset: dict) -> dict:
    """The half of a StatefulSet spec the API server refuses to update."""
    spec = statefulset.get("spec") or {}
    return {k: v for k, v in spec.items() if k not in MUTABLE_STATEFULSET_SPEC_FIELDS}


def identify(doc: dict) -> str:
    metadata = doc.get("metadata") or {}
    return f"{doc.get('kind')}/{metadata.get('name')}"


def dump(value: object) -> list[str]:
    return yaml.safe_dump(value, default_flow_style=False, sort_keys=True).splitlines()


def check_version_invariance(chart: Path, values: Path, failures: list[str]) -> None:
    """Every immutable StatefulSet field must survive a version-only bump untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        bumped = bumped_copy(chart, Path(tmp))
        before = render(chart, values)
        after = render(bumped, values)

    indexed = {
        identify(doc): doc
        for doc in after
        if doc.get("kind") == "StatefulSet"
    }

    for doc in before:
        if doc.get("kind") != "StatefulSet":
            continue
        name = identify(doc)
        counterpart = indexed.get(name)
        if counterpart is None:
            failures.append(
                f"{chart.name} [{values.name}] {name}: the StatefulSet is renamed by a version "
                f"bump, so an upgrade would orphan the running one rather than update it"
            )
            continue

        diff = list(difflib.unified_diff(
            dump(immutable_spec(doc)),
            dump(immutable_spec(counterpart)),
            fromfile=f"{name} @ chart version as committed",
            tofile=f"{name} @ chart version {PROBE_VERSION}",
            lineterm="",
        ))
        if diff:
            failures.append(
                f"{chart.name} [{values.name}] {name}: immutable spec fields change under a "
                f"version-only bump, so upgrading a live release is rejected by the API "
                f"server. Render these through `common.immutableLabels`.\n"
                + "\n".join(f"    {line}" for line in diff)
            )


def check_selector_matches_template(chart: Path, values: Path, failures: list[str]) -> None:
    """A workload's immutable selector must match the pod template it ships."""
    for extra, label in (
        ([], "as configured"),
        (["--set", f"nameOverride={PROBE_NAME_OVERRIDE}"], f"nameOverride={PROBE_NAME_OVERRIDE}"),
    ):
        for doc in render(chart, values, extra):
            if doc.get("kind") not in SELECTOR_KINDS:
                continue
            spec = doc.get("spec") or {}
            selector = ((spec.get("selector") or {}).get("matchLabels")) or {}
            template_labels = (
                ((spec.get("template") or {}).get("metadata") or {}).get("labels")
            ) or {}

            mismatched = {
                key: (value, template_labels.get(key))
                for key, value in selector.items()
                if template_labels.get(key) != value
            }
            if mismatched:
                detail = "\n".join(
                    f"    {key}: selector={selector_value!r} template={template_value!r}"
                    for key, (selector_value, template_value) in sorted(mismatched.items())
                )
                failures.append(
                    f"{chart.name} [{values.name}, {label}] {identify(doc)}: "
                    f"`selector` does not match template `labels`; the API server rejects this "
                    f"workload outright. The selector and the pod template must be rendered "
                    f"against the same context.\n{detail}"
                )


def main() -> int:
    failures: list[str] = []
    checked = 0

    verify_against_schema(failures)

    for chart in sorted(CHARTS_DIR.iterdir()):
        if not chart.is_dir() or is_library_chart(chart):
            continue

        values_files = sorted((chart / "ci").glob("*.yaml"))
        if not values_files:
            print(f"No ci/ values files for {chart.name}, skipping...")
            continue

        for values in values_files:
            print(f"Checking {chart.name} with {values.name}")
            check_version_invariance(chart, values, failures)
            check_selector_matches_template(chart, values, failures)
            checked += 1

    print()
    if failures:
        print(f"{len(failures)} immutable-field violation(s) found:\n")
        for failure in failures:
            print(f"  - {failure}\n")
        return 1

    print(f"OK: {checked} (chart, values) pair(s) carry no immutable-field violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
