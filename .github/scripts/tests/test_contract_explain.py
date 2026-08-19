#!/usr/bin/env python3
"""Reading a chart's contracts back out, over fixtures rather than over the charts themselves.

`explain-config.py` prints rather than fails, which is exactly why it needs tests: a gate that
gets a rule wrong turns a pull request red, and an explanation that gets one wrong is believed. A
key shown as supplyable from a file when the loader will not read it that way, or a setting
attributed to seven images when eight read it, is a wrong answer nothing else in this repository
would contradict.

Three things here are worth holding still:

**The reader attribution**, because it is the fact this command exists to produce and it exists
nowhere else — not in a chart, not in a declaration, not in a README. A test over two fixture
contracts that share a key is what says the attribution is derived rather than guessed.

**The tolerant merge.** `union_contracts` refuses two contracts that describe one key
differently, and it is right to: its output validates a document all of them read. This command
must do the opposite and show the disagreement, because across a chart's *documents* nobody ever
compares the two — `tankovault` v8.1.0 has seven such pairs today and no gate can see one of
them. A merge that silently picked the first description would hide them again.

**The staleness interlock**, honoured here for the same reason the gates honour it: a contract
that is not for the pinned digest describes some other build, and printing its settings as this
chart's is a confident wrong answer to the only question anyone runs this to ask.

The fixtures are the ones the union tests already use — `api` and `worker` share `database.url`
and `log.level` — wrapped in the `source` envelope a vendored file carries.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import config_contract as cc  # noqa: E402
from config_report import Report  # noqa: E402


def _load(name: str, filename: str):
    """Import a hyphenated entry point, which a plain `import` cannot name.

    Registered in `sys.modules` before it is executed, which the same helper in
    `test_contract_refresh.py` does not need to do: `@dataclass` resolves its own module out of
    `sys.modules` to decide what a `ClassVar` is, and a module that is not there yet fails at
    the decorator rather than at anything the test wrote.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


explain = _load("explain_config", "explain-config.py")

FIXTURES = SCRIPTS.parent / "testdata" / "contracts"
API_DIGEST = "sha256:" + "ab" * 32
WORKER_DIGEST = "sha256:" + "cd" * 32
OTHER_DIGEST = "sha256:" + "ef" * 32


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def setting(name: str, *occurrences: tuple[str, dict]) -> "explain.Setting":
    return explain.Setting(name=name, occurrences=list(occurrences))


# --------------------------------------------------------------------------------------------
# A chart on disk
# --------------------------------------------------------------------------------------------


class ChartFixture(unittest.TestCase):
    """A throwaway chart pinning one image per contract, wired exactly as a real one is."""

    def build(self, *images: tuple[str, str, str], documents: str | None = None) -> Path:
        """`images` is `(document name, fixture name, digest)`, one document per image."""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        chart = Path(work.name) / "demo"
        (chart / "contracts").mkdir(parents=True)

        values = {}
        declared = []
        for name, contract, digest in images:
            payload = fixture(contract)
            (chart / "contracts" / f"{name}.json").write_text(
                json.dumps(
                    {
                        "source": {
                            "image": f"docker.io/demo/{name}",
                            "digest": digest,
                            "sha256": "0" * 64,
                            "fetched": "2026-01-01T00:00:00Z",
                        },
                        "contract": payload,
                    }
                ),
                encoding="utf-8",
            )
            values[name] = {"repository": f"demo/{name}", "tag": f"v1@{digest}"}
            declared.append(
                f"  - name: {name}\n"
                f"    source: {{ kind: ConfigMap, key: config.toml }}\n"
                f"    images:\n"
                f"      - values: {name}\n"
                f"        contract: contracts/{name}.json\n"
            )

        (chart / "Chart.yaml").write_text("name: demo\nappVersion: v1\n", encoding="utf-8")
        (chart / "values.yaml").write_text(json.dumps(values), encoding="utf-8")
        (chart / "config-contract.yaml").write_text(
            documents if documents is not None else "documents:\n" + "".join(declared),
            encoding="utf-8",
        )
        return chart

    def collect(self, chart: Path) -> tuple["explain.Surface | None", Report]:
        from config_declaration import load_declaration

        report = Report()
        return explain.collect(chart, load_declaration(chart), report), report


