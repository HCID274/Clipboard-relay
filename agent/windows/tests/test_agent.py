import io
import json
import logging
from logging.handlers import RotatingFileHandler
from urllib.error import HTTPError

import pytest

import agent


def test_load_config_reuses_device_id_from_legacy_websocket_url(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )

    config = agent.load_config(config_path)

    assert config["device_id"] == "win-fukuoka"


def test_load_config_rejects_non_ascii_password(tmp_path) -> None:
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

    with pytest.raises(SystemExit):
        agent.load_config(config_path)


@pytest.mark.parametrize(
    "server_ws_url",
    ["https://clip.hcid274.cn/ws/agent", "wss:///ws/agent", "ws://:80/path"],
)
def test_load_config_rejects_websocket_url_without_valid_scheme_and_host(
    tmp_path, server_ws_url
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": server_ws_url,
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        agent.load_config(config_path)


def test_config_needs_password_recognizes_placeholder_and_valid_password(tmp_path) -> None:
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

    assert agent.config_needs_password(config_path) is True

    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )

    assert agent.config_needs_password(config_path) is False


@pytest.mark.parametrize(
    ("contents", "expected_status"),
    [
        ('{"password": "replace-with-relay-password"}', 0),
        ('{"password": "secret-password"}', 1),
        ("{not valid JSON", 2),
        ("[]", 2),
    ],
)
def test_password_setup_status_distinguishes_config_errors(
    tmp_path, contents, expected_status
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")

    assert agent.password_setup_status(config_path) == expected_status

    if expected_status == 2:
        with pytest.raises(ValueError):
            agent.config_needs_password(config_path)


def test_registration_prompts_with_hostname_and_persists_server_identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )
    captured_requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"device_id":"win-office"}'

    def open_request(request, timeout):
        captured_requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(agent.socket, "gethostname", lambda: "WIN OFFICE.local")
    # 可注入的 prompt：接收建议名并返回用户选定的 id。
    monkeypatch.setattr(agent, "urlopen", open_request)

    config = agent.register_configured_device(
        agent.load_config(config_path),
        config_path,
        prompt=lambda suggestion: suggestion,
    )

    assert config["device_id"] == "win-office"
    assert config["server_ws_url"].endswith("device_id=win-office")
    assert json.loads(config_path.read_text(encoding="utf-8"))["device_id"] == "win-office"
    assert json.loads(captured_requests[0][0].data) == {"device_id": "win-office-local"}


def test_registration_reports_server_error_detail(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "win-office",
            }
        ),
        encoding="utf-8",
    )
    error = HTTPError(
        "https://clip.hcid274.cn/api/devices/register",
        403,
        "Forbidden",
        {},
        io.BytesIO('{"detail":"已达设备数上限"}'.encode()),
    )
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(agent.RegistrationError, match="已达设备数上限"):
        agent.register_configured_device(agent.load_config(config_path), config_path)


def test_authentication_failure_uses_a_distinct_exit_code(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "win-office",
            }
        ),
        encoding="utf-8",
    )
    error = HTTPError(
        "https://clip.hcid274.cn/api/devices/register",
        401,
        "Unauthorized",
        {},
        io.BytesIO('{"detail":"invalid password"}'.encode()),
    )
    monkeypatch.setattr(agent, "setup_logging", lambda: None)
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(SystemExit) as exc_info:
        agent.run(register_only=True, config_path=config_path)

    assert exc_info.value.code == agent.AUTHENTICATION_FAILURE_EXIT_CODE
    assert json.loads(config_path.read_text(encoding="utf-8"))["password"] == ""


def test_clear_password_makes_the_installer_prompt_on_the_next_run(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "win-office",
            }
        ),
        encoding="utf-8",
    )

    agent.clear_password(config_path)

    assert agent.password_setup_status(config_path) == 0
    assert json.loads(config_path.read_text(encoding="utf-8"))["password"] == ""


def test_clear_password_also_clears_legacy_api_key(tmp_path) -> None:
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

    agent.clear_password(config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["password"] == ""
    assert saved["api_key"] == ""


def test_setup_logging_uses_bounded_rotating_file_handler(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "agent.log"
    monkeypatch.setattr(agent, "get_log_path", lambda: log_path)

    agent.setup_logging()

    try:
        file_handlers = [
            handler for handler in logging.getLogger().handlers if isinstance(handler, RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].maxBytes == agent.LOG_MAX_BYTES
        assert file_handlers[0].backupCount == agent.LOG_BACKUP_COUNT
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()


def test_run_sets_websocket_timeout_and_heartbeat(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-password",
                "device_id": "win-office",
            }
        ),
        encoding="utf-8",
    )
    timeouts = []
    run_options = []

    class InterruptingWebSocketApp:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def run_forever(self, **kwargs) -> None:
            run_options.append(kwargs)
            raise KeyboardInterrupt

    monkeypatch.setattr(agent, "setup_logging", lambda: None)
    monkeypatch.setattr(agent, "register_configured_device", lambda config, *_args: config)
    monkeypatch.setattr(agent.websocket, "setdefaulttimeout", timeouts.append)
    monkeypatch.setattr(agent.websocket, "WebSocketApp", InterruptingWebSocketApp)

    agent.run(config_path=config_path)

    assert timeouts == [agent.CONNECT_TIMEOUT_SECONDS]
    assert run_options == [
        {
            "http_proxy_timeout": agent.CONNECT_TIMEOUT_SECONDS,
            "ping_interval": agent.PING_INTERVAL_SECONDS,
            "ping_timeout": agent.PING_TIMEOUT_SECONDS,
            "skip_utf8_validation": True,
        }
    ]


def test_build_pong_reply_echoes_ping_fields() -> None:
    assert agent.build_pong_reply({"type": "ping", "t": 1.25, "id": "n1"}) == {
        "type": "pong",
        "t": 1.25,
        "id": "n1",
    }
