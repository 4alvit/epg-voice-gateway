# Validation record: 2026-09-18

This sanitized historical record describes a single installation. It is not a
claim that all channels, displays, or assistant accounts work. No deployment
addresses, account IDs, credentials, provider URLs, or signed grants are included.

## Installed and checked

- Gateway runtime `6dda2a7e10301690257fa38bb6a5759f639a33df` was installed using a
  verified image and module hashes. Its persistent database and existing proxy
  were preserved. Later gateway changes through `bcd8de6` affected examples,
  tests and documentation.
- The HA package refreshes the finite Home TV catalogue, revalidates exact
  title/channel selections, requests fresh grants, and resets to Ready. The
  final minimal dispatcher update used one script reload without restarting HA
  or interrupting the active baseline stream. Initial installation had required
  a separate validated HA restart.
- Google draft setup, HomeGraph enablement and protected service-account key
  installation were completed without billing activation. No project IAM roles
  were granted to that service account. During initial preparation, its OAuth
  exchange succeeded and Request Sync returned 404 because the household had
  not yet linked. This was the initial state, before the linking result below.
- Gateway/example tests: 324 passed, one optional test skipped; Ruff passed.
  HA validation: 31 YAML files and 22 tests passed. CI passed for the recorded
  gateway source and HA revision `ddcc100`. The HA change remained in an open PR.

## Google account linking

Real household OAuth linking completed later on the same day. The user reported
that Google Home displayed the integration name and **Linked**. A read-only HA
check independently confirmed issued Google refresh tokens for the dedicated
non-admin user and that user's registration in the Google integration's agent
store. HA registers that user when handling the initial SYNC request; this is
evidence of an actual Google linking flow reaching HA, rather than the earlier
synthetic checks.

The accepted flow used the dedicated Workers VPC relay for Google's token and
fulfillment requests. Browser authorization remained on the existing HA origin
and HA continued to validate the real OAuth credentials.

A read-only HomeGraph `devices:sync` request using the existing project service
account returned HTTP 200, matched the dedicated agent and confirmed three
devices in Google's stored inventory:

- **Play News Channel**: SCENE with the Scene trait.
- **Play Kukutiki**: SCENE with the Scene trait.
- **Home TV**: SENSOR with the Modes trait.

HA's `google_assistant.request_sync` service was also invoked once and its HA
HTTP request returned 200. That service did not expose the underlying Google
response status, so its acknowledgement is not recorded as a separate Google
HTTP 200. The independent inventory read confirms the devices stored in
HomeGraph; it does not prove a later EPG change automatically resynchronizes.
Catalogue recovery was checked separately below. Phone UI visibility and dynamic
catalogue speech remain separate checks. The fixed-shortcut voice failure and
successful recovery are recorded below.

A transient error on HA's browser login page was also reported during these
attempts. Its cause was not established; successful linking does not identify
or prove a fix for that earlier browser error.

## Fixed news shortcut: real voice attempt

The user requested the news shortcut by voice and reported Google's
**"Activating News Channel"** acknowledgement. HA recorded the news wrapper and
playback dispatcher starting for that attempt. The dispatcher then encountered
a REST connection failure approximately 10 milliseconds later; no Cast request
was issued and the display did not begin playback.

This establishes recognition and delivery of one real fixed-shortcut Google
EXECUTE request through to the HA scripts. It does not establish dynamic
programme-title recognition or successful video playback. HA runs script-based
Scenes asynchronously, so Google's acknowledgement can precede a later
dispatcher failure.

The gateway process was healthy, but its catalogue had exceeded the configured
twelve-hour freshness window. The installed `/readyz` readiness probe returned
HTTP 503, removing the pod from its Kubernetes Service endpoints. This explained
why HA received a connection failure instead of the gateway's structured stale
catalogue error.

Source diagnostics identified a persistent HTTP redirect from the configured
XMLTV address. A successful DNS lookup and TCP connection still returned HTTP
301; the gateway deliberately accepts only 200 or a validated cached 304 and
does not follow redirects. The final canonical HTTPS source was verified
directly, including TLS, a bounded download and parsing with the deployed
implementation. It mapped current programmes for all configured voice channels.
No credentials were forwarded through the redirect chain. The source address
remains in private configuration.

