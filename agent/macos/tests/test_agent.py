import io
import json
import logging
from logging.handlers import RotatingFileHandler
from urllib.error import HTTPError

import clipboard_relay_agent.agent as agent_module
from clipboard_relay_agent.agent import (
    AUTHENTICATION_FAILURE_EXIT_CODE,
    AuthenticationError,
    RegistrationError,
    build_headers,
    configure_logging,
    handle_message,
    main,
    register_configured_device,
    registration_url,
    send_registration_request,
    run_agent,
)
from clipboard_relay_agent.config import Config


def test_build_headers_uses_existing_api_key_header() -> None:
    assert build_headers("secret-key") == ["X-API-Key: secret-key"]


def test_registration_url_is_derived_from_websocket_url() -> None:
    assert registration_url("wss://clip.hcid274.cn/ws/agent?device_id=old") == (
        "https://clip.hcid274.cn/api/devices/register"
    )


def test_registration_prompts_from_hostname_and_persists_server_device_id(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-key",
            }
        ),
        encoding="utf-8",
    )
    requests = []

    def send_request(server_ws_url, password, device_id):
        requests.append((server_ws_url, password, device_id))
        return {"device_id": "my-mac"}

    monkeypatch.setattr(agent_module, "send_registration_request", send_request)
    config = register_configured_device(
        Config("wss://clip.hcid274.cn/ws/agent", "secret-key"),
        config_path,
        # 可注入的 prompt：接收建议名并返回用户选定的 id。
        prompt=lambda suggestion: suggestion,
        hostname="My Mac.local",
    )

    assert config.device_id == "my-mac"
    assert json.loads(config_path.read_text(encoding="utf-8"))["device_id"] == "my-mac"
    assert requests == [
        ("wss://clip.hcid274.cn/ws/agent", "secret-key", "my-mac-local")
    ]


def test_registration_reuses_saved_device_id_without_prompt(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-key",
                "device_id": "saved-mac",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        agent_module,
        "send_registration_request",
        lambda *_args: {"device_id": "saved-mac"},
    )

    config = register_configured_device(
        Config(
            "wss://clip.hcid274.cn/ws/agent",
            "secret-key",
            device_id="saved-mac",
        ),
        config_path,
        prompt=lambda _message: (_ for _ in ()).throw(AssertionError("prompted")),
    )

    assert config.device_id == "saved-mac"


def test_registration_http_401_raises_authentication_error(monkeypatch) -> None:
    """密码错误必须映射为 AuthenticationError，不能吞成普通注册失败。"""
    error = HTTPError(
        "https://clip.hcid274.cn/api/devices/register",
        401,
        "Unauthorized",
        {},
        io.BytesIO(b'{"detail":"invalid password"}'),
    )
    monkeypatch.setattr(
        agent_module,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    try:
        send_registration_request(
            "wss://example.test/ws/agent", "wrong-password", "mac-office"
        )
        raise AssertionError("expected AuthenticationError")
    except AuthenticationError as exc:
        assert "401" in str(exc)
        assert "invalid password" in str(exc)


def test_registration_http_403_is_not_authentication_error(monkeypatch) -> None:
    error = HTTPError(
        "https://clip.hcid274.cn/api/devices/register",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"detail":"device limit reached"}'),
    )
    monkeypatch.setattr(
        agent_module,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    try:
        send_registration_request(
            "wss://example.test/ws/agent", "secret-key", "mac-extra"
        )
        raise AssertionError("expected RegistrationError")
    except AuthenticationError:
        raise AssertionError("403 must not be AuthenticationError")
    except RegistrationError as exc:
        assert "403" in str(exc)


def test_main_authentication_failure_clears_password_and_uses_exit_code_3(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "mac-office",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_module, "configure_logging", lambda: logging.getLogger("test"))
    monkeypatch.setattr(
        agent_module,
        "send_registration_request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AuthenticationError("设备注册失败（HTTP 401）：invalid password")
        ),
    )

    exit_code = main(["--config", str(config_path), "--register-only"])

    assert exit_code == AUTHENTICATION_FAILURE_EXIT_CODE
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["password"] == ""
    assert saved["device_id"] == "mac-office"


