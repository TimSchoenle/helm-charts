# Helm Charts

A collection of Helm charts for Kubernetes applications, focusing on utility services and automation tools.

## TL;DR

```bash
helm repo add timschoenle https://timschoenle.github.io/helm-charts
helm repo update
helm search repo timschoenle
helm install my-release timschoenle/<chart-name>
```

## Prerequisites

- Kubernetes 1.19+
- Helm 3.0+ (CI validates against Helm 3.21 and 4.2)

## Usage

[Helm](https://helm.sh) must be installed to use the charts.
Please refer to Helm's [documentation](https://helm.sh/docs/) to get started.

### Add Helm Repository

Once Helm is set up properly, add the repository as follows:

```bash
helm repo add timschoenle https://timschoenle.github.io/helm-charts
```

### Update Repository

You can then run `helm search repo timschoenle` to see the available charts.

```bash
helm repo update
```

### Install a Chart

To install a chart from this repository:

```bash
helm install my-release timschoenle/<chart-name>
```

To install with custom values:

```bash
helm install my-release timschoenle/<chart-name> -f values.yaml
```

### Upgrade a Chart

To upgrade an existing release:

```bash
helm upgrade my-release timschoenle/<chart-name>
```

### Uninstall a Chart

To uninstall a chart:

```bash
helm uninstall my-release
```

## Available Charts

| Chart | Description | Chart Version |
|-------|-------------|---------------|
| [cloudflare-access-webhook-redirect](./charts/cloudflare-access-webhook-redirect) | Middleware service that authenticates requests using Cloudflare Access tokens before forwarding them to target services | Check [Chart.yaml](./charts/cloudflare-access-webhook-redirect/Chart.yaml) |
| [mp-stats-legacy-viewer](./charts/mp-stats-legacy-viewer) | Viewer for legacy MP statistics | Check [Chart.yaml](./charts/mp-stats-legacy-viewer/Chart.yaml) |
| [netcup-offer-bot](./charts/netcup-offer-bot) | Automated bot for monitoring and processing Netcup offers | Check [Chart.yaml](./charts/netcup-offer-bot/Chart.yaml) |
| [portfolio](./charts/portfolio) | Personal portfolio website | Check [Chart.yaml](./charts/portfolio/Chart.yaml) |
| [s3-bucket-perma-link](./charts/s3-bucket-perma-link) | Service for generating and managing permanent links to S3 bucket objects | Check [Chart.yaml](./charts/s3-bucket-perma-link/Chart.yaml) |

The [`common`](./charts/common) library chart is not published. It holds the shared template
partials every chart above composes, and is consumed locally via `file://../common`.

## Upgrading

Chart majors change the values contract. See [UPGRADING.md](./UPGRADING.md) for the
migration notes, including the NetworkPolicy egress hardening that can block traffic a
previous release silently allowed.

## Security posture

Every chart renders pods that satisfy the [restricted Pod Security Standard][pss]: non-root,
no privilege escalation, all Linux capabilities dropped, read-only root filesystem,
`seccompProfile: RuntimeDefault`, and no ServiceAccount token mounted. Any of it can be
relaxed per chart via `podSecurityContextPreset` / `securityContextPreset` and the
corresponding context values.

NetworkPolicies are opt-in (`networkPolicy.enabled`). When enabled, every generated egress
rule is scoped by a `to:` selector — DNS to the cluster DNS service, HTTP/HTTPS to a
configurable CIDR that excludes RFC1918 private space and the link-local range covering the
cloud instance metadata endpoint.

[pss]: https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted

## Chart Documentation

For detailed documentation on each chart, including configuration options and examples, please refer to the individual chart's README:

```bash
# View chart README
helm show readme timschoenle/<chart-name>

# View all available values
helm show values timschoenle/<chart-name>

# View chart information
helm show chart timschoenle/<chart-name>
```

You can also browse the documentation for each chart in the [charts directory](./charts/).


### Reporting Issues

If you encounter any issues or have feature requests:
1. Check if the issue already exists in the [issue tracker](https://github.com/timschoenle/helm-charts/issues)
2. If not, create a new issue with detailed information
3. Include Kubernetes and Helm versions
4. Provide relevant logs and configuration
