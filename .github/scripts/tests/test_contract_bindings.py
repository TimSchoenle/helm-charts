#!/usr/bin/env python3
"""Configuration binding markers: the grammar, the placement rule, and the six gate rules.

Every one of these has a plausible-looking wrong implementation that a run over this repository
would not expose, because all five enrolled charts are correct — which is the whole problem with
testing a gate against the tree it was written for.

Four of them are worth naming here, because getting any of them wrong makes the gate report
coverage it has not established:

  the placement rule    the marker is the first line inside the value's `@schema` block,
                        written as a YAML comment. Measured over ten placements on both pilots:
                        the schema block is the one region of the file whose comment content is
                        YAML rather than prose, so both generators discard it and both generated
                        files come back byte-identical. Every other position is refused and each
                        refusal names the damage it does, so a reader who finds one of them
                        tidier meets the measurement rather than a preference

  the values path       derived from indentation, so `csp.cloudflare.scriptNonce` is what a later
                        generator writes its probe against. A parser that returned the leaf name
                        would look right on every flat chart and be useless on the first nested
                        one

  the quoted hash       `password: "a#b"` is an ordinary value, and a parser that split on the
                        first `#` would read its key wrong and report a marker that is not there

  composed both ways    two values feeding one key is legal *only* when both say `composed`, and
                        a lone `composed` is refused. Implement only the first half and `composed`
                        becomes a way to switch off the duplicate rule

  coverage's namespace  over `schema.keys` alone. Counting `external.env` would make a chart that
                        declines to surface `RUST_LOG` look delinquent; counting the keys but
                        crediting an `external` marker for one would let a marker of the wrong
                        class satisfy the rule
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_bindings as cb  # noqa: E402
from config_declaration import DeclarationError, load_declaration  # noqa: E402
from config_report import Report  # noqa: E402
from entry import load  # noqa: E402

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"

DIGEST = "sha256:" + "1" * 64


entry = load("config_bindings_entry", "check-config-bindings.py")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------
# A chart on disk, because the gate reads two committed files and there is no point pretending
# --------------------------------------------------------------------------------------------


class ChartBuilder:
    """A throwaway `charts/` tree holding one chart, for the gate to walk."""

    def __init__(self, root: Path, name: str = "fixture"):
        self.root = root
        self.name = name
        self.dir = root / name
        (self.dir / "contracts").mkdir(parents=True)
        (self.dir / "Chart.yaml").write_text(
            f"name: {name}\nversion: 1.0.0\nappVersion: v1\n", encoding="utf-8"
        )

    def contract(self, document: str, contract: dict) -> ChartBuilder:
        (self.dir / "contracts" / f"{document}.json").write_text(
            json.dumps(
                {
                    "source": {
                        "image": "docker.io/example/app",
                        "digest": DIGEST,
                        "sha256": "0" * 64,
                        "fetched": "2026-01-01T00:00:00Z",
                    },
                    "contract": contract,
                }
            ),
            encoding="utf-8",
        )
        return self

    def values(self, text: str) -> ChartBuilder:
        (self.dir / "values.yaml").write_text(text, encoding="utf-8")
        return self

    def declaration(self, body: str) -> ChartBuilder:
        (self.dir / "config-contract.yaml").write_text(body, encoding="utf-8")
        return self

    def run(self) -> Report:
        report = Report()
        entry.run(self.root, report)
        return report


def messages(report: Report) -> str:
    return "\n".join(finding.message for _, finding in report.errors)


DECLARATION = """\
bindings: true
documents:
  - name: api
    source:
      kind: ConfigMap
      selector: { app: fixture }
      key: config.toml
    images:
      - values: image
        contract: contracts/api.json
