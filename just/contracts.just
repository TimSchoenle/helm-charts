# Configuration contracts: proving a chart's rendered settings are ones its image will accept.
#
# Every other gate in this repository looks at the chart or at Kubernetes. `values.schema.json`
# describes the chart's values; kubeconform describes Kubernetes objects; `helm unittest` describes
# what the templates emit. None of them can read the *application's* configuration, and a ConfigMap
# holding a `config.toml` full of keys the binary stopped reading is a valid ConfigMap by all three.
# `serde` ignores an unknown key by design, so the pod starts, reports healthy, and runs on a
# compiled default nobody chose.
#
# An image built on `terrace-config` publishes a JSON document naming every setting it reads —
# attached to the image digest as an OCI referrer and embedded in the image itself. This group
# vendors that document per chart and validates three things against it:
#
#   the document     the rendered `config.toml`, against the union of every image that reads it
#   the environment  every variable on every declared container, against that container's image
#   the file names   the secrets directory's contents and every `_FILE` target
#
# Every document declared here binds exactly one image today — `tankovault`'s nine services each
# render their own per-component ConfigMap — so that first union is the identity. The scope is
# still the document rather than the image, because that is the boundary the format draws and the
# one a document with a second reader would arrive on.
#
# Exactly one recipe here touches a network — `contracts`, the refresh. Everything else reads the
# committed file, so a registry outage cannot fail a pull request that changes no image, and
# re-running CI on an old commit validates against what was true then rather than what the registry
# serves today. That is also what keeps `just check` network-free.
#
# The contract diff lands in the same pull request as the digest bump, which is the design's whole
# point: a reviewer of a Renovate bump sees the removed key next to the chart's failing gate.

# --------------------------------------------------------------------------------------------
# The refresh — the only networked recipe
# --------------------------------------------------------------------------------------------

# Refresh every vendored contract from the image each chart pins.
#
# Resolves each declared image to a digest, verifies the signature, discovers the contract attached
# to that digest, checks that the image's `dev.terrace.config.prefix` label and the document's own
# dialect agree, and writes `charts/<chart>/contracts/<name>.json` — the published bytes inside a
# `source` envelope recording which digest they came from. That record is what the offline
# staleness interlock reads.
#
# Run by the Documentation job, whose existing commit step carries the result back to the branch.
# Running it locally is a convenience; `just check-contracts` reports the gap without a network.
#
# Deliberately NOT in `just check`: `check` is the cluster-free set and, since this group landed,
# the network-free set. The only reason a contributor's `just check` should need connectivity is
# the Kubernetes `$ref` resolution that already exists.
#
# Needs `oras` and `cosign`, both pinned in `justfile`. A chart whose image publishes no contract
# fails here rather than being skipped: declaring a document commits the chart to having one.
[doc("Refresh every vendored contract from the image each chart pins (the only networked recipe)")]
[group('contracts')]
contracts chart='':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    CONTRACT_SIGNER='{{ contract_signer }}' \
      "$python" {{ scripts }}/refresh-contracts.py {{ chart }} --charts {{ charts }}

# Report vendored contracts that no longer match the images their charts pin.
#
# The offline half of the recipe above, for a contributor who wants to know whether a refresh is
# owed without running one. `just check-config` catches the same gap per document and with a far
# better message; this catches it across the whole tree in one line.
[doc("Report vendored contracts that no longer match the images their charts pin")]
[group('contracts')]
check-contracts:
    #!/usr/bin/env bash
    set -euo pipefail

    stale=0
    git diff --exit-code -- '{{ charts }}/*/contracts/' || stale=1

    # `git diff` cannot see a file that was never added, and a refresh for a chart whose contract
    # is new writes exactly that — so an untracked contract would otherwise read as "no changes".
    untracked="$(git ls-files --others --exclude-standard -- '{{ charts }}/*/contracts/*')"
    if [ -n "$untracked" ]; then
      echo "untracked vendored contracts:" >&2
      echo "$untracked" | sed 's/^/  /' >&2
      stale=1
    fi

    if [ "$stale" -ne 0 ]; then
      echo "error: vendored contracts are stale; run 'just contracts'" >&2
      exit 1
    fi

# --------------------------------------------------------------------------------------------
# The gates — offline
# --------------------------------------------------------------------------------------------

