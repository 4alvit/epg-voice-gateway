# Google Home returns an error after HA login

A successful login in the phone browser does not prove Google's servers can
exchange the authorization code or request the device list. Diagnose those
steps before changing credentials, device categories or EPG matching.

## Locate the failed step

1. In Google Cloud Logs Explorer, select the integration's project and the time
   of the attempt. Look for `google.identity.accountlinking.type.AccountLinkingError`
   in `accountlinking-pa.googleapis.com/error`. Inspect `step`, `errorReason`
   and `response.status`. Request bodies may contain authorization codes and
   secrets: do not expand, copy or publish them for routine diagnosis.
2. `AUTH_CODE_EXCHANGE` with a non-JSON 403 means the token endpoint returned a
   rejection instead of the OAuth JSON Google expected. Compare its timestamp
   with Cloudflare Security Events for `/auth/token`. A `botFight` challenge is
   an edge failure before HA's token handler; an HA OAuth JSON error is a
   different failure. A probe from an allowed home IP does not prove Google
   server access.
3. Check the saved Console client ID, token URL, fulfillment URL, `email`/`name`
   scopes, and disabled HTTP Basic option against the
   [setup instructions](../examples/home-assistant/README.md#first-time-manual-google-linking).
   If the phone opens the HA app instead of returning to Google Home, also
   check the browser/app callback handling described by
   [HA's integration guide](https://www.home-assistant.io/integrations/google_assistant/).
4. Use count-only, private HA diagnostics to distinguish issued Google refresh
   tokens and registered Google agents. No issued token suggests failure at or
   before code exchange. A registered agent means SYNC reached HA; it does not
   prove Google accepted the response. Do not print `.storage/auth`, create an
   administrator account, or issue a shared HA token to bypass the failure.
5. After linking, independently check Google's stored device inventory with
   [HomeGraph `devices.sync`](https://developers.home.google.com/reference/home-graph/rest/v1/devices/sync):
   send `POST https://homegraph.googleapis.com/v1/devices:sync` with the linked
   `agentUserId`, using the same project's service-account credentials and the
   `https://www.googleapis.com/auth/homegraph` scope. Keep credentials and the
   agent ID private; retain only the status, agent-match boolean, device count
   and expected device types/traits. This reads inventory; it does not request
   a new fulfillment SYNC. A successful HA `google_assistant.request_sync`
   service call also does not expose Google's HTTP result. Verify later EPG
   changes, phone UI visibility and voice playback separately.

## Cloudflare Bot Fight Mode

In a recorded installation, two Google code exchanges received HTTP 403 and
non-JSON responses. The corresponding Cloudflare events identified
`managed_challenge` from `botFight`. The HA login from home succeeded, but no
Google refresh token was issued. The home-IP custom-rule skip did not bypass
the separate bot pipeline.

Free Bot Fight Mode cannot be skipped for selected URLs with WAF custom rules
or Page Rules. Adding more path exceptions there will not fix this cause.
[Cloudflare limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/#limitations).

Choose and validate a transport change explicitly:

- Keep zone protections and use the optional
  [narrow Workers VPC relay](../deploy/google-home-worker-vpc/README.md). VPC is
  currently a free beta; this is a dependency choice, not a permanent price
  guarantee. Browser login remains on HA's existing origin.
- Use an independently reachable trusted HTTPS origin with the necessary token
  and fulfillment paths. Verify IPv4/IPv6, TLS, firewall and HA authentication
  before changing Console URLs.
- Disable Bot Fight Mode only with acceptance of its zone-wide effect. This
  does not remove HA authentication, but other hostnames lose that bot layer.
  Other edge rules can still block Google, so real linking must be retested.

Do not disable Bot Fight Mode for the entire zone as an incidental diagnostic
step or assume an ordinary Worker fetching the same protected public HA URL
avoids the challenge. Keep paid upgrades outside this procedure.

After changing the selected route, start a new phone linking attempt. HA
authorization codes are single-use. Verify actual token exchange, SYNC and
current catalogue discovery before testing a microphone command.

## Plan A: keep Bot Fight Mode and use the private relay

Use the [Workers VPC relay runbook](../deploy/google-home-worker-vpc/README.md)
to route only Google's token exchange and fulfillment through the dedicated
Worker. Browser authorization stays on the existing HA HTTPS origin. Verify
the private target and public relay before changing the two Console URLs.
This plan preserves the zone's Bot Fight Mode and introduces the documented
Workers VPC beta dependency. Completing the deployment is not proof that
Google account linking succeeded.

The relay defaults to stock HA fulfillment: Terraform `channels_enabled = false`,
or a missing/string `"false"` `CHANNELS_ENABLED` binding. For the optional
[Home Channels integration](google-channels.md), install and verify its
authenticated HA endpoint first, then set `channels_enabled = true` or the
binding to the exact string `"true"`. Other configured values fail closed.
The relay's public `/api/google_assistant` and `/auth/token` URLs do not change;
only the fixed private fulfillment path changes to
`/api/epg_voice/google_assistant`. Browser authorization, OAuth credentials,
VPC destination, tunnel, WAF rules and billing remain unchanged. A caller
cannot activate this option through a header, body field or query parameter.

## Plan B: ordinary Cloudflare Tunnel with Bot Fight Mode disabled

This is a documented fallback, not a step to perform during Plan A. Do not
disable Bot Fight Mode now. Choose this plan only after explicitly accepting
that disabling Bot Fight Mode removes that bot protection for the entire
zone, including its other hostnames. The existing HA authentication and any
other enabled Cloudflare controls still apply.

```text
Phone browser -> existing HA HTTPS hostname -> ordinary Cloudflare Tunnel -> HA
Google token exchange and fulfillment -> same HA HTTPS hostname -> same Tunnel -> HA
```

Choose the direct fulfillment path before switching. If Home Channels is
enabled, use its authenticated `/api/epg_voice/google_assistant` endpoint and
retain the installed extension. Otherwise use stock `/api/google_assistant`.
Returning a channel-enabled installation to the stock endpoint would drop
Home Channels from subsequent device discovery. The relay's operator binding
does not affect requests that bypass it. See the
[channel routing instructions](google-channels.md#route-google-fulfillment).

1. Record the current Bot Fight Mode setting, the two Google Console URLs,
   and the existing HA hostname privately. Identify the configuration owner:
   if zone settings are managed with Terraform, prepare and review the change
   there rather than creating dashboard drift. Preserve the tunnel, its DNS
   record, HA authentication and the unrelated zone settings.
2. Check the existing public tunnel route for `/auth/token` and the selected
   fulfillment path. Review WAF rules, Cloudflare Access policies,
   country restrictions and source-IP allowlists for those paths. Google
   cannot complete an interactive Access login or a browser challenge during
   these server calls. A rule allowing only the home's IP will still reject
   Google. Resolve any separate restriction deliberately; do not assume
   disabling Bot Fight Mode bypasses it or broadly remove other protections.
3. After the zone-wide change is authorized, disable Bot Fight Mode through
   the existing configuration owner. Verify that the ordinary tunnel still
   reaches HA. Use invalid credentials for bounded probes: the token endpoint
   should return HA's JSON OAuth error, normally HTTP 400, and fulfillment
   should reject an invalid Bearer token with HTTP 401. Check that responses
   are not Cloudflare challenges. A successful probe from home still does
   not establish Google's access.
4. In Google Home Developer Console, set the **Token URL** to
   `https://YOUR_HA_HOST/auth/token`. Set **Cloud fulfillment URL** to
   `https://YOUR_HA_HOST/api/epg_voice/google_assistant` when Home Channels is
   enabled, or `https://YOUR_HA_HOST/api/google_assistant` for stock HA. Use the
   actual existing HTTPS hostname in the private configuration. Only the
   fulfillment path depends on the channel option; token exchange still uses
   `/auth/token`. Keep the browser **Authorization
   URL**, Google **Client ID**, scopes and disabled HTTP Basic option
   unchanged. Do not introduce a shared HA token or change account privileges.
5. Start a fresh linking attempt in the phone's Google Home app. Do not reuse
   an authorization code or an old callback. Confirm the actual Google token
   exchange and initial SYNC, and verify that Google discovers the expected
   catalogue. Inspect only sanitized status and count evidence; do not record
   real authorization codes or token values. A saved Console URL or a local
   HTTP probe does not prove that linking succeeded.
6. Verify catalogue resynchronization and, separately, a real voice command
   and playback on the target device. If another edge rule still blocks the
   request, return to the failed-step diagnostics rather than declaring the
   fallback successful.
7. Only after the fallback is validated, disable the unused relay's public
   address using `publish=false` or `workers_dev=false` with its established
   resource owner. Remove its dedicated VPC Service only after no Worker
   references it. Preserve the shared tunnel and HA configuration.

If Plan B fails, restore the recorded Console URLs and Bot Fight Mode setting
through their configuration owners. Returning to Plan A requires its relay
to remain deployed, published and verified. Restoring a previously challenged
ordinary tunnel URL alone does not restore working Google access.