"""


def declaration_with(unbound: str) -> str:
    """The fixture declaration plus a chart-level `unbound` block, which is not indented."""
    return DECLARATION + unbound


def bound(line: str, marker: str, schema: str = "type: string") -> str:
    """One value with its marker where the format puts it: first inside the `@schema` block.

    A helper rather than fifteen spelt-out blocks, so the placement lives in one place here too.
    The tests that are *about* the placement spell their own values out instead, because a helper
    that produced the wrong one would make them pass.
    """
    indent = line[: len(line) - len(line.lstrip(" "))]
    block = ("# @schema", f"# # {marker}", f"# {schema}", "# @schema", "# -- Description.")
    return "".join(f"{indent}{entry}\n" for entry in block) + line + "\n"


# Every `api` key bound, so a case that adds nothing starts from a passing chart and the failure
# under test is the only difference.
COVERED = (
    bound("sessionTtl: 3600", "@config projection auth.session_ttl", "type: integer")
    + bound('databaseUrl: ""', "@config projection database.url")
    + bound("logLevel: info", "@config projection log.level")
    + bound("repos: {}", "@config structured github.repos", "type: object")
)


# --------------------------------------------------------------------------------------------
# The grammar
# --------------------------------------------------------------------------------------------


class TestGrammar(unittest.TestCase):
    """`@config <class> <target> [optional] [when <values-path>]`, and nothing else."""

    def test_a_bare_projection(self):
        self.assertEqual(
            cb.parse_marker("projection isr.ttl_secs"),
            (cb.PROJECTION, None, "isr.ttl_secs", False, None),
        )

    def test_every_class_is_accepted(self):
        for cls in cb.CLASSES:
            self.assertEqual(cb.parse_marker(f"{cls} a.b")[0], cls)

    def test_optional(self):
        self.assertEqual(
            cb.parse_marker("projection telemetry.sentry_dsn optional"),
            (cb.PROJECTION, None, "telemetry.sentry_dsn", True, None),
        )

    def test_a_condition(self):
        self.assertEqual(
            cb.parse_marker("projection metrics.ip when metrics.enabled"),
            (cb.PROJECTION, None, "metrics.ip", False, "metrics.enabled"),
        )

    def test_optional_and_a_condition_compose(self):
        self.assertEqual(
            cb.parse_marker("projection a.b optional when c.d"),
            (cb.PROJECTION, None, "a.b", True, "c.d"),
        )

    def test_a_scoped_target(self):
        self.assertEqual(
            cb.parse_marker("projection bootstrap:auth.session_ttl"),
            (cb.PROJECTION, ("bootstrap",), "auth.session_ttl", False, None),
        )

    def test_a_scope_of_several_documents(self):
        """What `tankovault`'s branding needs: three documents declare it, two are sent it."""
        self.assertEqual(
            cb.parse_marker("projection api,frontend:branding.name")[1],
            ("api", "frontend"),
        )

    def test_an_unscoped_target_is_every_document_declaring_it(self):
        """`None` rather than a list, because the set is the contracts' answer and not the
        marker's — a document added later declaring the same key is bound by the same line."""
        self.assertIsNone(cb.parse_marker("projection auth.session_ttl")[1])

    def test_a_scope_naming_one_document_twice_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "names a document twice"):
            cb.parse_marker("projection api,api:auth.session_ttl")

    def test_an_external_variable(self):
        self.assertEqual(cb.parse_marker("external RUST_LOG")[1:3], (None, "RUST_LOG"))

    def test_an_unknown_class_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "unknown class 'gated'"):
            cb.parse_marker("gated metrics.ip")

    def test_a_missing_target_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "names no target"):
            cb.parse_marker("projection")

    def test_a_dangling_when_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "names no values path"):
            cb.parse_marker("projection a.b when")

    def test_the_suffix_order_is_fixed(self):
        """One relationship, one spelling — the reversed order is not silently accepted."""
        with self.assertRaisesRegex(cb.BindingError, "in that order"):
            cb.parse_marker("projection a.b when c.d optional")

    def test_trailing_prose_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "unexpected"):
            cb.parse_marker("projection a.b because reasons")


# --------------------------------------------------------------------------------------------
# The parser: attachment, values paths, and the placement rule
# --------------------------------------------------------------------------------------------


