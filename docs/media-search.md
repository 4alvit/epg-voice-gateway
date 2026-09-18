# Dynamic channel and programme search

The shared resolver searches all imported channel names and currently airing EPG
programme titles. It runs in the existing gateway and uses the existing SQLite
catalogue. No model server, vector database, paid API, or additional Kubernetes
workload is needed.

## Matching and boundaries

The resolver understands English and Russian playback verbs, optional explicit
`channel` or `programme` qualifiers, exact aliases, transliteration, complete
word combinations, and conservative single-character typos in longer words.
Examples are `play Example News`, `watch Columbo`, and `включи Кукутики`.
Acronyms are not fuzzy-matched. It does not infer language, genre, plot, or other
attributes that are absent from the playlist and EPG. Translated titles need an
explicit alias; transliteration is not translation.

Exact configured channel aliases outrank incidental title matches. Equally strong
matches remain ambiguous unless an operator explicitly prefers a channel ID.
More specific regional words remain constraints. An explicit quality such as
`HD` is checked against the primary playlist name; an unlabelled stream is not
assumed to be SD. Quality labels do not establish codec or receiver compatibility.
When a request is ambiguous, repeat the complete request with a more specific
name. This version does not retain a conversational selection session.

A matched channel still requires exactly one programme airing now. Missing,
overlapping, stale, or unavailable schedules never produce a playback grant.
The selected programme or channel is checked again immediately before a fresh
signed session is created. Searching creates no session and starts no device.
Queries are at most 200 characters; the complete search snapshot supports at most
10,000 channels and 10,000 current programme rows. Exceeding either bound returns
an unavailable-catalogue error instead of silently searching a truncated list.

## Operator configuration

Keep household values outside Git. The runtime environment uses JSON values:

```dotenv
EPG_CHANNEL_ALIASES={"Example International Europe":["Example News"]}
EPG_TITLE_ALIASES={"kukutiki":["Кукутики"],"columbo":["Коломбо"]}
EPG_MEDIA_PREFERRED_CHANNEL_IDS=[]
EPG_CHANNEL_VOICE_MODE=automatic
EPG_CHANNEL_VOICE_LIMIT=30
```

Both alias maps allow up to 200 groups and ten aliases per group. Channel names
and aliases are at most 100 characters; programme aliases are at most 200.
Preferences contain opaque IDs returned by the authenticated catalogue, never
provider stream URLs. Preference order is an operator decision, not a guess that
the first matching regional or quality variant is correct.

`EPG_CHANNEL_VOICE_MODE=explicit` is the backward-compatible default: only
`EPG_CHANNEL_VOICE_IDS` are published to Google. `automatic` derives names from
the complete imported playlist and adds collision-checked EPG names,
transliterations, acronyms, and operator aliases. Recognized trailing quality tags
are grouped into one stable variant, preferring an explicit ID and then a lower
labelled resolution. Regional editions remain separate. Indistinguishable duplicate
IDs require an explicit preference and are otherwise omitted. Availability does
not silently switch a chosen variant. Explicit aliases cannot claim another
channel's actual name.

Automatic publication is ordered by preferred IDs, then name and ID. Its limit
is 1–200, with 30 as the recommended initial size. Start with channels compatible
with the intended display. This publication bound does not restrict free-text
search: Assist and Alexa can search an imported channel outside Google's list.

## Entry points

- **Google Home:** the existing Channel trait accepts names/synonyms from its
  published list. After changing the list, request device sync and verify the
  resulting HomeGraph inventory. A candidate phrase is `Hey Google, play Example
  News on Home Channels`. Recognition must be checked on the actual microphone.
  Google Cloud-to-cloud does not pass an unrestricted utterance to this resolver.
- **Home Assistant Assist:** install the [sentence and intent examples](../examples/home-assistant/assist-README.md).
  Typed Assist, the HA phone app, or an Assist satellite can send the free-text
  query. Playback goes to the integration's fixed receiver. Installing Assist
  sentences does not reroute a Google display's microphone to Home Assistant.
- **Alexa:** the [custom skill](../examples/alexa/README.md) passes its
  `AMAZON.SearchQuery` slot into the same resolver. For example, `Alexa, ask home
  television to play Example News`. The device must advertise `VideoApp`.

These are different input paths to one resolver, not equivalent microphone
capabilities. A small language model could later parse richer utterances or rank
retrieved candidates, but it could not make Google forward text it does not send.
Any future model must select an existing ID and preserve the same ambiguity,
freshness, authorization, and signed-media checks.

## Internal APIs

Use the existing bearer API credential. Keep these routes private; do not add
them to the public Google Worker or public media ingress.

- `POST /v1/search` with `{"query":"play Example News"}` returns `matched`,
  `ambiguous`, `no_match`, or `unavailable`, plus at most five candidates with
  opaque channel IDs, display names, programme times, and match reasons. No
  provider address or playback URL is returned.
- `POST /v1/play-text` with the same body returns the existing signed playback
  grant after selection and revalidation. Optional `target` is an existing
  server-configured Cast alias; clients cannot provide arbitrary entities or URLs.
- Ambiguity returns HTTP 409 `ambiguous_media`; no match returns HTTP 404
  `no_media_match`; an unavailable matched channel returns HTTP 404
  `media_unavailable`. An expired selection returns `no_current_programme`.
  A stale or failed catalogue returns HTTP 503.

Existing `/v1/play`, `/v1/resolve`, Home TV programme options, and fixed scene
wrappers retain their contracts. The HLS Proxy remains an independent service.

## Verification and rollback

Run the full test suite, then verify the installed image and HA component hashes.
Check search without playback first: exact names, aliases, Cyrillic/transliterated
titles, ambiguity, unknown names, and missing schedules. Verify unauthorized
requests cannot search or create grants. Check Google publication separately from
full-catalogue search. Finally dispatch one compatible channel, observe sustained
receiver progress, and confirm moving video and sound with the user.

Gateway restarts invalidate in-memory media sessions. Update only while the
receiver is idle. Updating the HA component's Python files requires an HA restart;
reloading its config entry does not reload imported Python. Preserve the previous
image, environment, component directory, and HA configuration before updating.
Rollback those together, validate HA configuration, then restart while idle.
Returning to `explicit` restores the saved channel allowlist without changing
OAuth, the proxy, or the legacy scenes.