A separate pod-network path to one CoreDNS endpoint timed out, while the other
endpoint worked. Host routes, neighbors and VXLAN forwarding entries were
consistent; the precise cause of that path failure remains unresolved. No
cluster firewall, routing or DNS settings were changed to mask it.

The readiness template fix in `ce889a0` passed CI. It uses `/healthz` for Service
readiness while retaining `/readyz` and all control API freshness checks. The
private source URL and readiness probe were updated during an idle window.
The replacement pod then fetched and committed a fresh catalogue. Its runtime
source matched the validated destination, both health endpoints returned 200,
and the Service had a Ready endpoint. Verification preserved the database and
its integrity, the existing HLS proxy and all other runtime secret values.
The protected provisioning files were updated to match the installed change.

The HA catalogue controller recovered its choices and requested synchronization.
A subsequent read-only HomeGraph inventory returned HTTP 200 and confirmed the
three expected devices, with all six Home TV settings matching HA exactly.
This verifies that particular recovery synchronization, rather than merely
acceptance of the HA service call.

A manual invocation of the installed news wrapper then returned HA HTTP 200,
created a fresh media grant and sustained playback through a 150-second check.
The final non-launching receiver snapshot was PLAYING with 147.578 seconds of
timeline advancement; HA did not lose the session after playback started. The
user separately confirmed moving video and sound on the physical display.
This was a manual HA recovery check, not a second Google voice request.

The user subsequently repeated the Google news voice command and confirmed that
it worked. A read-only HA check independently observed a new news-script trigger,
a different media grant and the display in PLAYING state. The observer sent no
playback commands during that retry. This confirms one successful fixed-shortcut
voice request through Google, HA and the gateway to physical display playback.
The 150-second measurement above belongs to the preceding manual check; it is
not presented as a separate timed observation of the voice retry.

## Playback results

A minimal SD baseline passed a 150-second receiver check and remained playing
roughly fifteen minutes later. The full dynamic SD path passed another isolated
150-second check, including exact selection, fresh grant, minimal dispatch,
Ready reset and final PLAYING state with 150.473 seconds of timeline advancement.
It later stopped after upstream segment timeouts exhausted proxy retries.
That later failure remains part of the result.

Two HD candidates received correct selections and fresh grants but never reached
PLAYING during their isolated checks. Upstream proxy downloads continued without
an initiating timeout. The exact receiver/format cause was not established.
The gateway does not transcode; these channels are not verified playable.

A separate 125-second SD software-decode check passed. It does not establish
receiver hardware decoding or human picture/audio acceptance. An EPG-origin DNS
timeout was also observed; freshness enforcement remained enabled.

## Named channel selection

The optional Home Channels extension from `f54ead8` was installed later on
the same day. Its authenticated channel catalogue is a separate explicit
allowlist; the existing programme selector and fixed shortcuts were preserved.
The first advertised lineup contains one previously verified SD channel.

- Application tests: 417 passed, one optional test skipped. The 72 HA adapter
  tests are included in that total. Relay tests: 106 passed. Ruff, Terraform
  validation and CI passed.
- The installed gateway image and module hashes matched the committed source.
  The rollout changed only its image and the new channel allowlist. The
  database inode, source fingerprint, integrity, Service, persistent volume,
  HLS proxy deployment and proxy pods were preserved. Both health checks and
  the authenticated channel catalogue passed; missing authentication returned
  401.
- The eight HA component files matched the same source revision. Installed HA
  2026.9.1 configuration checking reported zero errors, and integration imports
  passed. A protected backup preceded installation. First discovery required a
  normal HA restart during an idle window; the existing Cast receiver recovered
  after startup. The entry was created through HA's configuration flow, without
  hand-editing config storage. A separate read confirmed its persisted settings
  and the loaded Cast, Google Assistant and channel adapter entries.
- Private authenticated SYNC retained all three stock devices and added one
  TV with the Channel trait for the designated non-admin user. An administrator
  did not receive that virtual device; unauthenticated requests and unknown
  channel selections were rejected.
- The existing Worker owner deployed the reviewed routing flag. Its active
  source and bindings matched the candidate. Public hostname, OAuth endpoints,
  private origin, tunnel, logging settings and the entire Bot Fight Mode
  settings object were unchanged. The prior immutable version remained
  available for rollback. No paid service was enabled.