# Validate every chart's rendered configuration against the contracts of the images it pins.
#
# Reuses `just render`, so the manifests these gates read are byte-identical to the ones
# kubeconform and kube-linter see — one render, one set of facts, and no third opinion about what
# the chart produces.
#
# Reads no network. The contracts are committed files, and the JSON Schema engine is refused any
# reference it would have to fetch: the union is walked first and a `$ref` that does not begin with
# `#` is fatal. Chart `values.schema.json` files legitimately name Kubernetes types by URL, which is
# why `render-chart` carries a retry; an *app config* schema must not, or an offline gate silently
# becomes a networked one and a third party gains a say in what CI accepts.
#
# Every violation is reported before it exits, matching `just render` and `validate-manifests`.
[doc("Validate every chart's rendered configuration against its images' contracts")]
[group('contracts')]
check-config out='rendered':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    just render '{{ out }}'
    "$python" {{ scripts }}/check-config.py '{{ out }}' --charts {{ charts }}

# Report which charts a configuration contract covers, and which it does not.
#
# Adopting a contract is opt-in: a chart carrying a `config-contract.yaml` is covered, and one
# without is reported rather than failed. The images here adopt the format on their own release
# schedules, so a gate that failed every chart whose image had not caught up would be red for
# reasons nobody in this repository can fix — and would end up disabled, which is worse than
# absent.
#
# What still fails is a chart contradicting itself: one that declares documents and leaves one of
# its own first-party images unaccounted for, so a new service added to a multi-image chart cannot
# be covered by nothing while the chart still looks covered.
#
# Needs no render, so it is cheap enough to sit in `just check` beside the gates that do.
[doc("Report which charts a configuration contract covers, and which it does not")]
[group('contracts')]
check-contract-coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/check-config.py --coverage-only --charts {{ charts }}

# Hold every configuration binding marker in a chart's values.yaml against the key it names.
#
# `check-contract-coverage` above is chart-level: it answers whether a chart has a contract, and
# its `unconfigured` escape hatch lists *images*. So the failure this repository actually keeps
# hitting is invisible to it — an image release adds a setting, the automated bump repins the
# digest and omits everything else, and nothing notices that the new key is reached by no chart
# value. `check-config` cannot see it either: a key nothing renders is not a key rendered wrongly.
#
# A `# @config` marker in values.yaml is the missing fact — one line inside the value's `@schema`
# block, naming the contract key that value feeds and how it gets there:
#
#     # @schema
#     # # @config projection telemetry.sentry_dsn optional
#     # type: string
#     # @schema
#     # -- Sentry DSN (`telemetry.sentry_dsn`). Empty disables Sentry entirely.
#     sentryDsn: ""
#
# It reads as a comment inside a comment because that is exactly what it is: inside the `@schema`
# delimiters the text is the schema, parsed as YAML, so a YAML comment there is discarded by the
# only parser that reads it. That is the one position in the file both generators ignore, and it
# was found by measuring ten of them — see `config_bindings.py`.
#
# This recipe is the gate over them. The grammar, the five rules, the placement constraint the
# two generated artefacts impose and every deliberate omission are in `config_bindings.py`'s and
# `check-config-bindings.py`'s docstrings, which is where they belong rather than repeated here.
#
# **Nothing writes a marker.** They are hand-written, and a generator would have had to land in
# the same change as the format it depends on, which is the one shape a reviewer cannot check.
#
# Enrolment is opt-in and declared, by `bindings: true` in `config-contract.yaml`, so a gate that
# failed unenrolled charts is not red for work nobody has scheduled — and does not end up
# disabled, which is worse than absent. Declared rather than inferred from the markers themselves
# because a chart that loses them would otherwise leave the report in silence, which is drift of
# exactly the kind this gate exists to see.
#
# Every chart that maps values onto a contract is enrolled — all seven. `tankovault` is where
# four of this format's rules come from — an unscoped marker binding every document that
# declares the key, several markers on one value, a chart-level `unbound` taking a list of keys,
# and a lone `composed` being legitimate — because its nine documents falsified the first draft of
# each. `check-config-bindings.py` records which measurement forced which.
#
# **In `just check`, unlike most of this group.** The recipes kept out of the aggregate are kept
# out for one of two reasons: they need a network (`contracts`), or they print rather than assert
# (`explain`, `contract-diff`, `check-config-secrets`). This one asserts, reads two committed
# files per chart, needs neither render nor cluster nor network, and cannot go red for a chart
# nobody enrolled. It also fails exactly where it is wanted: on the Renovate pull request that
# repinned the digest, in the same run that shows the reviewer the contract diff.
#
# Deliberately without the staleness interlock the gates above apply, for the reason
# `config-secrets` is: this compares values.yaml against the committed contract, whether that copy
# is current is `just check-contracts`' question, and refusing to run during a bump would withdraw
# the report from the one pull request it exists for.
[doc("Hold every `# @config` binding marker against the contract key it names")]
[group('contracts')]
check-config-bindings:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/check-config-bindings.py --charts {{ charts }}

