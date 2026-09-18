# Alexa custom skill

This adapter finds a channel or currently airing program and launches its stream
through Alexa's `VideoApp` interface, with an experimental APL Video fallback
for compatible displays. It is a private deployment adapter, not a published
multi-household video service. Keep provider credentials on the gateway.

## Configure the skill

1. Create an English (US) custom skill in the Alexa Developer Console.
2. Import [`en-US.json`](en-US.json) into the interaction model JSON editor.
3. Enable **Video App** under **Custom > Interfaces**. For the fallback, also
   enable **Alexa Presentation Language** and the intended screen profiles.
   Import the model's pause/resume intents, save, and build the model.
4. Configure the gateway's `/alexa` URL as the HTTPS skill endpoint. Use the exact
   skill ID in the gateway configuration. The endpoint must validate Amazon's
   signature, request timestamp, and skill ID before calling this adapter.
   The [dedicated Worker relay](../../deploy/alexa-worker-vpc/README.md) provides
   a restricted public control endpoint when Cloudflare zone rules block Amazon.
   In **Build > Endpoint**, select **HTTPS**, enter the complete `/alexa` URL as
   the default region, select the certificate option matching the endpoint's
   trusted certificate (use the wildcard option when its DNS name is covered by
   a wildcard certificate), and save. Remove obsolete regional
   endpoint overrides so they cannot continue routing to a template backend.
5. Enable development testing for your Amazon account. Prefer a device that
   advertises `VideoApp`; separately test an APL-only target using the capability
   requirements below.

First enable **Development** on the **Test** page and submit **"open home
television"**. The expected response starts **"Tell me which channel or program
to play."** It keeps the conversation open and does not launch media. This checks
real Amazon request delivery and signature verification independently of video.
Say **"stop"** to close that session. A direct unsigned HTTP request must be
rejected; it cannot substitute for this signed Amazon check.

If conversational simulation stops before showing Skill I/O, use **Manual
JSON** with a `LaunchRequest`, the exact skill application ID, `locale: en-US`,
and a freshly generated UTC timestamp. Submit to the saved default endpoint and
require **SUCCESSFUL** with the same help response, `shouldEndSession: false`,
and no video directive. Manual JSON is stateless, so it needs no subsequent
stop command. A generic console error is not a successful endpoint test; check
the gateway path and retry with a fresh timestamp without disabling validation.

Use **"Alexa, ask home television to play Kukutiki"** or open the skill and say
**"play Kukutiki"**. Channel queries such as **"play Example News"** use the same
[dynamic resolver](../../docs/media-search.md), including aliases and conservative
typo handling, across all imported channels. The English model does not enable Russian-language Alexa
support; configure program aliases when spoken names differ from EPG titles.

The gateway returns an explanatory response when no current program matches,
when the guide is unavailable, or when the device lacks a supported video interface. Multiple
matches are not silently resolved to an arbitrary channel.

## Playback requirements

- The media URL and every referenced playlist, segment, and key must be
  reachable by the playback device. Amazon documents an Internet-accessible
  HTTPS endpoint with a trusted certificate. A working local HTTPS route alone
  does not establish Alexa compatibility.
- Alexa `VideoApp` documents HLS with AAC audio and MPEG4/H.264 video, with a
  maximum resolution of 1280 x 720. This service does not transcode channels.
- APL support does not imply video support. The fallback requires an explicit
  `Alexa.Presentation.APL` interface with `runtime.maxVersion` at least `1.8`
  and `context.Viewport.video.codecs` containing `H_264_41` or `H_264_42`.
  Older APL documentation explicitly excludes live content. The newer capability
  gate permits an experiment; it is not a guarantee of live HLS support.
- Successful gateway tests or receipt of a launch directive do not prove that a
  physical device rendered the channel. Confirm actual audio and video.
- Alexa handles pause, resume, stop, and close in its native player. The APL
  player supplies on-screen controls and handles the corresponding voice
  intents only for its own current document. Reopening after a gateway restart
  is required; the skill does not maintain a resumable queue. Presentation-token
  ownership is process-local: use the supplied single-worker deployment, not
  multiple workers or replicas without request affinity and shared state.

The adapter omits `shouldEndSession` from video launch responses. APL autoplay
must not open the microphone while the video is playing.

## Diagnose a third-party screen

Set `EPG_ALEXA_CAPABILITY_DIAGNOSTICS=true` for a controlled diagnostic startup.
After successful Amazon authentication, the gateway logs three booleans:
`video_app`, `apl`, and `apl_video`, plus a fixed `apl_video_reason` category
(`eligible`, `missing_apl`, `unsupported_runtime`, `missing_video`, or
`unsupported_codec`). Eligibility is not proof of playback; the reason separates
an unsupported runtime from absent video capabilities without exposing raw
device metadata. The window stops after twenty verified requests
or ten minutes from startup, whichever comes first. It records no device IDs,
request bodies, queries, media URLs or tokens. Restore the setting to `false`
after collecting the result. Correlate one controlled invocation with its log;
the booleans intentionally do not identify a household or device.

If Amazon rejects the invocation before the endpoint receives it, an APL
backend response cannot establish compatibility. Do not disable request
verification or remove the working VideoApp path to guess around that failure.

## Channel names and pronunciation

Keep the open `AMAZON.SearchQuery` slot. No new intent is needed for REN TV:
**"Alexa, ask home television to play Ren TV."** Configure the recognizer's
alternate spelling, such as **Wren TV**, as an alias of the exact catalogue
channel. **Ren Television** or an unambiguous **Rainbow Television** alias
provides a longer English phrase. Preserve the actual channel name and pin the
intended stream variant; a missing current programme remains unavailable.

Test resolver matching, signed skill delivery and microphone recognition
separately. Typed or Manual JSON requests do not prove speech recognition.

## References

- [VideoApp interface, device checks, and response format](https://developer.amazon.com/en-US/docs/alexa/custom-skills/videoapp-interface-reference.html)
- [APL Video and playback controls](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-video.html)
- [APL device capabilities](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-interface.html)
- [Older APL live-content limitation](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-video-v1-7.html)
- [SearchQuery slots and carrier phrases](https://developer.amazon.com/en-US/docs/alexa/custom-skills/slot-type-reference.html#amazonsearchquery)