- Google Console retained Scene and Sensor and added TV. Authenticated SYNC
  through the public relay returned four devices. After RequestSync, an
  independent HomeGraph inventory read returned 200 and confirmed the linked
  agent, the three prior devices, and Home Channels with its expected named
  channel list. A public mixed QUERY returned both Home TV and Home Channels
  online while preserving Home TV's stock Modes state.

The default Python urllib client received an edge 1010 response during an
operator probe. The same authenticated request using an honest application
User-Agent succeeded; curl negative probes also returned the expected JSON
responses. The independent Google inventory check passed. No security setting
was changed to accommodate that operator client.

A single manual `selectChannel` request through the public Worker returned HTTP
200 and Google protocol SUCCESS in 2.623 seconds. It used a temporary,
normally authenticated session for the dedicated HA user and the advertised
channel key/name. It did not call the gateway or HA playback service directly.
The receiver reached PLAYING with a fresh signed session and remained on that
session for 151.424 seconds without a relaunch or playback loss. One final
passive native snapshot confirmed PLAYING and 154.804 seconds of timeline
advancement. No native connection was opened during the observation interval;
the final observer blocked control messages. The receiver was left playing.

This is manual protocol and receiver evidence, not recognition of a
named-channel microphone command or human picture/audio confirmation for the
new path. Those checks remain pending. See
[channel setup and usage](google-channels.md) for the separate checks and
rollback procedure.

## Shared media search and Assist input

A subsequent update added the shared channel/programme resolver and automatic
Google channel names. The final gateway image source is `ae6163a`; the HA component also
includes the text-only timeout adjustment in `7036647`. The HA configuration
examples are committed as `03f897f` in the existing configuration PR.

- Final local application suite: 618 passed, two optional/platform tests skipped.
  Linux CI for the gateway source: 619 passed, one optional grammar test skipped;
  all 106 relay tests, lint, and lock consistency checks passed. The optional
  grammar checks were separately executed with Hassil 3.12.0, matching the
  installed HA dependency. The text-timeout follow-up passed 108 focused HA
  and grammar checks. HA repository validation accepted 34 YAML files and CI
  passed for its configuration revision.
- Independent review corrected Russian light-command interception, opposite
  quality synonyms, order-dependent acronym collisions, and programme selection
  after a newly overlapping EPG update. Regression checks cover these cases.
- The live indexed snapshot contained 1,562 channels and 983 current rows.
  CNN spelling variants selected the configured preferred channel. A missing
  current programme returned no match; an ambiguous name required
  clarification. Search checked the complete catalogue independently of the
  30-name Google publication limit.
- Installed API checks rejected missing authentication and URL-shaped text.
  Actual node search latency during rollout ranged from approximately 0.8 to
  2.0 seconds. Assist therefore permits four seconds per gateway call and eight
  seconds for the complete action. Google retains its existing deadlines.
- Initial automatic publication took 2.6–4.1 seconds from HA and exceeded its
  two-second Google deadline. That check failed before RequestSync and its test
  login was revoked. The final version caches immutable names per catalogue
  import while reading selected-channel availability freshly on every request.
  Four installed HA-to-gateway reads then took 43–58 milliseconds. Cold builds
  and database import commits are not covered by that steady-state measurement.
- The gateway image and modules matched the committed source. Database integrity,
  inode, source fingerprint, Service, PVC, HLS Proxy deployment and proxy pods
  were preserved. Only the gateway image and reviewed channel settings changed.
- HA installation changed only the custom component and three Assist YAML files.
  Both staged and installed checks returned zero errors. Relevant authentication,
  Google registration and existing integration configuration were preserved;
  unrelated live configuration drift was left intact. Protected configuration
  and component archives were verified before one restart during an idle window.
  HA, Google, Cast, the new service, and the configured receiver recovered.
- Private and public SYNC retained the three stock devices and the same virtual
  TV identity, now with 30 automatic channels and the configured short CNN aliases.
  An independent HomeGraph inventory read confirmed the new list after RequestSync.
  Unauthenticated access was rejected, the owner did not receive the dedicated
  virtual TV, and both new internal APIs returned 404 on public ingress surfaces.
  The temporary dedicated-user session was revoked and then rejected with 401.