# Write every bound value's `@schema` block in a chart's values.yaml from the contract.
#
# A contract states a key's type as JSON Schema, and as of `schema_version: 2` that object nests:
# a container-typed key whose element type describes itself carries the element under `items` for
# a sequence and `additionalProperties` for a map, composed through both when they are stacked.
# That is a full description of a struct somebody else owns, published by the image that owns it
# — so a chart that wants an operator's editor to catch a misspelt field before the service does
# no longer has to transcribe it by hand.
#
# **Every value carrying a `# @config projection`, `structured` or `external` marker is written
# from that key's constraint.** Nothing enrols it: the marker naming the key is the enrolment, and
# it is also the one statement in this repository of which key a value feeds, so nothing here
# restates a binding and a chart that is not bindings-enrolled has no derived shapes at all.
#
# It was opt-in per value until measurement said what that was worth: nine values were enrolled
# and 185 were not, and 114 of the 185 already carried the block their contract describes — right
# by hand, and held there by nothing. 178 are generated now, and the six that are not each say why
# in a marker.
#
# Run by `just schema`, before helm-schema, since it edits helm-schema's input. Deliberately *not*
# run by `just contracts`: that recipe fetches the contract, and one command that both fetches a
# document and rewrites the chart from it produces a commit in which nobody can see which caused
# which.
[doc("Write every derived `@schema` block in a chart's values.yaml from its contract")]
[group('contracts')]
config-shapes:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config_shapes.py --charts {{ charts }}

# Fail when a value's `@schema` block is not what its contract describes.
#
# The asserting half of the recipe above, and the gate over the three markers that depart from it:
#
#   a bound value with no marker             the block is behind its contract
#   `@config-shape <path> handwritten ...`   the chart's appVersion moved past the version the key
#                                            was read at, or the contract describes it now and the
#                                            hand copy is superseded
#   `@config-shape-except <path> <sub> ...`  the contract stopped describing the position the
#                                            chart overrides, so the override is now a narrowing
#   `@config-shape-narrow <path> <sub> ...`  the contract started describing the position the
#                                            chart adds, so somebody has to choose between them
#
# A transcription is held against the version it was read at — which an automated bump moves
# while touching nothing else, leaving a schema that refuses a field the new release added. Once
# the image publishes the key, the hand copy is superseded and the gate says so rather than
# letting a stale copy sit there looking maintained. The two departure markers age the same way in
# opposite directions, which is why they are two markers rather than one that accepts either.
#
# In `just check` rather than left to the Documentation job. The generated half the docs job does
# repair, but the hand-written half it cannot: re-reading a struct is a person's job, and the
# question is not whether the file changed but whether the schema still describes it. Reads
# committed files only — no network, no render, no cluster.
[doc("Fail when a derived `@schema` block is not what its contract describes")]
[group('contracts')]
check-config-shapes:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config_shapes.py --charts {{ charts }} --check

# Unit tests for the contract model, over fixtures rather than over a registry.
#
# The union is tested here and nowhere else: every document in the repository binds a single image,
# so the merge is the identity in production and these fixtures are the only place its rules run.
# They are worth keeping — a document read by several binaries is what the format is for, and the
# merge is what such a document would land on. Beside it are the classification order, the two-step
# value check and the file-supplyable rule — every one of them normative, and every one of them a
# place where a plausible-looking implementation quietly under-checks.
[doc("Unit tests for the contract model, over fixtures rather than over a registry")]
[group('contracts')]
test-contract-union:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" -m unittest discover -s {{ scripts }}/tests -p 'test_contract_*.py' -v

# --------------------------------------------------------------------------------------------
# Reading a contract, rather than checking against one
# --------------------------------------------------------------------------------------------

