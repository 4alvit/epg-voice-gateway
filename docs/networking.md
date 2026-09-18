# Control and media networking

Voice commands and video take different routes. The same gateway process
handles both, but the ingress hosts and allowed paths keep them separate.

The [dedicated Alexa Worker relay](../deploy/alexa-worker-vpc/README.md) offers a
restricted `POST /alexa` control route over an existing tunnel, with repeatable
Terraform, request limits, an exact public hostname, and signature preservation.
Its Kubernetes example adds only connector-to-gateway egress when the connector
namespace already denies that traffic. Test this network path from the actual
connector namespace before publishing; a successful operator-machine probe does
not establish connector reachability. Existing Google and energy relays remain
separate deployments.

```text
Alexa or Home Assistant
  -> control HTTPS endpoint (Cloudflare may carry this traffic)
  -> gateway: search current EPG and authorize playback

Playback device
  -> media HTTPS endpoint (direct LAN or WAN; DNS-only if using Cloudflare DNS)
  -> gateway: validate the playback session and rewrite HLS references
  -> existing internal HLS proxy
  -> upstream provider
```

The gateway is a thin authorization and URL-rewriting layer in front of the
existing HLS service. HLS caching and provider integration stay in that service.
There is no transcoder or second installation of the HLS proxy in this project.

## Control endpoint

Use either the separate Traefik control ingress example or the hostname/path
rules in `deploy/cloudflared-control.example.yaml` with an existing tunnel.
Allow only `/alexa`, `/v1/resolve`, and `/v1/play`. If a tunnel targets the gateway
Service directly, it bypasses Traefik's path restrictions; retain the example's
explicit path allowlist and same-host 404 fallback. Never create a catch-all
control-host route to the entire gateway application.

Amazon requests are authenticated by signature verification and the configured
skill ID. A Cloudflare Access login page or challenge in front of `/alexa`
prevents Amazon from using the skill. An IP-restricted home-only rule also blocks
Amazon servers. Preserve the application's request verification even when an
edge rule has to change. Do not weaken protections for unrelated hostnames.

`/v1/resolve` and `/v1/play` require the gateway's bearer API token. A local Home
Assistant instance can call the internal Kubernetes/LAN endpoint instead of
using a public control route. A Cloudflare account, Worker, paid plan, or new
subscription is not required by the application itself.

The authenticated `GET /v1/catalogue` used by Home TV and `GET /v1/channels`
used by the optional Home Channels extension stay on that internal route.
The supplied public ingress/tunnel allowlists intentionally omit them.
Configure HA's catalogue connection with the LAN origin, not the public control
host.

Google's OAuth exchange and fulfillment also need a route without browser bot
challenges. A successful HA login from home does not validate these server calls.
If Cloudflare logs identify Bot Fight Mode, use the
[linking diagnostic procedure](google-linking-troubleshooting.md) and choose a
verified transport. The optional narrow VPC relay has a beta dependency and
requires explicit deployment; it does not change the base gateway networking.

### Diagnosing Alexa verification timeouts

A healthy HTTP endpoint does not prove that the gateway can resolve and fetch
Amazon's signing certificate. Repeated failures near the six-second gateway
deadline, with `alexa_request_deadline_exceeded stage=verification`, require
checking DNS and outbound HTTPS from the gateway pod itself. This diagnostic
contains no request body, signature, certificate URL, device ID, or account ID.

For Kubernetes, inspect the active DNS Service endpoints and test both UDP and
TCP DNS against every resolver from the gateway's network namespace. Check A
and AAAA queries for an internal service name and Amazon's certificate hostname.
A Ready CoreDNS pod can still be unreachable across a broken node overlay;
testing only the DNS Service once can select the healthy endpoint and miss it.

Repair the verified routing or host-firewall fault through its persistent owner.
For an installation that actually uses Flannel VXLAN, verify the existing
UDP 8472 node-peer rules and the return route. Any added rule should allow only
the required peer, interface and port while preserving existing blocklists and
default rejection. Keep real peer addresses outside Git, preserve rollback
files, and verify both resolver endpoints after the repair. Do not pin an
ephemeral CoreDNS pod IP into the gateway as a workaround.

The gateway caches up to eight validated certificate chains for five minutes
and still validates each request and certificate on cache hits. Cold downloads
and expiry refreshes need working DNS. The socket timeout does not bound system
DNS resolution; the HTTP deadline fails closed, and a verification thread keeps
its concurrency slot until it actually exits. Verify repeated signed requests,
including a cold cache, rather than treating one successful retry as recovery.

