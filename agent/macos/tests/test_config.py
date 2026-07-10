import json
from pathlib import Path

import pytest

from clipboard_relay_agent.config import ConfigError, load_config


def test_load_config_reads_required_values_and_default_reconnect(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
                "api_key": "secret-key",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.server_ws_url == "wss://clip.hcid274.cn/ws/agent?device_id=mac-china"
    assert config.api_key == "secret-key"
    assert config.reconnect_seconds == 5


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "missing.json")


def test_load_config_rejects_blank_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
                "api_key": "",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="api_key"):
        load_config(config_path)