# Explain what a chart's pinned images read: every setting, its spellings, and which image reads it.
#
# Every other recipe in this group is a gate — it holds a rendered manifest against a contract and
# reports the difference. None of them ever shows a contributor what the contract *says*, and the
# vendored file is the only place in this repository that states, per setting, what the binary
# calls it, what values it accepts, whether it is required, whether a file may supply it and what
# happens when nothing sets it. Answering "what can I put in `config.toml`, and why is the value I
# set being ignored?" out of a 400 KB JSON document is the job.
#
# The reader attribution is the part worth having. `tankovault` pins nine images across nine
# documents, and which of them reads a given setting is stated nowhere else: not in the chart, not
# in `config-contract.yaml`, not in the README. It is derived from the contracts the declaration
# binds, so it cannot go stale against them — a bump that moves a setting from one service to
# another moves this output with it — and it is how an operator tells a local change from a
# fleet-wide one before making it.
#
# Offline and read-only, like every recipe here but the refresh, and honouring the same staleness
# interlock the gates do: a contract that is not for the digest the chart pins describes some
# other build, and a confident wrong answer is worse here than in a gate, because nothing
# downstream re-checks what a person was told.
#
# Deliberately NOT in `just check`. It asserts nothing — it prints — and a recipe that cannot fail
# has no business in an aggregate whose whole output is a pass or a fail.
#
# `just explain` alone lists the charts that have a contract. A second argument selects by
# substring, or by glob when it carries one, over the setting's path and prints the full entry for
# each match; `--full` does that without a pattern and `--json` emits the same selection for
# something other than a person. A pattern matching nothing exits non-zero, which is `grep`'s
# convention and the reason is in the script's docstring: an answer indistinguishable from a typo
# in the pattern is not an answer.
[doc("Explain what a chart's pinned images read: settings, spellings, and which image reads which")]
[group('contracts')]
explain chart='' pattern='' *flags:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/explain-config.py '{{ chart }}' '{{ pattern }}' \
      --charts {{ charts }} {{ flags }}

# --------------------------------------------------------------------------------------------
# The review aid — offline
# --------------------------------------------------------------------------------------------

# Report how a chart's vendored contracts changed against another revision, and what that costs.
#
# `contracts` runs inside the Renovate pull request that repins the digest, which is the design's
# whole point and is also why the reviewer is looking at several hundred lines of reordered JSON.
# The question they have is not in those lines: did the image gain a setting the chart has to
# write, drop one the chart still writes, or move a default out from under it — and, given the
# answer, what should this chart's own version be. This recipe is that answer.
#
# Severity is graded by what the loader does at boot rather than by the shape of the diff, which
# is why a removed key and a changed dialect are the two most severe things here. Both rename
# every spelling the chart emits while nothing in the rendered manifests looks different, and
# `external.unknown` is `reject` across every contract in this repository — so the old spelling is
# a container that will not start, not a key quietly ignored.
#
# The suggestion is shown with the findings that produced it and never written anywhere. Nothing
# here edits `Chart.yaml`: the mapping from finding to bump is a defensible default, and only the
# reviewer knows whether the chart actually writes the key that moved. A recipe that bumped the
# version would be asserting it knows that, and would be wrong the first time a removed key was
# one no template ever emitted.
#
# Reads no network — the other side comes from `git show`, exactly as every gate above reads the
# committed file. Deliberately not part of `just check`: differences are the normal outcome of a
# bump, so a gate that failed on them would be red on every pull request it exists to explain.
# For a caller that needs the distinction as a status rather than as output, `--exit-code` gives
# three: 0 identical, 1 error, 2 differences -- on top of which `just` prints its own "failed
# with exit code 2", which is simply what any non-zero recipe looks like to it.
#
#   just contract-diff                            every chart, against origin/main
#   just contract-diff tankovault                 one chart
#   just contract-diff portfolio HEAD~1           against another revision
#   just contract-diff '' origin/main --json      the machine-readable report
[doc("Report how a chart's vendored contracts changed against another revision, and what it costs")]
[group('contracts')]
contract-diff chart='' ref='origin/main' *flags='':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/contract-diff.py {{ chart }} --ref '{{ ref }}' \
      --charts {{ charts }} {{ flags }}

# --------------------------------------------------------------------------------------------
# The credential surface
# --------------------------------------------------------------------------------------------

