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

   The dashboard half of that is its own defect and was its own bug here: an `ALERTS` overlay
   written without a namespace matcher draws a staging release's alert markers across production's
   graphs, on a dashboard whose every other query is scoped by the `$namespace` variable.

6. **A panel nobody can read.** A description is where a panel says what it means and what to do
   about it, and it is the only documentation a dashboard carries. Cheap to write while the query
   is fresh, and never written afterwards.

7. **A dashboard an operator can edit.** Both delivery mechanisms re-apply the JSON from the chart,
   so an edit made in Grafana is discarded on the next reload with no message. `editable: false`
   is the honest spelling.

8. **A tunable threshold whose anchor no longer matches its rule.** `rules/tunables.yaml` names,
   per alert, the exact substring of an expression an operator's `metrics.prometheusRule.thresholds`
   override rewrites. Edit the rule and the anchor silently stops matching — or worse, starts
   matching a different comparison — and the override becomes a no-op, or moves the wrong number.

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

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from rule_anchors import suggest_anchor

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
    def tunables(self) -> dict[str, dict]:
        """Declared threshold tunables, keyed alert -> tunable name -> declaration.

        Read from `rules/tunables.yaml`, which sits beside the rules and outside the `rules/*.yml`
        glob on purpose: promtool reads the rule files strictly and would reject an unknown
        top-level key, and the PrometheusRule CRD would prune one.
        """
        path = self.path / "rules" / "tunables.yaml"
        if not path.exists():
            return {}
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return doc.get("tunables") or {}

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

    # 7. Every declared tunable must still be substitutable into the rule it names.
    problems.extend(audit_tunables(chart))

    problems.extend(audit_dashboards(chart))
    problems.extend(audit_readme_template(chart))
    return problems


def audit_tunables(chart: Chart) -> list[str]:
    """Every anchor in `rules/tunables.yaml` must identify exactly one number in exactly one rule.

    Two properties, and both have to hold for a substitution to mean anything:

      - the anchor occurs exactly once in its alert's expression. Zero occurrences and the
        override is a no-op; more than one and the substitution rewrites a comparison the author
        did not mean, which is the whole reason an anchor exists rather than a bare literal.
      - the declared default occurs exactly once inside the anchor, spelled the way the
        expression spells it. `avg15m` contains a `1`, so an anchor wide enough to include the
        metric name is often too wide to carry a default of `1` unambiguously.

    Mirrors what `common.prometheus.rules.presetErrors` refuses at render time. Duplicated on
    purpose: this one runs on every pull request, against the committed files, without a render.
    """
    problems: list[str] = []
    if not chart.tunables:
        return problems

    exprs = {rule["alert"]: str(rule["expr"]) for _file, rule in chart.alerts}
    for alert, declarations in chart.tunables.items():
        if alert not in exprs:
            problems.append(
                f"{chart.name}/rules/tunables.yaml: declares tunables for `{alert}`, which the "
                f"chart does not ship. The declaration is dead, and an operator setting it would "
                f"be refused for the wrong reason."
            )
            continue
        expr = exprs[alert]
        for tunable, declaration in (declarations or {}).items():
            where = f"{chart.name}/rules/tunables.yaml: `{alert}.{tunable}`"
            anchor = str(declaration.get("anchor") or "")
            if not anchor:
                problems.append(f"{where} declares no `anchor`, so there is nothing to substitute.")
                continue
            found = expr.count(anchor)
            if found != 1:
                problems.append(
                    f"{where} anchors on {anchor!r}, which occurs {found} time(s) in that "
                    f"alert's expression. An anchor has to occur exactly once — widen it until "
                    f"it does, or the substitution rewrites the wrong comparison."
                    + repair_hint(expr, declaration.get("default"))
                )
                continue
            literal = format_default(declaration.get("default"))
            inside = anchor.count(literal)
            if inside != 1:
                problems.append(
                    f"{where} declares the default {literal}, which occurs {inside} time(s) in "
                    f"its anchor {anchor!r}. It has to occur exactly once, spelled the way the "
                    f"expression spells it, or an override edits the wrong number."
                )
    return problems


def repair_hint(expr: str, default) -> str:
    """An anchor that would work, when there is exactly one place the default could mean.

    A broken anchor is almost always the fallout of a rule edit — a selector gained a label, an
    expression was rewrapped — and the repair is mechanical: find the number again, widen until
    unique. Doing that by hand means reading PromQL and counting occurrences, which is the work
    `just add-tunable` exists to remove, so the same derivation answers here rather than leaving
    the author to it.

    Silent when the default appears more than once. Choosing between two comparisons is the
    author's call, and a confident wrong suggestion costs more than no suggestion.
    """
    suggestion = suggest_anchor(expr, format_default(default))
    if not suggestion:
        return ""
    return f"\n      `just add-tunable` would derive: {suggestion}"


def format_default(value) -> str:
    """A declared default as the rule file spells it.

    Go renders a template value with `%v`, and a YAML integer reaches Helm as an integer, so
    `172800` stays `172800` rather than becoming `172800.0` the way Python's `str(float)` would.
    Matching that here is what makes this check agree with the render-time one.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


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

# An `ALERTS` selector, and the namespace matcher it has to carry. `ALERTS` is Prometheus' own
# series about every firing alert in the cluster, so it is the one query on a dashboard that is
# unscoped by default rather than by omission.
ALERTS_SELECTOR = re.compile(r"\bALERTS(?:_FOR_STATE)?\s*\{([^}]*)\}")
NAMESPACE_MATCHER = re.compile(r'\bnamespace\s*(?:=~?|!~?|!=)\s*"')


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

        # (c2) A dashboard is re-applied from the chart by both delivery mechanisms, so an edit
        #      made in Grafana survives until the sidecar's next reload and then vanishes without
        #      a message. Better to say so than to offer an edit that cannot last.
        if doc.get("editable"):
            problems.append(
                f"{where}: `editable` is true, but both delivery mechanisms re-apply this JSON "
                f"from the chart — an edit made in Grafana is discarded on the next reload with "
                f"no message. Set it to false."
            )

        # (c3) Every panel needs a description. It is the only place a dashboard explains what a
        #      panel means, and it is written while the query is fresh or never.
        for panel in doc.get("panels", []):
            if panel.get("type") == "row":
                continue
            if not panel.get("description"):
                problems.append(
                    f"{where}: panel {panel.get('title', '?')!r} has no `description`. That is "
                    f"the only documentation a dashboard carries, and the reader of a panel is "
                    f"rarely the author of its query."
                )

        # (c4) `ALERTS` is Prometheus' own series about every alert firing anywhere in the
        #      cluster. Unlike a chart's own metrics it carries no scope of its own, so an overlay
        #      written without a namespace matcher draws a neighbouring release's alerts over
        #      these graphs — on a dashboard whose every other query is scoped by `$namespace`.
        for filename_, title, expr in chart.dashboard_exprs():
            if filename_ != filename:
                continue
            for match in ALERTS_SELECTOR.finditer(expr):
                if NAMESPACE_MATCHER.search(match.group(1)):
                    continue
                problems.append(
                    f"{where}: {title!r} queries `ALERTS` without a namespace matcher, so it "
                    f"shows alerts from every release of this chart in the cluster rather than "
                    f"the one this dashboard is scoped to. Add `namespace=\"$namespace\"` to "
                    f"the selector."
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
