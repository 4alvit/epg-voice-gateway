# Alexa custom skill

This adapter finds a channel or currently airing program and launches its stream through Alexa's `VideoApp`
interface. It is a private deployment adapter, not a published multi-household
video service. Keep the provider playlist and credentials on the gateway.

## Configure the skill

1. Create an English (US) custom skill in the Alexa Developer Console.
2. Import [`en-US.json`](en-US.json) into the interaction model JSON editor.
3. Enable **Video App** under **Custom > Interfaces**, save, and build the model.
4. Configure the gateway's `/alexa` URL as the HTTPS skill endpoint. Use the exact
   skill ID in the gateway configuration. The endpoint must validate Amazon's
   signature, request timestamp, and skill ID before calling this adapter.
5. Enable development testing for your Amazon account and test from a device
   that advertises `VideoApp` in the request's supported interfaces.

Use **"Alexa, ask home television to play Kukutiki"** or open the skill and say
**"play Kukutiki"**. Channel queries such as **"play Example News"** use the same
[dynamic resolver](../../docs/media-search.md), including aliases and conservative
typo handling, across all imported channels. The English model does not enable Russian-language Alexa
support; configure program aliases when spoken names differ from EPG titles.

The gateway returns an explanatory response when no current program matches,
when the guide is unavailable, or when the device lacks `VideoApp`. Multiple
matches are not silently resolved to an arbitrary channel.

## Playback requirements

- The media URL and every referenced playlist, segment, and key must be
  reachable by the playback device. Amazon documents an Internet-accessible
  HTTPS endpoint with a trusted certificate. A working local HTTPS route alone
  does not establish Alexa compatibility.
- Alexa `VideoApp` documents HLS with AAC audio and MPEG4/H.264 video, with a
  maximum resolution of 1280 x 720. This service does not transcode channels.
- APL support does not imply `VideoApp` support. Verify the exact target device.
- Successful gateway tests or receipt of a launch directive do not prove that a
  physical device rendered the channel. Confirm actual audio and video.
- Alexa handles pause, resume, stop, and close while its native video player is
  active. The custom skill does not maintain a resumable video queue.

The adapter deliberately omits `shouldEndSession` from video launch responses,
as required by the VideoApp response contract.

## References

- [VideoApp interface, device checks, and response format](https://developer.amazon.com/en-US/docs/alexa/custom-skills/videoapp-interface-reference.html)
- [SearchQuery slots and carrier phrases](https://developer.amazon.com/en-US/docs/alexa/custom-skills/slot-type-reference.html#amazonsearchquery)