class TestParser(unittest.TestCase):
    def parse(self, text: str) -> list[cb.Marker]:
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "values.yaml"
            path.write_text(text, encoding="utf-8")
            return cb.parse_values(path, "fixture")

    def test_a_marker_is_the_first_line_inside_the_value_s_schema_block(self):
        markers = self.parse(
            "# @schema\n"
            "# # @config projection isr.ttl_secs\n"
            "# type: integer\n"
            "# @schema\n"
            "# -- Revalidation interval in seconds.\n"
            "ttlSecs: 0\n"
        )
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].values_path, "ttlSecs")
        self.assertEqual(markers[0].target, "isr.ttl_secs")

    def test_the_line_reported_is_the_marker_s_own(self):
        """What a reader has to edit, which is no longer the line the value is on."""
        markers = self.parse(bound("ttlSecs: 0", "@config projection isr.ttl_secs"))
        self.assertEqual(markers[0].line, 2)

    def test_the_values_path_is_the_full_dotted_path(self):
        """Derived from the *value's* indentation, not the marker's, though they agree here."""
        markers = self.parse(
            "csp:\n"
            "  cloudflare:\n"
            + bound("    scriptNonce: true", "@config projection csp.cloudflare.script_nonce")
        )
        self.assertEqual(markers[0].values_path, "csp.cloudflare.scriptNonce")

    def test_a_sibling_key_closes_the_nesting(self):
        markers = self.parse(
            "csp:\n"
            "  cloudflare:\n"
            "    scriptNonce: true\n"
            + bound("ttlSecs: 0", "@config projection isr.ttl_secs")
        )
        self.assertEqual(markers[0].values_path, "ttlSecs")

    def test_a_marker_on_a_key_that_opens_a_map(self):
        """`structured` binds a subtree, so the block sits above a key with no scalar on it."""
        markers = self.parse(
            bound("entries:", "@config structured bucket.entries", "type: object")
            + "  first: x\n"
        )
        self.assertEqual((markers[0].values_path, markers[0].cls), ("entries", cb.STRUCTURED))

    def test_the_schema_itself_is_untouched_by_the_parser(self):
        """Only the marker's own line is read; the schema around it is helm-schema's business."""
        markers = self.parse(
            "# @schema\n"
            "# # @config projection isr.ttl_secs\n"
            "# type: integer\n"
            "# minimum: 0\n"
            "# enum: [0, 1]\n"
            "# @schema\n"
            "# -- Revalidation interval in seconds.\n"
            "ttlSecs: 0\n"
        )
        self.assertEqual([marker.values_path for marker in markers], ["ttlSecs"])

    def test_every_other_placement_is_refused_with_what_it_costs(self):
        """Ten placements were measured on both pilots; nine of them are refused here.

        Four are byte-clean and still refused, because one relationship has one spelling: the
        block's last line, a schema line's own trailing comment, `# config:` as a schema key, and
        a marker separated from the block by a blank line. The rest each break something — a
        leading newline in fifteen generated descriptions, fifteen fouled README rows, a `helm
        schema` that will not parse the block at all — and the message says which.
        """
        cases = {
            # Outside the delimiters: both generators read that run as the description.
            "own comment line above the description": (
                "# @schema\n# type: integer\n# @schema\n"
                "# @config projection isr.ttl_secs\n# -- Interval.\nttlSecs: 0\n",
                "comment line of its own",
            ),
            "own comment line below the description": (
                "# @schema\n# type: integer\n# @schema\n"
                "# -- Interval.\n# @config projection isr.ttl_secs\nttlSecs: 0\n",
                "comment line of its own",
            ),
            "no block at all": (
                "# @config projection isr.ttl_secs\nttlSecs: 0\n",
                "comment line of its own",
            ),
            # Inside the delimiters, but as schema content: `@` is a reserved YAML indicator.
            "bare inside the block": (
                "# @schema\n# @config projection isr.ttl_secs\n# type: integer\n# @schema\n"
                "# -- Interval.\nttlSecs: 0\n",
                "reserved indicator in YAML",
            ),
            # Inside the delimiters and byte-clean, but below the schema rather than above it.
            "below the schema": (
                "# @schema\n# type: integer\n# # @config projection isr.ttl_secs\n# @schema\n"
                "# -- Interval.\nttlSecs: 0\n",
                "below the schema",
            ),
            # Still on the value line, which is where this format started.
            "the value's trailing comment": (
                "# @schema\n# type: integer\n# @schema\n"
                "# -- Interval.\nttlSecs: 0  # @config projection isr.ttl_secs\n",
                "the value's trailing comment",
            ),
        }
        for name, (values, expected) in cases.items():
            with self.subTest(placement=name):
                with self.assertRaisesRegex(cb.BindingError, expected):
                    self.parse(values)

    def test_a_value_may_bind_several_keys(self):
        """`tankovault`'s `internal.tls.certDir` is the directory three contract keys are built
        from, by three `printf`s in one template. Under the trailing-comment placement a second
        marker could not be written at all, and that was mistaken for a rule."""
        markers = self.parse(
            "# @schema\n"
            "# # @config composed internal.tls.cert\n"
            "# # @config composed internal.tls.key\n"
            "# # @config composed internal.tls.ca\n"
            "# type: string\n"
            "# @schema\n"
            "certDir: /etc/tls\n"
        )
        self.assertEqual(
            [(marker.values_path, marker.target) for marker in markers],
            [
                ("certDir", "internal.tls.cert"),
                ("certDir", "internal.tls.key"),
                ("certDir", "internal.tls.ca"),
            ],
        )
        self.assertEqual([marker.line for marker in markers], [2, 3, 4])

    def test_one_value_binding_one_key_twice_is_refused(self):
        """The only duplicate that cannot be meant: one binding said twice."""
        with self.assertRaisesRegex(cb.BindingError, "already binds"):
            self.parse(
                "# @schema\n"
                "# # @config projection isr.ttl_secs\n"
                "# # @config projection isr.ttl_secs\n"
                "# type: integer\n"
                "# @schema\n"
                "ttlSecs: 0\n"
            )

    def test_a_marker_below_the_schema_is_refused(self):
        """The run is at the top of the block, so a value's bindings are read in one place."""
        with self.assertRaisesRegex(cb.BindingError, "below the schema"):
            self.parse(
                "# @schema\n"
                "# type: integer\n"
                "# # @config projection isr.ttl_secs\n"
                "# @schema\n"
                "ttlSecs: 0\n"
            )

    def test_a_marker_whose_block_never_reaches_a_value_is_refused(self):
        """The cost of a placement attached by contiguity: every way it can miss is an error."""
        for values, expected in (
            (bound("ttlSecs: 0", "@config projection isr.ttl_secs").replace(
                "# -- Description.\n", "# -- Description.\n\n"
            ), "a blank line"),
            ("# @schema\n# # @config projection isr.ttl_secs\n# type: integer\n# @schema\n",
             "the end of the file"),
            ("# @schema\n# # @config projection isr.ttl_secs\n# type: integer\n# @schema\n"
             "# @schema\n# type: string\n# @schema\nother: x\n",
             "a second `@schema` block"),
        ):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(cb.BindingError, expected):
                    self.parse(values)

    def test_a_marker_on_a_sequence_item_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "sequence item"):
            self.parse("hosts:\n  - example.com  # @config projection a.b\n")

    def test_a_block_whose_value_is_a_sequence_item_is_refused(self):
        with self.assertRaisesRegex(cb.BindingError, "a sequence item"):
            self.parse(
                "hosts:\n"
                "  # @schema\n"
                "  # # @config projection a.b\n"
                "  # type: string\n"
                "  # @schema\n"
                "  - example.com\n"
            )

    def test_a_hash_inside_a_quoted_value_is_not_a_comment(self):
        markers = self.parse(
            'password: "a # b"\n' + bound("ttlSecs: 0", "@config projection isr.ttl_secs")
        )
        self.assertEqual([marker.values_path for marker in markers], ["ttlSecs"])

    def test_a_value_holding_a_hash_still_takes_its_marker(self):
        markers = self.parse(
            bound('dsn: "https://x#y"', "@config projection telemetry.sentry_dsn")
        )
        self.assertEqual(markers[0].target, "telemetry.sentry_dsn")

    def test_a_word_that_merely_begins_like_the_marker_is_not_one(self):
        self.assertEqual(
            self.parse(
                "# @schema\n# # @configuration of sorts\n# type: integer\n# @schema\n"
                "ttlSecs: 0\n"
            ),
            [],
        )

    def test_keys_inside_a_sequence_do_not_disturb_the_nesting(self):
        markers = self.parse(
            "ingress:\n"
            "  hosts:\n"
            "    - host: example.com\n"
            "      path: /\n"
            + bound("ttlSecs: 0", "@config projection isr.ttl_secs")
        )
        self.assertEqual(markers[0].values_path, "ttlSecs")

    def test_a_block_scalar_body_does_not_disturb_the_nesting(self):
        markers = self.parse(
            "script: |\n"
            "  key: not-a-values-path\n"
            + bound("ttlSecs: 0", "@config projection isr.ttl_secs")
        )
        self.assertEqual(markers[0].values_path, "ttlSecs")

    def test_a_file_with_no_marker_yields_nothing(self):
        self.assertEqual(self.parse("# -- Just a value.\nttlSecs: 0\n"), [])

    def test_every_problem_in_a_file_is_reported_at_once(self):
        """The posture every gate in this group takes: one broken line does not hide the rest."""
        with self.assertRaises(cb.BindingError) as raised:
            self.parse(
                bound("first: 1", "@config gated a.b")
                + bound("second: 2", "@config projection")
            )
        self.assertIn("values.yaml:2", str(raised.exception))
        self.assertIn("values.yaml:8", str(raised.exception))

