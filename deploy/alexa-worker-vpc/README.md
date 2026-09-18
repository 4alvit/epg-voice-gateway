# Dedicated Alexa control relay

This optional Worker exposes only `POST /alexa` on a dedicated HTTPS
`workers.dev` hostname. It reaches the existing EPG Voice Gateway through one
private Workers VPC Service and an existing Cloudflare Tunnel:

```text
Amazon Alexa -> dedicated Worker -> fixed VPC Service -> existing Tunnel -> gateway /alexa
Alexa video device -> existing signed HTTPS media endpoint -> existing HLS Proxy
```

The Worker carries small skill requests and JSON responses. It never carries
HLS playlists or video segments. The gateway remains responsible for Amazon
signature, certificate, timestamp, and exact skill-ID validation. Its
`EPG_ALEXA_SKILL_ID` must identify the separate media skill. Do not point an
unrelated energy skill at this backend.

Adding this directory does not publish anything. Terraform and Wrangler
examples disable publication by default. They contain no account, tunnel,
subscription, or provider credentials.

## Plan A: existing free Workers account and VPC beta

Workers VPC is free during its open beta; normal Workers request and compute
limits still apply. Use the existing Workers Free plan. Do not activate a
trial, upgrade the plan, add payment details, or assume that VPC will remain
free after beta. If availability or terms change, stop and choose an approved
alternative. See [VPC pricing](https://developers.cloudflare.com/workers-vpc/platform/pricing/)
and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/).

This module changes no zone settings, Bot Fight Mode rules, Access policies,
DNS records, tunnel ingress, account namespace, or billing configuration.
Forwarding requests to an already challenged public hostname does not create
the private path described here.

## Request contract

- Require HTTPS, the exact configured `PUBLIC_HOST`, and `POST /alexa` without
  a query or fragment. All other paths and methods return a generic JSON 404.
- Require JSON content type, an uncompressed body, `Signature-256`, and
  `SignatureCertChainUrl`. The current gateway accepts SHA-256 signatures only;
  the legacy `Signature` header does not substitute for `Signature-256`.
- Preserve the original signed request bytes. Forward only content type and
  those two signature headers. Do not forward cookies, authorization tokens,
  client IP headers, caller routing hints, or Cloudflare Access credentials.
- Use only the fixed `BACKEND` VPC service and `/alexa` path. There is no public
  `fetch` fallback, caller-selected destination, or redirect following.
- Bound requests to 32 KiB, responses to 64 KiB, and the complete exchange to
  seven seconds, including stalled request and response bodies.
- Return valid JSON-object responses with upstream status 200 or 400–599;
  discard all upstream headers. Redirects, HTML challenges, malformed JSON,
  transport failures, and deadline failures become generic JSON 502 responses.
- Set `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and
  `Referrer-Policy: no-referrer` on every response. Disable Worker observability,
  logs, traces, logpush, tail consumers, and preview URLs. Never log signed
  request bodies, device identifiers, or signed playback URLs.

The relay does not verify signatures itself. Supplying arbitrary strings in
the two required headers cannot authorize a request: the gateway still rejects
invalid signatures, certificates, timestamps, and skill IDs.

## Verify the private target first

1. Identify the existing tunnel, every active connector, its network namespace,
   QUIC transport, and a compatible cloudflared version. Probe the gateway from
   the connector's own network namespace. Reachability from Home Assistant or
   another pod does not prove this path.
2. The default target is `127.0.0.1:8080`. Use loopback only if that namespace
   contains the gateway or a verified dedicated forwarder. For a Kubernetes
   connector, set `backend_ipv4` to the verified reachable Service IPv4 and
   `backend_port` to its service port. All connectors of the selected tunnel
   must reach the same intended backend. Keep actual addresses outside Git.
3. Check private `GET /healthz` returns the gateway's JSON health response.
   An unsigned private `POST /alexa` must fail closed. Set the exact media skill
   ID in the existing protected gateway configuration and retain its certificate
   verification and outbound certificate-fetch requirements.
4. Verify the account already has the intended workers.dev namespace, and
   choose an unused dedicated Worker name. Do not adopt or replace a different
   skill's Worker. Choose one resource owner: Terraform or Wrangler.

If an existing Kubernetes connector has restricted egress, adapt
[`connector-egress.example.yaml`](connector-egress.example.yaml) after checking
the live connector namespace and pod labels, gateway namespace and app label,
and service target port. Apply this additive policy while retaining the current
policy and its DNS and tunnel rules. Then probe `/healthz` from a container
sharing the connector pod's network namespace, and verify unrelated destinations
remain blocked. Delete only the added policy to roll back; do not replace the
existing connector policy.

## Terraform deployment

The [module](terraform/main.tf), pinned to Cloudflare provider **5.24.0**, owns
exactly four dedicated resources: one HTTP VPC Service, one Worker, one immutable
Worker version, and one deployment. `backend_ipv4` and `backend_port` select a
single destination. They do not grant access to an arbitrary private network.

Copy this directory to a protected working location outside Git. Copy
[`terraform.tfvars.example`](terraform/terraform.tfvars.example) to a private
variables file and replace its placeholders. Supply `CLOUDFLARE_API_TOKEN` via
the operator's environment; no gateway or provider token belongs in Terraform
variables. Protect plans and state using the established state-storage policy.

From the copied directory:

```sh
terraform -chdir=terraform init
terraform -chdir=terraform validate
terraform -chdir=terraform plan -var-file=/private/path/alexa-relay.tfvars \
  -out=/private/path/alexa-relay.tfplan
