# Google voice through Home Assistant

The supported route is:

**Google Routine -> exposed Home Assistant script -> gateway `/v1/play` ->
Home Assistant Cast -> configured display**

The script contains a fixed program query and a fixed target alias. Google does
not supply an arbitrary spoken program name to this script. This example does
not depend on the discontinued Google Conversational Actions service.

## Install

1. Add your Nest Hub, Chromecast, or other compatible display to the Home
   Assistant Google Cast integration. Confirm ordinary video playback first.
2. Configure the gateway's Home Assistant URL, bearer token, and allowed target
   aliases. For this example, map `kitchen` to the actual `media_player` entity.
   The gateway does not accept an arbitrary entity ID or service from the caller.
3. Merge the example secrets into your private Home Assistant `secrets.yaml`.
   Use an address that Home Assistant can reach. A direct LAN route is suitable;
   a public Cloudflare command endpoint is not required for this local call.
4. Install [`package.yaml`](package.yaml) as a Home Assistant package, or merge
   its `rest_command` and `script` sections into your existing configuration.
   Validate the configuration and reload the affected integration and scripts,
   or restart Home Assistant when required.
5. Run `script.play_kukutiki` manually. A successful API response means Home
   Assistant accepted the playback command. Confirm the display actually plays
   the selected live channel, including audio.
6. Expose `script.play_kukutiki` through your existing Home Assistant Google
   Assistant integration. Scripts are exposed as scenes. Assign the script an
   area if other household members need to use it.
7. Create a Google Home Routine with the starter **"Play Kukutiki"** and an
   action that activates the exposed **Play Kukutiki** scene. The exact available
   Routine action wording depends on the Google Home interface and language.

If you configure Google Assistant exposure in YAML, merge this entry into the
existing integration rather than creating a second top-level configuration:

```yaml
google_assistant:
  # Keep the existing project and credential settings here.
  entity_config:
    script.play_kukutiki:
      name: Play Kukutiki
      expose: true
```

Create another fixed script and Routine for each program or target you need.
The API itself accepts a query string, so a different supported voice frontend
can call the same gateway without changing the EPG or playback service.

## Playback and authentication

- `EPG_API_TOKEN` authenticates the script to the gateway. It is separate from
  the Home Assistant bearer token used by the gateway to call `play_media`.
- Keep both tokens in private configuration or secret storage. Do not paste
  them, provider URLs, or playlist credentials into public examples or logs.
- The Cast receiver must reach the returned HTTPS media URL and its referenced
  resources. Cloudflare Access browser login and custom service-token headers
  are not a playback authentication mechanism for the receiver.
- The gateway forwards a scoped session URL. It does not transcode streams;
  codecs and resolution must work on the selected physical device.
- Non-200 gateway replies fail the example script rather than reporting that
  playback started. A successful command remains distinct from device playback.
- Home Assistant supports manual Google Assistant integration without requiring
  a Home Assistant Cloud subscription. Use the integration already configured
  for your home; this example does not purchase or enable a subscription.

Home Assistant may fetch the HLS manifest and start the Cast receiver before
answering the gateway. The gateway allows 40 seconds for that response, with
five-second connection, pool, and write timeouts. The example `rest_command`
waits 60 seconds so the outer request can outlast the gateway's playback call.
Keep this timeout ordering when adapting the script: ending the gateway's wait
too early can revoke a session while the display is still starting playback.

## References

- [Home Assistant Google Assistant integration and exposed scripts](https://www.home-assistant.io/integrations/google_assistant/)
- [Home Assistant Google Cast integration](https://www.home-assistant.io/integrations/cast/)
- [Home Assistant RESTful Command responses](https://www.home-assistant.io/integrations/rest_command/)
- [Google Conversational Actions sunset](https://developers.google.com/assistant/ca-sunset)