class TestUnboundDeclaration(unittest.TestCase):
    """`config-contract.yaml` rejects unknown keys by design, so this had to be a real extension."""

    def load(self, body: str):
        with tempfile.TemporaryDirectory() as workspace:
            chart = Path(workspace) / "fixture"
            chart.mkdir()
            (chart / "config-contract.yaml").write_text(body, encoding="utf-8")
            return load_declaration(chart)

    def test_an_entry_is_read(self):
        declaration = self.load(
            declaration_with(
                "unbound:\n"
                "  - keys: [discord.webhook_url]\n"
                "    reason: Delivered as a secret file.\n"
            )
        )
        [unbound] = declaration.unbound
        self.assertEqual(unbound.keys, ("discord.webhook_url",))
        self.assertEqual(unbound.reason, "Delivered as a secret file.")
        self.assertIsNone(unbound.documents)

    def test_one_reason_covers_several_keys(self):
        """What `tankovault` needs: 144 keys and three sentences, not 144 sentences."""
        declaration = self.load(
            declaration_with(
                "unbound:\n"
                "  - keys: [a.one, a.two, a.three]\n"
                "    reason: The image's own default stands.\n"
            )
        )
        self.assertEqual(declaration.unbound[0].keys, ("a.one", "a.two", "a.three"))

    def test_a_scope_narrows_the_write_off(self):
        declaration = self.load(
            declaration_with(
                "unbound:\n"
                "  - keys: [branding.name]\n"
                "    documents: [api]\n"
                "    reason: Only the frontend is sent this half of the block.\n"
            )
        )
        self.assertEqual(declaration.unbound[0].documents, ("api",))

    def test_a_scope_naming_no_declared_document_is_refused(self):
        with self.assertRaisesRegex(DeclarationError, "which this chart does not declare"):
            self.load(
                declaration_with(
                    "unbound:\n  - keys: [a.b]\n    documents: [worker]\n"
                    "    reason: Because.\n"
                )
            )

    def test_one_key_written_off_twice_is_refused(self):
        """Two reasons for one key means one of them is not the reason."""
        with self.assertRaisesRegex(DeclarationError, "already written off"):
            self.load(
                declaration_with(
                    "unbound:\n"
                    "  - keys: [a.b]\n    reason: One.\n"
                    "  - keys: [a.b]\n    reason: Two.\n"
                )
            )

    def test_an_absent_list_is_empty_rather_than_missing(self):
        self.assertEqual(self.load(DECLARATION).unbound, [])

    def test_a_reason_is_mandatory(self):
        with self.assertRaisesRegex(DeclarationError, "no `reason`"):
            self.load(declaration_with("unbound:\n  - keys: [discord.webhook_url]\n"))

    def test_an_entry_with_no_keys_is_refused(self):
        with self.assertRaisesRegex(DeclarationError, "`keys` is missing or empty"):
            self.load(declaration_with("unbound:\n  - reason: Because.\n"))

    def test_there_is_no_pattern_form(self):
        """The keys are listed, so a key the next release adds cannot be pre-emptively covered."""
        with self.assertRaisesRegex(DeclarationError, "`keys` is missing or empty"):
            self.load(declaration_with("unbound:\n  - keys: []\n    reason: Because.\n"))

    def test_an_unknown_field_is_refused(self):
        with self.assertRaisesRegex(DeclarationError, "unknown key"):
            self.load(
                declaration_with(
                    "unbound:\n  - keys: [a.b]\n"
                    "    reason: Because.\n    gates: [env]\n"
                )
            )

    def test_the_gate_exemption_axis_is_untouched(self):
        """`exempt` is per fixture, per gate and per document; `unbound` is per key and per chart.

        They sit at different levels of the file now, which is the clearest statement yet that
        they are different axes: one relaxes a check for one rendered fixture, the other records
        that no value reaches a key anywhere.
        """
        declaration = self.load(
            DECLARATION.replace(
                "documents:\n",
                "unbound:\n  - keys: [database.url]\n"
                "    reason: Delivered as a secret file.\ndocuments:\n",
            )
            + "    exempt:\n"
            "      - values: '*'\n"
            "        gates: [closed]\n"
            "        reason: The chart renders a key the contract omits.\n"
        )
        self.assertEqual(declaration.documents[0].relaxed("ci/anything.yaml"), {"closed"})
        self.assertEqual([entry.keys for entry in declaration.unbound], [("database.url",)])


