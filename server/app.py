import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
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


async def reject_websocket(websocket: WebSocket) -> None:
    """在 WebSocket 握手完成后关闭被拒绝的连接，避免未 accept 就 close。"""
    await websocket.accept()
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


def _is_websocket_not_connected_error(error: RuntimeError) -> bool:
    """Starlette may raise RuntimeError when a server-closed socket is read again."""
    return 'WebSocket is not connected. Need to call "accept" first.' in str(error)


# 应用层 RTT：服务端发 ping、Agent 回 pong；列表接口返回缓存的 latency_ms。
# 测速（软）与判死（硬）分离：单次超时只清空 RTT；连续失败才踢半开连接。
# 间隔约 1s 保证 UI 刷新体感；超时放宽以容纳跨境抖动（见社区心跳惯例）。
LATENCY_PROBE_INTERVAL_SECONDS = 1.0
LATENCY_PROBE_TIMEOUT_SECONDS = 3.0
LATENCY_PROBE_MAX_FAILURES = 3
UI_TICKET_TTL_SECONDS = 60.0
MAX_UI_CLIENTS = 32


class UiTickets:
    """浏览器用共享密码换取的一次性短期票据，票据绝不包含共享密码。"""

    def __init__(self) -> None:
        self._tickets: dict[str, float] = {}

    def _discard_expired(self, now: float) -> None:
        for token, expires_at in tuple(self._tickets.items()):
            if expires_at <= now:
                self._tickets.pop(token, None)

    def issue(self) -> str:
        now = time.monotonic()
        self._discard_expired(now)
        # 票据存储同样有上限，避免反复登录请求无限占用单 worker 的内存。
        while len(self._tickets) >= MAX_UI_CLIENTS * 4:
            oldest_token = next(iter(self._tickets))
            self._tickets.pop(oldest_token, None)
        token = uuid.uuid4().hex
        self._tickets[token] = now + UI_TICKET_TTL_SECONDS
        return token

    def consume(self, token: str | None) -> bool:
        if not isinstance(token, str):
            return False
        now = time.monotonic()
        self._discard_expired(now)
        expires_at = self._tickets.pop(token, None)
        return expires_at is not None and expires_at > now


@dataclass(eq=False)
class _UiClient:
    """每个浏览器拥有一个只保留最新快照的有界队列。"""

    websocket: WebSocket
    queue: asyncio.Queue[dict[str, Any]]
    sender_task: asyncio.Task[None]


