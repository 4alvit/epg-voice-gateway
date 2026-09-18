"""Domain records keep playback credentials out of search results."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ProgramMatch:
    title: str
    channel_id: str
    channel_name: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class Channel:
    channel_id: str
    name: str
    epg_id: str
    epg_name: str
    stream_url: str = field(repr=False)
    shift_seconds: int = 0


@dataclass(frozen=True)
class Programme:
    epg_id: str
    title: str
    starts_at: datetime
    ends_at: datetime
