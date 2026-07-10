import logging
import json
from logging.handlers import RotatingFileHandler

import clipboard_relay_agent.agent as agent_module
from clipboard_relay_agent.agent import (
    build_connection_target,
    build_headers,
    configure_logging,
    handle_message,
    run_agent,
)
from clipboard_relay_agent.config import Config


def test_build_headers_uses_existing_api_key_header() -> None:
    assert build_headers("secret-key") == ["X-API-Key: secret-key"]


def test_build_connection_target_uses_static_ip_with_original_host_and_sni() -> None:
    target = build_connection_target("wss://clip.hcid274.cn/ws/agent?device_id=mac-china")

    assert target.url == "wss://64.176.40.67/ws/agent?device_id=mac-china"
    assert target.host == "clip.hcid274.cn"
    assert target.sslopt == {"server_hostname": "clip.hcid274.cn"}


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

    handle_message(
        '{"type":"ping","text":"hello"}',
        copy_text=copied.append,
        logger=logging.getLogger("test.clipboard"),
    )

    assert copied == []


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

    handle_message(
        '{"type":"clipboard","text":"hello"}',
        copy_text=fail_copy,
        logger=logging.getLogger("test.clipboard"),
    )

    assert "failed to write clipboard" in caplog.text
    assert "hello" not in caplog.text


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
            "host": "clip.hcid274.cn",
            "sslopt": {"server_hostname": "clip.hcid274.cn"},
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


def test_run_agent_passes_raw_websocket_messages_to_handler(monkeypatch) -> None:
    handled = []

    class MessageWebSocketApp:
        def __init__(self, *_args, **kwargs) -> None:
            self.on_message = kwargs["on_message"]

        def run_forever(self, **_kwargs) -> None:
            self.on_message(self, b'{"type":"clipboard","text":"raw"}')
            raise KeyboardInterrupt

    monkeypatch.setattr(agent_module.websocket, "WebSocketApp", MessageWebSocketApp)
    monkeypatch.setattr(agent_module, "handle_message", lambda msg, **_kwargs: handled.append(msg))

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