# --------------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------------


class GateCase(unittest.TestCase):
    def check(self, values: str, unbound: str = "", contract: dict | None = None) -> Report:
        with tempfile.TemporaryDirectory() as workspace:
            builder = ChartBuilder(Path(workspace))
            builder.contract("api", contract if contract is not None else fixture("api"))
            builder.declaration(declaration_with(unbound))
            builder.values(values)
            return builder.run()

    def assertPasses(self, report: Report) -> None:
        self.assertEqual(messages(report), "")

    def assertFails(self, report: Report, pattern: str) -> None:
        self.assertRegex(messages(report), pattern)


class TestEnrolment(GateCase):
    def test_a_chart_that_does_not_declare_bindings_is_not_checked(self):
        with tempfile.TemporaryDirectory() as workspace:
            builder = ChartBuilder(Path(workspace))
            builder.contract("api", fixture("api"))
            builder.declaration(DECLARATION.replace("bindings: true\n", ""))
            builder.values("# -- Nothing bound here.\nttlSecs: 0\n")
            report = Report()
            self.assertEqual(entry.run(Path(workspace), report), [])
            self.assertEqual(report.findings, [])

    def test_a_declared_chart_owes_every_key_of_its_contract(self):
        self.assertFails(
            self.check(bound("sessionTtl: 3600", "@config projection auth.session_ttl")),
            r"declares 'database\.url' and no chart value binds it",
        )

    def test_markers_without_the_switch_are_refused(self):
        """The half that inferring enrolment got right, kept: a marker nothing holds is worse."""
        with tempfile.TemporaryDirectory() as workspace:
            builder = ChartBuilder(Path(workspace))
            builder.contract("api", fixture("api"))
            builder.declaration(DECLARATION.replace("bindings: true\n", ""))
            builder.values(bound("sessionTtl: 3600", "@config projection auth.session_ttl"))
            self.assertFails(builder.run(), "does not declare `bindings: true`")

    def test_the_switch_without_markers_is_refused(self):
        """The half it got wrong. A chart that loses its markers used to leave in silence."""
        with tempfile.TemporaryDirectory() as workspace:
            builder = ChartBuilder(Path(workspace))
            builder.contract("api", fixture("api"))
            builder.declaration(DECLARATION)
            builder.values("# -- Nothing bound here.\nttlSecs: 0\n")
            self.assertFails(builder.run(), "carries no `@config` marker")

    def test_markers_with_no_declaration_at_all_are_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            builder = ChartBuilder(Path(workspace))
            builder.values(bound("sessionTtl: 3600", "@config projection auth.session_ttl"))
            self.assertFails(builder.run(), "no config-contract.yaml")

