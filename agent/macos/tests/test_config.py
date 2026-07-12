import json
from pathlib import Path

import pytest

from clipboard_relay_agent.config import (
    ConfigError,
    clear_password,
    config_needs_password,
    load_config,
    save_device_id,
    set_password,
)


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


def test_load_config_trims_password(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "  secret-key  ",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.password == "secret-key"


def test_load_config_rejects_non_ascii_password(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "中文密码",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="ASCII"):
        load_config(config_path)


@pytest.mark.parametrize(
    "server_ws_url",
    ["https://clip.hcid274.cn/ws/agent", "wss:///ws/agent", "ws://:80/path"],
)
def test_load_config_rejects_websocket_url_without_valid_scheme_and_host(
    tmp_path: Path, server_ws_url: str
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": server_ws_url,
                "password": "secret-key",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="ws:// or wss:// URL with a host"):
        load_config(config_path)


def test_load_config_rejects_placeholder_password(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "replace-with-relay-password",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="placeholder"):
        load_config(config_path)


def test_password_setup_replaces_placeholder_and_preserves_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "replace-with-relay-password",
                "reconnect_seconds": 7,
            }
        ),
        encoding="utf-8",
    )

    assert config_needs_password(config_path) is True
    set_password(config_path, "new-secret")

    assert config_needs_password(config_path) is False
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["password"] == "new-secret"
    assert saved["reconnect_seconds"] == 7


def test_clear_password_makes_installer_prompt_on_the_next_run(tmp_path: Path) -> None:
    """401 后清密码：下次安装应重新认为需要输入密码。"""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "mac-office",
                "reconnect_seconds": 7,
            }
        ),
        encoding="utf-8",
    )

    clear_password(config_path)

    assert config_needs_password(config_path) is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["password"] == ""
    assert saved["device_id"] == "mac-office"
    assert saved["reconnect_seconds"] == 7


def test_clear_password_also_clears_legacy_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "api_key": "legacy-key",
            }
        ),
        encoding="utf-8",
    )

    clear_password(config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["password"] == ""
    assert saved["api_key"] == ""
    assert config_needs_password(config_path) is True


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
