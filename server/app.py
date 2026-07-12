import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
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


def _update_last_active(device_id: str, timestamp: str | None = None) -> None:
    """更新内存中的 last_active，并尽量持久化到磁盘。

    调用方须持有 device_lock。写盘失败只记日志、不抛异常，以便连接/断开流程仍能完成
    （在线/离线状态仍正确）。
    """
    record = DEVICES.get(device_id)
    if record is None:
        return
    record["last_active"] = _now() if timestamp is None else timestamp
    try:
        _write_devices()
    except OSError as error:
        LOGGER.warning(
            "failed to persist last_active for device %s: %s",
            device_id,
            error,
        )


def _ascii_bytes(value: str) -> bytes | None:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        return None


def _credential_matches(candidate: str | None) -> bool:
    if candidate is None:
        return False
    candidate_bytes = _ascii_bytes(candidate.strip())
    if candidate_bytes is None:
        return False
    for configured in (PASSWORD, API_KEY):
        configured = configured.strip()
        if not configured:
            continue
        configured_bytes = _ascii_bytes(configured)
        if configured_bytes is not None and hmac.compare_digest(
            candidate_bytes, configured_bytes
        ):
            return True
    return False


def _auth_configured() -> bool:
    return bool(PASSWORD.strip() or API_KEY.strip())


def check_api_key(candidate: str | None) -> None:
    if not _auth_configured():
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


# 应用层 RTT：服务端发 ping、Agent 回 pong；列表接口返回缓存的 latency_ms。
LATENCY_PROBE_INTERVAL_SECONDS = 3.0
LATENCY_PROBE_TIMEOUT_SECONDS = 1.5


@dataclass
class _PendingPing:
    """一次进行中的 RTT 探测：必须由「当前连接 + 匹配 probe_id」的 pong 完成。"""

    future: asyncio.Future[None]
    probe_id: str
    websocket: WebSocket


class AgentConnections:
    """在线 Agent 的 WebSocket 连接表，以及每台设备最近一次测得的 RTT。"""

    def __init__(self) -> None:
        self.websockets: dict[str, WebSocket] = {}
        # 最近一次成功 ping/pong 的往返毫秒数；离线或超时则为缺失/None。
        self.latency_ms: dict[str, int | None] = {}
        # 每台设备最多一个进行中的测速；键为 device_id。
        self._pending_pings: dict[str, _PendingPing] = {}

    def _cancel_pending(self, device_id: str) -> None:
        pending = self._pending_pings.pop(device_id, None)
        if pending is not None and not pending.future.done():
            pending.future.cancel()

    def _clear_latency_state(self, device_id: str) -> None:
        self._cancel_pending(device_id)
        self.latency_ms.pop(device_id, None)

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old_websocket = self.websockets.get(device_id)
        # 必须先作废探测并切换表项，再 await close 旧连接。
        # 若先 close，await 让出期间旧连接的匹配 pong 仍可能完成旧探测。
        self._clear_latency_state(device_id)
        self.websockets[device_id] = websocket
        if old_websocket is not None and old_websocket is not websocket:
            try:
                await old_websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            except Exception as close_error:
                LOGGER.warning(
                    "failed to close replaced agent websocket %s: %s",
                    device_id,
                    close_error,
                )

    def disconnect(self, device_id: str, websocket: WebSocket) -> bool:
        if self.websockets.get(device_id) is not websocket:
            return False
        self.websockets.pop(device_id, None)
        self._clear_latency_state(device_id)
        return True

    async def remove(self, device_id: str) -> None:
        websocket = self.websockets.pop(device_id, None)
        self._clear_latency_state(device_id)
        if websocket is not None:
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)

    async def send_clipboard(self, device_id: str, text: str) -> None:
        websocket = self.websockets.get(device_id)
        if websocket is None:
            raise RuntimeError("agent offline")
        try:
            await websocket.send_json({"type": "clipboard", "text": text})
        except Exception:
            try:
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            except Exception as close_error:
                LOGGER.warning(
                    "failed to close agent websocket %s after send failure: %s",
                    device_id,
                    close_error,
                )
            raise RuntimeError("agent offline")

    def handle_agent_text(
        self, device_id: str, raw: str, websocket: WebSocket
    ) -> None:
        """处理 Agent 上行文本；仅当 pong 来自当前连接且 probe_id 匹配时完成测速。"""
        # 旧连接被替换后仍可能收到迟到帧：必须与当前表中的 socket 同一对象。
        if self.websockets.get(device_id) is not websocket:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict) or payload.get("type") != "pong":
            return
        pending = self._pending_pings.get(device_id)
        if pending is None or pending.future.done():
            return
        if pending.websocket is not websocket:
            return
        if payload.get("id") != pending.probe_id:
            return
        pending.future.set_result(None)

    async def measure_latency(
        self,
        device_id: str,
        *,
        timeout: float = LATENCY_PROBE_TIMEOUT_SECONDS,
    ) -> int | None:
        """对在线 Agent 发应用层 ping，等待匹配 pong，返回整毫秒 RTT；失败返回 None。"""
        websocket = self.websockets.get(device_id)
        if websocket is None:
            self.latency_ms.pop(device_id, None)
            return None

        self._cancel_pending(device_id)

        loop = asyncio.get_running_loop()
        probe_id = uuid.uuid4().hex
        sent_at = time.perf_counter()
        pending = _PendingPing(
            future=loop.create_future(),
            probe_id=probe_id,
            websocket=websocket,
        )
        self._pending_pings[device_id] = pending
        try:
            await websocket.send_json(
                {"type": "ping", "id": probe_id, "t": sent_at}
            )
            await asyncio.wait_for(pending.future, timeout=timeout)
            # 若测速过程中连接被替换，丢弃结果（避免把旧会话 RTT 记到新连接）。
            if self.websockets.get(device_id) is not websocket:
                self.latency_ms[device_id] = None
                return None
            ms = max(0, int(round((time.perf_counter() - sent_at) * 1000)))
            self.latency_ms[device_id] = ms
            return ms
        except asyncio.CancelledError:
            # 重连/删除取消了 pending.future 时，wait_for 会抛 CancelledError。
            if self.websockets.get(device_id) is websocket:
                self.latency_ms[device_id] = None
            return None
        except Exception:
            # 超时、发送失败：不保留过期延迟，前端显示 "—"。
            if self.websockets.get(device_id) is websocket:
                self.latency_ms[device_id] = None
            return None
        finally:
            if self._pending_pings.get(device_id) is pending:
                self._pending_pings.pop(device_id, None)