# --------------------------------------------------------------------------------------------
# Which images read which setting
# --------------------------------------------------------------------------------------------


class TestReaderAttribution(ChartFixture):
    """The one fact this command produces that exists nowhere else in the repository."""

    def setUp(self):
        self.chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        self.surface, self.report = self.collect(self.chart)

    def test_a_setting_only_one_image_reads_names_that_image(self):
        self.assertEqual(self.surface.keys["auth.session_ttl"].readers, ["api"])
        self.assertEqual(self.surface.keys["worker.concurrency"].readers, ["worker"])

    def test_a_shared_setting_names_every_image_that_reads_it(self):
        self.assertEqual(self.surface.keys["database.url"].readers, ["api", "worker"])

    def test_every_image_contributes_its_own_settings(self):
        self.assertEqual(
            sorted(self.surface.keys),
            [
                "auth.session_ttl",
                "database.url",
                "github.repos",
                "log.level",
                "tuning.ratio",
                "worker.concurrency",
                "worker.debug",
            ],
        )

    def test_the_images_are_named_by_their_contract_and_carry_their_provenance(self):
        readers = {reader.name: reader for reader in self.surface.readers}
        self.assertEqual(sorted(readers), ["api", "worker"])
        self.assertEqual(readers["api"].image, "docker.io/demo/api")
        self.assertEqual(readers["api"].digest, API_DIGEST)
        self.assertEqual(readers["api"].documents, ("api",))

    def test_readers_are_sorted_rather_than_declaration_ordered(self):
        reversed_chart = self.build(
            ("worker", "worker", WORKER_DIGEST), ("api", "api", API_DIGEST)
        )
        surface, _ = self.collect(reversed_chart)
        self.assertEqual(surface.keys["database.url"].readers, ["api", "worker"])

    def test_all_of_them_is_said_as_all_rather_than_listed(self):
        shared = self.surface.keys["database.url"]
        self.assertEqual(explain.reader_list(shared, 2), "all 2")
        self.assertEqual(explain.reader_list(shared, 3), "api, worker")
        # One image is not a fleet, and `all 1` would be a strange way to say `server`.
        self.assertEqual(explain.reader_list(self.surface.keys["log.level"], 1), "api")


# --------------------------------------------------------------------------------------------
# Merging what several images say about one setting
# --------------------------------------------------------------------------------------------


