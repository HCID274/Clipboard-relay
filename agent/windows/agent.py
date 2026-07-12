import argparse
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pyperclip
import websocket

# 共享包位于 agent/clipboard_relay_shared（与 windows/ 同级）。
_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from clipboard_relay_shared.device import (  # noqa: E402
    build_agent_ws_url,
    registration_url,
    suggested_device_id,
)
from clipboard_relay_shared.prompt import prompt_device_id  # noqa: E402


TASK_NAME = "ClipboardRelayAgent"
STABLE_CONNECTION_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 8
PING_INTERVAL_SECONDS = 30
PING_TIMEOUT_SECONDS = 10
LOG_MAX_BYTES = 1_048_576
LOG_BACKUP_COUNT = 3
PLACEHOLDER_PASSWORDS = frozenset(
    {"replace-with-shared-key", "replace-with-relay-password"}
)
AUTHENTICATION_FAILURE_EXIT_CODE = 3


class RegistrationError(RuntimeError):
    pass


class AuthenticationError(RegistrationError):
    pass


def get_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.json"


def get_log_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "ClipboardRelay" / "agent.log"


def validate_password(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("password missing from config")
    password = value.strip()
    if not password.isascii():
        raise ValueError("password must contain only ASCII characters")
    if password in PLACEHOLDER_PASSWORDS:
        raise ValueError("password still uses placeholder value")
    return password


def config_needs_password(config_path: Path) -> bool:
    """可读配置是否仍需要用户提供密码。"""
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read config file: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config root must be a JSON object")
    try:
        validate_password(config.get("password", config.get("api_key")))
        return False
    except ValueError:
        return True


def password_setup_status(config_path: Path) -> int:
    """返回安装脚本用的密码状态码。

    0 — 需要提示用户输入密码；1 — 现有密码合法；2 — 配置无法检查。
    """
    try:
        return 0 if config_needs_password(config_path) else 1
    except ValueError:
        return 2


def setup_logging() -> None:
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    ]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.info("%s starting, log=%s", TASK_NAME, log_path)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    config_path = config_path or get_config_path()
    if not config_path.exists():
        logging.error("config file missing: %s", config_path)
        sys.exit(1)

    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        logging.error("config file is invalid JSON: %s", exc)
        sys.exit(1)
    except OSError as exc:
        logging.error("failed to read config file: %s", exc)
        sys.exit(1)

    if not isinstance(config, dict):
        logging.error("config root must be a JSON object")
        sys.exit(1)

    server_ws_url = config.get("server_ws_url", config.get("ws_url"))
    parsed_server_url = urlparse(server_ws_url) if isinstance(server_ws_url, str) else None
    if (
        parsed_server_url is None
        or parsed_server_url.scheme not in {"ws", "wss"}
        or not parsed_server_url.hostname
    ):
        logging.error("server_ws_url must be a ws:// or wss:// URL with a host")
        sys.exit(1)

    try:
        api_key = validate_password(config.get("password", config.get("api_key")))
    except ValueError as exc:
        logging.error("%s", exc)
        sys.exit(1)

    device_id = config.get("device_id")
    if device_id is None:
        legacy_device_ids = parse_qs(parsed_server_url.query).get("device_id")
        if legacy_device_ids:
            device_id = legacy_device_ids[0]
    if device_id is not None and (not isinstance(device_id, str) or not device_id.strip()):
        logging.error("device_id must be a non-empty string")
        sys.exit(1)

    reconnect_seconds = config.get("reconnect_seconds", 5)
    if not isinstance(reconnect_seconds, (int, float)) or reconnect_seconds <= 0:
        logging.error("reconnect_seconds must be a positive number")
        sys.exit(1)

    return {
        "server_ws_url": server_ws_url,
        "api_key": api_key,
        "device_id": device_id,
        "reconnect_seconds": float(reconnect_seconds),
    }


