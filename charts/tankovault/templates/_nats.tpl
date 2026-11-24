{{/*
Per-service users on the bundled NATS.

Upstream v11.3.0 (`deploy/nats/nats.conf`, TimSchoenle/TankoVault#723) gives every service that
speaks to the broker a user of its own, allowed only the subjects its code publishes and
subscribes to. Without it any pod that can reach port 4222 can queue scan tasks, forge chapter
events or push notifications to any user. The network policy narrows who can reach the port; it
cannot say what a client may do once connected.

Only the bundled NATS: an external broker's users are its own configuration, and
`externalNats.perServiceSecret` is how a release delivers credentials for one.
*/}}
{{- define "tankovault.natsAuthEnabled" -}}
{{- if and .Values.nats.enabled .Values.nats.auth.enabled -}}true{{- end -}}
{{- end -}}

{{/*
The NATS user of each service that speaks to the broker, keyed by service key. The user name is
the service slug, as upstream names them.
*/}}
{{- define "tankovault.natsUsers" -}}
{{- range $service, $spec := include "tankovault.serviceSpecs" . | fromYaml -}}
{{- if $spec.needsNats }}
{{ $service }}: {{ $spec.slug }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The Secret keys one NATS user's credentials are stored under. Underscores rather than the slug's
hyphen, so every key in the Secret keeps the `__`-separated shape of a configuration path.

Args: the user (service slug).
*/}}
{{- define "tankovault.natsPasswordKey" -}}
{{- printf "nats__password__%s" (replace "-" "_" .) -}}
{{- end -}}

{{- define "tankovault.natsUrlKey" -}}
{{- printf "nats__url__%s" (replace "-" "_" .) -}}
{{- end -}}

{{/*
The environment variable the NATS server reads one user's password from. `nats.conf` resolves
an undefined `$VARIABLE` from the environment, so the password never appears in the ConfigMap.

Args: the user (service slug).
*/}}
{{- define "tankovault.natsPasswordEnv" -}}
{{- printf "TANKOVAULT_NATS_PASSWORD_%s" (replace "-" "_" . | upper) -}}
{{- end -}}

{{/*
The bundled NATS server configuration under per-service users.

The `authorization` block is upstream's `deploy/nats/nats.conf` at v11.3.0, verbatim apart from
the password variables, which are spelt by `tankovault.natsPasswordEnv`. A new subject, consumer
or JetStream call in a service fails with a permissions violation at runtime until it is added
here, and nothing offline checks this against the code: diff it against upstream's file on every
image update that touches `crates/bus` or `deploy/nats/`.

The listener settings replace the `-js -sd /data -m 8222` flags the server runs with when auth
is off, so the two shapes serve the same ports and the same store.
*/}}
{{- define "tankovault.natsConf" -}}
{{- $users := values (include "tankovault.natsUsers" . | fromYaml) | sortAlpha -}}
{{- if ne (join "," $users) "api,control-plane,notifier,worker" -}}
{{- fail (printf "tankovault.natsConf defines users api, control-plane, notifier and worker, but the services that need NATS are now %s; add or remove the matching `authorization` entries" (join ", " $users)) -}}
{{- end -}}
port: 4222
http: 8222
jetstream {
  store_dir: "/data"
}

authorization {
  users: [
    # `/v1/me/stream` relay: subscribes to per-user live notifications, publishes nothing.
    {
      user: api
      password: ${{ include "tankovault.natsPasswordEnv" "api" }}
      permissions: {
        publish: { deny: [">"] }
        subscribe: { allow: ["notify.user.*"] }
      }
    }

    # Dispatches scan tasks, aggregates progress and reconciles lanes against the broker.
    {
      user: control-plane
      password: ${{ include "tankovault.natsPasswordEnv" "control-plane" }}
      permissions: {
        publish: {
          allow: [
            "scan.tasks.>"
            "scan.events.progress"
            "$JS.API.STREAM.CREATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.CREATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.INFO.TANKOVAULT_TASKS"
            "$JS.API.STREAM.INFO.TANKOVAULT_EVENTS"
            # `lane_backlog` reads every worker lane.
            "$JS.API.CONSUMER.INFO.TANKOVAULT_TASKS.*"
            "$JS.API.CONSUMER.INFO.TANKOVAULT_EVENTS.tankovault-progress"
            "$JS.API.CONSUMER.CREATE.TANKOVAULT_EVENTS.tankovault-progress"
            "$JS.API.CONSUMER.CREATE.TANKOVAULT_EVENTS.tankovault-progress.>"
            "$JS.API.CONSUMER.MSG.NEXT.TANKOVAULT_EVENTS.tankovault-progress"
            "$JS.ACK.TANKOVAULT_EVENTS.tankovault-progress.>"
            "$JS.ACK.*.*.TANKOVAULT_EVENTS.tankovault-progress.>"
          ]
        }
        subscribe: { allow: ["_INBOX.>"] }
      }
    }

    # Runs scan tasks from per-provider lanes; fans out child tasks and emits progress and
    # chapter events.
    {
      user: worker
      password: ${{ include "tankovault.natsPasswordEnv" "worker" }}
      permissions: {
        publish: {
          allow: [
            "scan.tasks.>"
            "scan.events.progress"
            "scan.events.chapter_discovered"
            "$JS.API.STREAM.CREATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.CREATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.INFO.TANKOVAULT_TASKS"
            "$JS.API.STREAM.INFO.TANKOVAULT_EVENTS"
            "$JS.API.CONSUMER.NAMES.TANKOVAULT_TASKS"
            "$JS.API.CONSUMER.INFO.TANKOVAULT_TASKS.*"
            "$JS.API.CONSUMER.CREATE.TANKOVAULT_TASKS.>"
            "$JS.API.CONSUMER.MSG.NEXT.TANKOVAULT_TASKS.*"
            # `retire_wildcard_task_consumer`, the pre-lane consumer, and nothing else.
            "$JS.API.CONSUMER.DELETE.TANKOVAULT_TASKS.tankovault-workers"
            "$JS.ACK.TANKOVAULT_TASKS.>"
            "$JS.ACK.*.*.TANKOVAULT_TASKS.>"
          ]
        }
        subscribe: { allow: ["_INBOX.>"] }
      }
    }

    # Turns chapter events into notifications and pushes them live.
    {
      user: notifier
      password: ${{ include "tankovault.natsPasswordEnv" "notifier" }}
      permissions: {
        publish: {
          allow: [
            "notify.user.*"
            "$JS.API.STREAM.CREATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.CREATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_TASKS"
            "$JS.API.STREAM.UPDATE.TANKOVAULT_EVENTS"
            "$JS.API.STREAM.INFO.TANKOVAULT_TASKS"
            "$JS.API.STREAM.INFO.TANKOVAULT_EVENTS"
            "$JS.API.CONSUMER.INFO.TANKOVAULT_EVENTS.tankovault-notifier"
            "$JS.API.CONSUMER.CREATE.TANKOVAULT_EVENTS.tankovault-notifier"
            "$JS.API.CONSUMER.CREATE.TANKOVAULT_EVENTS.tankovault-notifier.>"
            "$JS.API.CONSUMER.MSG.NEXT.TANKOVAULT_EVENTS.tankovault-notifier"
            "$JS.ACK.TANKOVAULT_EVENTS.tankovault-notifier.>"
            "$JS.ACK.*.*.TANKOVAULT_EVENTS.tankovault-notifier.>"
          ]
        }
        subscribe: { allow: ["_INBOX.>"] }
      }
    }
  ]
}
{{- end -}}
