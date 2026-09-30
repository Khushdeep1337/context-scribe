"""Validated contracts shared by ingestion, inference, and persistence."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re

CATEGORIES = ("decision", "proposal", "constraint", "action", "fact")


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Message timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class Message:
    id: str
    guild_id: str
    channel_id: str
    author_id: str
    content: str
    updated_at: str
    is_bot: bool = False

    def __post_init__(self) -> None:
        for value in (self.id, self.guild_id, self.channel_id, self.author_id):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
                raise ValueError("Discord IDs must be numeric strings")
        if not isinstance(self.content, str) or len(self.content) > 4000:
            raise ValueError("Message content must be a string of at most 4000 characters")
        object.__setattr__(self, "updated_at", timestamp(self.updated_at))

    @property
    def url(self) -> str:
        return f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{self.id}"


@dataclass(frozen=True)
class Candidate:
    category: str
    summary: str
    evidence: str
    confidence: float


def validate_extraction(data: object, source: str) -> list[Candidate]:
    if not isinstance(data, dict) or set(data) != {"items"}:
        raise ValueError("Expected an object containing only items")
    items = data["items"]
    if not isinstance(items, list) or len(items) > 5:
        raise ValueError("Expected at most five context items")
    result = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"category", "summary", "evidence", "confidence"}:
            raise ValueError("Invalid context item fields")
        if item["category"] not in CATEGORIES:
            raise ValueError("Unknown category")
        for key, limit in (("summary", 500), ("evidence", 1000)):
            if not isinstance(item[key], str) or not 1 <= len(item[key].strip()) <= limit:
                raise ValueError(f"Invalid {key} length")
        if item["evidence"] not in source:
            raise ValueError("Evidence must be an exact substring of the source")
        confidence = item["confidence"]
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Confidence must be a finite number between zero and one")
        candidate = Candidate(**item)
        if candidate not in result:
            result.append(candidate)
    return result