def _write_config(config_path: Path, raw: dict[str, Any]) -> None:
    temporary_path = config_path.with_name(f".{config_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_path.replace(config_path)


def save_device_id(config_path: Path, device_id: str) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["device_id"] = device_id
    _write_config(config_path, raw)


def clear_password(config_path: Path) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config root must be a JSON object")
    raw["password"] = ""
    # 兼容旧配置：两个字段同时存在时，错误密码不能继续从 api_key 生效。
    if "api_key" in raw:
        raw["api_key"] = ""
    _write_config(config_path, raw)


def _registration_http_error(exc: HTTPError) -> RegistrationError:
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("detail", exc.reason)
    except (json.JSONDecodeError, AttributeError):
        detail = exc.reason
    error_type = AuthenticationError if exc.code == 401 else RegistrationError
    return error_type(f"设备注册失败（HTTP {exc.code}）：{detail}")


def register_configured_device(
    config: dict[str, Any],
    config_path: Path,
    *,
    prompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    device_id = config.get("device_id")
    if device_id is None:
        suggestion = suggested_device_id(socket.gethostname())
        # prompt 可在测试中注入；生产环境使用共享的预填交互。
        device_id = (prompt or prompt_device_id)(suggestion)

    request = Request(
        registration_url(config["server_ws_url"]),
        data=json.dumps({"device_id": device_id}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": config["api_key"]},
        method="POST",
    )
    try:
        with urlopen(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise _registration_http_error(exc) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"设备注册失败：{exc}") from exc

    registered_id = payload.get("device_id") if isinstance(payload, dict) else None
    if not isinstance(registered_id, str):
        raise RegistrationError("设备注册失败：服务端返回了无效响应")
    try:
        save_device_id(config_path, registered_id)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RegistrationError(f"设备身份保存失败：{exc}") from exc
    config["device_id"] = registered_id
    config["server_ws_url"] = build_agent_ws_url(config["server_ws_url"], registered_id)
    return config


def on_open(ws_app: websocket.WebSocketApp) -> None:
    logging.info("connected to %s", ws_app.url)


def build_pong_reply(payload: dict[str, Any]) -> dict[str, Any]:
    """由服务端 ping 构造 pong 载荷，供测服务器↔本机 RTT。"""
    reply: dict[str, Any] = {"type": "pong"}
    if "t" in payload:
        reply["t"] = payload["t"]
    if "id" in payload:
        reply["id"] = payload["id"]
    return reply


def on_message(ws_app: websocket.WebSocketApp, message: str) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        logging.warning("ignored invalid JSON message")
        return

    if not isinstance(payload, dict):
        logging.warning("ignored non-object JSON message")
        return

    message_type = payload.get("type")
    if message_type == "ping":
        try:
            ws_app.send(json.dumps(build_pong_reply(payload), ensure_ascii=True))
        except Exception:
            logging.exception("failed to send pong")
        return

    if message_type != "clipboard":
        logging.warning("ignored message with unsupported type")
        return

    text = payload.get("text")
    if not isinstance(text, str):
        logging.warning("ignored clipboard message with non-string text")
        return

    logging.info("received clipboard text length=%d", len(text))
    try:
        pyperclip.copy(text)
    except Exception:
        logging.exception("failed to write clipboard")
        try:
            ws_app.send(json.dumps({"type": "clipboard_report", "status": "failed"}))
        except Exception:
            logging.exception("failed to send clipboard failure report")
        return

    logging.info("clipboard write succeeded length=%d", len(text))


def on_error(ws_app: websocket.WebSocketApp, error: Any) -> None:
    logging.error("websocket error: %s", error)


def on_close(
    ws_app: websocket.WebSocketApp,
    close_status_code: int | None,
    close_msg: str | None,
) -> None:
    logging.warning(
        "websocket closed status=%s message=%s",
        close_status_code,
        close_msg,
    )


def run(*, register_only: bool = False, config_path: Path | None = None) -> None:
    setup_logging()
    websocket.setdefaulttimeout(CONNECT_TIMEOUT_SECONDS)
    active_config_path = config_path or get_config_path()
    config = load_config(active_config_path)
    try:
        config = register_configured_device(config, active_config_path)
    except AuthenticationError as exc:
        logging.error("%s", exc)
        print(str(exc), file=sys.stderr)
        try:
            clear_password(active_config_path)
        except (OSError, ValueError, json.JSONDecodeError) as clear_exc:
            logging.error("failed to clear rejected password: %s", clear_exc)
        raise SystemExit(AUTHENTICATION_FAILURE_EXIT_CODE) from exc
    except RegistrationError as exc:
        logging.error("%s", exc)
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    if register_only:
        print(f"设备 {config['device_id']} 注册成功。")
        return
    reconnect_seconds = config["reconnect_seconds"]
    reconnect_attempts = 0

    while True:
        opened = False
        started_at = time.monotonic()

        def handle_open(ws_app: websocket.WebSocketApp) -> None:
            nonlocal opened
            opened = True
            on_open(ws_app)

        ws_app = websocket.WebSocketApp(
            config["server_ws_url"],
            header=[f"X-API-Key: {config['api_key']}"],
            on_open=handle_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        try:
            ws_app.run_forever(
                http_proxy_timeout=CONNECT_TIMEOUT_SECONDS,
                ping_interval=PING_INTERVAL_SECONDS,
                ping_timeout=PING_TIMEOUT_SECONDS,
                skip_utf8_validation=True,
            )
        except KeyboardInterrupt:
            logging.info("stopped by user")
            return
        except Exception:
            logging.exception("websocket run failed")

        connected_seconds = time.monotonic() - started_at
        if opened and connected_seconds >= STABLE_CONNECTION_SECONDS:
            reconnect_attempts = 0
            logging.info(
                "stable connection ended after %.1f seconds; reset reconnect attempts",
                connected_seconds,
            )
        else:
            reconnect_attempts += 1
            logging.warning(
                "reconnect attempt %d after short or failed connection opened=%s duration=%.1f seconds",
                reconnect_attempts,
                opened,
                connected_seconds,
            )

        logging.info("reconnecting in %.1f seconds", reconnect_seconds)
        time.sleep(reconnect_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Windows Clipboard Relay Agent.")
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--config", type=Path, default=get_config_path())
    arguments = parser.parse_args()
    run(register_only=arguments.register_only, config_path=arguments.config)
