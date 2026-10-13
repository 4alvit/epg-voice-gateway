# Deploy the gateway on Kubernetes

This project consumes an existing HLS proxy and an existing EPG source. It does
not install, configure, restart, or replace either service. OttPlay is not a
runtime dependency. Use a general-purpose server or VM with network access to
the HLS proxy, rather than an energy controller.

The examples use reserved placeholder names. Replace them in private deployment
files. Keep provider URLs, account identifiers, domains, IP addresses, skill IDs,
tokens, and deployment receipts outside the public repository.

## Runtime model

EPG and channel data use an indexed SQLite database on a 2 GiB ReadWriteOnce
PersistentVolumeClaim. The Deployment mounts it at `/data` and sets
`EPG_DATABASE_PATH=/data/catalog.sqlite3`. Use the cluster's existing default
StorageClass or select one in the private manifest; no cloud database account,
subscription, or external database service is required.

Run one replica with one Uvicorn worker. Playback sessions remain in memory;
a restart invalidates existing playback URLs and requires another play command.
The Deployment uses `Recreate` to prevent overlapping instances with different
session stores. Do not add replicas or workers until shared session storage is
implemented. SQLite persistence does not remove the guide freshness checks.
The pod does not need a Kubernetes API token and runs as UID/GID 10001 with
`fsGroup: 10001`, a read-only root filesystem, and a writable `/data` mount.

`/healthz` is the process check used by the startup, liveness and Kubernetes
readiness probes. The application serves it after initialization. Keeping the
pod in the Service when a feed becomes stale allows existing signed media
sessions to continue and control requests to receive a useful HTTP error.
`/readyz` separately reports whether the catalogue is fresh enough to resolve
programming data. Stale or unavailable data still causes new resolution,
catalogue and playback requests to fail closed with HTTP 503; no new playback
grant is issued. A Ready pod is not proof of fresh EPG, stream reachability or
device compatibility. Check `/readyz` and an authenticated current lookup as
separate deployment acceptance gates.

Changing a probe in the Deployment updates its pod template and triggers a
rollout. Apply this change during an idle window: replacing the process
invalidates its in-memory media sessions even though catalogue expiry alone
does not. Do not restart an active stream just to test stale-data behavior.

The supplied CPU and memory settings are starting budgets, not a capacity
guarantee. EPG size and concurrent streams affect consumption. Observe the
installed workload before increasing concurrency. The gateway does not
transcode video.

## Build an image

From the project directory, build for the worker's architecture and push to a
registry the cluster can already access:

```sh
docker buildx build --platform linux/amd64 \
  --tag registry.example.invalid/epg-voice-gateway:reviewed-version \
  --push .
```

Use an `arm64` build instead when the selected worker is ARM. Cross-platform
builds require a builder that supports the target architecture. Existing k3s
registry mirrors and credentials remain operator-owned; this project does not
modify them. Pin the deployed image by digest when possible.

For a registry-free installation, build and save the image on a compatible
machine, then import the archive into **every eligible worker**:

```sh
docker build --tag epg-voice-gateway:reviewed-version .
docker save epg-voice-gateway:reviewed-version --output gateway-image.tar
sudo k3s ctr images import gateway-image.tar
```

Copy the archive to each target worker before running the import command there.
Set the Deployment image to `docker.io/library/epg-voice-gateway:reviewed-version`
and `imagePullPolicy: Never`. Restrict scheduling to compatible, prepared workers
in the private manifest. Do not import a host-architecture image onto a worker
with a different architecture.

## Prepare configuration

Copy `deploy/runtime.env.example` to a private directory outside this repository.
Protect the directory and file before entering credentials:

```sh
install -d -m 700 /private/path/epg-voice-gateway
install -m 600 deploy/runtime.env.example \
  /private/path/epg-voice-gateway/runtime.env
```

Edit that file locally. Generate independent random values for `EPG_API_TOKEN`
and `EPG_SIGNING_KEY`; use at least 32 random bytes for each. Do not reuse an HLS
provider password. The example's replacement strings are not working secrets.
The file is a Kubernetes environment file, not a shell script: values are
literal, JSON has no surrounding shell quotes, and the file must not be sourced.

Configure these service addresses:

- `EPG_HLS_PROXY_URL`: the internal origin of the existing HLS proxy, including
  its scheme and optional port, with no credentials, query string, or path.
- `EPG_PLAYLIST_PATH`: the existing proxy's playlist path. Matching XMLTV IDs are
  preferred; the gateway can also match normalized channel names, so verify the
  resulting channel mapping against the configured guide.
- `EPG_XMLTV_URLS`: a JSON array of trusted operator-configured XMLTV feed URLs.
  Keep any URL containing provider credentials in this secret file.
- `EPG_MEDIA_BASE_URL`: the separate HTTPS media origin reachable by the playback
  device. It must never point to the Cloudflare control endpoint.
- `EPG_ALEXA_SKILL_ID`: the exact ID of the custom skill allowed to call `/alexa`.
- `EPG_HA_URL`, `EPG_HA_TOKEN`, `EPG_HA_TARGETS`: optional Home Assistant connection
  and an explicit target-name-to-entity mapping for Cast playback.

The API token authorizes control requests. Media sessions use their own bounded
authorization; the raw provider URL is not a client-supplied control parameter.
Changing the signing key or restarting the pod invalidates current sessions.

Create the namespace and load the environment file without printing its values:

```sh
kubectl create namespace epg-voice-gateway --dry-run=client -o yaml | kubectl apply -f -
kubectl -n epg-voice-gateway create secret generic epg-voice-gateway-env \
  --from-env-file=/private/path/epg-voice-gateway/runtime.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

Kubernetes Secret values are not encrypted merely because they are base64
encoded. Use the cluster's existing restricted RBAC and encryption-at-rest
configuration. Avoid saving rendered Secret YAML or unrestricted pod environment
dumps in build logs or troubleshooting reports.

## Install the workload

Copy `deploy/k3s.yaml` to the private directory. Set its image and, if needed,
node selector and image pull policy. Apply only this project's resources:

```sh
kubectl apply -f /private/path/epg-voice-gateway/k3s.yaml
kubectl -n epg-voice-gateway rollout status deployment/epg-voice-gateway
kubectl -n epg-voice-gateway get pods
```

Secret changes require a controlled Deployment restart before the process reads
the new environment. Every restart ends active sessions; schedule updates
accordingly. The image disables HTTP access logs because media request paths
contain bearer grants. Also disable or redact full media paths in ingress and
load-balancer access logs.

Initially use a local port forward for health and authenticated control checks:

```sh
kubectl -n epg-voice-gateway port-forward service/epg-voice-gateway 18080:8080
```

In another terminal, check `http://127.0.0.1:18080/healthz` and `/readyz`. A media
URL issued through this forward still uses `EPG_MEDIA_BASE_URL`; the forward is
not a substitute for the device-facing HTTPS route.

## Add ingress deliberately

Read [networking.md](networking.md) before publishing an endpoint. The control
and media templates are separate and are not included in `deploy/k3s.yaml`.
They reference operator-provided TLS Secrets in the gateway namespace. They do
not issue certificates, modify DNS, or open a router/firewall.

Do not apply these templates to the existing HLS proxy's Deployment or copy a
historical HLS installation manifest over the live service. The only dependency
is its current internal endpoint.

## Storage and backup

The PVC belongs to this gateway. It is independent of the HLS proxy's cache and
configuration volumes. A node-local StorageClass pins the data to a worker;
inspect its volume affinity and recovery behavior before moving the workload
to another node. Do not assume that a ReadWriteOnce volume is replicated or
backed up. Use a local or block-backed filesystem with SQLite-compatible locking;
do not put a WAL-mode SQLite database on NFS or another shared network filesystem
([SQLite WAL requirements](https://sqlite.org/wal.html)). The database is a local
cache of configured sources, while the
private environment file remains the source of connection credentials and
configuration.

Treat database backups as private: channel records can contain sensitive
upstream references even when the public API does not expose them. Use SQLite's
online backup API to create a consistent backup while the service runs, or stop
the single writer before copying the database. Copying only the main file of an
active database can miss changes in its write-ahead log. Store backups outside
the live PVC, restrict access, and retain the matching application version and
private configuration separately. Restoring an EPG database does not restore
playback sessions or turn old guide records into fresh data.

## Roll back

To undo an application update, restore the previously reviewed image and
configuration, then wait for rollout completion. Session URLs from the stopped
process cannot be recovered; issue new play commands.

To remove this installation, delete only the gateway's two optional Ingresses,
Deployment, Service, and dedicated environment Secret. Keep shared Traefik,
certificates, DNS infrastructure, the existing HLS proxy, and Home Assistant.
Keep the gateway PVC unless you explicitly intend to discard its stored data;
take a consistent backup before deleting or replacing it.
Remove a dedicated DNS record or tunnel rule only if it was created for this
installation. Do not delete a namespace that contains other workloads.

## Existing CI runner

The repository's runner is the existing `gha-runner-epg-voice` Deployment in
`github-actions-epg-voice`. It was provisioned with `kubectl create`; there is no
complete runner manifest in this repository. The scoped
[`channel-timeout.patch.json`](../deploy/runner-mp/channel-timeout.patch.json)
records its worker startup setting without taking ownership of the remaining
Deployment configuration.

On a busy host, the runner's worker can take longer than the default 30 seconds
to receive its job. Runner 2.337.0 supports
[`GITHUB_ACTIONS_RUNNER_CHANNEL_TIMEOUT`](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Listener/JobDispatcher.cs#L50-L74)
from 30 to 300 seconds. Set it to 120 seconds after a confirmed worker-channel
timeout. This setting does not change the workflow's individual job timeouts.

First verify that the repository runner is idle and the cluster context is
correct. A server dry run validates the patch without replacing the runner pod:

```sh
gh api repos/4alvit/epg-voice-gateway/actions/runners \
  --jq '.runners[] | {name, status, busy}'
kubectl -n github-actions-epg-voice patch deployment gha-runner-epg-voice \
  --type=strategic --patch-file=deploy/runner-mp/channel-timeout.patch.json \
  --dry-run=server -o name
```

Once the runner is idle, apply the same reviewed patch and wait for its rollout:

```sh
kubectl -n github-actions-epg-voice patch deployment gha-runner-epg-voice \
  --type=strategic --patch-file=deploy/runner-mp/channel-timeout.patch.json
kubectl -n github-actions-epg-voice rollout status deployment/gha-runner-epg-voice \
  --timeout=180s
```

Strategic merge matches the `runner` container and environment variable by name,
so repeating this patch is idempotent. It preserves the existing image,
resources, scheduling, registration, token references and all other environment
variables. After the runner reconnects, retry the affected CI job and verify
that its worker starts and the complete test suite passes.
