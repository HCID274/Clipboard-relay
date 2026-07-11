from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


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
    device_id: str | None = None

    @property
    def password(self) -> str:
        return self.api_key


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
    password_key = "password" if "password" in raw else "api_key"
    api_key = _required_string(raw, password_key).strip()
    if not api_key.isascii():
        raise ConfigError(
            f"Config value {password_key!r} must contain only ASCII characters"
        )
    reconnect_seconds = _reconnect_seconds(raw.get("reconnect_seconds", DEFAULT_RECONNECT_SECONDS))
    device_id = raw.get("device_id")
    if device_id is None:
        legacy_device_ids = parse_qs(urlparse(server_ws_url).query).get("device_id")
        if legacy_device_ids:
            device_id = legacy_device_ids[0]
    if device_id is not None and (not isinstance(device_id, str) or not device_id.strip()):
        raise ConfigError("Config value 'device_id' must be a non-empty string")

    return Config(
        server_ws_url=server_ws_url,
        api_key=api_key,
        reconnect_seconds=reconnect_seconds,
        device_id=device_id,
    )


def save_device_id(path: Path, device_id: str) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot update config file: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")
    raw["device_id"] = device_id
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_path.replace(path)
    except OSError as exc:
        raise ConfigError(f"Cannot update config file: {path}") from exc


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Config value {key!r} must be a non-empty string")
    return value


def _reconnect_seconds(value: Any) -> int:
    if not isinstance(value, int) or value < 1:
        raise ConfigError("Config value 'reconnect_seconds' must be a positive integer")
    return value
