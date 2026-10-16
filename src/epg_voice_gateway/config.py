"""Operator configuration. Production identities and secrets are supplied at runtime."""

import re
import unicodedata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .search import normalize


def origin(value: str, *, https: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.port == 0:
        raise ValueError("A nonzero port is required")
    schemes = {"https"} if https else {"http", "https"}
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(character) <= 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("A credential-free HTTP(S) origin is required")
    return value.rstrip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EPG_", extra="ignore", hide_input_in_errors=True)

    api_token: SecretStr
    signing_key: SecretStr
    hls_proxy_url: str
    media_base_url: str
    require_https: bool = False
    playlist_path: str = "/playlist.m3u8"
    xmltv_urls: list[str] = Field(min_length=1, max_length=8)
    database_path: str = ":memory:"
    alexa_skill_id: str = ""
    alexa_capability_diagnostics: bool = False
    title_aliases: dict[str, list[str]] = Field(default_factory=dict, max_length=200)
    voice_catalogue_limit: int = Field(default=30, ge=1, le=50)
    voice_channel_ids: list[str] = Field(default_factory=list, max_length=200)
    voice_preferred_queries: list[str] = Field(default_factory=list, max_length=50)
    channel_voice_ids: list[str] = Field(default_factory=list, max_length=200)
    channel_voice_limit: int = Field(default=30, ge=1, le=200)
    channel_voice_mode: Literal["explicit", "automatic"] = "explicit"
    channel_aliases: dict[str, list[str]] = Field(default_factory=dict, max_length=200)
    media_preferred_channel_ids: list[str] = Field(default_factory=list, max_length=200)
    refresh_seconds: int = Field(default=300, ge=30, le=86400)
    max_stale_seconds: int = Field(default=900, ge=60, le=172800)
    session_ttl_seconds: int = Field(default=7200, ge=60, le=86400)
    max_source_bytes: int = Field(default=64 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024)
    ha_url: str = ""
    ha_token: SecretStr = SecretStr("")
    ha_targets: dict[str, str] = Field(default_factory=dict)

    @field_validator("voice_channel_ids", "channel_voice_ids", "media_preferred_channel_ids")
    @classmethod
    def voice_channels(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-f0-9]{24}", value) for value in values):
            raise ValueError("Voice channel IDs must contain 24 lowercase hexadecimal characters")
        return list(dict.fromkeys(values))

    @field_validator("channel_aliases")
    @classmethod
    def channel_names(cls, values: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, aliases in values.items():
            if len(aliases) > 10:
                raise ValueError("At most ten aliases per channel are supported")
            for label in (name, *aliases):
                if (
                    not normalize(label) or len(label) > 100
                    or any(unicodedata.category(c).startswith("C") for c in label)
                    or re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", label, re.IGNORECASE)
                ):
                    raise ValueError("Channel aliases must be safe names of at most 100 characters")
        return values

    @field_validator("title_aliases")
    @classmethod
    def programme_aliases(cls, values: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, aliases in values.items():
            if len(aliases) > 10:
                raise ValueError("At most ten aliases per programme are supported")
            for label in (name, *aliases):
                if (
                    not normalize(label) or len(label) > 200
                    or any(unicodedata.category(c).startswith("C") for c in label)
                    or re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", label, re.IGNORECASE)
                ):
                    raise ValueError("Programme aliases must be safe names of at most 200 characters")
        return values

    @field_validator("voice_preferred_queries")
    @classmethod
    def voice_queries(cls, values: list[str]) -> list[str]:
        result = []
        seen = set()
        for value in values:
            label = " ".join(value.split())
            normalized = normalize(label)
            if (
                not normalized or len(label) > 100
                or normalized in {"ready", "unavailable"}
                or any(unicodedata.category(character).startswith("C") for character in value)
            ):
                raise ValueError("Voice preferences must be safe labels of at most 100 characters")
            if normalized not in seen:
                result.append(label)
                seen.add(normalized)
        return result

    @field_validator("hls_proxy_url")
    @classmethod
    def proxy_origin(cls, value: str) -> str:
        return origin(value)

    @field_validator("media_base_url")
    @classmethod
    def media_origin(cls, value: str) -> str:
        return origin(value, https=True)

    @field_validator("ha_url")
    @classmethod
    def ha_origin(cls, value: str) -> str:
        return origin(value) if value else ""

    @field_validator("api_token", "signing_key")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < 32:
            raise ValueError("At least 32 bytes of secret material are required")
        return value

    @field_validator("playlist_path")
    @classmethod
    def bounded_catalogue_path(cls, value: str) -> str:
        if value not in ("/playlist.m3u8", "/m3u8"):
            raise ValueError("Use an existing HLS proxy catalogue endpoint")
        return value

    @field_validator("database_path")
    @classmethod
    def database_location(cls, value: str) -> str:
        if value != ":memory:" and (not value.startswith("/") or value.endswith("/")):
            raise ValueError("The database path must be absolute or :memory:")
        return value

    @field_validator("xmltv_urls")
    @classmethod
    def xmltv_sources(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlsplit(value)
            if parsed.port == 0 or "\\" in value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
                raise ValueError("XMLTV source URL contains an invalid port or character")
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.fragment:
                raise ValueError("XMLTV sources must be HTTP(S) URLs")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Do not embed HTTP basic-auth credentials in XMLTV URLs")
        return values

    @model_validator(mode="after")
    def coherent_config(self):
        if self.require_https:
            endpoints = [self.hls_proxy_url, self.media_base_url, *self.xmltv_urls]
            if self.ha_url:
                endpoints.append(self.ha_url)
            if any(urlsplit(value).scheme != "https" for value in endpoints):
                raise ValueError("HTTPS-only mode requires HTTPS for every configured upstream")
        if self.max_stale_seconds < self.refresh_seconds:
            raise ValueError("The stale-data window must cover the refresh interval")
        if bool(self.ha_url) != bool(self.ha_token.get_secret_value()):
            raise ValueError("Configure both Home Assistant URL and token")
        return self
