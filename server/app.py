import asyncio
import hmac
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PASSWORD = os.getenv("RELAY_PASSWORD", "")
API_KEY = os.getenv("API_KEY", "")
DEVICES_FILE = Path(os.getenv("DEVICES_FILE", BASE_DIR / "devices.json"))
LOGGER = logging.getLogger("clipboard_relay")
DEVICE_ID_PATTERN = re.compile(r"^[a-z0-9-]{3,32}$")


def _read_max_devices() -> int:
    raw_value = os.getenv("MAX_DEVICES", "10")
    try:
        value = int(raw_value)
    except ValueError:
        LOGGER.warning("invalid MAX_DEVICES=%r; using 10", raw_value)
        return 10
    if value < 1:
        LOGGER.warning("MAX_DEVICES must be positive; using 10")
        return 10
    return value


MAX_DEVICES = _read_max_devices()

INITIAL_TIMESTAMP = "2026-07-11T00:00:00Z"
INITIAL_DEVICES: dict[str, dict[str, str]] = {
    device_id: {
        "device_id": device_id,
        "created_at": INITIAL_TIMESTAMP,
        "last_active": INITIAL_TIMESTAMP,
    }
    for device_id in ("win-fukuoka", "mac-china")
}


def _write_device_file(path: Path, devices: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    records = sorted(devices.values(), key=lambda item: item["device_id"])
    temporary_path.write_text(
        json.dumps({"devices": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _load_devices(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload["devices"] if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            raise ValueError("device list must be an array")
        devices: dict[str, dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("device record must be an object")
            device_id = record.get("device_id")
            if not isinstance(device_id, str) or not DEVICE_ID_PATTERN.fullmatch(device_id):
                raise ValueError("device record has an invalid device_id")
            created_at = record.get("created_at")
            last_active = record.get("last_active")
            if not isinstance(created_at, str) or not isinstance(last_active, str):
                raise ValueError("device record has invalid timestamps")
            devices[device_id] = {
                "device_id": device_id,
                "created_at": created_at,
                "last_active": last_active,
            }
        return devices
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("failed to load device registry %s; starting empty: %s", path, exc)
        return {}


def _load_or_create_devices(path: Path) -> dict[str, dict[str, Any]]:
    if path.exists():
        return _load_devices(path)
    initial_devices = {
        device_id: record.copy() for device_id, record in INITIAL_DEVICES.items()
    }
    _write_device_file(path, initial_devices)
    return initial_devices


DEVICES = _load_or_create_devices(DEVICES_FILE)
device_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_devices(devices: dict[str, dict[str, Any]] | None = None) -> None:
    source = DEVICES if devices is None else devices
    _write_device_file(DEVICES_FILE, source)


def _credential_matches(candidate: str | None) -> bool:
    if candidate is None:
        return False
    return any(
        configured and hmac.compare_digest(candidate, configured)
        for configured in (PASSWORD, API_KEY)
    )


def check_api_key(candidate: str | None) -> None:
    if not PASSWORD and not API_KEY:
        raise HTTPException(status_code=500, detail="RELAY_PASSWORD or API_KEY is not configured")
    if not _credential_matches(candidate):
        raise HTTPException(status_code=401, detail="invalid password")


def _normalize_device_id(value: Any) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="invalid device_id")
    device_id = value.lower()
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise HTTPException(status_code=400, detail="invalid device_id")
    return device_id


app = FastAPI(title="Clipboard Relay")


class AgentConnections:
    def __init__(self) -> None:
        self.websockets: dict[str, WebSocket] = {}

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old_websocket = self.websockets.get(device_id)
        if old_websocket is not None:
            await old_websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        self.websockets[device_id] = websocket

    def disconnect(self, device_id: str, websocket: WebSocket) -> bool:
        if self.websockets.get(device_id) is not websocket:
            return False
        self.websockets.pop(device_id, None)
        return True

    async def remove(self, device_id: str) -> None:
        websocket = self.websockets.pop(device_id, None)
        if websocket is not None:
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)

    async def send_clipboard(self, device_id: str, text: str) -> None:
        websocket = self.websockets.get(device_id)
        if websocket is None:
            raise RuntimeError("agent offline")
        try:
            await websocket.send_json({"type": "clipboard", "text": text})
        except Exception:
            if self.websockets.get(device_id) is websocket:
                self.websockets.pop(device_id, None)
            raise RuntimeError("agent offline")


agents = AgentConnections()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/devices/register")
async def register_device(
    payload: dict = Body(...),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    check_api_key(x_api_key)
    device_id = _normalize_device_id(payload.get("device_id"))
    async with device_lock:
        existing = DEVICES.get(device_id)
        if existing is not None:
            return existing.copy()
        if len(DEVICES) >= MAX_DEVICES:
            raise HTTPException(status_code=403, detail="已达设备数上限")
        timestamp = _now()
        record = {
            "device_id": device_id,
            "created_at": timestamp,
            "last_active": timestamp,
        }
        updated_devices = {**DEVICES, device_id: record}
        _write_devices(updated_devices)
        DEVICES[device_id] = record
        return record.copy()


@app.get("/api/devices")
async def list_devices(
    x_api_key: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    check_api_key(x_api_key)
    async with device_lock:
        return [
            {**record, "online": device_id in agents.websockets}
            for device_id, record in sorted(DEVICES.items())
        ]


@app.delete("/api/devices/{device_id}")
async def delete_device(
    device_id: str,
    x_api_key: str | None = Header(default=None),
) -> dict[str, bool]:
    check_api_key(x_api_key)
    normalized_device_id = _normalize_device_id(device_id)
    async with device_lock:
        if normalized_device_id not in DEVICES:
            raise HTTPException(status_code=404, detail="device not found")
        updated_devices = DEVICES.copy()
        updated_devices.pop(normalized_device_id)
        _write_devices(updated_devices)
        DEVICES.pop(normalized_device_id)
        await agents.remove(normalized_device_id)
    return {"ok": True}


@app.post("/api/send")
async def send_text(
    payload: dict = Body(...),
    x_api_key: str | None = Header(default=None),
) -> dict[str, bool]:
    check_api_key(x_api_key)
    target = payload.get("target")
    text = payload.get("text")
    if not isinstance(target, str) or target not in DEVICES:
        raise HTTPException(status_code=400, detail="invalid target")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    try:
        await agents.send_clipboard(target, text)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="target device is not connected")
    return {"ok": True}


@app.get("/api/status")
async def connection_status(
    x_api_key: str | None = Header(default=None),
) -> dict[str, dict[str, bool]]:
    check_api_key(x_api_key)
    async with device_lock:
        return {
            "devices": {
                device_id: device_id in agents.websockets for device_id in DEVICES
            }
        }


@app.websocket("/ws/agent")
async def websocket_agent(websocket: WebSocket) -> None:
    api_key = websocket.headers.get("x-api-key")
    if not (PASSWORD or API_KEY) or not _credential_matches(api_key):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        device_id = _normalize_device_id(websocket.query_params.get("device_id"))
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    async with device_lock:
        if device_id not in DEVICES:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await agents.connect(device_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if agents.disconnect(device_id, websocket):
            async with device_lock:
                record = DEVICES.get(device_id)
                if record is not None:
                    last_active = _now()
                    updated_devices = {
                        key: value.copy() for key, value in DEVICES.items()
                    }
                    updated_devices[device_id]["last_active"] = last_active
                    _write_devices(updated_devices)
                    record["last_active"] = last_active