def test_handle_message_copies_clipboard_text_without_logging_body(caplog) -> None:
    copied = []

    caplog.set_level(logging.INFO)

    handle_message(
        '{"type":"clipboard","text":"very sensitive text"}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == ["very sensitive text"]
    assert "length=19" in caplog.text
    assert "very sensitive text" not in caplog.text


def test_handle_message_decodes_bytes_messages() -> None:
    copied = []

    handle_message(
        b'{"type":"clipboard","text":"from bytes"}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == ["from bytes"]


def test_handle_message_ignores_invalid_json(caplog) -> None:
    copied = []

    caplog.set_level(logging.WARNING)

    handle_message(
        "not json",
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == []
    assert "invalid JSON" in caplog.text


def test_handle_message_ignores_non_clipboard_type() -> None:
    copied = []

    reply = handle_message(
        '{"type":"status","text":"hello"}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == []
    assert reply is None


def test_handle_message_ping_returns_pong_without_touching_clipboard() -> None:
    copied = []

    reply = handle_message(
        '{"type":"ping","t":123.45,"id":"abc"}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == []
    assert reply == {"type": "pong", "t": 123.45, "id": "abc"}


def test_handle_message_ignores_non_string_text(caplog) -> None:
    copied = []

    caplog.set_level(logging.WARNING)

    handle_message(
        '{"type":"clipboard","text":123}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == []
    assert "clipboard text is not a string" in caplog.text


def test_handle_message_logs_copy_failure_without_raising(caplog) -> None:
    def fail_copy(_: str) -> None:
        raise RuntimeError("pbcopy unavailable")

    caplog.set_level(logging.ERROR)

    reply = handle_message(
        '{"type":"clipboard","text":"hello"}',
        copy_text=fail_copy,
        logger=logging.getLogger("test.clipboard"),
    )

    assert "failed to write clipboard" in caplog.text
    assert "hello" not in caplog.text
    assert reply == {"type": "clipboard_report", "status": "failed"}


def test_run_agent_exits_cleanly_on_keyboard_interrupt(monkeypatch) -> None:
    class InterruptingWebSocketApp:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_forever(self, **_kwargs) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", InterruptingWebSocketApp)

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )


def test_run_agent_enables_websocket_heartbeat(monkeypatch) -> None:
    calls = []

    class CapturingWebSocketApp:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_forever(self, **kwargs) -> None:
            calls.append(kwargs)
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", CapturingWebSocketApp)

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )

    assert calls == [
        {
            "http_proxy_timeout": 8,
            "ping_interval": 30,
            "ping_timeout": 10,
            "skip_utf8_validation": True,
        }
    ]


def test_configure_logging_uses_single_bounded_file_handler(tmp_path) -> None:
    logger = configure_logging(tmp_path / "agent.log")

    try:
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == 1_048_576
        assert handler.backupCount == 3
    finally:
        for handler in logger.handlers:
            handler.close()


def test_run_agent_passes_raw_websocket_messages_to_handler(monkeypatch, tmp_path) -> None:
    handled = []

    class MessageWebSocketApp:
        def __init__(self, *_args, **kwargs) -> None:
            self.on_message = kwargs["on_message"]

        def run_forever(self, **_kwargs) -> None:
            self.on_message(self, b'{"type":"clipboard","text":"raw"}')
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", MessageWebSocketApp)
    monkeypatch.setattr(agent_module, "handle_message", lambda msg, **_kwargs: handled.append(msg))
    monkeypatch.setattr(agent_module, "STATUS_PATH", tmp_path / "status.json")

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )

    assert handled == [b'{"type":"clipboard","text":"raw"}']


def test_update_status_writes_device_and_connection_metadata(tmp_path) -> None:
    status_path = tmp_path / "status.json"

    agent_module.update_status(
        status_path=status_path,
        server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
        connected=True,
        event="connected",
        reconnect_attempts=2,
    )

    status = json.loads(status_path.read_text(encoding="utf-8"))

    assert status["connected"] is True
    assert status["device_id"] == "mac-china"
    assert status["event"] == "connected"
    assert status["reconnect_attempts"] == 2
    assert status["server_ws_url"] == "wss://clip.hcid274.cn/ws/agent?device_id=mac-china"
    assert "updated_at" in status


def test_run_agent_records_connection_lifecycle_status(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "status.json"
    events = []

    class ClosingWebSocketApp:
        def __init__(self, *_args, **kwargs) -> None:
            self.on_open = kwargs["on_open"]
            self.on_close = kwargs["on_close"]

        def run_forever(self, **_kwargs) -> None:
            self.on_open(self)
            events.append(json.loads(status_path.read_text(encoding="utf-8")))
            self.on_close(self, 1006, "ping timeout")
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", ClosingWebSocketApp)
    monkeypatch.setattr(agent_module, "STATUS_PATH", status_path)

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )

    final_status = json.loads(status_path.read_text(encoding="utf-8"))

    assert events[0]["connected"] is True
    assert events[0]["event"] == "connected"
    assert final_status["connected"] is False
    assert final_status["event"] == "closed"
    assert final_status["last_close_status"] == 1006
    assert final_status["last_close_reason"] == "ping timeout"


def test_run_agent_does_not_write_status_for_each_server_message(monkeypatch, tmp_path) -> None:
    status_path = tmp_path / "status.json"

    class MessageWebSocketApp:
        def __init__(self, *_args, **kwargs) -> None:
            self.on_open = kwargs["on_open"]
            self.on_message = kwargs["on_message"]

        def send(self, _message: str) -> None:
            return None

        def run_forever(self, **_kwargs) -> None:
            self.on_open(self)
            persisted_before_message = status_path.read_text(encoding="utf-8")
            self.on_message(self, '{"type":"ping","id":"probe"}')
            assert status_path.read_text(encoding="utf-8") == persisted_before_message
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", MessageWebSocketApp)
    monkeypatch.setattr(agent_module, "STATUS_PATH", status_path)

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )


def test_run_agent_sets_websocket_default_timeout(monkeypatch) -> None:
    timeouts = []

    class InterruptingWebSocketApp:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_forever(self, **_kwargs) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", InterruptingWebSocketApp)
    monkeypatch.setattr(agent_module.websocket, "setdefaulttimeout", timeouts.append)

    run_agent(
        Config(
            server_ws_url="wss://clip.hcid274.cn/ws/agent?device_id=mac-china",
            api_key="secret-key",
            reconnect_seconds=5,
        ),
        logging.getLogger("test.clipboard"),
    )

    assert timeouts == [8]