class UiConnections:
    """浏览器状态订阅者；慢客户端只会丢弃旧快照，不会拖慢其他客户端。"""

    def __init__(self) -> None:
        self._clients: set[_UiClient] = set()
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        snapshot_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> _UiClient | None:
        await websocket.accept()
        async with self._lock:
            if len(self._clients) < MAX_UI_CLIENTS:
                # 初始快照的生成、客户端入订和后续推送共用此锁，避免新客户端错过中间版本。
                snapshot = await snapshot_factory()
                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
                sender_task = asyncio.create_task(
                    self._send_loop(websocket, queue), name="ui-snapshot-sender"
                )
                client = _UiClient(websocket, queue, sender_task)
                self._clients.add(client)
                queue.put_nowait(snapshot)
                return client
        # 不在管理锁中等待关闭帧，避免异常浏览器阻塞其他 UI 客户端的连接管理。
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return None

    async def disconnect(self, client: _UiClient) -> None:
        async with self._lock:
            self._clients.discard(client)
        client.sender_task.cancel()

    async def publish(
        self, snapshot_factory: Callable[[], Awaitable[dict[str, Any]]]
    ) -> None:
        """在订阅锁内生成并投递最新快照，投递过程不会等待网络 I/O。"""
        async with self._lock:
            self._publish_locked(await snapshot_factory())

    def _publish_locked(self, snapshot: dict[str, Any]) -> None:
        """将快照放入所有客户端的单槽队列；调用方必须持有订阅锁。"""
        for client in tuple(self._clients):
            if client.queue.full():
                try:
                    client.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                client.queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                # 发送协程可能恰好取走了旧消息；下一次状态变化会再次投递快照。
                pass

    async def _send_loop(
        self, websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        try:
            while True:
                await websocket.send_json(await queue.get())
        except (WebSocketDisconnect, asyncio.CancelledError):
            raise
        except Exception:
            # 浏览器断开或网络发送失败时，发送协程自行退出并回收客户端槽位。
            return
        finally:
            sender_task = asyncio.current_task()
            async with self._lock:
                for client in tuple(self._clients):
                    if client.sender_task is sender_task:
                        self._clients.discard(client)


ui_tickets = UiTickets()
ui_clients = UiConnections()


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
        # 连续测速失败次数；成功清零，达到阈值才踢连接。
        self._probe_failures: dict[str, int] = {}

    def _cancel_pending(self, device_id: str) -> None:
        pending = self._pending_pings.pop(device_id, None)
        if pending is not None and not pending.future.done():
            pending.future.cancel()

    def _clear_latency_state(self, device_id: str) -> None:
        self._cancel_pending(device_id)
        self.latency_ms[device_id] = None

    def _reset_probe_failures(self, device_id: str) -> None:
        self._probe_failures.pop(device_id, None)

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old_websocket = self.websockets.get(device_id)
        # 必须先作废探测并切换表项，再 await close 旧连接。
        # 若先 close，await 让出期间旧连接的匹配 pong 仍可能完成旧探测。
        self._clear_latency_state(device_id)
        self._reset_probe_failures(device_id)
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
        self._reset_probe_failures(device_id)
        return True

    async def remove(self, device_id: str) -> None:
        websocket = self.websockets.pop(device_id, None)
        self._clear_latency_state(device_id)
        self._reset_probe_failures(device_id)
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
        # 后台周期和新连接的立即探测可能重叠；已有探测时绝不能取消并重发。
        if device_id in self._pending_pings:
            return None

        websocket = self.websockets.get(device_id)
        if websocket is None:
            self.latency_ms[device_id] = None
            return None
        # 真实 Starlette WebSocket 必然提供 send_json；此分支让测试替身或内部错误不被误判为
        # Agent 网络半开，从而避免服务端因为自身调用错误踢掉有效连接。
        if not callable(getattr(websocket, "send_json", None)):
            LOGGER.warning("agent websocket %s has no send_json method", device_id)
            return None

        loop = asyncio.get_running_loop()
        probe_id = uuid.uuid4().hex
        sent_at = time.perf_counter()
        pending = _PendingPing(
            future=loop.create_future(),
            probe_id=probe_id,
            websocket=websocket,
        )
        self._pending_pings[device_id] = pending

        async def send_ping_and_wait_for_pong() -> None:
            """在同一超时预算内完成 ping 发送和匹配 pong 等待。"""
            await websocket.send_json(
                {"type": "ping", "id": probe_id, "t": sent_at}
            )
            await pending.future

        try:
            # 半开连接可能让发送永久阻塞；超时必须同时约束发送和 pong 等待。
            await asyncio.wait_for(send_ping_and_wait_for_pong(), timeout=timeout)
            # 若测速过程中连接被替换，丢弃结果（避免把旧会话 RTT 记到新连接）。
            if self.websockets.get(device_id) is not websocket:
                self.latency_ms[device_id] = None
                return None
            ms = max(0, int(round((time.perf_counter() - sent_at) * 1000)))
            self.latency_ms[device_id] = ms
            self._probe_failures[device_id] = 0
            await publish_device_snapshot()
            return ms
        except asyncio.CancelledError:
            # 重连/删除取消了 pending.future 时，wait_for 会抛 CancelledError。
            if self.websockets.get(device_id) is websocket:
                self.latency_ms[device_id] = None
            return None
        except Exception:
            # 单次超时：只清 RTT 并计数；连续失败才踢，避免跨境抖动误杀在线 Agent。
            if self.websockets.get(device_id) is websocket:
                self.latency_ms[device_id] = None
                failures = self._probe_failures.get(device_id, 0) + 1
                self._probe_failures[device_id] = failures
                await publish_device_snapshot()
                if failures >= LATENCY_PROBE_MAX_FAILURES:
                    LOGGER.warning(
                        "agent %s probe failed %s times; marking unavailable",
                        device_id,
                        failures,
                    )
                    await mark_agent_unavailable(device_id, websocket)
            return None
        finally:
            if self._pending_pings.get(device_id) is pending:
                self._pending_pings.pop(device_id, None)


agents = AgentConnections()
device_state_version = 0


def _device_records_locked(*, fresh_online_last_active: bool = True) -> list[dict[str, Any]]:
    """在 device_lock 保护下生成浏览器和 REST 共用的实时设备记录。"""
    any_online = any(device_id in agents.websockets for device_id in DEVICES)
    now = _now() if fresh_online_last_active and any_online else None
    records: list[dict[str, Any]] = []
    for device_id, record in sorted(DEVICES.items()):
        online = device_id in agents.websockets
        records.append(
            {
                **record,
                "last_active": now if online and now is not None else record["last_active"],
                "online": online,
                "latency_ms": agents.latency_ms.get(device_id) if online else None,
            }
        )
    return records


async def device_snapshot() -> dict[str, Any]:
    """生成带单调版本号的全量快照，客户端可以安全地忽略乱序旧消息。"""
    global device_state_version
    async with device_lock:
        device_state_version += 1
        return {
            "type": "devices_snapshot",
            "version": device_state_version,
            # 推送快照不需要为了展示“当前时刻”消耗时间戳；连接、断开和注册仍会持久化它。
            "devices": _device_records_locked(fresh_online_last_active=False),
        }


async def publish_device_snapshot() -> None:
    """在 UI 订阅锁内生成并投递全量快照，保证建连不会漏掉版本。"""
    await ui_clients.publish(device_snapshot)


async def mark_agent_unavailable(device_id: str, websocket: WebSocket) -> None:
    """探测失败时撤销当前连接并关闭套接字，修复半开连接造成的假在线。"""
    async with device_lock:
        if not agents.disconnect(device_id, websocket):
            return
        _update_last_active(device_id)
    try:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
    except Exception as close_error:
        LOGGER.warning("failed to close timed-out agent websocket %s: %s", device_id, close_error)
    await publish_device_snapshot()


async def _latency_probe_loop() -> None:
    """后台周期性探测所有在线 Agent 的服务器↔设备 RTT。"""
    while True:
        try:
            await asyncio.sleep(LATENCY_PROBE_INTERVAL_SECONDS)
            online_ids = list(agents.websockets)
            if not online_ids:
                continue
            for device_id in online_ids:
                # measure_latency 会自行拒绝重叠探测；每轮 fire-and-forget，间隔由 sleep 控制。
                asyncio.create_task(
                    agents.measure_latency(device_id), name=f"latency-{device_id}"
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


@app.post("/api/ui-ticket")
async def create_ui_ticket(
    x_api_key: str | None = Header(default=None),
) -> dict[str, str]:
    """浏览器使用 HTTP 头中的密码换取一次性短期 WebSocket 票据。"""
    check_api_key(x_api_key)
    return {"ticket": ui_tickets.issue()}


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
        created_record = record.copy()
    await publish_device_snapshot()
    return created_record


@app.get("/api/devices")
async def list_devices(
    x_api_key: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    check_api_key(x_api_key)
    async with device_lock:
        return _device_records_locked()


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
    await publish_device_snapshot()
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
        await reject_websocket(websocket)
        return
    try:
        device_id = _normalize_device_id(websocket.query_params.get("device_id"))
    except HTTPException:
        await reject_websocket(websocket)
        return
    async with device_lock:
        if device_id not in DEVICES:
            await reject_websocket(websocket)
            return
        await agents.connect(device_id, websocket)
        _update_last_active(device_id)
    await publish_device_snapshot()
    # 连接建立后尽快测一次 RTT，不必等后台周期。
    asyncio.create_task(agents.measure_latency(device_id), name=f"latency-{device_id}")
    try:
        while True:
            # 消费 Agent 上行（pong 等）；剪贴板只由服务端下发。
            try:
                raw = await websocket.receive_text()
            except RuntimeError as error:
                if _is_websocket_not_connected_error(error):
                    raise WebSocketDisconnect(code=status.WS_1006_ABNORMAL_CLOSURE) from error
                raise
            agents.handle_agent_text(device_id, raw, websocket)
    except WebSocketDisconnect:
        async with device_lock:
            if agents.disconnect(device_id, websocket):
                _update_last_active(device_id)
                disconnected = True
            else:
                disconnected = False
        if disconnected:
            await publish_device_snapshot()


@app.websocket("/ws/ui")
async def websocket_ui(websocket: WebSocket) -> None:
    """同源浏览器通过一次性短期票据订阅设备全量快照。"""
    if not ui_tickets.consume(websocket.query_params.get("ticket")):
        await reject_websocket(websocket)
        return
    client = await ui_clients.connect(websocket, device_snapshot)
    if client is None:
        return
    try:
        while True:
            # 浏览器无需发送业务消息；持续 receive 用于感知断开并回收发送协程。
            try:
                await websocket.receive_text()
            except RuntimeError as error:
                if _is_websocket_not_connected_error(error):
                    raise WebSocketDisconnect(code=status.WS_1006_ABNORMAL_CLOSURE) from error
                raise
    except WebSocketDisconnect:
        await ui_clients.disconnect(client)
