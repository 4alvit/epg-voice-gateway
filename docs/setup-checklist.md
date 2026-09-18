# Setup checklist

Follow this sequence for a single-household installation. The linked files are
the detailed instructions; this page records their order and completion gates.
Replace placeholders only in private copies. No paid cloud database, Cloudflare
Worker, Home Assistant Cloud subscription, or billing activation is part of
the base setup. A separate optional VPC relay can address Cloudflare bot
challenges but introduces a beta dependency; see
[linking diagnostics](google-linking-troubleshooting.md).
Existing provider access and compatible playback hardware are
operator-supplied dependencies.

1. **Identify dependencies.** Confirm the existing HLS Proxy endpoint, playlist,
   XMLTV feed, deployment architecture, and intended Cast player. The gateway
   does not install or reconfigure the HLS Proxy. See
   [deployment prerequisites](deployment.md).
2. **Prepare private configuration.** Copy
   [`runtime.env.example`](../deploy/runtime.env.example) outside Git, protect it,
   generate independent API/signing secrets, and set the feed and media origins.
   Configure persistent SQLite storage and an explicit tested-channel allowlist.
   See [configuration](deployment.md#prepare-configuration).
3. **Build and install.** Build for the target worker, publish or import the
   image, select its immutable version, create the environment Secret, and apply
   the private Kubernetes manifest. Keep one replica and worker. Verify image,
   volume, `/healthz`, `/readyz`, and authenticated current EPG lookup.
   See [installation](deployment.md#install-the-workload).
4. **Connect networking.** Provide trusted HTTPS for the complete `/media/`
   resource chain, reachable from the actual receiver. Google Cast can use a
   verified LAN media route; Alexa requires a separately tested public route.
   Keep video outside Cloudflare's proxy/tunnel path. Keep HA-to-gateway control
   calls, including `/v1/catalogue`, internal. See [networking](networking.md).
5. **Install the HA package.** Configure the fixed receiver, bearer credential,
   internal API URLs and optional news-channel ID. Merge package and logging
   settings without replacing unrelated configuration. Back up, validate with
   the installed HA version, activate during an idle window, and test physical
   playback before adding voice control. See
   [HA installation](../examples/home-assistant/README.md#install).
6. **Configure the dynamic catalogue.** Set allowed channels, preferred labels,
   aliases and catalogue limit. Confirm that Home TV refreshes without playing,
   a selection resolves the exact current title/channel, and it resets to Ready.
   See [dynamic selection](../examples/home-assistant/README.md#dynamic-current-programme-selector).
7. **Create the Google draft.** Accept the developer terms, select Scene/Sensor,
   upload the generic icon, and configure the documented OAuth URLs, scopes,
   client ID and fulfillment URL. Use the public HA origin for browser
   authorization; use either that origin or the verified optional relay for
   token exchange and fulfillment, following the selected transport plan.
   Save the draft; household linking does not require public certification. See
   [Console setup](../examples/home-assistant/README.md#first-time-manual-google-linking).
8. **Choose synchronization.** Manual synchronization can omit the service
   account with the documented HA configuration. For automatic catalogue sync,
   enable HomeGraph in the same project, create/store its dedicated JSON key,
   configure HA, and activate it after validation. Do not confuse the key with
   the Console client secret. See
   [HomeGraph](../examples/home-assistant/README.md#homegraph-and-the-service-account-key).
9. **Link the household.** On the project-owner Google account, add
   **[test] Your integration name** under Google Home's **Works with Google Home**
   and sign in with the dedicated non-admin HA login. Record that user's HA ID
   for automatic sync and ask Google to sync devices. Confirm actual discovery;
   service-account authentication and synthetic requests do not complete linking.
   See [phone instructions](../examples/home-assistant/README.md#link-and-verify-on-the-household-account).
10. **Verify voice and each channel.** Speak a currently listed option using
    "Hey Google, set Home TV option to ...". Check fresh title/channel selection,
    sustained playback, moving video and audio; repeat the same choice to test
    a fresh session. Test every candidate channel separately and record later
    failures. See [acceptance](acceptance.md#google-acceptance).
11. **Add Alexa only if needed.** Configure the separate custom skill, model,
    Video App interface and signature-protected `/alexa` endpoint. Verify the
    actual target advertises VideoApp and can fetch public HTTPS media. A Google
    result does not validate Alexa. See [Alexa setup](../examples/alexa/README.md).
12. **Record and recover.** Keep private configuration, version/digest receipts,
    backups and detailed diagnostics outside Git. Commit only generic procedures
    and sanitized results. Preserve the database and existing HLS Proxy during
    rollback; gateway restarts invalidate media sessions. See
    [rollback](deployment.md#roll-back) and
    [the recorded validation](validation-2026-09-18.md).

These are operator-run steps. The repository does not provision your router,
DNS, certificates, Google accounts, or existing proxy automatically. Installation
helpers and raw receipts containing household data stay in protected local
storage; they are not prerequisites for following the generic procedure.