- Typed Assist queries in English and Russian reached the new intent and returned
  a safe no-match response without starting playback. A subsequent
  `play CNN on home channels` request through HA's conversation API acknowledged
  dispatch in 4.226 seconds and started a fresh signed session. During a
  150-second observation window the receiver reached Playing and retained that
  session without playback loss. A final passive native snapshot confirmed
  Playing and 148.115 seconds of timeline advancement. No native connection was
  opened during the observation interval; the final observer blocked controls.
  The receiver was left playing. Physical microphone recognition and current
  human picture/audio confirmation remain separate, unverified gates.

At the end of the resolver rollout, a read-only console check found Video App
enabled for the separate media skill, while its endpoint still pointed to the
original reflector Lambda. The later control endpoint rollout is recorded below.

## Dedicated Alexa HTTPS control endpoint

The dedicated relay from `920d3a9` was deployed through its own protected
Terraform state. Its initial plan created exactly one VPC Service, Worker,
immutable version, and deployment. A second plan changed only that Worker's
publication flag. The subsequent plan reported no changes.

Before publication, the single active tunnel connector was matched to the
tested Kubernetes pod, including its connector ID and QUIC connections. Its
existing egress policy initially blocked the gateway port. One additive policy
then allowed only that connector's traffic to the gateway's namespace, app label,
and HTTP port. Connector-origin health checks returned 200 and unsigned Alexa
requests returned 400. The unrelated HLS Proxy remained blocked. Existing
policies and services were preserved; no application or connector restarted.

Public TLS verification accepted the endpoint's trusted wildcard certificate.
Nine public checks verified route rejection, private API/media isolation, and
invalid-signature rejection at the gateway through the VPC path. Local relay
tests passed all 93 cases; Terraform formatting and validation passed against
provider 5.24.0. The runtime relay hash matched the committed source. Existing
Workers, VPC Services, account namespace, subscriptions, entitlements and Workers
account settings were unchanged. No billing, DNS or zone-security change was
part of this rollout. Generic configuration and Plan A/Plan B instructions are
in [the relay guide](../deploy/alexa-worker-vpc/README.md); real deployment inputs,
state, rollback information and evidence remain outside Git.

The separate media skill's HTTPS manifest was saved with the wildcard certificate
option, no regional overrides, and development testing enabled. Its existing
English model rebuilt successfully with zero errors and warnings.

An actual Amazon-signed `LaunchRequest` submitted through the console's Manual
JSON tool then completed with **SUCCESSFUL** and the gateway's help response:
"Tell me which channel or program to play. For example, say, play CNN or play
Kukutiki." A temporary status-only observer independently recorded the matching
POST with HTTP 200 and no Worker exception. The gateway's signature, certificate,
timestamp and exact skill-ID checks remained enabled. No video was requested.
This establishes the signed control path; it does not establish microphone
recognition or physical video playback.

The console's conversational simulator had returned an unsupported-device
message, and two earlier Manual JSON attempts reported an unspecified console
error. Their causes were not established. The successful fresh Manual JSON
request is the acceptance evidence; the earlier errors are not proof that
Video App is categorically unsupported by the simulator. Temporary diagnostic
streaming was removed, with no active tails or persistent request logging left.

CI for `920d3a9` passed: Python 619 passed and one skipped, Google relay 106
passed, Alexa relay 93 passed, Ruff passed, and runtime lock consistency passed.

## Alexa request failures and DNS recovery

Subsequent household attempts to launch the skill and request either example
all failed. A controlled Echo Show launch reproduced a signed POST returning
HTTP 400. Worker metrics showed requests taking approximately 6.25 seconds,
consistent with the gateway's six-second request deadline. The initial single
successful Manual JSON request had not established repeatable operation.

Inside the gateway, an ordinary Amazon certificate-host DNS lookup failed after
20.113 seconds. One of the two cluster DNS backends was unreachable from the
gateway's node. A host firewall rejected that node's VXLAN packets with ICMP
administratively prohibited. An exact node-peer `/32`, incoming-interface and
UDP overlay-port allowance was added before the terminal rejection, preserving
the existing IP filter and all other rules. The rule was persisted through the
host's existing firewall owner, with protected backups and rollback evidence.
No routing change, node restart or DNS restart was needed. All 24 DNS endpoint
probes and 30 ordinary resolver calls then passed; their respective maximum
durations were 132 and 50.6 milliseconds.