# List every credential the images in this repository declare secret, and how each one is spelt.
#
# Forty-seven key declarations across the tree carry `secret: true`, and until this recipe existed
# no document anywhere said what they are. Somebody writing a values file has to know which
# credentials a chart needs, what to name the keys of the Secret that carries them, which
# environment spelling and which `_FILE` spelling address the same value, and — for a chart whose
# services run different binaries — which of them actually reads each one. Every part of that
# answer was already published by the images and readable only by opening nine JSON documents by
# hand.
#
# Reads the committed contracts and nothing else: no render, no cluster, no network. Cheap enough
# to run while writing the values file it exists to help with, which is the only time it is
# wanted.
#
# Deliberately skips the staleness interlock the gates apply. A Renovate digest bump produces one
# run in which the pinned digest and the vendored contract disagree, and withholding the inventory
# then would remove the document at exactly the moment a reviewer is reading a credential change.
# Whether the vendored copy is current is `just check-contracts`' question, and it answers it in
# one line rather than by refusing to describe anything.
#
# `--json` for a machine; the table is the answer for a person.
[doc("List every credential the images declare secret, and how each one is spelt")]
[group('contracts')]
config-secrets *args:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config-secrets.py --charts {{ charts }} {{ args }}

# Report what the charts deliver against the credentials the images declare. Report only.
#
# The gates in `check-config` are structurally unable to ask this. Gate 3 checks that every secret
# file a pod is *given* is a name the contract knows, so it refuses a name nothing declares and is
# blind by construction to the opposite defect: a credential the image declares and no channel
# supplies. That one renders cleanly, passes every gate, starts, reports healthy, and fails at the
# first request that needs it. Gate 3 is also scoped to the containers a `config-contract.yaml`
# names as consumers, so an init container or a sidecar sharing the pod is a container no gate
# opens — and several Deployments here run one that mounts the whole per-service credential set
# while running a different image entirely.
#
# Reuses `just render`, so the manifests read here are the ones every other gate reads: one
# render, one set of facts. Reconciled against every `ci/` values file each chart ships, because a
# credential delivered under one fixture is delivered — the chart has a channel for it — while
# over-projection is a property of the fixture that produced it and is reported naming that file.
#
# **Exits 0 whatever it finds, and that is the point of the first pass.** Its three questions are
# new, and a report over an established repository is where they earn their triage: a finding that
# turns out to be a considered design decision belongs in a document, not in a red pipeline on a
# pull request that changed nothing. A follow-up promotes it once these findings are triaged, and
# the promotion is one line of `config-secrets.py` — the findings are already `Finding`s at the
# level each has earned, so nothing else moves.
#
# Deliberately not in `just check` for the same reason: an aggregate a contributor is expected to
# run before pushing must not be a place where advisory output accumulates.
[doc("Report what the charts deliver against the credentials the images declare (report only)")]
[group('contracts')]
check-config-secrets out='rendered' *args:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    just render '{{ out }}'
    "$python" {{ scripts }}/config-secrets.py --reconcile '{{ out }}' --charts {{ charts }} {{ args }}

# --------------------------------------------------------------------------------------------
# The round trip — generated
# --------------------------------------------------------------------------------------------
#
# Everything above validates the configuration a chart *renders*. None of it can see the round
# trip: that a setting an operator writes into the chart's values arrives in the application's
# document, at the path the image reads it from, carrying the value that was asked for. A typo in
# a template helper produces a document that satisfies the contract perfectly — every key it does
# contain is legal — and the setting is simply absent, so the pod starts, reports healthy, and
# runs on the compiled default the whole pipeline exists to stop it running on.
#
# The two gates compose rather than overlap. `check-config` refuses a document carrying a key
# nothing reads; the suites below refuse a document missing a key somebody asked for. Neither
# sees the other's defect.
#
# 491 key declarations exist across this repository and the hand-written suites exercise a
# handful, which is not a gap anybody closes by hand — and a suite written by hand goes stale the
# first time an image renames a key, silently, because a case nobody wrote is a case nobody
# misses. Generating it from the contract is what ties the two together: the contract moves, the
# suite moves with it, and `check-contract-tests` is what says so.

