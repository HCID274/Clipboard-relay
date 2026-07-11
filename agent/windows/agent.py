import argparse
import json
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import pyperclip
import websocket


TASK_NAME = "ClipboardRelayAgent"
STABLE_CONNECTION_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 8
DEVICE_ID_REPLACEMENT_PATTERN = re.compile(r"[^a-z0-9-]+")


class RegistrationError(RuntimeError):
    pass


def get_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.json"


def get_log_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        base = Path.home() / "AppData" / "Roaming"
    return base / "ClipboardRelay" / "agent.log"


def setup_logging() -> None:
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
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
    if not isinstance(server_ws_url, str) or urlparse(server_ws_url).scheme not in {"ws", "wss"}:
        logging.error("server_ws_url must be a ws:// or wss:// URL")
        sys.exit(1)

    api_key = config.get("password", config.get("api_key"))
    if not isinstance(api_key, str) or not api_key.strip():
        logging.error("password missing from config")
        sys.exit(1)
    api_key = api_key.strip()
    if not api_key.isascii():
        logging.error("password must contain only ASCII characters")
        sys.exit(1)
    if api_key in {"replace-with-shared-key", "replace-with-relay-password"}:
        logging.error("password still uses placeholder value")
        sys.exit(1)

    device_id = config.get("device_id")
    if device_id is None:
        legacy_device_ids = parse_qs(urlparse(server_ws_url).query).get("device_id")
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


def registration_url(server_ws_url: str) -> str:
    parsed = urlparse(server_ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, parsed.netloc, "/api/devices/register", "", "", ""))


def build_agent_ws_url(server_ws_url: str, device_id: str) -> str:
    parsed = urlparse(server_ws_url)
    query = parse_qs(parsed.query)
    query["device_id"] = [device_id]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def suggested_device_id(hostname: str) -> str:
    suggestion = DEVICE_ID_REPLACEMENT_PATTERN.sub("-", hostname.lower()).strip("-")
    suggestion = suggestion[:32].rstrip("-")
    return suggestion if len(suggestion) >= 3 else "my-device"


def save_device_id(config_path: Path, device_id: str) -> None:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["device_id"] = device_id
    temporary_path = config_path.with_name(f".{config_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_path.replace(config_path)


def register_configured_device(
    config: dict[str, Any], config_path: Path
) -> dict[str, Any]:
    device_id = config.get("device_id")
    if device_id is None:
        suggestion = suggested_device_id(socket.gethostname())
        entered = input(f"设备名称 [{suggestion}]: ").strip()
        device_id = entered or suggestion

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
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", exc.reason)
        except (json.JSONDecodeError, AttributeError):
            detail = exc.reason
        raise RegistrationError(f"设备注册失败（HTTP {exc.code}）：{detail}") from exc
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


def on_message(ws_app: websocket.WebSocketApp, message: str) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        logging.warning("ignored invalid JSON message")
        return

    if not isinstance(payload, dict):
        logging.warning("ignored non-object JSON message")
        return

    if payload.get("type") != "clipboard":
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
    active_config_path = config_path or get_config_path()
    config = load_config(active_config_path)
    try:
        config = register_configured_device(config, active_config_path)
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
            ws_app.run_forever()
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