Gateway revision `0d1b0be` also adds a bounded cache of validated Amazon
certificate bytes, with at most eight entries and a five-minute lifetime.
Certificate trust, dates, hostname and request signatures remain checked on
every request; expired entries have no stale fallback. Fixed warning categories
distinguish verification and handler deadlines without logging request bodies,
identifiers or credentials. Socket timeouts still do not bound system DNS,
which is why the network repair is required independently of the cache.

The verified image was installed by changing only the gateway image. Runtime
source hashes, health and readiness checks passed. Settings, secrets, SQLite
integrity and identity, Service, PVC and the separate HLS Proxy deployment and
pods were preserved. Local tests passed with 634 passed and two skipped. CI
passed with 635 Python tests passed and one skipped, 106 Google relay tests,
93 Alexa relay tests, Ruff and runtime-lock verification. The generic firewall
helper and documentation passed ten offline tests and remain in an unmerged
infrastructure PR.

After deployment, one control-only launch sent to the idle Echo Show correlated
with a signed POST returning HTTP 200 at 20:43:57 UTC, without a Worker exception.
This confirms provider delivery and the gateway response for that device;
human confirmation of its spoken response was not collected. The first observed
post-deployment request took 3.17 seconds at the Worker, below the deadline.

Three fresh Amazon Console Manual JSON requests then completed with
**SUCCESSFUL**, each independently correlated with signed HTTP 200 at the edge:

- `LaunchRequest`: the expected help and reprompt, keeping the session open.
- `PlayProgramIntent` with `CNN`: a `VideoApp.Launch` directive for
  **CNN International Europe**, with **Quest Means Business** as the programme.
- `PlayProgramIntent` with `Kukutiki`: the expected no-available-match response,
  ending the session without a video directive. This does not demonstrate
  Kukutiki playback or availability in the current catalogue.

Manual JSON does not play video on a physical display. The typed conversational
simulator continued to report an unsupported-device message without skill I/O;
that separate simulator behavior remains unexplained. No Home Energy, HLS
Proxy, zone-security or billing settings changed during this incident.
The temporary observer was deleted after verification; the API confirmed zero
active tails, and both local diagnostic observers were stopped. Persistent
request logging was not enabled.

## Household Alexa playback confirmation

The user subsequently confirmed that CNN plays on an Alexa device. This adds
human confirmation of actual channel playback to the signed control and
response-contract checks above. The report did not specify playback duration,
pause/resume behavior or the exact device model, so those details remain open.

The same user reported that Alexa on a Samsung Family Hub refrigerator says
it cannot play video skills. That target has not passed playback acceptance.
The adapter currently requires `VideoApp` and otherwise returns a spoken
unsupported-device response before catalogue lookup or media-session creation.
The refrigerator's signed capability envelope and exact spoken wording were
not captured, so this report alone does not distinguish that adapter response
from an Amazon-side invocation restriction.

