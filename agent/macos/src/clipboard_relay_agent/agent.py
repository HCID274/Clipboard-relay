from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse, urlunparse
from urllib.request import Request, urlopen

import pyperclip
import websocket

from clipboard_relay_agent.config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    clear_password,
    load_config,
    save_device_id,
)

# 源码态：共享包在 agent/clipboard_relay_shared；wheel 态：经 packages 打进 site-packages。
def _ensure_shared_on_path() -> None:
    try:
        import clipboard_relay_shared  # noqa: F401
        return
    except ImportError:
        pass
    agent_root = Path(__file__).resolve().parents[3]
    if agent_root.name == "agent" and str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))


_ensure_shared_on_path()

from clipboard_relay_shared.device import (  # noqa: E402
    build_agent_ws_url,
    registration_url,
    suggested_device_id,
)
from clipboard_relay_shared.prompt import prompt_device_id  # noqa: E402


LOG_DIR = Path.home() / "Library" / "Logs" / "ClipboardRelay"
LOG_PATH = LOG_DIR / "agent.log"
STATUS_DIR = Path.home() / "Library" / "Application Support" / "ClipboardRelay"
STATUS_PATH = STATUS_DIR / "status.json"
LOGGER_NAME = "clipboard_relay_agent"
PING_INTERVAL_SECONDS = 30
PING_TIMEOUT_SECONDS = 10
CONNECT_TIMEOUT_SECONDS = 8
LOG_MAX_BYTES = 1_048_576
LOG_BACKUP_COUNT = 3
# 与 Windows Agent 一致：安装脚本用此退出码识别「密码错误并已/应清本地密码」。
AUTHENTICATION_FAILURE_EXIT_CODE = 3
STATIC_HOST_IPS = {
    "clip.hcid274.cn": "64.176.40.67",
}


class RegistrationError(RuntimeError):
    """服务端拒绝或无法完成设备注册时抛出。"""


class AuthenticationError(RegistrationError):
    """共享密码校验失败（HTTP 401）时抛出。"""


class StaticAddressHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, connect_host: str, tls_host: str, port: int, timeout: int) -> None:
        super().__init__(tls_host, port=port, timeout=timeout)
        self.connect_host = connect_host

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.connect_host, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


@dataclass(frozen=True)
class ConnectionTarget:
    url: str
    host: str | None
    sslopt: dict[str, str] | None


def build_connection_target(server_ws_url: str) -> ConnectionTarget:
    parsed = urlparse(server_ws_url)
    hostname = parsed.hostname
    if hostname is None:
        return ConnectionTarget(url=server_ws_url, host=None, sslopt=None)

    static_ip = STATIC_HOST_IPS.get(hostname)
    if static_ip is None:
        return ConnectionTarget(url=server_ws_url, host=None, sslopt=None)

    netloc = f"{static_ip}:{parsed.port}" if parsed.port is not None else static_ip

    rewritten_url = urlunparse(parsed._replace(netloc=netloc))
    return ConnectionTarget(
        url=rewritten_url,
        host=parsed.netloc,
        sslopt={"server_hostname": hostname},
    )


def build_headers(api_key: str) -> list[str]:
    return [f"X-API-Key: {api_key}"]


def _registration_error(status_code: int, reason: str, body: bytes) -> RegistrationError:
    try:
        detail = json.loads(body.decode("utf-8")).get("detail", reason)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        detail = reason
    # 401 单独成类，便于 main/安装脚本清本地密码并提示重输。
    error_type = AuthenticationError if status_code == 401 else RegistrationError
    return error_type(f"设备注册失败（HTTP {status_code}）：{detail}")


