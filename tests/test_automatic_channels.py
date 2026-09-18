"""Automatic voice labels stay deterministic and never guess ambiguous channels."""

import hashlib
import json
from dataclasses import replace
from itertools import permutations

import pytest

from epg_voice_gateway.automatic_channels import build_automatic_channels
from epg_voice_gateway.channel_catalogue import ChannelCandidate
from epg_voice_gateway.search import normalize


def candidate(index, name, epg_name="", available=True):
    return ChannelCandidate(f"{index:024x}", name, epg_name, available)


def channels(candidates, **kwargs):
    return build_automatic_channels(candidates, **kwargs)["channels"]


def test_empty_and_repeatable_digest():
    result = build_automatic_channels([])
    assert result == {"channels": [], "revision": hashlib.sha256(b"[]").hexdigest()}
    result = build_automatic_channels([candidate(1, "News")])
    encoded = json.dumps(
        result["channels"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    assert result["revision"] == hashlib.sha256(encoded).hexdigest()


def test_priority_then_human_name_are_stable_across_input_order():
    items = [candidate(1, "Zulu"), candidate(2, "Alpha"), candidate(3, "Bravo")]
    kwargs = {"preferred_channel_ids": [items[0].channel_id, items[2].channel_id]}
    result = build_automatic_channels(items, **kwargs)
    assert [row["name"] for row in result["channels"]] == ["Zulu", "Bravo", "Alpha"]
    assert build_automatic_channels(list(reversed(items)), **kwargs) == result
    assert channels(items, preferred_channel_ids=["missing"])[0]["name"] == "Alpha"


def test_duplicate_preferences_do_not_let_unpreferred_variants_take_precedence():
    items = [candidate(1, "Zulu"), candidate(2, "News HD"), candidate(3, "News SD")]
    result = channels(items, preferred_channel_ids=[
        items[0].channel_id, items[0].channel_id, items[1].channel_id,
    ])
    assert [row["channel_id"] for row in result] == [items[0].channel_id, items[1].channel_id]


@pytest.mark.parametrize("high_quality", [
    "HD", "FHD", "Full HD", "UHD", "Ultra-HD", "4K", "8K", "720p", "1080i", "2160p",
    "[HD]", "(HD)", "HD 1080p",
])
def test_quality_variants_prefer_sd_and_preserve_real_id(high_quality):
    items = [candidate(1, f"News {high_quality}"), candidate(2, "News SD")]
    result = channels(items)
    assert result == [{
        "channel_id": items[1].channel_id, "name": "News", "synonyms": ["News SD"],
        "available": True,
    }]


def test_unmarked_is_preferred_over_hd_and_explicit_preference_overrides_quality():
    items = [candidate(1, "News HD"), candidate(2, "News")]
    assert channels(items)[0]["channel_id"] == items[1].channel_id
    preferred = channels(items, preferred_channel_ids=[items[0].channel_id])
    assert preferred[0]["channel_id"] == items[0].channel_id
    assert preferred[0]["synonyms"] == ["News HD"]
    assert channels(items + [candidate(3, "News SD")])[0]["channel_id"] == f"{3:024x}"


def test_availability_never_selects_a_different_quality_variant():
    items = [candidate(1, "News SD", available=False), candidate(2, "News HD")]
    assert channels(items)[0]["channel_id"] == items[0].channel_id
    assert channels(items)[0]["available"] is False
    available = [replace(items[0], available=True), items[1]]
    assert channels(available)[0]["channel_id"] == items[0].channel_id
    assert build_automatic_channels(available)["revision"] != (
        build_automatic_channels(items)["revision"]
    )


def test_regional_language_and_brand_tokens_are_never_stripped():
    items = [
        candidate(1, "CNN CA HD"), candidate(2, "CNN International Europe SD"),
        candidate(3, "CNN International Asia SD"), candidate(4, "News English HD"),
        candidate(5, "News Russian SD"), candidate(6, "HD Cinema"), candidate(7, "4K"),
    ]
    result = channels(items)
    assert {row["name"] for row in result} == {
        "CNN CA", "CNN International Europe", "CNN International Asia",
        "News English", "News Russian", "HD Cinema", "4K",
    }
    assert all("CNN" not in row["synonyms"] for row in result)


def test_identical_quality_duplicates_need_an_explicit_preference():
    items = [candidate(1, "News HD"), candidate(2, "NEWS hd"), candidate(3, "Cinema")]
    assert [row["name"] for row in channels(items)] == ["Cinema"]
    result = channels(items, preferred_channel_ids=[items[1].channel_id])
    assert result[0]["channel_id"] == items[1].channel_id
    assert channels([items[0], items[0]])[0]["channel_id"] == items[0].channel_id


def test_conflicting_rows_with_same_id_are_omitted_without_input_order_bias():
    first = candidate(1, "First")
    assert channels([first, replace(first, name="Second")]) == []
    assert channels([first, replace(first, available=False)]) == []


def test_punctuation_and_unicode_name_collisions_exclude_all_before_limit():
    items = [candidate(1, "Néws"), candidate(2, "NE\u0301WS!"), candidate(3, "Unique")]
    result = channels(items, preferred_channel_ids=[items[0].channel_id], limit=1)
    assert [row["name"] for row in result] == ["Unique"]
    assert channels([candidate(1, "Елка"), candidate(2, "Ёлка")]) == []


def test_short_names_are_only_generated_when_unique_across_all_rows():
    items = [candidate(1, "CNN International Europe"), candidate(2, "ZDF Theater")]
    assert "CNN" in channels(items)[0]["synonyms"]
    items.append(candidate(3, "CNN CA"))
    result = channels(items, preferred_channel_ids=[items[0].channel_id], limit=1)
    assert "CNN" not in result[0]["synonyms"]


def test_omitted_duplicate_still_reserves_its_generated_short_name():
    items = [
        candidate(1, "CNN International Europe"), candidate(2, "CNN CA"),
        candidate(3, "CNN CA"),
    ]
    result = channels(items)
    assert len(result) == 1
    assert "CNN" not in result[0]["synonyms"]


def test_every_variant_reserves_its_short_names_independently_of_source_order():
    items = [
        candidate(1, "CNN Europe HD"), candidate(2, "cnn europe SD"),
        candidate(3, "CNN International"),
    ]
    result = build_automatic_channels(items)
    assert all("CNN" not in row["synonyms"] for row in result["channels"])
    assert all(build_automatic_channels(list(order)) == result for order in permutations(items))


@pytest.mark.parametrize("alias", [
    "News HD", "HD News", "News FullHD", "News UltraHD", "News 4K", "News 720p",
    "News хд", "News 4к",
])
def test_quality_qualified_epg_and_config_aliases_cannot_launch_sd(alias):
    item = candidate(1, "News SD", epg_name=alias)
    result = channels([item], aliases={"News": [alias]})
    assert result[0]["synonyms"] == ["News SD"]


@pytest.mark.parametrize("stream,alias", [
    ("News", "News SD"), ("News", "News HD"), ("News HD", "News SD"),
    ("News FHD", "News HD"), ("News 4K", "News Full HD"),
])
def test_alias_quality_requires_the_same_known_primary_quality(stream, alias):
    result = channels([candidate(1, stream, epg_name=alias)], aliases={"News": [alias]})
    assert alias not in result[0]["synonyms"]


@pytest.mark.parametrize("stream,alias", [
    ("News SD", "News 480i"), ("News FHD", "News FullHD"),
    ("News UHD", "News Ultra HD"), ("News 4K", "News UHD"),
])
def test_equivalent_known_quality_labels_are_preserved(stream, alias):
    assert alias in channels([candidate(1, stream, epg_name=alias)])[0]["synonyms"]


def test_unique_transliteration_and_acronym_are_whole_labels():
    result = channels([
        candidate(1, "Кино Мир"), candidate(2, "British News Network"),
        candidate(3, "TV Кино"),
    ])
    by_name = {row["name"]: row["synonyms"] for row in result}
    assert "Kino Mir" in by_name["Кино Мир"]
    assert "BNN" in by_name["British News Network"]
    assert "TV Kino" in by_name["TV Кино"]
    assert not any("play " in name.lower() for row in result for name in row["synonyms"])


def test_transliteration_cannot_shadow_an_actual_channel_name():
    result = channels([candidate(1, "Кино"), candidate(2, "Kino")])
    assert {row["name"] for row in result} == {"Кино", "Kino"}
    assert all(not row["synonyms"] for row in result)


def test_explicit_cnn_alias_can_choose_one_preferred_regional_channel():
    items = [candidate(1, "CNN International Europe"), candidate(2, "CNN CA")]
    result = channels(items, preferred_channel_ids=[items[0].channel_id], aliases={
        "CNN": ["CNN International Europe"],
    })
    assert "CNN" in result[0]["synonyms"]
    assert "CNN" not in result[1]["synonyms"]


def test_ambiguous_explicit_alias_never_picks_the_first_channel():
    items = [candidate(1, "News Europe"), candidate(2, "News Asia")]
    aliases = {"News": ["News Europe", "News Asia"]}
    assert all("News" not in row["synonyms"] for row in channels(items, aliases=aliases))
    assert channels(list(reversed(items)), aliases=aliases) == channels(items, aliases=aliases)
    preferred = channels(items, aliases=aliases, preferred_channel_ids=[items[1].channel_id])
    assert "News" in preferred[0]["synonyms"]
    both = channels(items, aliases=aliases, preferred_channel_ids=[i.channel_id for i in items])
    assert all("News" not in row["synonyms"] for row in both)


def test_alias_groups_and_generated_names_cannot_shadow_a_canonical_name():
    result = channels([candidate(1, "British News Network"), candidate(2, "BNN")], aliases={
        "BNN": ["British News Network"],
    }, preferred_channel_ids=[f"{1:024x}"])
    assert all("BNN" not in row["synonyms"] for row in result)


def test_shared_epg_names_and_overlapping_aliases_are_checked_before_limit():
    items = [candidate(1, "First", "Shared"), candidate(2, "Second", "SHARED!")]
    aliases = {"First": ["Choice"], "Second": ["CHOICE!"]}
    assert channels(items, aliases=aliases, limit=1)[0]["synonyms"] == []


def test_ambiguous_alias_group_cannot_be_overridden_by_another_config_entry():
    items = [candidate(1, "First"), candidate(2, "Second")]
    aliases = {"Shared": ["First", "Second"], "First": ["Shared"]}
    result = channels(items, aliases=aliases)
    assert all("Shared" not in row["synonyms"] for row in result)
    assert channels(items, aliases=dict(reversed(list(aliases.items())))) == result


def test_alias_of_an_omitted_duplicate_cannot_be_assigned_to_another_channel():
    items = [candidate(1, "First"), candidate(2, "Second"), candidate(3, "Second")]
    result = channels(items, aliases={"First": ["Shared"], "Second": ["Shared"]})
    assert [row["name"] for row in result] == ["First"]
    assert result[0]["synonyms"] == []


def test_unique_epg_label_is_preserved_and_synonyms_are_bounded():
    item = candidate(1, "News", "News International")
    assert channels([item])[0]["synonyms"] == ["News International"]
    result = channels([item], aliases={"News": [f"News Alias {i}" for i in range(20)]})
    assert len(result[0]["synonyms"]) == 10
    assert len({normalize(name) for name in result[0]["synonyms"]}) == 10


def test_unsafe_names_never_leave_the_builder():
    items = [
        candidate(1, "  Ｎｅｗｓ   SD ", "www.private.example.test"),
        candidate(2, "Private https://private.example.test/token"),
        candidate(3, "Bad\nName"), candidate(4, "Bad\u200bName"), candidate(5, "a" * 101),
        candidate(6, "!@#$"), replace(candidate(7, "Invalid ID"), channel_id="invalid"),
        replace(candidate(8, "Invalid availability"), available=1),
    ]
    result = channels(items, aliases={"News": [
        "Fine", "https://private.example.test/secret", "www.private.example.test",
        "Invalid\x7f", "Invalid\n", "x" * 101,
    ]})
    assert result == [{
        "channel_id": items[0].channel_id, "name": "News", "synonyms": ["Fine", "News SD"],
        "available": True,
    }]


def test_all_ten_thousand_candidates_participate_even_when_only_one_is_published():
    items = [candidate(i + 1, f"Channel {i}") for i in range(10_000)]
    items[0] = candidate(1, "CNN International Europe")
    items[-1] = candidate(10_000, "CNN CA")
    result = channels(items, preferred_channel_ids=[items[0].channel_id], limit=1)
    assert result[0]["name"] == "CNN International Europe"
    assert "CNN" not in result[0]["synonyms"]


@pytest.mark.parametrize("limit", [0, -1, 201, 1.5, True, None])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="bounds"):
        build_automatic_channels([], limit=limit)


def test_input_and_output_limits():
    items = [candidate(i + 1, f"Channel {i}") for i in range(201)]
    assert len(channels(items)) == 30
    assert len(channels(items, limit=200)) == 200
    with pytest.raises(ValueError, match="bounds"):
        build_automatic_channels([items[0]] * 10_001)