Amazon documents that not all screened devices implement
[VideoApp](https://developer.amazon.com/en-US/docs/alexa/custom-skills/videoapp-interface-reference.html).
Previously displayed APL energy cards do not establish this capability. An
[APL Video](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-video.html)
fallback would be a separate implementation requiring the target's explicit
video capabilities and a physical playback test; it is not installed or
validated by this result.

## Experimental APL fallback and REN channel publication

The later `c07693f` rollout adds a capability-gated APL Video fallback while
preserving VideoApp priority. It requires APL runtime 1.8 or newer and an
advertised H.264 viewport codec. It provides touch and voice controls, scoped
presentation tokens and fixed playback-error text. Verified touch events only
acknowledge controls already applied locally; voice intents issue commands for
the owned current document. Runtime-error notifications receive no speech or
playback directives. The supplied deployment remains single-worker; reopening
the skill after restart is required for active APL documents.

The media skill's APL interface was enabled alongside VideoApp, all offered
screen profiles were enabled, and Pause/Resume intents were added. The open
SearchQuery slot, carrier phrases, invocation name and HTTPS endpoint were
preserved. Amazon model build version 4 succeeded. A signed Manual JSON CNN
request advertising APL 1.8 and H.264 then returned **SUCCESSFUL** with a
`RenderDocument` response and Video component in 1,098 milliseconds. This is
response-contract acceptance, not physical APL playback. Additional console
submissions were stopped when concurrent browser use interfered with input;
no unrelated clipboard content was submitted or retained.

REN TV was found in four stream variants. Bounded media samples identified an
SD H.264/AAC stream at 720x576 and two HD H.264/AAC streams at 1920x1080. The SD
guide identity had channel metadata but no retained programmes in the active
import window; the HD identity had current programme coverage. The mapping was
working as designed. No unverified HD schedule was copied onto the SD edition.
One available HD variant was explicitly preferred for a device trial, with the
1080p resolution recorded as exceeding VideoApp's documented 720p maximum.

The existing CNN preference was preserved and six exact REN aliases were added:
**REN TV**, **Wren TV**, **R E N TV**, **Ren Tee Vee**, **Ren Television**, and
**Rainbow Television**. Offline checks against all 1,562 channel labels and 983
current programme rows found no publication collision and selected the intended
variant for every alias. Live Google HomeGraph inventory subsequently matched
both HA fulfillment paths: the same 30 channels, CNN first, REN second, all six
REN aliases, and unchanged virtual-TV identity and stock devices. Scheduled
synchronization propagated the change without a manual Request Sync or HA
restart. The temporary verification login was revoked and rejection verified.
These checks do not establish microphone recognition.

The installed image and settings passed exact-source and preservation checks:
SQLite integrity, inode and source fingerprint, Service, PVC, and the separate
HLS Proxy deployment and pods were unchanged. CI for `c07693f` passed 716 Python
tests with one skipped, 106 Google relay tests, 93 Alexa relay tests, Ruff and
runtime-lock verification. All deployment identities, provider addresses,
credentials and raw diagnostic material remain outside Git.

The refrigerator was then invoked through its existing Alexa integration, with
the user's permission to interrupt radio playback. Its Amazon-signed request
reached the gateway successfully and advertised APL, but neither VideoApp nor
eligibility for the fallback. The user confirmed hearing the unsupported-video
response. Follow-up revision `67098d2` added a fixed diagnostic rejection reason;
a fresh signed refrigerator launch returned HTTP 200 and
`apl_video_reason=unsupported_runtime`. The device did not advertise a compatible
APL 1.8-or-newer runtime. No unsupported video directive was forced onto it.
This is an unsuccessful refrigerator playback attempt, despite a working
control endpoint and a successful synthetic-capability APL response.

A separate `ask home television to play ren tv` command was sent to the normal
Alexa display. Its signed request returned HTTP 200 with no Worker exception.
That is control-path acceptance; physical REN picture/audio confirmation and
microphone recognition remain separate from the injected text command.
The user subsequently confirmed that REN playback started on Alexa. This
establishes household playback acceptance for the selected HD variant on that
device; it does not establish Google/Lenovo compatibility, sustained playback,
or recognition of the phrase spoken into a microphone.

The follow-up image passed the same source, configuration, database and service
preservation checks. An initial verification connected before application
startup finished; a subsequent ready-pod verification passed. Six additional
health probes returned 200 in 5.5–78 milliseconds. No CPU or network changes
were needed. CI for `67098d2` passed 731 Python tests with one skipped, both
relay suites, Ruff and runtime-lock verification. Diagnostic mode was reset to false in the protected provisioning
file and Kubernetes Secret without restarting active playback. The running
process's already bounded window expires after twenty verified requests or ten
minutes from startup; future starts have diagnostics disabled. The temporary
provider observer was deleted, zero active tails were verified, and its local
observer process was stopped.

## Remaining acceptance

Account linking, HomeGraph inventory, recovery synchronization and the fixed
news voice shortcut have passed their recorded checks. Google Home phone UI
visibility, continued scheduled resynchronization as programmes change, dynamic
catalogue speech recognition and repeated dynamic programme selection remain
open. The named-channel microphone and physical playback checks are recorded
separately above. HD compatibility and longer source stability also remain open.
Alexa CNN playback has the separate household confirmation above. The
refrigerator's APL attempt was rejected for runtime incompatibility. Physical
APL playback/controls on a compatible target, Google REN playback and microphone
recognition remain separate gates. Alexa REN playback now has household
confirmation above. No public integration release or PR merge occurred.

Raw diagnostics and deployment-specific helpers remain private. Earlier native
observer checks could implicitly launch a Cast receiver and are inconclusive;
the final checks used HA observation and one guarded non-launching native status
snapshot at the end. Final channel tests were serialized to respect proxy limits.