# Generate the helm-unittest suites that prove each chart delivers what its contract declares.
#
# A chart is enrolled by carrying `charts/<chart>/contract-tests.yaml`, exactly as
# `config-contract.yaml` is what enrols it in `just check-config` — so the rollout is a property
# of the tree rather than a list somebody has to remember to edit, and a chart states there which
# values every generated case has to carry before it will render at all.
#
# Every chart with a contract is enrolled — all seven, 22 suites between them. The rollout ran
# one chart at a time because the probe written into a chart's values is chosen from the contract
# rather than by a person, and what that is worth against a real image is answered by running it
# and not by reviewing it. What each chart needed was measured the same way, by enrolling it and
# reading the failure:
#
#   portfolio               a `baseline`: its own guard refuses the pair its two probes walk into.
#   mp-stats-legacy-viewer  nothing. Six of its seven keys pass today; enrolling it is one file.
#   cloudflare-access-…     `prerequisites`: chart values the guard refuses to render without —
#   netcup-offer-bot        a target base and an allowed path, a webhook URL, one bucket entry.
#   s3-bucket-perma-link    Not configuration, so not a `baseline`: a baseline writes into the
#                           document the cases assert against, and it is dropped from the case
#                           probing the key it sets, where a prerequisite is dropped from
#                           nothing — a case without it never renders. Being undroppable is why
#                           a prerequisite naming a path under `config` is refused outright.
#   tankovault              the same prerequisites, an order of magnitude larger, plus three of
#                           its own: nine documents all keyed `config.toml`, told apart by the
#                           `app.kubernetes.io/component` discriminator the declaration already
#                           carries; `.Values.config` as the *lowest*-precedence layer there, so
#                           each document names its own `services.<name>.config` as the probe
#                           root; and one values path — `internal.identity` — that is both a
#                           bound contract key and a render prerequisite, whose probe therefore
#                           goes into the raw tree rather than onto the value.
#
# Deliberately NOT run by the Documentation job, unlike `contracts` and `sync-kube-refs`. Those
# regenerate a fact about an image or a Kubernetes release, which no reviewer has an opinion
# about; this regenerates a test, and a test that appears on a pull request without its author
# having seen it is a test nobody has read. `check-contract-tests` is therefore in `just check`
# where its siblings are not — see the comment there.
[doc("Generate the helm-unittest suites that prove each chart delivers what its contract declares")]
[group('contracts')]
contract-tests chart='':
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/generate-contract-tests.py {{ chart }} --charts {{ charts }}

# Report generated suites that no longer match the contracts they were generated from.
#
# The offline half of the recipe above, and the reason the generated file is worth committing at
# all: a suite regenerated on the fly would test whatever the contract says today, which is a
# tautology. Committed, it is a diff a reviewer reads — the removed key, the changed default, the
# probe that stopped being valid — landing in the same pull request as the digest bump that
# caused it.
#
# Compares the bytes the generator would write against the bytes on disk, and prints the
# difference rather than only the verdict. `check-contracts` above reaches for `git diff` because
# what it checks is whether a *refresh* was run; here there is nothing to run first, so the
# comparison is the script's own and works on a dirty tree and on a fresh clone alike.
[doc("Report generated contract suites that no longer match the contracts behind them")]
[group('contracts')]
check-contract-tests:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/generate-contract-tests.py --check --charts {{ charts }}

# Write the credential reference each contracted chart's README carries, from the contract.
#
# Which credentials an image needs, what to name the files that carry them and which of a
# multi-service chart's binaries reads each one are all published per key — and all of them were
# transcribed into every README by hand. That transcription drifts silently, because nothing
# downstream reads a README: `tankovault` documented seven of the sixteen its nine contracts
# declare, under one name no contract carries.
#
# The rows come from the contract now, between two HTML comments the chart places wherever the
# reference belongs. What stays hand-written is the pair of columns no contract can fill — the
# chart value that carries a credential, and the condition under which a release needs it — and
# both live in `config-contract.yaml` beside the keys they describe, where an entry that outlives
# its credential or names a renamed value fails the gate rather than standing.
#
# Run by `just docs` before helm-docs, since it edits helm-docs' input.
[doc("Write each chart's README credential reference from its contract")]
[group('contracts')]
config-readme:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config_readme.py --charts {{ charts }}

# Fail when a chart's README credential reference is not what its contract describes.
#
# The asserting half of the recipe above. In `just check` rather than left to the Documentation
# job, which does repair it: the two hand-written columns are not repairable, and an entry naming
# a credential the image dropped or a chart value somebody renamed is exactly what an automated
# digest bump produces. Reads committed files only — no network, no render, no cluster.
[doc("Fail when a chart's README credential reference is behind its contract")]
[group('contracts')]
check-config-readme:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}
    "$python" {{ scripts }}/config_readme.py --charts {{ charts }} --check