def send_registration_request(
    server_ws_url: str, password: str, device_id: str
) -> dict[str, Any]:
    url = registration_url(server_ws_url)
    parsed = urlparse(url)
    body = json.dumps({"device_id": device_id}).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-API-Key": password}
    static_ip = STATIC_HOST_IPS.get(parsed.hostname or "")

    try:
        if parsed.scheme == "https" and static_ip is not None and parsed.hostname is not None:
            connection = StaticAddressHTTPSConnection(
                static_ip,
                parsed.hostname,
                parsed.port or 443,
                CONNECT_TIMEOUT_SECONDS,
            )
            try:
                connection.request("POST", parsed.path, body=body, headers=headers)
                response = connection.getresponse()
                response_body = response.read()
                if response.status >= 400:
                    raise _registration_error(response.status, response.reason, response_body)
            finally:
                connection.close()
        else:
            request = Request(url, data=body, headers=headers, method="POST")
            try:
                with urlopen(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
                    response_body = response.read()
            except HTTPError as exc:
                raise _registration_error(exc.code, exc.reason, exc.read()) from exc
        payload = json.loads(response_body.decode("utf-8"))
    except RegistrationError:
        raise
    except (
        OSError,
        URLError,
        http.client.HTTPException,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise RegistrationError(f"设备注册失败：{exc}") from exc

    if not isinstance(payload, dict):
        raise RegistrationError("设备注册失败：服务端返回了无效响应")
    return payload


def register_configured_device(
    config: Config,
    config_path: Path,
    *,
    prompt: Callable[[str], str] | None = None,
    hostname: str | None = None,
) -> Config:
    device_id = config.device_id
    if device_id is None:
        suggestion = suggested_device_id(hostname or socket.gethostname())
        # prompt 可在测试中注入；生产环境使用共享的预填交互。
        device_id = (prompt or prompt_device_id)(suggestion)

    payload = send_registration_request(config.server_ws_url, config.api_key, device_id)
    registered_id = payload.get("device_id")
    if not isinstance(registered_id, str):
        raise RegistrationError("设备注册失败：服务端返回了无效响应")
    save_device_id(config_path, registered_id)
    return replace(config, device_id=registered_id)


def extract_device_id(server_ws_url: str) -> str:
    device_ids = parse_qs(urlparse(server_ws_url).query).get("device_id")
    return device_ids[0] if device_ids else ""


def update_status(
    *,
    status_path: Path | None = None,
    server_ws_url: str,
    connected: bool,
    event: str,
    reconnect_attempts: int,
    last_error: str | None = None,
    last_close_status: int | None = None,
    last_close_reason: str | None = None,
) -> None:
    status_path = status_path or STATUS_PATH
    status_path.parent.mkdir(parents=True, exist_ok=True)
    updated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    status: dict[str, Any] = {
        "connected": connected,
        "event": event,
        "server_ws_url": server_ws_url,
        "device_id": extract_device_id(server_ws_url),
        "updated_at": updated_at,
        "reconnect_attempts": reconnect_attempts,
        "pid": os.getpid(),
    }

    if last_error is not None:
        status["last_error"] = last_error
    if last_close_status is not None:
        status["last_close_status"] = last_close_status
    if last_close_reason is not None:
        status["last_close_reason"] = last_close_reason

    status_path.write_text(json.dumps(status, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def handle_message(
    message: str | bytes,
    *,
    copy_text: Callable[[str], None] = pyperclip.copy,
    logger: logging.Logger | None = None,
) -> dict[str, Any] | None:
    """处理服务端下行消息。

    * ``clipboard``：写入系统剪贴板。
    * ``ping``：返回 ``pong`` 载荷，供调用方立刻回传以测服务器↔本机 RTT。
    其它类型忽略。返回值非 None 时表示需要经 WebSocket 发回服务端。
    """
    active_logger = logger or logging.getLogger(LOGGER_NAME)

    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            active_logger.warning("ignored non-utf8 message")
            return None

    try:
        payload: Any = json.loads(message)
    except json.JSONDecodeError:
        active_logger.warning("ignored invalid JSON message")
        return None

    if not isinstance(payload, dict):
        active_logger.warning("ignored non-object JSON message")
        return None

    message_type = payload.get("type")
    if message_type == "ping":
        # 原样回传服务端带的 t，便于对账；RTT 以服务端本地时钟为准。
        reply: dict[str, Any] = {"type": "pong"}
        if "t" in payload:
            reply["t"] = payload["t"]
        if "id" in payload:
            reply["id"] = payload["id"]
        return reply

    if message_type != "clipboard":
        return None

    text = payload.get("text")
    if not isinstance(text, str):
        active_logger.warning("ignored clipboard message: clipboard text is not a string")
        return None

    try:
        copy_text(text)
    except Exception:
        active_logger.exception("failed to write clipboard")
        return None

    active_logger.info("clipboard updated length=%s", len(text))
    return None


def configure_logging(log_path: Path = LOG_PATH) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def run_agent(config: Config, logger: logging.Logger) -> None:
    websocket.setdefaulttimeout(CONNECT_TIMEOUT_SECONDS)
    reconnect_attempts = 0

    while True:
        current_reconnect_attempts = reconnect_attempts
        device_id = config.device_id or extract_device_id(config.server_ws_url)
        websocket_url = build_agent_ws_url(config.server_ws_url, device_id)
        connection_target = build_connection_target(websocket_url)

        def on_open(_ws: websocket.WebSocketApp) -> None:
            logger.info("connected to %s", websocket_url)
            update_status(
                server_ws_url=websocket_url,
                connected=True,
                event="connected",
                reconnect_attempts=current_reconnect_attempts,
            )

        def on_message(ws_app: websocket.WebSocketApp, msg: str) -> None:
            reply = handle_message(msg, logger=logger)
            if reply is not None:
                try:
                    ws_app.send(json.dumps(reply, ensure_ascii=True))
                except Exception:
                    logger.exception("failed to send pong")
            update_status(
                server_ws_url=websocket_url,
                connected=True,
                event="message",
                reconnect_attempts=current_reconnect_attempts,
            )

        def on_error(_ws: websocket.WebSocketApp, err: Any) -> None:
            logger.error("websocket error: %s", err)
            update_status(
                server_ws_url=websocket_url,
                connected=False,
                event="error",
                reconnect_attempts=current_reconnect_attempts,
                last_error=str(err),
            )

        def on_close(_ws: websocket.WebSocketApp, status: int | None, reason: str | None) -> None:
            logger.info("websocket closed status=%s reason=%s", status, reason)
            update_status(
                server_ws_url=websocket_url,
                connected=False,
                event="closed",
                reconnect_attempts=current_reconnect_attempts,
                last_close_status=status,
                last_close_reason=reason,
            )

        ws = websocket.WebSocketApp(
            connection_target.url,
            header=build_headers(config.api_key),
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        run_options: dict[str, Any] = {
            "http_proxy_timeout": CONNECT_TIMEOUT_SECONDS,
            "ping_interval": PING_INTERVAL_SECONDS,
            "ping_timeout": PING_TIMEOUT_SECONDS,
            "skip_utf8_validation": True,
        }
        if connection_target.host is not None:
            run_options["host"] = connection_target.host
        if connection_target.sslopt is not None:
            run_options["sslopt"] = connection_target.sslopt

        try:
            ws.run_forever(**run_options)
        except KeyboardInterrupt:
            logger.info("agent stopped by keyboard interrupt")
            return
        logger.info("reconnecting in %s seconds", config.reconnect_seconds)
        reconnect_attempts += 1
        time.sleep(config.reconnect_seconds)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the macOS Clipboard Relay Agent.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config.json. Defaults to {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--register-only",
        action="store_true",
        help="Register the configured device and exit without starting the WebSocket loop.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logger = configure_logging()

    try:
        config = load_config(args.config)
        config = register_configured_device(config, args.config)
    except AuthenticationError as exc:
        # 密码错误：清空本地密码，避免 LaunchAgent 用错误凭据无限重试。
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        try:
            clear_password(args.config)
            print(
                "共享密码被服务器拒绝，已从本地配置清除；"
                "请重新运行安装脚本并输入正确密码。",
                file=sys.stderr,
            )
        except ConfigError as clear_exc:
            logger.error("failed to clear rejected password: %s", clear_exc)
        return AUTHENTICATION_FAILURE_EXIT_CODE
    except (ConfigError, RegistrationError) as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 1

    if args.register_only:
        print(f"设备 {config.device_id} 注册成功。")
        return 0

    run_agent(config, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