class TestTolerantMerge(unittest.TestCase):
    """A disagreement between two images is a finding here, not a refusal."""

    def test_required_unions_because_any_reader_requiring_it_wins(self):
        merged = setting(
            "database.url",
            ("api", {"path": "database.url", "required": False}),
            ("worker", {"path": "database.url", "required": True}),
        )
        self.assertTrue(merged.value("required"))
        # And the rule is `cc.UNIONED_FIELDS`, restated rather than reinvented, so a union is not
        # reported as a disagreement.
        self.assertNotIn("required", merged.divergent())

    def test_empty_prose_is_an_absence_rather_than_a_contradiction(self):
        merged = setting(
            "bind_addr",
            ("api", {"path": "bind_addr", "docs": ""}),
            ("worker", {"path": "bind_addr", "docs": "The listener."}),
        )
        self.assertEqual(merged.value("docs"), "The listener.")

    def test_two_descriptions_of_one_setting_are_both_kept(self):
        merged = setting(
            "bind_addr",
            ("api", {"path": "bind_addr", "docs": "Public."}),
            ("render", {"path": "bind_addr", "docs": ""}),
            ("worker", {"path": "bind_addr", "docs": "Ops only."}),
        )
        self.assertEqual(merged.divergent(), ["docs"])
        self.assertEqual(
            merged.variants("docs"),
            [("Public.", ["api"]), ("", ["render"]), ("Ops only.", ["worker"])],
        )

    def test_a_disagreement_about_a_constraint_is_reported_and_not_resolved(self):
        merged = setting(
            "port",
            ("api", {"path": "port", "constraint": {"type": "integer", "maximum": 65535}}),
            ("worker", {"path": "port", "constraint": {"type": "integer"}}),
        )
        self.assertEqual(merged.divergent(), ["constraint"])
        self.assertEqual(merged.representative()["constraint"]["maximum"], 65535)

    def test_a_field_only_one_image_publishes_is_still_carried(self):
        merged = setting(
            "log.level",
            ("api", {"path": "log.level"}),
            ("worker", {"path": "log.level", "note": "ignored in tests"}),
        )
        self.assertEqual(merged.value("note"), "ignored in tests")

    def test_the_representative_keeps_the_publication_order_of_its_fields(self):
        merged = setting(
            "log.level",
            ("api", {"path": "log.level", "env": "X", "ty": "Level"}),
            ("worker", {"path": "log.level", "env": "X", "ty": "Level", "secret": False}),
        )
        self.assertEqual(list(merged.representative()), ["path", "env", "ty", "secret"])


class TestDivergenceIsReported(ChartFixture):
    """No gate compares two images that never share a document. This is the only thing that does."""

    def test_a_shared_setting_described_differently_becomes_a_warning(self):
        drifted = fixture("worker")
        for entry in drifted["schema"]["keys"]:
            if entry["path"] == "database.url":
                entry["docs"] = "Somewhere else entirely."

        chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        path = chart / "contracts" / "worker.json"
        vendored = json.loads(path.read_text(encoding="utf-8"))
        vendored["contract"] = drifted
        path.write_text(json.dumps(vendored), encoding="utf-8")

        surface, report = self.collect(chart)
        explain.report_divergences(surface, None, report)

        messages = [finding.message for _, finding in report.warnings]
        self.assertTrue(any("database.url" in message for message in messages), messages)
        self.assertTrue(any("`docs`" in message for message in messages), messages)
        self.assertEqual(report.errors, [])

    def test_a_divergence_outside_the_selection_is_not_reported(self):
        chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        surface, report = self.collect(chart)
        surface.keys["database.url"].occurrences[1][1]["docs"] = "drifted"
        explain.report_divergences(surface, "log.level", report)
        self.assertEqual(report.warnings, [])


# --------------------------------------------------------------------------------------------
# The interlock
# --------------------------------------------------------------------------------------------


class TestStalenessInterlock(ChartFixture):
    """Printing a contract that is not for the pinned digest is a confident wrong answer."""

    def test_a_bumped_digest_produces_no_surface_at_all(self):
        chart = self.build(("api", "api", API_DIGEST))
        values = json.loads((chart / "values.yaml").read_text(encoding="utf-8"))
        values["api"]["tag"] = f"v2@{OTHER_DIGEST}"
        (chart / "values.yaml").write_text(json.dumps(values), encoding="utf-8")

        surface, report = self.collect(chart)
        self.assertIsNone(surface)
        self.assertTrue(report.errors)
        self.assertIn("refreshes it", report.errors[0][1].message)

    def test_one_stale_document_refuses_the_whole_chart(self):
        # Rather than printing the eight that bound and quietly dropping the ninth: a listing
        # missing a document is indistinguishable from an image that reads nothing.
        chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        values = json.loads((chart / "values.yaml").read_text(encoding="utf-8"))
        values["worker"]["tag"] = f"v2@{OTHER_DIGEST}"
        (chart / "values.yaml").write_text(json.dumps(values), encoding="utf-8")

        surface, _ = self.collect(chart)
        self.assertIsNone(surface)