agents = AgentConnections()


async def _latency_probe_loop() -> None:
    """后台周期性探测所有在线 Agent 的服务器↔设备 RTT。"""
    while True:
        try:
            await asyncio.sleep(LATENCY_PROBE_INTERVAL_SECONDS)
            online_ids = list(agents.websockets)
            if not online_ids:
                continue
            await asyncio.gather(
                *(agents.measure_latency(device_id) for device_id in online_ids),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("latency probe loop error: %s", error)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    probe_task = asyncio.create_task(_latency_probe_loop(), name="latency-probe")
    try:
        yield
    finally:
        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Clipboard Relay", lifespan=lifespan)


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
        # 在线设备在列表接口中报告「当前仍活跃」，但不因每次轮询写盘；
        # 离线设备保留上次持久化的时间戳。
        # latency_ms 为服务器↔Agent 的应用层 RTT（后台探测缓存）；离线为 null。
        any_online = any(device_id in agents.websockets for device_id in DEVICES)
        now = _now() if any_online else None
        result: list[dict[str, Any]] = []
        for device_id, record in sorted(DEVICES.items()):
            online = device_id in agents.websockets
            latency = agents.latency_ms.get(device_id) if online else None
            result.append(
                {
                    **record,
                    "last_active": now if online else record["last_active"],
                    "online": online,
                    "latency_ms": latency,
                }
            )
        return result


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
    if not _auth_configured() or not _credential_matches(api_key):
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
        _update_last_active(device_id)
    # 连接建立后尽快测一次 RTT，不必等后台周期。
    asyncio.create_task(agents.measure_latency(device_id), name=f"latency-{device_id}")
    try:
        while True:
            # 消费 Agent 上行（pong 等）；剪贴板只由服务端下发。
            raw = await websocket.receive_text()
            agents.handle_agent_text(device_id, raw, websocket)
    except WebSocketDisconnect:
        async with device_lock:
            if agents.disconnect(device_id, websocket):
                _update_last_active(device_id)
