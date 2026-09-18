"""Contract examples for free-text resolution without playback side effects."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from epg_voice_gateway.channel_catalogue import ChannelCandidate
from epg_voice_gateway.models import ProgramMatch
from epg_voice_gateway.resolver import MAX_CANDIDATES, resolve_media

START = datetime(2026, 1, 1, 12, tzinfo=UTC)
END = START + timedelta(hours=1)


def channel(number, name, *, epg_name="", available=True):
    return ChannelCandidate(f"{number:024x}", name, epg_name, available)


def programme(item, title):
    return ProgramMatch(title, item.channel_id, item.name, START, END)


def resolve(query, *items, titles=None, **kwargs):
    titles = titles or ["Unrelated current programme"] * len(items)
    programmes = [programme(item, title) for item, title in zip(items, titles) if title]
    return resolve_media(query, list(items), programmes, **kwargs)


@pytest.mark.parametrize("query", ["CNN", "play CNN", "WATCH cnn!", "C.N.N.", "включи CNN"])
def test_cnn_combinations_match_safe_channel_identifier(query):
    cnn = channel(1, "CNN International Europe")
    result = resolve(query, cnn)
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == cnn.channel_id
    assert result["matches"][0]["match_kind"] == "channel"
    assert result["matches"][0]["title"] is None
    assert set(result["matches"][0]) == {
        "channel_id", "channel_name", "title", "starts_at", "ends_at",
        "match_kind", "score", "reason",
    }


@pytest.mark.parametrize("query", ["кукутики", "играй Kukutiki", "покажи кукутики пожалуйста"])
def test_cyrillic_and_transliteration_find_a_current_programme(query):
    kids = channel(2, "Children's TV")
    result = resolve(query, kids, titles=["Кукутики"])
    assert result["status"] == "matched"
    match = result["matches"][0]
    assert match["match_kind"] == "programme"
    assert match["title"] == "Кукутики"
    assert match["starts_at"] == START.isoformat()
    assert match["ends_at"] == END.isoformat()
    json.dumps(result)


@pytest.mark.parametrize("query", ["Коломбо", "play Columbo", "пожалуйста включи Kolombo"])
def test_existing_whole_phrase_programme_alias_groups(query):
    film = channel(3, "Classic Films")
    result = resolve(query, film, titles=["Columbo: Murder by the Book"], aliases={
        "Коломбо": ["Columbo", "Kolombo"],
    })
    assert result["status"] == "matched"
    assert result["matches"][0]["title"] == "Columbo: Murder by the Book"


def test_aliases_are_whole_phrase_not_unbounded_substitutions():
    film = channel(3, "Classic Films")
    result = resolve("other Columbo", film, titles=["Коломбо"], aliases={
        "Коломбо": ["Columbo"],
    })
    assert result["status"] == "no_match"


def test_configured_channel_alias_beats_same_named_programme():
    cnn, other = channel(1, "CNN International Europe"), channel(2, "Documentaries")
    result = resolve("CNN", cnn, other, titles=["News", "CNN"], channel_aliases={
        "CNN International Europe": ["CNN", "си эн эн"],
    })
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == cnn.channel_id
    assert result["matches"][0]["reason"] == "channel_alias"


def test_programme_exact_match_beats_weak_channel_match():
    named, airing = channel(1, "Columbo Fan Channel"), channel(2, "Classic Films")
    result = resolve("Columbo", named, airing, titles=["Other", "Columbo"])
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == airing.channel_id
    assert result["matches"][0]["match_kind"] == "programme"


@pytest.mark.parametrize("query", ["kukutki", "kuuktiki", "кукутки"])
def test_single_conservative_typo(query):
    kids = channel(1, "Children's TV")
    result = resolve(query, kids, titles=["Кукутики"])
    assert result["status"] == "matched"
    assert result["matches"][0]["reason"] == "conservative_typo"


@pytest.mark.parametrize("query", ["CNM", "CNN UK", "cnn america", "kutki"])
def test_short_acronyms_regions_and_large_typos_are_not_guessed(query):
    assert resolve(query, channel(1, "CNN US"))["status"] == "no_match"


def test_normalized_tokens_allow_word_order_without_substring_search():
    cnn = channel(1, "CNN International Europe")
    assert resolve("Europe CNN", cnn)["status"] == "matched"
    assert resolve("inter", cnn)["status"] == "no_match"


def test_same_search_on_multiple_channels_is_ambiguous_regardless_of_input_order():
    left, right = channel(1, "CNN International Europe"), channel(2, "CNN US")
    result = resolve("CNN", left, right)
    assert result["status"] == "ambiguous"
    assert len(result["matches"]) == 2
    assert resolve("CNN", right, left) == result


def test_preference_breaks_only_ties_and_does_not_override_region():
    europe, us = channel(1, "CNN International Europe"), channel(2, "CNN US")
    preferences = [europe.channel_id, us.channel_id]
    result = resolve("CNN", us, europe, preferred_channel_ids=preferences)
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == europe.channel_id
    result = resolve("CNN US", us, europe, preferred_channel_ids=preferences)
    assert result["matches"][0]["channel_id"] == us.channel_id


@pytest.mark.parametrize("query, expected", [
    ("CNN HD", 2), ("CNN SD", 1), ("CNN 4K", 3), ("CNN ultra HD", 3),
])
def test_quality_is_a_hard_constraint_even_for_preferred_and_epg_aliases(query, expected):
    items = [
        channel(1, "CNN SD", epg_name="CNN"),
        channel(2, "CNN HD", epg_name="CNN SD"),
        channel(3, "CNN UHD", epg_name="CNN HD"),
    ]
    result = resolve(query, *items, preferred_channel_ids=[items[0].channel_id])
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == items[expected - 1].channel_id


def test_unknown_quality_is_not_claimed_as_sd():
    assert resolve("CNN SD", channel(1, "CNN"))["status"] == "no_match"


@pytest.mark.parametrize("quality, label", [
    ("SD", "480i"), ("SD", "576i"), ("HD", "720i"),
    ("FHD", "FullHD"), ("FHD", "Full-HD"), ("FHD", "Full.HD"),
    ("FHD", "Full_HD"), ("FHD", "2K"),
    ("UHD", "UltraHD"), ("UHD", "Ultra-HD"), ("UHD", "2160i"),
    ("8K", "4320i"),
])
def test_quality_vocabulary_matches_automatic_channel_publication(quality, label):
    item = channel(1, f"News {label}")
    result = resolve(f"News {quality}", item)
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == item.channel_id
    # Equivalent labels must also work when spoken instead of stored.
    assert resolve(f"News {label}", channel(2, f"News {quality}"))["status"] == "matched"


def test_programme_quality_filter_uses_channel_stream_label():
    sd, hd = channel(1, "Movies SD"), channel(2, "Movies HD")
    result = resolve("Columbo HD", sd, hd, titles=["Columbo", "Columbo"])
    assert result["status"] == "matched"
    assert result["matches"][0]["channel_id"] == hd.channel_id


def test_explicit_kind_constrains_otherwise_ambiguous_name():
    named, airing = channel(1, "Columbo"), channel(2, "Classic Films")
    options = {"titles": ["Other", "Columbo"]}
    assert resolve("Columbo", named, airing, **options)["status"] == "ambiguous"
    selected = resolve("play channel Columbo", named, airing, **options)
    assert selected["query"]["kind"] == "channel"
    assert selected["matches"][0]["channel_id"] == named.channel_id
    selected = resolve("включи сериал Columbo", named, airing, **options)
    assert selected["query"]["kind"] == "programme"
    assert selected["matches"][0]["channel_id"] == airing.channel_id


def test_channel_without_current_epg_is_unavailable_and_never_falls_back():
    cnn, unrelated = channel(1, "CNN"), channel(2, "CNN Documentaries")
    result = resolve("CNN", cnn, unrelated, titles=[None, "Other"])
    assert result["status"] == "unavailable"
    assert result["matches"][0]["channel_id"] == cnn.channel_id
    assert result["matches"][0]["title"] is None


def test_source_unavailability_and_conflicting_epg_both_fail_closed():
    cnn = channel(1, "CNN", available=False)
    assert resolve("CNN", cnn)["status"] == "unavailable"
    cnn = channel(1, "CNN")
    result = resolve_media("CNN", [cnn], [programme(cnn, "First"), programme(cnn, "Second")])
    assert result["status"] == "unavailable"


def test_duplicate_identical_guide_rows_do_not_create_false_ambiguity():
    cnn = channel(1, "CNN")
    row = programme(cnn, "News")
    assert resolve_media("CNN", [cnn], [row, row])["status"] == "matched"


def test_unknown_and_empty_catalogue_return_no_match():
    assert resolve_media("CNN", [], []) == {
        "status": "no_match", "matches": [],
        "query": {"text": "CNN", "kind": "auto", "quality": None},
    }


@pytest.mark.parametrize("query", [
    "", " ", "x" * 201, "https://example.invalid/private.m3u8",
    "ftp://example.invalid/channel", "http:example.invalid", "www.example.invalid",
    "192.0.2.10", "CNN\n", "CNN\x00", "CNN\u202e", "play", "HD", "CNN SD HD",
])
def test_invalid_queries_are_rejected_without_returning_their_text(query):
    with pytest.raises(ValueError) as caught:
        resolve_media(query, [], [])
    assert len(query.strip()) < 4 or query not in str(caught.value)


def test_unsafe_source_labels_cannot_be_returned():
    unsafe = channel(1, "CNN https://example.invalid/private")
    assert resolve("CNN", unsafe)["status"] == "no_match"


def test_five_result_cap_keeps_ambiguity_and_no_hidden_first_choice():
    items = [channel(number, f"CNN Region {number}") for number in range(20)]
    result = resolve("CNN", *items)
    assert result["status"] == "ambiguous"
    assert len(result["matches"]) == 5
    assert "selected" not in result


def test_catalogue_and_configuration_bounds():
    cnn = channel(1, "CNN")
    with pytest.raises(ValueError, match="bounds"):
        resolve_media("CNN", [cnn] * (MAX_CANDIDATES + 1), [])
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_media("CNN", [cnn, cnn], [])
    with pytest.raises(ValueError, match="alias bounds"):
        resolve("CNN", cnn, aliases={"CNN": ["other"] * 51})
    with pytest.raises(ValueError, match="safe display labels"):
        resolve("CNN", cnn, aliases={"CNN": ["https://example.invalid"]})
    with pytest.raises(ValueError, match="preferred"):
        resolve("CNN", cnn, preferred_channel_ids=["provider-id"])