# --------------------------------------------------------------------------------------------
# Selecting
# --------------------------------------------------------------------------------------------


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.settings = {
            name: setting(name, ("api", {"path": name}))
            for name in ("auth.session_ttl", "database.url", "log.level", "Log.Rotation")
        }

    def test_no_pattern_takes_everything_in_path_order(self):
        self.assertEqual(
            [item.name for item in explain.select(self.settings, None)],
            ["Log.Rotation", "auth.session_ttl", "database.url", "log.level"],
        )

    def test_a_plain_pattern_is_a_case_insensitive_substring(self):
        self.assertEqual(
            [item.name for item in explain.select(self.settings, "LOG")],
            ["Log.Rotation", "log.level"],
        )

    def test_a_glob_is_matched_against_the_whole_path(self):
        self.assertEqual(
            [item.name for item in explain.select(self.settings, "log.*")], ["log.level"]
        )

    def test_a_glob_matching_nothing_selects_nothing(self):
        self.assertEqual(explain.select(self.settings, "auth.*.ttl"), [])


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------


class TestConstraintProse(unittest.TestCase):
    def test_a_two_sided_bound_reads_as_a_range(self):
        self.assertEqual(
            explain.describe_constraint({"type": "integer", "minimum": 0, "maximum": 65535}),
            "integer, 0 to 65535",
        )

    def test_a_one_sided_bound_says_which_side(self):
        self.assertEqual(
            explain.describe_constraint({"type": "integer", "minimum": 1}), "integer, at least 1"
        )
        self.assertEqual(
            explain.describe_constraint({"type": "integer", "maximum": 9}), "integer, at most 9"
        )

    def test_an_exclusive_bound_is_not_reported_as_an_inclusive_one(self):
        self.assertEqual(
            explain.describe_constraint({"exclusiveMinimum": 0}),
            "above 0",
        )

    def test_an_enum_lists_its_values(self):
        self.assertEqual(
            explain.describe_constraint({"type": "string", "enum": ["a", "b"]}),
            'string, one of "a" | "b"',
        )

    def test_a_length_bound_says_characters(self):
        self.assertEqual(
            explain.describe_constraint({"type": "string", "minLength": 1, "maxLength": 64}),
            "string, 1 to 64 characters",
        )

    def test_an_unrecognised_keyword_is_printed_rather_than_dropped(self):
        # Under-reporting a constraint is the failure this pipeline exists to remove, and it is
        # no better coming from the explainer than from a gate.
        described = explain.describe_constraint({"type": "string", "contentEncoding": "base64"})
        self.assertIn('contentEncoding="base64"', described)

    def test_nothing_to_say_is_said_with_nothing(self):
        self.assertEqual(explain.describe_constraint(None), "")
        self.assertEqual(explain.describe_constraint({}), "")


class TestFullEntry(unittest.TestCase):
    DIALECT = {"prefix": "FIXTURE_", "nesting_separator": "__", "indirection_suffix": "_FILE"}

    def entry(self, path: str) -> "explain.Setting":
        for item in fixture("api")["schema"]["keys"]:
            if item["path"] == path:
                return setting(path, ("api", item))
        raise AssertionError(path)

    def rendered(self, path: str, total: int = 1) -> str:
        return "\n".join(explain.full(self.entry(path), self.DIALECT, total))

    def test_every_spelling_the_loader_accepts_is_shown(self):
        text = self.rendered("database.url")
        self.assertIn("FIXTURE_DATABASE__URL", text)
        self.assertIn("FIXTURE_DATABASE__URL_FILE", text)
        self.assertIn("database__url", text)

    def test_a_text_key_is_shown_as_supplyable_from_a_file(self):
        self.assertIn("from a file   yes", self.rendered("database.url"))

    def test_a_key_no_file_can_supply_says_so_beside_the_spellings(self):
        # The `_FILE` and secrets-file spellings exist for every key, and for anything but a
        # `text` key neither of them works — a file delivers a string and `Figment::extract` will
        # not coerce one. Printing the spelling without printing that is how an operator ends up
        # mounting a Secret that is silently never read.
        text = self.rendered("auth.session_ttl")
        self.assertIn("FIXTURE_AUTH__SESSION_TTL_FILE", text)
        self.assertIn("from a file   no", text)
        self.assertIn("read as integer", text)

    def test_the_markers_and_the_enum_reach_the_entry(self):
        self.assertIn("secret        yes", self.rendered("database.url"))
        self.assertIn("required      yes", self.rendered("database.url"))
        self.assertIn("debug | info | warn", self.rendered("log.level"))

    def test_a_single_image_chart_does_not_attribute_readers(self):
        self.assertNotIn("read by", self.rendered("database.url", total=1))
        self.assertIn("read by", self.rendered("database.url", total=2))


