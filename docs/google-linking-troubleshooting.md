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
