from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = (
    Path.home() / "Library" / "Application Support" / "ClipboardRelay" / "config.json"
)
DEFAULT_RECONNECT_SECONDS = 5


class ConfigError(RuntimeError):
    """Raised when the agent cannot load a usable configuration."""


@dataclass(frozen=True)
class Config:
    server_ws_url: str
    api_key: str
    reconnect_seconds: int = DEFAULT_RECONNECT_SECONDS


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {path}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")

    server_ws_url = _required_string(raw, "server_ws_url")
    api_key = _required_string(raw, "api_key")
    reconnect_seconds = _reconnect_seconds(raw.get("reconnect_seconds", DEFAULT_RECONNECT_SECONDS))

    return Config(
        server_ws_url=server_ws_url,
        api_key=api_key,
        reconnect_seconds=reconnect_seconds,
    )


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Config value {key!r} must be a non-empty string")
    return value


def _reconnect_seconds(value: Any) -> int:
    if not isinstance(value, int) or value < 1:
        raise ConfigError("Config value 'reconnect_seconds' must be a positive integer")
    return value