class TestCompactListing(unittest.TestCase):
    def rows(self, *paths: str, total: int = 1) -> list[str]:
        keys = {item["path"]: item for item in fixture("api")["schema"]["keys"]}
        return explain.compact([setting(path, ("api", keys[path])) for path in paths], total)

    def test_required_and_secret_are_two_markers_in_one_column(self):
        (line,) = self.rows("database.url")
        self.assertIn("RS", line)
        (line,) = self.rows("log.level")
        self.assertIn("..", line)

    def test_the_default_column_disappears_when_nothing_has_one(self):
        # Every one of `tankovault`'s 454 declarations publishes no default, and 167 rows of a
        # column holding only dashes is a column that costs width and says nothing.
        (line,) = self.rows("log.level")
        self.assertNotIn("-", line)
        (line,) = self.rows("auth.session_ttl")
        self.assertIn("3600", line)


# --------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------


class TestExitStatus(ChartFixture):
    """The status is an answer, not an accident — see the module docstring for which is which."""

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = explain.main(list(argv))
        return status, out.getvalue(), err.getvalue()

    def charts_root(self, chart: Path) -> str:
        return str(chart.parent)

    def test_no_chart_lists_the_charts_that_have_one(self):
        chart = self.build(("api", "api", API_DIGEST))
        status, out, _ = self.run_main("--charts", self.charts_root(chart))
        self.assertEqual(status, 0)
        self.assertIn("demo", out)

    def test_an_unknown_chart_lists_them_and_fails(self):
        chart = self.build(("api", "api", API_DIGEST))
        status, _, err = self.run_main("nosuch", "--charts", self.charts_root(chart))
        self.assertEqual(status, 2)
        self.assertIn("is not a chart with a configuration contract", err)

    def test_an_explicit_opt_out_is_reported_and_succeeds(self):
        chart = self.build(
            documents="documents: []\nreason: the image publishes no contract yet\n"
        )
        status, out, _ = self.run_main("demo", "--charts", self.charts_root(chart))
        self.assertEqual(status, 0)
        self.assertIn("opted out", out)
        self.assertIn("publishes no contract yet", out)

    def test_a_pattern_matching_nothing_is_answered_with_a_failure(self):
        # `grep`'s convention. "Does this image read this setting?" is a question, and an answer
        # indistinguishable from a typo in the pattern is not one.
        chart = self.build(("api", "api", API_DIGEST))
        status, _, err = self.run_main("demo", "zzz", "--charts", self.charts_root(chart))
        self.assertEqual(status, 1)
        self.assertIn("nothing this chart's images read matches", err)

    def test_a_pattern_matching_something_succeeds(self):
        chart = self.build(("api", "api", API_DIGEST))
        status, out, _ = self.run_main("demo", "database", "--charts", self.charts_root(chart))
        self.assertEqual(status, 0)
        self.assertIn("FIXTURE_DATABASE__URL", out)

    def test_a_stale_contract_refuses_to_print_anything(self):
        chart = self.build(("api", "api", API_DIGEST))
        values = json.loads((chart / "values.yaml").read_text(encoding="utf-8"))
        values["api"]["tag"] = f"v2@{OTHER_DIGEST}"
        (chart / "values.yaml").write_text(json.dumps(values), encoding="utf-8")

        status, out, err = self.run_main("demo", "--charts", self.charts_root(chart))
        self.assertEqual(status, 3)
        self.assertNotIn("FIXTURE_DATABASE__URL", out)
        self.assertIn("just contracts", err)

    def test_the_whole_surface_is_printed_without_a_pattern(self):
        chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        status, out, _ = self.run_main("demo", "--charts", self.charts_root(chart))
        self.assertEqual(status, 0)
        self.assertIn("database.url", out)
        self.assertIn("all 2", out)
        self.assertIn("worker.concurrency  u16          ..  -  worker", out)
        self.assertIn("loader variables", out)
        self.assertIn("external variables", out)
        self.assertIn("PORT", out)


