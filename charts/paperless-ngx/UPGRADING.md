# Upgrading the paperless-ngx chart

Migration notes, newest first. Only the versions listed here need anything beyond
`helm upgrade`; everything in between is an image or dependency bump.

| Version | Applies to | Step |
|---|---|---|
| [2.0.0](#200) | releases that set any `resourcesPreset` | replace it with the `resources` block it stood for |
| [2.0.0](#200) | releases that set `networkPolicy.{ingress,egress}.extraRules` | rename to `networkPolicy.{extraIngress,extraEgress}` |
| [2.0.0](#200) | releases that set `networkPolicy.cilium.{toFQDNs,dnsMatchPatterns}` | move them under `networkPolicy.cilium.egress` |

The values contract is enforced by `values.schema.json`, so a key a new major removed or renamed
fails the render with the offending path named, rather than being silently ignored.

## 2.0.0

### Resource t-shirt sizes are gone

`resourcesPreset` is removed at the top level and from `postgresql`, `valkey`, `tika.server`,
`tika.gotenberg`, `backup` and `restore`. `resources` is the only knob left, and each of those
blocks now ships with the numbers its preset used to expand to — so a release that never
overrode a preset gets byte-identical pods and needs no change at all.

A preset was a word that meant something different in every chart, and reading the library was
the only way to find out what `medium` actually reserved. The values file now says it.

Only a release that set a preset explicitly has to change. Substitute the block it stood for:

| Preset | requests | limits |
|---|---|---|
| `nano` | `cpu: 10m`, `memory: 32Mi` | `memory: 64Mi` |
| `micro` | `cpu: 25m`, `memory: 64Mi` | `memory: 128Mi` |
| `small` | `cpu: 50m`, `memory: 128Mi` | `memory: 256Mi` |
| `medium` | `cpu: 100m`, `memory: 256Mi` | `memory: 512Mi` |
| `large` | `cpu: 250m`, `memory: 512Mi` | `memory: 1Gi` |

```yaml
# Before
tika:
  server:
    resourcesPreset: large

# After
tika:
  server:
    resources:
      requests:
        cpu: 250m
        memory: 512Mi
      limits:
        memory: 1Gi
```

No preset ever set a CPU limit, and the defaults still do not: a CPU limit cannot protect the
node the way a memory limit does — it only throttles the workload that owns it once it is hit.
Set `resources.limits.cpu` if you want one. The application's own `resources` is unchanged; it
was already explicit, because no preset was large enough for OCR.

### The network policy values match the `common` library

The chart's own policy builder and the library's now take the same value tree, so a rule is
written the same way whichever chart you are configuring. Three renames:

```yaml
# Before                                  # After
networkPolicy:                            networkPolicy:
  ingress:                                  extraIngress: [...]
    extraRules: [...]                       extraEgress: [...]
  egress:                                   cilium:
    extraRules: [...]                         egress:
  cilium:                                       toFQDNs: [...]
    toFQDNs: [...]                              dnsMatchPatterns: [...]
    dnsMatchPatterns: [...]
```

Nothing about where those rules land has changed: `extraIngress` and `extraEgress` are still
appended to the application's own policy and to nothing else. The bundled datastores and the
backup pod have policies this chart derives in full, and widening those alongside would be a
surprise rather than a convenience.

### The Cilium dialect gained the rules it was missing

`networkPolicy.cilium` now carries the same additions the library offers, all of them scoped to
the application's own pair:

| Value | What it says |
|---|---|
| `cilium.ingress.fromEntities` | named source sets — `cluster`, `host`, `remote-node`, `kube-apiserver` |
| `cilium.egress.toEntities` / `entityPorts` | named destination sets, optionally port-restricted |
| `cilium.egress.fqdnPorts` | override the ports the `toFQDNs` rule allows |
| `cilium.egress.httpRules` | L7 HTTP rules layered onto the `toFQDNs` rule |
| `cilium.extraIngress` / `cilium.extraEgress` | verbatim CiliumNetworkPolicy rules |

Setting `cilium.egress.toFQDNs` no longer requires setting `cilium.egress.dnsMatchPatterns`
alongside it. Previously the chart refused to render that combination, even though the L7 DNS
rule it was guarding is emitted unconditionally and defaults to `*` — so the guard fired on the
shipped defaults and the error it printed was wrong. Narrowing `dnsMatchPatterns` below what
`toFQDNs` names still denies those destinations; that part was always true, and is now stated in
the values file rather than enforced by a check that could not tell the difference.
