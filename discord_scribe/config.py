"""Explicit local configuration; credentials only come from the environment."""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from urllib.parse import urlparse


@dataclass(frozen=True)
class Config:
    project: str
    guild_id: str
    channel_ids: frozenset[str]
    author_ids: frozenset[str]
    database: Path
    export: Path
    model: str = "openai/gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    api_base: str | None = None
    interval_seconds: int = 3600

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        required = {"project", "guild_id", "channel_ids", "author_ids", "database", "export"}
        if not required <= data.keys() or data.keys() - required - {"model", "api_key_env", "api_base", "interval_seconds"}:
            raise ValueError("Missing or unknown configuration fields; see scribe.example.toml")
        for name in ("project", "database", "export", "model", "api_key_env", "api_base"):
            if name in data and (not isinstance(data[name], str) or not data[name].strip()):
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("channel_ids", "author_ids"):
            if not isinstance(data[name], list):
                raise ValueError(f"{name} must be a list")
        for value in [data["guild_id"], *data["channel_ids"], *data["author_ids"]]:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
                raise ValueError("Configured Discord IDs must be numeric strings")
        if "api_base" in data:
            url = urlparse(data["api_base"])
            local = url.hostname in {"localhost", "127.0.0.1", "::1"}
            if not url.hostname or url.scheme not in {"https", "http"} or (url.scheme == "http" and not local) or url.username or url.password or url.query or url.fragment:
                raise ValueError("API base must use HTTPS (HTTP is allowed for localhost only)")
        if "/" not in data.get("model", "openai/gpt-4o-mini"):
            raise ValueError("Use a provider/model name, such as openai/gpt-4o-mini")
        interval = data.get("interval_seconds", 3600)
        if type(interval) is not int or not 10 <= interval <= 86400:
            raise ValueError("interval_seconds must be an integer from 10 to 86400")
        for name in ("database", "export"):
            data[name] = (path.resolve().parent / data[name]).resolve()
        if data["database"] == data["export"] or path.resolve() in {data["export"], data["database"]}:
            raise ValueError("Database, export, and configuration paths must be distinct")
        data["channel_ids"] = frozenset(data["channel_ids"])
        data["author_ids"] = frozenset(data["author_ids"])
        return cls(**data)
