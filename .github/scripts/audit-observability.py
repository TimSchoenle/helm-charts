#!/usr/bin/env python3
"""Static audit of every chart's Prometheus rules and Grafana dashboards.

promtool answers "does this PromQL parse" and, given a test, "does it fire". Neither question
catches the ways a rule set rots quietly, and all of these have been real in this repository:

1. **An alert nobody tests.** A rule added without a test is a rule whose first real evaluation
   happens during an incident. A test suite cannot notice its own absence.

2. **A runbook naming a label the alert cannot carry.** Prometheus renders alert annotations with
   Go templating against the alert's own label set, and a reference to a label that is not there
   resolves to the empty string rather than failing. `kubectl -n {{ $labels.namespace }} logs ...`
   on an alert whose expression aggregated `namespace` away renders as `kubectl -n logs ...` — a
   runbook that is confidently wrong, delivered at 3am, and green in every test.

3. **A recorded series whose level prefix lies.** `namespace_consumer:events_queue_depth:current`
   grouping by `consumer_name` is a series whose documented label set is wrong, which every
   downstream `by` clause and every dashboard legend then inherits.

4. **A dashboard panel querying a recorded series that does not exist.** Grafana renders an empty
   panel; nothing anywhere reports an error.

5. **A selector that escapes namespace scoping** by omitting the chart's scope placeholder, so one
   release's rules evaluate against every namespace in the cluster.

Everything is discovered rather than configured: any chart with a `rules/` or `dashboards/`
directory is audited, its test suite is expected at `charts/<chart>/rules-tests/`, and a chart
that grows rules without growing tests fails this gate.

Usage: audit-rules.py --charts charts --testdata .github/testdata
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml

# Labels the Prometheus Operator attaches to every series it scrapes. An expression reaching a raw
# metric without aggregating carries these through, so a runbook may reference them.
SCRAPE_LABELS = {"namespace", "job", "pod", "instance", "service", "container", "endpoint"}

RECORDED = re.compile(r"\b([a-z][a-z0-9_]*):([a-z][a-z0-9_]*):([a-z][a-z0-9_]*)\b")
BY_CLAUSE = re.compile(r"\bby\s*\(([^)]*)\)")
MATCHER_LABEL = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=~|!~|!=|=)\s*\"")
EQUALITY_LABEL = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*\"")
LABEL_REF = re.compile(r"\$labels\.([a-zA-Z_][a-zA-Z0-9_]*)")
ALERT_REF = re.compile(r"\b([A-Z][A-Za-z0-9]{4,})\b")


class Chart:
    """One chart's rules, dashboards, and the conventions derived from them."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.name = path.name
        # Co-located with the rules, alongside the chart's `tests/` and `ci/` directories, so a
        # rule and its test are edited together rather than a directory apart.
        self.suite = path / "rules-tests"
        self.groups = self._load_groups()
        self.scope_label = self._scope_label()
        # Multi-word label names (`consumer_name`) would otherwise be split into two labels when a
        # level prefix is decoded. Derived from the labels the chart's own rules group by, so the
        # audit configures itself instead of carrying a per-chart allowlist.
        self.multiword = sorted(
            (label for label in self._grouped_labels() if "_" in label), key=len, reverse=True
        )

    def _load_groups(self) -> list[dict]:
        groups = []
        for path in sorted((self.path / "rules").glob("*.yml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for group in doc.get("groups", []):
                group["_file"] = f"{self.name}/rules/{path.name}"
                groups.append(group)
        return groups

    def _grouped_labels(self) -> set[str]:
        labels: set[str] = set()
        for rule in self.rules:
            for clause in BY_CLAUSE.findall(rule["expr"]):
                labels.update(part.strip() for part in clause.split(",") if part.strip())
        return labels

    def _scope_label(self) -> str | None:
        """The scope placeholder label, if this chart opts into rule scoping.

        Presence of `metrics.prometheusRule.scope` in values.yaml is the opt-in; the label follows
        the chart name, matching what the chart's own `rules.scopePlaceholder` helper emits.
        """
        values = self.path / "values.yaml"
        if not values.exists():
            return None
        doc = yaml.safe_load(values.read_text(encoding="utf-8")) or {}
        rule_values = (doc.get("metrics") or {}).get("prometheusRule") or {}
        if "scope" not in rule_values:
            return None
        return f"{self.name.replace('-', '_')}_scope"

    @property
    def rules(self) -> list[dict]:
        return [rule for group in self.groups for rule in group.get("rules", [])]

    @property
    def alerts(self) -> list[tuple[str, dict]]:
        return [
            (group["_file"], rule)
            for group in self.groups
            for rule in group.get("rules", [])
            if "alert" in rule
        ]

    @property
    def recorded(self) -> set[str]:
        return {rule["record"] for rule in self.rules if "record" in rule}

    def level_labels(self, level: str) -> set[str]:
        remaining = level
        labels: set[str] = set()
        for word in self.multiword:
            if word in remaining:
                labels.add(word)
                remaining = remaining.replace(word, "_").strip("_")
        labels.update(part for part in remaining.split("_") if part)
        return labels

    def output_labels(self, expr: str) -> tuple[set[str], bool]:
        """The label set an expression's result carries, and whether that set is *complete*.

        Complete means "these labels and no others", which is the only basis on which a missing
        label can be called a defect. Three constructions give it:

          - an aggregation with `by (...)`, which drops everything it does not name;
          - a recorded series, whose level prefix is its declared label set;
          - `absent()`, which synthesises a series from the equality matchers alone.

        A bare metric selector does not. `rate(some_metric{namespace="x"}[5m])` carries the scrape
        labels *and whatever labels the metric itself defines*, which nothing here can know — so
        the labels are returned as a lower bound and marked incomplete, and callers must not
        conclude anything from a name's absence. `up` is the exception: it is synthesised by
        Prometheus and carries only the target's labels.
        """
        by_clauses = BY_CLAUSE.findall(expr)
        if by_clauses:
            # The *outermost* aggregation decides, and it is first in the string because PromQL
            # writes aggregations prefix-style: `count by (namespace) (count by (namespace,
            # version) (...))` yields `namespace`, which is the point of the inner aggregation.
            labels = {part.strip() for part in by_clauses[0].split(",") if part.strip()}
            # `le` is an input to `histogram_quantile`, not an output: the function consumes the
            # bucket dimension. Grouping by `le` is mandatory inside a quantile and a bug anywhere
            # else, so this is narrowly scoped to that call.
            if "histogram_quantile" in expr:
                labels.discard("le")
            return labels, True

        labels: set[str] = set()
        complete = False
        for match in RECORDED.finditer(expr):
            labels.update(self.level_labels(match.group(1)))
            complete = True

        matchers = {n for n in MATCHER_LABEL.findall(expr) if n != self.scope_label}

        if not complete:
            if "absent(" in expr:
                return {n for n in EQUALITY_LABEL.findall(expr) if n != self.scope_label}, True
            # `up` is Prometheus' own series about a target, so the scrape labels are all of it.
            # Any other bare metric may carry labels of its own that this cannot see.
            bare_metrics = set(re.findall(r"\b([a-z][a-z0-9_]*)\s*\{", expr)) - {"up"}
            complete = not bare_metrics
            labels |= SCRAPE_LABELS

        return labels | matchers, complete

    def dashboards(self) -> list[tuple[str, dict]]:
        return [
            (path.name, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted((self.path / "dashboards").glob("*.json"))
        ]

    def dashboard_exprs(self) -> list[tuple[str, str, str]]:
        found = []
        for filename, doc in self.dashboards():
            for panel in doc.get("panels", []):
                for target in panel.get("targets", []) or []:
                    if "expr" in target:
                        found.append((filename, panel.get("title", "?"), target["expr"]))
            for annotation in doc.get("annotations", {}).get("list", []):
                if "expr" in annotation:
                    found.append((filename, "annotation", annotation["expr"]))
            for variable in doc.get("templating", {}).get("list", []):
                if variable.get("type") == "query" and isinstance(variable.get("query"), str):
                    found.append((filename, f"variable {variable['name']}", variable["query"]))
        return found


def audit(chart: Chart) -> list[str]:
    problems: list[str] = []

    # 1. A recorded series' level prefix is its documented label set; it must be true.
    for group in chart.groups:
        for rule in group.get("rules", []):
            if "record" not in rule:
                continue
            declared = chart.level_labels(rule["record"].split(":")[0])
            produced, trustworthy = chart.output_labels(rule["expr"])
            if trustworthy and declared != produced:
                problems.append(
                    f"{group['_file']}: recording rule `{rule['record']}` declares "
                    f"{sorted(declared)} in its level prefix but groups by {sorted(produced)}. "
                    f"The level prefix is the series' documented label set; a mismatch means "
                    f"every reader and every downstream `by` clause works from a lie."
                )

    # 2. Every `$labels.X` in a runbook must be a label the alert can carry.
    for filename, rule in chart.alerts:
        produced, trustworthy = chart.output_labels(rule["expr"])
        if not trustworthy:
            continue
        referenced: set[str] = set()
        for text in rule.get("annotations", {}).values():
            referenced.update(LABEL_REF.findall(str(text)))
        missing = sorted(referenced - produced)
        if missing:
            problems.append(
                f"{filename}: alert `{rule['alert']}` references {missing} in its annotations, "
                f"but its expression produces only {sorted(produced)}. Prometheus renders a "
                f"missing label as an empty string, so the runbook ships a command with a hole "
                f"in it rather than failing."
            )

    # 3. Every alert must be exercised by the chart's test suite.
    if chart.alerts and not chart.suite.is_dir():
        problems.append(
            f"{chart.name} ships {len(chart.alerts)} alert(s) but has no test suite at "
            f"{chart.suite}. Every chart with rules needs one; see the README in an existing "
            f"chart's `rules-tests/` for the shape."
        )
    elif chart.alerts:
        tested: set[str] = set()
        for path in sorted(chart.suite.glob("*_test.yml")):
            tested.update(ALERT_REF.findall(path.read_text(encoding="utf-8")))
        untested = sorted({rule["alert"] for _f, rule in chart.alerts} - tested)
        if untested:
            problems.append(
                f"{chart.name}: {len(untested)} alert(s) have no test in {chart.suite}: "
                f"{untested}. An untested alert is one whose first real evaluation happens "
                f"during an incident."
            )

    # 4. Paging and the runbook must never be a matter of luck.
    for filename, rule in chart.alerts:
        if not (rule.get("labels") or {}).get("severity"):
            problems.append(f"{filename}: alert `{rule['alert']}` has no `severity` label.")
        for key in ("summary", "description"):
            if not (rule.get("annotations") or {}).get(key):
                problems.append(f"{filename}: alert `{rule['alert']}` has no `{key}` annotation.")

    # 5. Every selector must carry the scope placeholder, for charts that opt into scoping. The
    #    chart's own validator catches the case where *no* rule has it; this catches the far more
    #    likely one where a newly added rule forgot.
    if chart.scope_label:
        for group in chart.groups:
            for rule in group.get("rules", []):
                if chart.scope_label not in rule["expr"]:
                    name = rule.get("record") or rule.get("alert")
                    problems.append(
                        f"{group['_file']}: `{name}` carries no `{chart.scope_label}` "
                        f"placeholder, so it evaluates against every namespace in the cluster "
                        f"even when rule scoping is enabled."
                    )

    # 6. A dashboard panel must not query a recorded series nothing produces.
    for filename, title, expr in chart.dashboard_exprs():
        for match in RECORDED.finditer(expr):
            name = match.group(0)
            if name not in chart.recorded:
                problems.append(
                    f"{chart.name}/dashboards/{filename}: panel {title!r} queries `{name}`, "
                    f"which no recording rule produces. Grafana renders that as an empty panel "
                    f"and reports nothing."
                )

    problems.extend(audit_dashboards(chart))
    problems.extend(audit_readme_template(chart))
    return problems


# A correctly escaped literal: the whole action is one raw or quoted string, whatever is inside
# it. Removed before scanning, since the text it protects is exactly the text that would
# otherwise look like a defect.
ESCAPED_LITERAL = re.compile(r"\{\{-?\s*(?:`[^`]*`|\"[^\"]*\")\s*-?\}\}")

# A Go action whose body is empty or reaches for Prometheus' own `$labels`/`$value`.
UNESCAPED_ACTION = re.compile(r"\{\{-?\s*(\$(?:labels|value)\b[^}]*?|)\s*-?\}\}")


def audit_readme_template(chart: Chart) -> list[str]:
    """`README.md.gotmpl` must not contain Go actions it does not mean.

    helm-docs renders these files as Go templates, and documentation *about* alerting inevitably
    quotes the two syntaxes Go would try to evaluate: a Grafana legend `{{ }}` and a Prometheus
    annotation `{{ $labels.job }}`. Go answers the first with "missing value for command" and the
    second with "undefined variable", and helm-docs downgrades both to a **warning** — so the
    chart's README is silently left at whatever it said before, which is how a version badge ends
    up describing a release two versions old.

    The escape is a raw string: {{ `{{ $labels.job }}` }}.
    """
    template = chart.path / "README.md.gotmpl"
    if not template.exists():
        return []
    problems = []
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        for match in UNESCAPED_ACTION.finditer(ESCAPED_LITERAL.sub("", line)):
            body = match.group(1).strip() or "(empty)"
            problems.append(
                f"{chart.name}/README.md.gotmpl:{number}: `{match.group(0)}` is a Go template "
                f"action, not the literal text {body}. helm-docs fails this chart with a warning "
                f"rather than an error, leaving its README stale. Escape it as a raw string: "
                f"{{{{ `{match.group(0)}` }}}}."
            )
    return problems


# Grafana's own variables, always available and never declared in `templating.list`.
BUILTIN_VARIABLES = {
    "__all", "__interval", "__interval_ms", "__rate_interval", "__range", "__range_s",
    "__range_ms", "__dashboard", "__from", "__to", "__timeFilter", "__user", "__org",
    "__name", "__field", "__series", "__value", "__data", "__auto", "__auto_interval",
}
VARIABLE_REF = re.compile(r"\$(?:\{(\w+)(?::[^}]*)?\}|(\w+))")
LEGEND_LABEL = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def walk_datasources(node, path="$"):
    """Every `datasource` field in a dashboard, wherever it is nested."""
    if isinstance(node, dict):
        if "datasource" in node:
            yield path, node["datasource"]
        for key, value in node.items():
            yield from walk_datasources(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_datasources(value, f"{path}[{index}]")


def audit_dashboards(chart: Chart) -> list[str]:
    """Checks a rendered-empty dashboard would never announce on its own.

    The motivating defect: a dashboard pinned `"uid": "tankovault-prometheus"`, a datasource name
    provisioned by upstream's docker-compose stack and present in no Kubernetes cluster. Every
    panel pointed at a datasource that did not exist. It shipped, it installed, it rendered, and
    every graph was empty — because Grafana treats an unresolvable datasource as a query failure
    per panel, not as a broken dashboard.
    """
    problems: list[str] = []

    for filename, doc in chart.dashboards():
        where = f"{chart.name}/dashboards/{filename}"

        variables = {v.get("name"): v for v in doc.get("templating", {}).get("list", [])}
        datasource_vars = {
            name for name, v in variables.items() if v.get("type") == "datasource"
        }

        for key in ("uid", "title", "schemaVersion"):
            if not doc.get(key):
                problems.append(f"{where}: dashboard has no `{key}`.")

        # (a) No hardcoded datasource. A literal uid is only correct on the one cluster that
        #     happens to have provisioned that exact name, and silently empty everywhere else.
        for path, datasource in walk_datasources(doc):
            if datasource is None:
                problems.append(
                    f"{where}: `{path}.datasource` is null, so the panel silently falls back to "
                    f"whichever datasource Grafana considers default. Reference the dashboard's "
                    f"datasource variable instead."
                )
                continue
            uid = datasource.get("uid") if isinstance(datasource, dict) else str(datasource)
            if not uid:
                problems.append(f"{where}: `{path}.datasource` has no `uid`.")
                continue
            match = re.fullmatch(r"\$\{(\w+)\}|\$(\w+)", str(uid))
            if not match:
                problems.append(
                    f"{where}: `{path}.datasource.uid` is the literal {uid!r}. A hardcoded uid "
                    f"resolves only on a cluster that provisioned that exact name and renders an "
                    f"empty panel everywhere else. Use a `datasource`-type template variable so "
                    f"the dashboard travels."
                )
                continue
            referenced = match.group(1) or match.group(2)
            if referenced not in datasource_vars:
                problems.append(
                    f"{where}: `{path}.datasource.uid` references `${referenced}`, which is not "
                    f"a `datasource`-type template variable on this dashboard."
                )

        # (b) Every panel target must declare a datasource. Omitting it is the same silent
        #     default-datasource fallback as (a), just spelled by absence.
        for panel in doc.get("panels", []):
            if panel.get("type") == "row":
                continue
            title = panel.get("title", "?")
            if "datasource" not in panel:
                problems.append(f"{where}: panel {title!r} declares no datasource.")
            for target in panel.get("targets", []) or []:
                if "datasource" not in target:
                    problems.append(
                        f"{where}: a query on panel {title!r} declares no datasource."
                    )

        # (c) Every `$variable` used anywhere must be declared, or it reaches Prometheus as a
        #     literal `$foo` and matches nothing.
        for filename_, title, expr in chart.dashboard_exprs():
            if filename_ != filename:
                continue
            for match in VARIABLE_REF.finditer(expr):
                name = match.group(1) or match.group(2)
                if name in BUILTIN_VARIABLES or name in variables:
                    continue
                problems.append(
                    f"{where}: {title!r} uses `${name}`, which is not a template variable on "
                    f"this dashboard. Grafana sends it to Prometheus verbatim, where it matches "
                    f"nothing."
                )

        # (d) A legend placeholder naming a label the query cannot produce renders as an empty
        #     string — the same defect as a runbook naming a label it does not carry, and just as
        #     invisible.
        for panel in doc.get("panels", []):
            if panel.get("type") == "row":
                continue
            title = panel.get("title", "?")
            for target in panel.get("targets", []) or []:
                legend, expr = target.get("legendFormat"), target.get("expr")
                if not legend or not expr or "ALERTS" in expr:
                    continue
                produced, trustworthy = chart.output_labels(expr)
                if not trustworthy:
                    continue
                missing = sorted(set(LEGEND_LABEL.findall(legend)) - produced)
                if missing:
                    problems.append(
                        f"{where}: panel {title!r} has legend {legend!r} naming {missing}, "
                        f"which its query does not produce — every series would render with a "
                        f"blank in the legend."
                    )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts", required=True, type=pathlib.Path)
    args = parser.parse_args()

    charts = [
        Chart(path)
        for path in sorted(args.charts.iterdir())
        if path.is_dir() and ((path / "rules").is_dir() or (path / "dashboards").is_dir())
    ]
    if not charts:
        print("no chart ships rules or dashboards; nothing to audit")
        return 0

    failed = False
    for chart in charts:
        problems = audit(chart)
        if problems:
            failed = True
            print(f"\n{chart.name}: {len(problems)} problem(s)\n", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}\n", file=sys.stderr)
        else:
            print(
                f"    {chart.name}: {len(chart.alerts)} alerts, {len(chart.recorded)} recording "
                f"rules, {len(chart.dashboard_exprs())} dashboard queries — all sound"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
