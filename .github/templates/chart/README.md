# The new-chart scaffold

What `just new-chart` copies, and what it generates.

The split is deliberate and it is the whole point of the directory. Two thirds of a chart in this
repository is chassis — the same thirty values blocks, the same templates, the same podspec wiring
— and it is identical across charts because `charts/common` already owns the logic and these files
only name it. Measured rather than assumed: 2,069 lines of every chart's `values.yaml` are
byte-identical to another chart's, which is 14% of the values surface, and `serviceaccount.yaml`
is the same fourteen lines in six of the eight charts. A new chart that starts from a copied
neighbour inherits whichever of those blocks that neighbour happened to have drifted, and nothing
notices.

So the chassis is *copied* from here, and only the part a contract decides is *generated*.

## Layout

```
Chart.yaml               copied, placeholders substituted
values.chassis.yaml      appended to the generated values.yaml; never copied as a file
README.md.gotmpl         copied — the helm-docs source `just chart-readmes` renders
README.md                this file; never copied

templates/               copied into every chart, whatever its form
  serviceaccount.yaml      identical in six of the eight charts today
  networkpolicy.yaml       one line; `common.networkPolicy` is the whole of it

contract/                copied only when the image publishes a contract
  templates/
    configmap.yaml         the rendered configuration document
    secret.yaml            the credentials, when the chart renders its own
    deployment.yaml        the workload, wired for file configuration

plain/                   copied only when it does not
  templates/
    deployment.yaml        the same workload, with no ConfigMap to mount
```

`contract/` and `plain/` are the same files written two ways rather than one file with a
conditional, because the difference is structural: a plain chart has no configuration document, no
secrets directory and no `_FILE` indirection, so its podspec is not the contracted one with parts
switched off. `config_scaffold.Surface` is the seam that picks between them.

Placeholders are `%%NAME%%`, and they are *not* Go template syntax — these files are full of
`{{ ... }}` that must survive to the chart untouched, so the scaffolder substitutes a form Helm
never looks at. `new-chart.py` refuses a file that still holds one after substitution: a
placeholder nobody replaced would otherwise reach the chart as literal text and render as itself.

## Generated rather than copied

Everything that depends on what the image actually reads. On the contracted form:

    values.yaml             one block per contract key: its type from `constraint`, its
                            documentation from `docs`, its default from `default_value`, and the
                            `# @config` marker naming the key it feeds
    templates/_helpers.tpl  `derivedConfig`, `secretData`, `secretKeys`, `validateValues`
    config-contract.yaml    the document, its source selector, its image and its consumers
    contract-tests.yaml     the round-trip enrolment, with a prerequisite per required credential
    ci/test-values.yaml     the credentials the render guard demands, and nothing else
    tests/configmap_test.yaml   a first suite, for a person to extend
    contracts/<name>.json   vendored by `refresh-contracts.py` from the image's registry

On the plain form, `values.yaml` stops after the image block, `_helpers.tpl` defines nothing, and
the fixture is empty. Each is deliberately *less* than the contracted equivalent rather than the
same thing with empty parts: a `configMount` in a chart whose image does not read a mounted file
is a value an operator can set and nothing honours, and an empty `derivedConfig` is a helper that
looks like a mapping and maps nothing. Both would be scaffolding somebody has to notice and
delete.

## Keeping this directory honest

Nothing gates the chassis against the charts. It was seeded from the majority text of the eight
existing charts and it will drift as they do — a value gains a sentence in one chart and the
scaffold keeps the old one. That is a real gap and it is stated here rather than hidden: the fix
is a gate that diffs the chassis against every chart that carries the block, which is worth
writing the first time the drift bites and is not worth guessing at now.

What *is* gated is the result. A chart this scaffold produces is checked by `just check` like any
other, so a chassis block that stops rendering fails on the next chart somebody creates — and
`tests/test_contract_scaffold.py` renders both forms from a hand-built contract on every run.