## Media endpoint

Set `EPG_MEDIA_BASE_URL` to the media hostname and route only `/media/` to the
gateway. Use a certificate trusted by the playback device. All HLS resource
types, including nested manifests, segments, encryption keys, and initialization
segments, must stay behind session authorization. Do not redirect devices to
the raw provider URL or expose a generic URL-fetching proxy.

The existing HLS proxy may retain its own LAN HTTPS endpoint for other clients.
It is a different route from the voice gateway's authorized media endpoint.
Existing proxy query values are not a substitute for the gateway's expiring
session grants. This project does not make the existing HLS service public.

When using Cloudflare DNS, use a **DNS-only** media record. Keep media outside
Cloudflare Tunnel, Workers, and the proxied CDN path. Cloudflare documents
restrictions for video and large-file delivery through Tunnel; this design
does not depend on a paid streaming product or a beta feature.

Two network modes are possible:

- **LAN-only:** media DNS resolves to a reachable private address. This can serve
  local Cast devices after actual reachability and codec verification. It does
  not establish Internet reachability for the Alexa VideoApp acceptance test.
- **Direct WAN:** the media hostname resolves to your direct IPv4 and/or IPv6
  address. Forward TCP 443 for IPv4 where needed, or allow TCP 443 to the selected
  host in the IPv6 firewall. Expose only the media ingress, not HLS administration,
  the Kubernetes API, or a general Traefik dashboard.

An IPv6 prefix alone does not prove end-to-end IPv6 playback support. Test the
actual device and the external requester. If a hostname advertises both A and
AAAA records, both routes need working TLS and routing; a broken advertised
address can cause intermittent failures. Some Cast devices use their own DNS
resolvers, so do not rely on a local `.local` hostname or a laptop-only DNS
override. Split DNS and NAT loopback behavior also need device testing.

The examples do not modify router rules, IPv6 firewall rules, DNS records,
certificates, Cloudflare plans, or existing tunnels automatically.

## Platform constraints

Alexa must advertise the `VideoApp` interface in the incoming request before
the skill can return `VideoApp.Launch`. A screen and working APL cards do not
prove support for this separate interface. Amazon documents HTTPS HLS with
MPEG-TS/AAC, MPEG4 or H.264 video, and a maximum resolution of 1280x720 for this
interface. A 4K channel needs a compatible existing rendition or a separate
transcoding service; this gateway does not resize video.

Google playback uses Home Assistant and Cast. Home TV advertises a finite list
of current EPG titles through Google's Modes trait; one private handler validates
and plays any offered choice. Fixed shortcuts remain available through exposed
HA scripts and optional Google Home Routines, with predefined programme queries
or catalogue channels and a fixed display.
The HA dispatcher calls the gateway over the LAN, then starts Cast locally.
The optional [Home Channels extension](google-channels.md) adds named channel
selection through a separate authenticated HA fulfillment wrapper. With the
Worker relay, explicitly enable `channels_enabled` only after installing and
verifying that wrapper. The public fulfillment path and OAuth endpoints stay
unchanged; the private route selects the wrapper, which delegates existing
devices to HA's Google integration. With a direct HTTPS origin, configure the
wrapper's documented fulfillment path instead. Media keeps its separate route.
Only Google's HA account-linking and fulfillment paths need a reachable
control endpoint for the manual Google integration; the gateway control API
can remain private. This is not a general Google
conversational endpoint accepting an arbitrary spoken program title. Google's
Conversational Actions were discontinued; exposing an HTTPS API alone does not
restore that feature. A Google Home speaker without a screen must target a
separate display or TV for video.

## References

- [Amazon VideoApp interface](https://developer.amazon.com/en-US/docs/alexa/custom-skills/videoapp-interface-reference.html)
- [Google Cast supported media](https://developers.google.com/cast/docs/media)
- [Home Assistant Cast integration](https://www.home-assistant.io/integrations/cast/)
- [Home Assistant Google Assistant integration](https://www.home-assistant.io/integrations/google_assistant/)
- [Google Conversational Actions sunset](https://developers.google.com/assistant/ca-sunset)
- [Cloudflare Tunnel streaming restrictions](https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/#large-file-and-streaming-traffic-through-tunnel)