# --------------------------------------------------------------------------------------------
# Creating a chart
# --------------------------------------------------------------------------------------------

# Create a new chart: the chassis every chart here shares, and the configuration surface its
# image's contract describes.
#
# Every other recipe in this group reads a chart and holds it against a contract. This one runs
# the other way: it fetches the contract and *writes* the chart that satisfies it — the values
# blocks, the `# @config` markers, the template helpers that project them, the declaration, the
# round-trip enrolment and the fixtures.
#
# Both halves of a chart here are hand-copied from a neighbour today and both go wrong doing it.
# The chassis drifts silently: 2,069 lines of the eight charts' values.yaml files are
# byte-identical to another chart's and the rest of each block is not, which is what copy-editing
# one neighbour repeatedly looks like. The configuration half goes wrong differently — it is
# written by reading a 400 KB JSON document by hand, so a key gets missed, and a key nothing
# surfaces is exactly the defect `check-config-bindings` exists to catch after the fact.
#
# **The second networked recipe in this repository, and it uses the first one to do it.** The
# vendoring is `refresh-contracts.py` itself, called as a module rather than reimplemented, so a
# contract this writes has been through the same signature verification, the same label agreement
# check and the same `source` envelope as every contract `just contracts` has ever written. There
# is no second path into `charts/*/contracts/` — one that arrived some other way would be trusted
# by every gate downstream.
#
# Deliberately NOT in `just check`, for the reason `contracts` is not: `check` is the network-free
# set. It is also not something an aggregate could run, since it creates a directory.
#
# The chart it writes passes `just check`. What it does not write is the prose — the chart
# description and the README — and anything the workload needs beyond a Deployment: a Service, an
# Ingress, a PodMonitor, a volume. None of that is in a contract, and a `server.port` key says the
# binary can bind a port, not that anything should reach it. The recipe prints what is left as an
# ordered list rather than leaving it to be discovered.
#
# **Two forms, and the second is why this is not only a contracts recipe.** With a contract the
# chart gets the whole configuration surface. With `--no-contract` it gets the chassis and the
# workload and asserts nothing about the image's configuration — no `config` escape hatch, no
# `configMount`, no ConfigMap, no Secret — which is what `teamspeak` and `paperless-ngx` are, and
# two thirds of what every other chart here is. A `config-contract.yaml` is written in that form
# only when the image is first-party, decided from the same list `check-contract-coverage` reads,
# because a chart pinning `paperless-ngx` owes no declaration and one that could only say "no" is
# a file nobody should be asked to write.
#
#   just new-chart foo timschoenle/foo 'v1.2.3@sha256:...'
#   just new-chart foo timschoenle/foo v1.2.3 'What foo deploys, in one sentence.'
#   just new-chart foo library/redis 8.0 '' '' --no-contract
#   just new-chart foo timschoenle/foo v1.2.3 '' 'No contract until 2.0.' --no-contract
#
# `description` and `reason` are recipe parameters rather than flags, because `*flags` reaches
# the shell unquoted and a sentence there is word-split. Pass '' to skip one.
#
# Pin by digest. A tag alone works — the refresh resolves it — but the chart then carries a
# mutable reference, and `just check-config` refuses to validate against a contract that cannot be
# tied to a digest.
[doc("Create a new chart: the shared chassis, plus the surface its image's contract describes")]
[group('contracts')]
new-chart name repository version description='' reason='' *flags:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ resolve_python }}

    # `description` and `reason` are recipe parameters rather than flags because `*flags` is
    # interpolated into the command line unquoted, so `just` hands the shell a bare string and
    # the shell word-splits it. A one-word reason survives that; a sentence does not, and one
    # containing a `;` terminates the command. Both are prose by definition, so both get a
    # parameter this recipe can quote.
    args=(--charts {{ charts }} --templates .github/templates/chart)
    if [ -n '{{ description }}' ]; then args+=(--description '{{ description }}'); fi
    if [ -n '{{ reason }}' ]; then args+=(--reason '{{ reason }}'); fi

    CONTRACT_SIGNER='{{ contract_signer }}' \
      "$python" {{ scripts }}/new-chart.py '{{ name }}' '{{ repository }}' '{{ version }}' \
        "${args[@]}" {{ flags }}