```

Review a plan that creates only the four dedicated resources. Keep `publish=false`
for the initial installation, then verify the resulting namespace, exact VPC
target, code hash, and bindings. Review a second plan with `publish=true` and
apply it only when endpoint publication is authorized. This does not enable a
paid plan. Keep the initial state and previous Worker version for recovery.

The module outputs `alexa_url`, the proposed endpoint, and `relay_sha256`, the
reviewed source hash. A Terraform apply is not proof of VPC transport, Amazon
request verification, or video playback.

For Wrangler, provision the exact single HTTP VPC Service separately and copy
[`wrangler.example.jsonc`](wrangler.example.jsonc) to private `wrangler.jsonc`
beside the relay. Fill in the exact service ID and public hostname, verify with
`wrangler deploy --dry-run`, and retain `workers_dev=false` until publication
is authorized. Never manage the same Worker with both Terraform and Wrangler.

## Verify and connect the media skill

After publication, verify these separate acceptance gates:

1. Browser GET, unknown paths, queries, private `/v1` APIs, and `/media` paths
   return 404. Missing signature headers are rejected before a backend call.
2. A synthetic JSON request with both bounded signature headers reaches the
   gateway and receives its expected invalid-request rejection. Compare this
   sanitized result with a direct private probe; an edge-only 400 is not
   evidence that the VPC path works. Do not record real Alexa request payloads.
3. Open the separate media skill in Amazon Developer Console. Retain its existing
   model and invocation name. Select **Endpoint → HTTPS**, use the verified
   `alexa_url`, and choose the certificate option matching the trusted public
   certificate presented by the workers.dev endpoint. Preserve previous values
   privately before saving. Ensure **Interfaces → Video App** is enabled.
4. Use the development skill's simulator to obtain a genuine Amazon-signed
   request. Confirm it reaches the gateway without a Cloudflare challenge and
   returns a valid skill response. Synthetic invalid-signature probes cannot
   prove this gate.
5. Invoke the development media skill on a device that exposes `VideoApp`.
   Verify recognition, a fresh signed media grant, moving video, sound, and
   sustained playback separately. A device that renders APL cards need not
   support third-party `VideoApp.Launch` video.

This is a self-hosted household backend. Connecting a development skill is
separate from public store certification or support for multiple households.
There is no automatic store publication in this module.

## Plan B: ordinary Tunnel with an explicit Bot Fight Mode change

Plan B avoids the Workers VPC beta. It uses a dedicated HTTPS hostname in the
existing zone and an ordinary Cloudflare Tunnel route to the same gateway.
Free Bot Fight Mode operates across the zone and cannot be skipped by WAF
custom or Page Rules. Disabling it is a separate, explicit zone-wide decision;
do not apply that change while deploying Plan A.
[Cloudflare Bot Fight Mode limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/#limitations).

If that decision is approved:

1. Preserve existing zone and tunnel configuration. Disable Bot Fight Mode
   through the established zone resource owner. Other WAF, Access, country, or
   IP restrictions may still block Amazon and require separate diagnosis.
2. Add only a dedicated control hostname with path `^/alexa$` to the ordinary
   tunnel. Follow it with an HTTP 404 catch-all for that hostname. Keep the
   existing final global catch-all after all existing ingress rules. Do not
   publish `/v1`, health endpoints, or media through this control hostname.
3. Verify a trusted public certificate, synthetic rejection behavior, and a
   genuine signed Amazon request. Set the media skill's HTTPS endpoint to the
   verified ordinary-tunnel URL, preserving its previous value privately.
4. Verify actual video playback before disabling Plan A with `publish=false`
   through its existing Terraform owner, or `workers_dev=false` through Wrangler.
   Keep the separate signed media endpoint unchanged.

If abandoning either attempt, restore the recorded skill endpoint and preserve
the prior Worker version, resource state, and tunnel configuration. A restored
URL that still receives Bot Fight Mode challenges is a configuration rollback,
not a working fallback. Remove a dedicated VPC Service or additive NetworkPolicy
only after no remaining workload depends on it.

## Local verification

```sh
node --test deploy/alexa-worker-vpc/relay.test.mjs
terraform -chdir=deploy/alexa-worker-vpc/terraform fmt -check
terraform -chdir=deploy/alexa-worker-vpc/terraform validate
```

Run these commands from the repository root; Terraform validation requires an
initialized provider directory. Tests use synthetic bodies and a mock VPC
binding. They cover signed-byte preservation, exact-route rejection, signature
header requirements, size boundaries, upstream response filtering, and deadlines.
They do not deploy resources or establish physical-device acceptance.
