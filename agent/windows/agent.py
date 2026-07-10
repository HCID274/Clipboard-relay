import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import pyperclip
import websocket


EXPECTED_WS_URL = "wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka"
TASK_NAME = "ClipboardRelayAgent"
STABLE_CONNECTION_SECONDS = 60


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


def load_config() -> dict[str, Any]:
    config_path = Path(__file__).resolve().parent / "config.json"
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
    if server_ws_url != EXPECTED_WS_URL:
        logging.error("server_ws_url must be %s", EXPECTED_WS_URL)
        sys.exit(1)

    api_key = config.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        logging.error("api_key missing from config")
        sys.exit(1)
    api_key = api_key.strip()
    if api_key == "replace-with-shared-key":
        logging.error("api_key still uses placeholder value")
        sys.exit(1)

    reconnect_seconds = config.get("reconnect_seconds", 5)
    if not isinstance(reconnect_seconds, (int, float)) or reconnect_seconds <= 0:
        logging.error("reconnect_seconds must be a positive number")
        sys.exit(1)

    max_reconnect_attempts = config.get("max_reconnect_attempts", 60)
    if not isinstance(max_reconnect_attempts, int) or max_reconnect_attempts <= 0:
        logging.error("max_reconnect_attempts must be a positive integer")
        sys.exit(1)

    return {
        "server_ws_url": server_ws_url,
        "api_key": api_key,
        "reconnect_seconds": float(reconnect_seconds),
        "max_reconnect_attempts": max_reconnect_attempts,
    }


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


def run() -> None:
    setup_logging()
    config = load_config()
    reconnect_seconds = config["reconnect_seconds"]
    max_reconnect_attempts = config["max_reconnect_attempts"]
    reconnect_attempts = 0

    while reconnect_attempts < max_reconnect_attempts:
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
                "reconnect attempt %d/%d after short or failed connection opened=%s duration=%.1f seconds",
                reconnect_attempts,
                max_reconnect_attempts,
                opened,
                connected_seconds,
            )

        if reconnect_attempts >= max_reconnect_attempts:
            break

        logging.info("reconnecting in %.1f seconds", reconnect_seconds)
        time.sleep(reconnect_seconds)

    logging.error("max reconnect attempts reached; exiting")


if __name__ == "__main__":
    run()