class TestRule1TargetExists(GateCase):
    def test_a_covered_chart_passes(self):
        self.assertPasses(self.check(COVERED))

    def test_an_unknown_contract_path_fails(self):
        self.assertFails(
            self.check(COVERED + bound("typo: 1", "@config projection auth.session_tt1")),
            r"contract key 'auth\.session_tt1', which no contract this chart declares carries "
            r"\(did you mean auth\.session_ttl\?\)",
        )

    def test_an_unknown_external_variable_fails(self):
        self.assertFails(
            self.check(COVERED + bound("port: 1", "@config external PORTT")),
            r"external\.env variable 'PORTT'",
        )

    def test_a_contract_key_named_as_an_external_variable_fails(self):
        """The two namespaces are separate, and a class picks exactly one of them."""
        self.assertFails(
            self.check(COVERED + bound("ttl: 1", "@config external auth.session_ttl")),
            r"external\.env variable 'auth\.session_ttl'",
        )

    def test_a_scope_naming_an_undeclared_document_fails(self):
        self.assertFails(
            self.check(COVERED + bound("x: 1", "@config projection worker:auth.session_ttl")),
            "is scoped to 'worker', which this chart does not declare",
        )


class TestRule2ClassMatchesTheKey(GateCase):
    def test_structured_on_a_scalar_key_fails(self):
        values = COVERED.replace(
            "# @config projection auth.session_ttl", "# @config structured auth.session_ttl"
        )
        self.assertFails(self.check(values), "the contract calls 'auth.session_ttl' a scalar")

    def test_projection_on_a_structured_key_fails(self):
        values = COVERED.replace(
            "# @config structured github.repos", "# @config projection github.repos"
        )
        self.assertFails(self.check(values), "the contract calls 'github.repos' structured")


class TestRule3TheCondition(GateCase):
    def test_a_condition_naming_a_real_values_path_passes(self):
        self.assertPasses(
            self.check(
                "metrics:\n  enabled: false\n"
                + COVERED.replace(
                    "# @config projection log.level",
                    "# @config projection log.level when metrics.enabled",
                )
            )
        )

    def test_a_condition_naming_nothing_fails(self):
        self.assertFails(
            self.check(
                COVERED.replace(
                    "# @config projection log.level",
                    "# @config projection log.level when metrics.enabled",
                )
            ),
            "no value at that path",
        )


class TestRule4Uniqueness(GateCase):
    def test_a_value_cannot_carry_two_markers_at_all(self):
        """No gate rule covers it because `parse_values` refuses it first, two ways.

        A second marker on one line is prose inside the first, which `parse_marker` refuses; a
        second marker line in one block is refused by the parser's own rule. Under the earlier
        placement the second case could not be written down at all, and this is the test that
        records the difference.
        """
        with self.assertRaisesRegex(cb.BindingError, "unexpected"):
            self.check(
                COVERED + bound("both: 1", "@config projection log.level @config projection a.b")
            )

    def test_two_values_binding_one_key_fail(self):
        self.assertFails(
            self.check(COVERED + bound("second: info", "@config projection log.level")),
            r"'log\.level' is bound by 2 values",
        )

    def test_two_composed_values_binding_one_key_pass(self):
        values = COVERED.replace(
            bound("logLevel: info", "@config projection log.level"),
            bound("host: 0.0.0.0", "@config composed log.level")
            + bound("port: 8080", "@config composed log.level"),
        )
        self.assertPasses(self.check(values))

    def test_a_mixed_pair_fails(self):
        values = COVERED.replace(
            bound("logLevel: info", "@config projection log.level"),
            bound("host: 0.0.0.0", "@config composed log.level")
            + bound("port: 8080", "@config projection log.level"),
        )
        self.assertFails(self.check(values), "only `composed` admits several")

    def test_a_lone_composed_is_legitimate(self):
        """One value and a literal the chart supplies — `tankovault`'s `metrics.listen`.

        This used to be refused, on the reasoning that a composition of one is a projection. It
        is not: `printf "0.0.0.0:%v" .Values.metrics.port` produces a key whose text is not the
        value's text, and there is no third thing to call it. The rule was rejecting a shape that
        occurs, so it went; what it protected against — `composed` used to wave away the duplicate
        rule — is unreachable, because that rule only fires when two values bind one key.
        """
        values = COVERED.replace(
            "# @config projection log.level", "# @config composed log.level"
        )
        self.assertPasses(self.check(values))