class TestJsonOutput(ChartFixture):
    def emit(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            status = explain.main(list(argv) + ["--json"])
        return status, json.loads(out.getvalue())

    def setUp(self):
        self.chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        self.root = str(self.chart.parent)

    def test_the_selection_carries_the_readers_and_the_derived_readings(self):
        _, document = self.emit("demo", "database.url", "--charts", self.root)
        (key,) = document["keys"]
        self.assertEqual(key["readers"], ["api", "worker"])
        self.assertEqual(key["text_form"], "text")
        self.assertTrue(key["file_supplyable"])

    def test_a_key_no_file_can_supply_says_so_to_a_machine_too(self):
        _, document = self.emit("demo", "auth.session_ttl", "--charts", self.root)
        (key,) = document["keys"]
        self.assertEqual(key["text_form"], "integer")
        self.assertFalse(key["file_supplyable"])

    def test_the_images_carry_their_provenance(self):
        _, document = self.emit("demo", "--charts", self.root)
        self.assertEqual([image["name"] for image in document["images"]], ["api", "worker"])
        self.assertEqual(document["images"][0]["digest"], API_DIGEST)
        self.assertEqual(document["dialect"]["prefix"], "FIXTURE_")

    def test_external_and_loader_variables_are_their_own_sections(self):
        _, document = self.emit("demo", "--charts", self.root)
        self.assertEqual([item["name"] for item in document["external"]], ["PORT"])
        self.assertEqual(
            [item["env"] for item in document["loader"]],
            ["FIXTURE_CONFIG", "FIXTURE_SECRETS_DIR"],
        )

    def test_nothing_but_json_reaches_stdout_so_a_pipe_stays_usable(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            explain.main(["demo", "--charts", self.root, "--json"])
        json.loads(out.getvalue())

    def test_a_pattern_matching_nothing_still_emits_a_document_and_fails(self):
        # The status is the answer; the document is what a pipeline downstream still has to
        # parse, so it is emitted either way.
        status, document = self.emit("demo", "zzz", "--charts", self.root)
        self.assertEqual(status, 1)
        self.assertEqual(document["keys"], [])


class TestUnionIsNotReused(ChartFixture):
    """Why this command merges for itself rather than calling `cc.union_contracts`.

    Two of a chart's documents legitimately carry different `json_schema.title` — they are
    different files read by different binaries — and `union_contracts` is right to refuse that
    pair, because its output is a schema one document is validated against. Reusing it across a
    chart's documents would mean `just explain tankovault` printing nothing at all, so this is
    the measurement behind that decision rather than an assumption about it.
    """

    def test_two_documents_cannot_be_unioned_but_can_be_explained(self):
        with self.assertRaises(cc.ContractError):
            cc.union_contracts([("api", fixture("api")), ("other", fixture("foreign-dialect"))])

        chart = self.build(("api", "api", API_DIGEST), ("worker", "worker", WORKER_DIGEST))
        surface, _ = self.collect(chart)
        self.assertEqual(len(surface.readers), 2)


if __name__ == "__main__":
    unittest.main()
