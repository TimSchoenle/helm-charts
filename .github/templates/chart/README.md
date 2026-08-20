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

`just check-chassis` diffs this copy against every chart that carries the block, and reports
rather than fails. The chassis is not the authority: it was seeded from the majority text of the
eight existing charts, so it is right about most blocks by construction and has no claim to be
right about any particular one. A chart that diverged on purpose is a normal thing for a chart to
do; what the report is for is the other case, a block edited in one chart and nowhere else.

It currently reports 63 of 211 shared blocks as differing, which is the measure of how much had
already drifted before anything was watching. Reading the list and deciding, per block, whether
the chart or the scaffold is behind is the work — the recipe only makes the list.

What *is* gated is the result. A chart this scaffold produces is checked by `just check` like any
other, so a chassis block that stops rendering fails on the next chart somebody creates — and
`tests/test_contract_scaffold.py` renders both forms from a hand-built contract on every run.