class TestRule5Coverage(GateCase):
    def test_a_missing_key_fails(self):
        self.assertFails(
            self.check(
                COVERED.replace(
                    bound("repos: {}", "@config structured github.repos", "type: object"), ""
                )
            ),
            r"declares 'github\.repos' and no chart value binds it",
        )

    def test_an_unbound_entry_covers_it(self):
        self.assertPasses(
            self.check(
                COVERED.replace(
                    bound("repos: {}", "@config structured github.repos", "type: object"), ""
                ),
                unbound="unbound:\n  - keys: [github.repos]\n    reason: Not offered.\n",
            )
        )

    def test_a_secret_key_is_not_exempt_by_default(self):
        """Silence is never an answer: `database.url` is `secret: true` and still owed."""
        report = self.check(
            COVERED.replace(bound('databaseUrl: ""', "@config projection database.url"), "")
        )
        self.assertFails(report, r"declares 'database\.url' and no chart value binds it")
        self.assertFails(report, "check-config-secrets")

    def test_an_unbound_entry_naming_no_contract_key_fails(self):
        self.assertFails(
            self.check(
                COVERED, unbound="unbound:\n  - keys: [github.repo]\n    reason: Typo.\n"
            ),
            r"writes off 'github\.repo', which no contract this chart declares carries",
        )

    def test_a_key_both_bound_and_unbound_fails(self):
        self.assertFails(
            self.check(
                COVERED,
                unbound="unbound:\n  - keys: [github.repos]\n    reason: Not offered.\n",
            ),
            "while a marker binds it",
        )

    def test_external_variables_are_not_owed(self):
        """`PORT` belongs to the toolchain, not to the loader; declining to surface it is fine."""
        self.assertPasses(self.check(COVERED))

    def test_an_external_marker_does_not_credit_a_contract_key(self):
        contract = copy.deepcopy(fixture("api"))
        contract["external"]["env"].append(
            {
                "name": "GITHUB_REPOS",
                "owner": "octocrab",
                "docs": "Unrelated.",
                "ty": "String",
                "values": [],
                "constraint": {"type": "string"},
                "text_form": "text",
                "default": None,
                "required": False,
                "secret": False,
            }
        )
        values = COVERED.replace(
            bound("repos: {}", "@config structured github.repos", "type: object"),
            bound("repos: {}", "@config external GITHUB_REPOS", "type: object"),
        )
        self.assertFails(
            self.check(values, contract=contract),
            r"declares 'github\.repos' and no chart value binds it",
        )


# --------------------------------------------------------------------------------------------
# The pilots, as they are committed
# --------------------------------------------------------------------------------------------


