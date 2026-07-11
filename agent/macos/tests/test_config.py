import json
from pathlib import Path

import pytest

from clipboard_relay_agent.config import ConfigError, load_config, save_device_id


def test_load_config_reads_required_values_and_default_reconnect(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
                "password": "secret-key",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.server_ws_url == "wss://clip.hcid274.cn/ws/agent?device_id=mac-china"
    assert config.password == "secret-key"
    assert config.device_id == "mac-china"
    assert config.reconnect_seconds == 5


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "missing.json")


def test_load_config_rejects_blank_password(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
                "password": "",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="password"):
        load_config(config_path)


def test_save_device_id_preserves_config_and_persists_normalized_identity(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-key",
                "reconnect_seconds": 7,
            }
        ),
        encoding="utf-8",
    )

    save_device_id(config_path, "mac-studio")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["device_id"] == "mac-studio"
    assert saved["password"] == "secret-key"
    assert saved["reconnect_seconds"] == 7
