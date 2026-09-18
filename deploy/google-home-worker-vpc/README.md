# Optional Google server-request relay

This template addresses a confirmed failure in which Cloudflare Bot Fight Mode
challenged Google's OAuth code exchange. A browser login succeeded, but Google
received an HTML HTTP 403 from `/auth/token` instead of HA's JSON response.
The relay exposes only two server-to-server paths on a dedicated `workers.dev`
host and reaches HA through one private Workers VPC Service:

```text
Phone browser -> existing HA HTTPS login -> Google callback
Google -> dedicated Worker -> fixed VPC Service -> existing Tunnel -> HA
```

The Worker preserves HA's OAuth and bearer-token validation. It has no shared
HA long-lived token, provider credentials, Google key, browser login page or
media route. HA's ordinary non-admin permissions still determine the linked
user's access; the relay does not provide entity-level OAuth scopes.

Adding these files does not deploy or publish a route. The templates start with
publication disabled. Do not simply forward to the challenged public HA host:
that does not establish a working private path around Bot Fight Mode.

## Cost and deployment choice

Workers VPC is an open beta, free during beta, with standard Workers request and
compute limits. Use an existing **Workers Free** account only. Do not activate a
trial, add billing, upgrade the plan, or promise permanent free VPC availability.
If terms or pricing change, stop and choose an approved alternative.
[VPC pricing](https://developers.cloudflare.com/workers-vpc/platform/pricing/),
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/).

A non-beta alternative needs a separately reachable HTTPS origin for these
paths, or an explicit decision to disable the zone's Bot Fight Mode. Disabling
it affects the whole zone. WAF custom or Page Rule exceptions cannot skip free
Bot Fight Mode. This module manages no zone settings, WAF rules, DNS or existing
public tunnel ingress. See [linking diagnostics](../../docs/google-linking-troubleshooting.md).

## Relay contract

- Require HTTPS and the exact `PUBLIC_HOST`. Reject query strings and all
  routes/methods except `POST /auth/token` and `POST /api/google_assistant`.
- Token requests use `application/x-www-form-urlencoded`. Fulfillment uses
  `application/json` and the original Bearer Authorization header.
- Keep original request bytes. Forward no cookies or caller-supplied forwarding
  headers. The binding, not request parameters, fixes the private destination.
- Bound request bodies to 32 KiB, responses to 256 KiB, and the complete request
  to eight seconds. Do not follow redirects or fall back to public `fetch`.
- Return accepted JSON responses with their upstream status. Preserve an
  upstream fulfillment authentication rejection as a generic JSON 401/403 even
  when HA supplied plain text, so an expired token can trigger Google's refresh
  flow. Do not return an upstream HTML challenge page.
- Use `Cache-Control: no-store`; disable Worker logs, traces and preview URLs.
  Do not log bodies, bearer tokens, authorization codes or token responses.

Run the local contract tests with Node.js 22 or newer:

```sh
node --test deploy/google-home-worker-vpc/relay.test.mjs
```

The tests use a mock binding. They do not prove VPC transport, Google linking
or playback on a physical device.

## Prepare the private connection

1. Verify the existing tunnel's connector host, network namespace, QUIC
   transport and compatible cloudflared version. The connector must reach HA.
   The supplied Terraform targets `127.0.0.1:8123`; it is suitable only when that
   namespace contains HA or a verified dedicated forwarder to HA, such as an
   existing sidecar. A container's loopback is not its host's loopback.
   Every connector in that tunnel
   must resolve loopback to the same intended service. Prefer one verified
   connector record; several edge connections for it are normal.
2. Confirm an unauthenticated `GET /api/google_assistant` at that private target
   reaches HA and returns 405. Verify HA's existing trusted-proxy configuration
   accepts the connector. Do not broadly relax trusted proxies as a workaround.
3. Inspect the existing account's Workers namespace and select an unused Worker
   name. This module must not adopt or replace an unrelated Worker. Registering
   a new account namespace can expose other Workers; the template instead
   requires an already-existing namespace.
4. Choose Terraform or Wrangler as the resource owner, not both. Keep actual
   account/tunnel/service IDs, deployment config and receipts outside Git.

## Terraform

The [generic module](terraform/main.tf) creates one HA HTTP VPC Service, one
Worker, its immutable code version and deployment. Provider credentials come
from `CLOUDFLARE_API_TOKEN`, not variables or committed files. Copy
[`terraform.tfvars.example`](terraform/terraform.tfvars.example) to a protected
private file and fill in your existing account, tunnel and namespace.

From this directory:

```sh
terraform -chdir=terraform init
terraform -chdir=terraform validate
terraform -chdir=terraform plan -var-file=/private/path/relay.tfvars \
  -out=/private/path/relay.tfplan
```

Protect local state and plans; use your established state backend or keep the
working directory outside Git. Review only the four dedicated resources before
applying the plan. Keep `publish=false` for the initial installation and verify
the resulting namespace, exact VPC target and binding. Then review a second
plan with `publish=true` and apply it when publication is authorized. The module
does not enable a paid plan. It must not modify existing Workers or zone rules.

For Wrangler, create the single private HTTP VPC Service first, copy
[`wrangler.example.jsonc`](wrangler.example.jsonc) to a private working copy next
to the relay, and fill in the exact public host and service ID. Keep
`workers_dev=false` until the configuration is reviewed. Use `wrangler deploy
--dry-run` before publishing. Do not manage the same Worker with both tools.

## Verify before changing Google Console

After authorized publication, use requests without real credentials to verify:

1. Unknown paths, browser GETs, and queries return 404 without reaching HA.
2. An invalid authorization code sent to `/auth/token` reaches HA and returns
   its JSON OAuth error, normally 400. A rejection only at the Worker is not
   private-origin proof.
3. An invalid Bearer token sent to `/api/google_assistant` reaches HA and returns
   401. No challenge HTML should appear. Inspect only statuses and sanitized
   counters; do not capture real token exchanges.
4. Bot Fight Mode and the rest of the zone's protections remain unchanged.

Then change only the Google draft's **Token URL** and **Cloud fulfillment URL**
to the Worker's matching paths. Keep the existing HA **Authorization URL**,
Google **Client ID**, scopes and Basic-auth setting unchanged. Retain the old
values privately. Start a fresh linking flow on the phone; do not reuse an old
callback code. Confirm Google's token exchange and initial SYNC, then test
catalogue synchronization and physical voice/playback separately.

## Rollback

Restore the prior Console URLs if abandoning the relay, then disable its public
address with `publish=false` or `workers_dev=false` through the chosen resource
owner. A prior URL still challenged by Bot Fight Mode is a rollback of the
configuration, not a working fallback. Keep HA OAuth and the zone protections.
Delete the dedicated VPC Service only after its Worker no longer uses it;
preserve the shared tunnel and HA configuration.