class TestTheEnrolledCharts(unittest.TestCase):
    """Every enrolled chart, read from the tree rather than rebuilt.

    Between them they carry each shape the format claims to have, which is the point of asserting
    them here rather than only over fixtures:

      `portfolio`               twenty-one pure projections, plus the three `external.env`
                                variables the Dioxus toolchain and `tracing` own — and the only
                                chart whose Sentry block sits outside `telemetry`
      `netcup-offer-bot`        an optional projection, a pair gated on `metrics.enabled`, a
                                subtree gated on `telemetry.sentry.enabled`, keys that are
                                optional *and* gated, and two credentials written off rather
                                than marked
      `s3-bucket-perma-link`    the only `structured` binding in the tree, three credentials
                                delivered as files, and `attach_stacktrace` — singular, where
                                its three siblings spell the same setting plural
      `mp-stats-legacy-viewer`  the only `composed` one: `server.bind_addr` is `printf` over two
                                values, and both say so

    `tankovault` is deliberately absent — see `test_tankovault_is_not_enrolled_yet`.
    """

    charts = SCRIPTS.parents[1] / "charts"

    def markers(self, chart: str) -> dict[str, cb.Marker]:
        found = cb.parse_values(self.charts / chart / "values.yaml", chart)
        return {marker.values_path: marker for marker in found}

    def test_portfolio_binds_an_external_variable(self):
        marker = self.markers("portfolio")["server.port"]
        self.assertEqual((marker.cls, marker.target), (cb.EXTERNAL, "PORT"))

    def test_portfolio_projects_a_nested_value(self):
        marker = self.markers("portfolio")["csp.cloudflare.scriptNonce"]
        self.assertEqual(marker.target, "csp.cloudflare.script_nonce")

    def test_netcup_marks_the_sentry_switch_optional(self):
        """`enabled: false` is falsy, so the whole block is omitted rather than written off."""
        marker = self.markers("netcup-offer-bot")["telemetry.sentry.enabled"]
        self.assertTrue(marker.optional)
        self.assertIsNone(marker.condition)

    def test_netcup_gates_the_rest_of_the_sentry_block_on_the_switch(self):
        """Every key under it is inert while the switch is down, so none is written then."""
        markers = self.markers("netcup-offer-bot")
        for path in ("telemetry.sentry.sampleRate", "telemetry.sentry.captureLevel"):
            self.assertEqual(markers[path].condition, "telemetry.sentry.enabled")

    def test_netcup_marks_the_derived_sentry_tags_optional_and_gated(self):
        """Both at once: only written when the switch is on, and omitted when left empty."""
        marker = self.markers("netcup-offer-bot")["telemetry.sentry.environment"]
        self.assertTrue(marker.optional)
        self.assertEqual(marker.condition, "telemetry.sentry.enabled")

    def test_netcup_gates_the_metrics_subtree(self):
        for path in ("metrics.ip", "metrics.port"):
            marker = self.markers("netcup-offer-bot")[path]
            self.assertEqual(marker.condition, "metrics.enabled")

    def test_netcup_writes_off_its_credentials_rather_than_marking_them(self):
        """Both travel the Secret rather than `config.toml`, so neither carries a marker."""
        markers = self.markers("netcup-offer-bot")
        self.assertNotIn("discord.webhookUrl", markers)
        self.assertNotIn("telemetry.sentry.dsn", markers)
        declaration = load_declaration(self.charts / "netcup-offer-bot")
        [unbound] = declaration.unbound
        self.assertEqual(unbound.keys, ("discord.webhook_url", "telemetry.sentry.dsn"))
        self.assertTrue(unbound.reason)

    def test_the_structured_binding_is_the_bucket_map(self):
        """The operator names the keys under it, which is the whole of what `structured` says."""
        marker = self.markers("s3-bucket-perma-link")["bucket.entries"]
        self.assertEqual((marker.cls, marker.target), (cb.STRUCTURED, "bucket.entries"))

    def test_the_s3_credentials_are_written_off_rather_than_marked(self):
        declaration = load_declaration(self.charts / "s3-bucket-perma-link")
        [unbound] = declaration.unbound
        self.assertEqual(
            sorted(unbound.keys),
            ["s3.access_key", "s3.secret_key", "telemetry.sentry.dsn"],
        )
        self.assertIn("check-config-secrets", unbound.reason)

    def test_s3_spells_the_stacktrace_key_the_way_its_image_does(self):
        """Singular here and plural in all three sibling charts, which is the trap.

        A plural marker would name a key this contract does not carry and the gate would say so;
        a plural *projection* with a correct marker would not, so the assertion is worth having
        next to the one the gate already makes.
        """
        marker = self.markers("s3-bucket-perma-link")["telemetry.sentry.attachStacktrace"]
        self.assertEqual(marker.target, "telemetry.sentry.attach_stacktrace")

    def test_portfolio_keeps_sentry_outside_the_telemetry_namespace(self):
        """The other three nest it under `telemetry`; this image does not, and copying wins
        nothing.
        """
        marker = self.markers("portfolio")["sentry.enabled"]
        self.assertEqual(marker.target, "sentry.enabled")

    def test_the_composed_pair_is_the_bind_address(self):
        """Two values, one key, and a `printf` between them that no marker claims to reproduce."""
        markers = self.markers("mp-stats-legacy-viewer")
        for path in ("server.host", "server.port"):
            self.assertEqual(
                (markers[path].cls, markers[path].target), (cb.COMPOSED, "server.bind_addr")
            )

    def test_every_enrolled_chart_passes_the_gate(self):
        report = Report()
        enrolled = entry.run(self.charts, report)
        self.assertEqual(
            [(chart, keys, external) for chart, keys, external in enrolled],
            [
                ("cloudflare-access-webhook-redirect", 20, 0),
                ("discord-alertmanager", 58, 0),
                ("mp-stats-legacy-viewer", 25, 0),
                ("netcup-offer-bot", 16, 0),
                ("portfolio", 21, 3),
                ("s3-bucket-perma-link", 21, 0),
                ("tankovault", 219, 0),
            ],
        )
        self.assertEqual(messages(report), "")

    def test_tankovault_binds_one_value_into_every_document_that_reads_it(self):
        """The rule that chart forced: unscoped means every document declaring the key.

        `metrics.enabled` is one line of `derivedConfig` written into all eight services, and one
        value carries one marker. 53 markers resolve to 219 bindings here, which is the
        arithmetic of that rule and the reason the chart is enrolable at all.
        """
        marker = self.markers("tankovault")["metrics.enabled"]
        self.assertIsNone(marker.documents)
        self.assertEqual(marker.target, "metrics.enabled")

    def test_tankovault_scopes_the_branding_block_by_who_reads_it(self):
        """Three documents declare `branding.*`; each service is sent only the half it reads."""
        markers = self.markers("tankovault")
        self.assertEqual(markers["branding.name"].documents, ("api", "frontend"))
        self.assertEqual(markers["branding.botUserAgent"].documents, ("worker",))

    def test_tankovault_binds_three_keys_from_one_directory(self):
        """One value, three markers — the case the earlier one-marker rule made unwriteable."""
        found = cb.parse_values(self.charts / "tankovault" / "values.yaml", "tankovault")
        targets = [m.target for m in found if m.values_path == "internal.tls.certDir"]
        self.assertEqual(targets, ["internal.tls.cert", "internal.tls.key", "internal.tls.ca"])

    def test_tankovault_writes_off_what_it_does_not_surface_in_families(self):
        """146 keys, four reasons, and every key still listed by name."""
        declaration = load_declaration(self.charts / "tankovault")
        written_off = [key for entry in declaration.unbound for key in entry.keys]
        self.assertEqual(len(written_off), len(set(written_off)))
        self.assertGreater(len(written_off), 100)
        self.assertLess(len(declaration.unbound), 10)
        for entry in declaration.unbound:
            self.assertTrue(entry.reason.strip())


if __name__ == "__main__":
    unittest.main()
