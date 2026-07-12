import asyncio
import json

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

import app as relay_app


PASSWORD = "ui-test-password"
TIMESTAMP = "2026-07-12T00:00:00Z"


def record(device_id: str) -> dict[str, str]:
    return {
        "device_id": device_id,
        "created_at": TIMESTAMP,
        "last_active": TIMESTAMP,
    }


def clear_server_state() -> None:
    relay_app.DEVICES.clear()
    relay_app.agents.websockets.clear()
    relay_app.agents.latency_ms.clear()
    relay_app.agents._pending_pings.clear()
    relay_app.ui_tickets._tickets.clear()
    relay_app.ui_clients._clients.clear()
    relay_app.device_state_version = 0


@pytest.fixture(autouse=True)
def reset_server_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay_app, "PASSWORD", PASSWORD)
    monkeypatch.setattr(relay_app, "API_KEY", "")
    monkeypatch.setattr(relay_app, "DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(relay_app, "device_lock", asyncio.Lock())
    clear_server_state()
    yield
    clear_server_state()


@pytest.fixture
def client() -> TestClient:
    with TestClient(relay_app.app) as test_client:
        yield test_client


def headers(password: str = PASSWORD) -> dict[str, str]:
    return {"X-API-Key": password}


def issue_ticket(client: TestClient) -> str:
    response = client.post("/api/ui-ticket", headers=headers())
    assert response.status_code == 200
    return response.json()["ticket"]


def test_ui_ticket_authentication_and_initial_snapshot(client: TestClient) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")

    assert client.post("/api/ui-ticket", headers=headers("wrong")).status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/ui?ticket=not-a-ticket"):
            pass
    assert exc_info.value.code == 1008

    ticket = issue_ticket(client)
    with client.websocket_connect(f"/ws/ui?ticket={ticket}") as websocket:
        snapshot = websocket.receive_json()

    assert snapshot == {
        "type": "devices_snapshot",
        "version": 1,
        "devices": [
            {
                **record("mac-china"),
                "online": False,
                "latency_ms": None,
            }
        ],
    }
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/ui?ticket={ticket}"):
            pass
    assert exc_info.value.code == 1008


def test_ui_subscription_serializes_initial_snapshot_with_publish() -> None:
    """建连中的初始快照和并发推送必须在同一订阅锁下按版本完成。"""

    class FakeWebSocket:
        def __init__(self) -> None:
            self.accepted = False
            self.sent_versions: list[int] = []
            self.version_two_sent = asyncio.Event()

        async def accept(self) -> None:
            self.accepted = True

        async def send_json(self, payload: dict) -> None:
            self.sent_versions.append(payload["version"])
            if payload["version"] == 2:
                self.version_two_sent.set()

        async def close(self, code: int) -> None:
            raise AssertionError(f"unexpected close with code {code}")

    async def verify_subscription_order() -> None:
        connections = relay_app.UiConnections()
        websocket = FakeWebSocket()
        initial_snapshot_started = asyncio.Event()
        allow_initial_snapshot = asyncio.Event()
        published_snapshot_started = asyncio.Event()

        async def initial_snapshot() -> dict:
            initial_snapshot_started.set()
            await allow_initial_snapshot.wait()
            return {"version": 1}

        async def published_snapshot() -> dict:
            published_snapshot_started.set()
            return {"version": 2}

        connecting = asyncio.create_task(connections.connect(websocket, initial_snapshot))
        await initial_snapshot_started.wait()
        publishing = asyncio.create_task(connections.publish(published_snapshot))
        await asyncio.sleep(0)
        assert not published_snapshot_started.is_set()

        allow_initial_snapshot.set()
        connected_client = await connecting
        assert connected_client is not None
        await publishing
        await asyncio.wait_for(websocket.version_two_sent.wait(), timeout=1)

        assert websocket.accepted is True
        assert websocket.sent_versions[-1] == 2
        await connections.disconnect(connected_client)

    asyncio.run(verify_subscription_order())


def test_ui_receives_registration_and_agent_connect_disconnect_snapshots(
    client: TestClient,
) -> None:
    with client.websocket_connect(f"/ws/ui?ticket={issue_ticket(client)}") as ui_websocket:
        initial = ui_websocket.receive_json()
        assert initial["devices"] == []

        registered = client.post(
            "/api/devices/register",
            headers=headers(),
            json={"device_id": "mac-china"},
        )
        assert registered.status_code == 200
        registered_snapshot = ui_websocket.receive_json()
        assert registered_snapshot["devices"][0]["online"] is False

        with client.websocket_connect(
            "/ws/agent?device_id=mac-china", headers=headers()
        ):
            connected_snapshot = ui_websocket.receive_json()
            assert connected_snapshot["devices"][0]["online"] is True

        disconnected_snapshot = ui_websocket.receive_json()
        assert disconnected_snapshot["devices"][0]["online"] is False

        deleted = client.delete("/api/devices/mac-china", headers=headers())
        assert deleted.status_code == 200
        deleted_snapshot = ui_websocket.receive_json()
        assert deleted_snapshot["devices"] == []


def test_probe_timeout_marks_agent_offline_and_closes_socket() -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")

    class SilentWebSocket:
        def __init__(self) -> None:
            self.close_codes: list[int] = []

        async def send_json(self, _payload: dict) -> None:
            return None

        async def close(self, code: int) -> None:
            self.close_codes.append(code)

    async def measure_timeout() -> SilentWebSocket:
        websocket = SilentWebSocket()
        relay_app.agents.websockets["mac-china"] = websocket  # type: ignore[assignment]
        assert await relay_app.agents.measure_latency("mac-china", timeout=0.01) is None
        return websocket

    websocket = asyncio.run(measure_timeout())

    assert "mac-china" not in relay_app.agents.websockets
    assert websocket.close_codes == [1011]


def test_probe_timeout_during_blocked_ping_send_marks_agent_offline() -> None:
    """ping 发送在半开连接中阻塞时，探测也必须在预算内下线该设备。"""
    relay_app.DEVICES["mac-china"] = record("mac-china")

    class BlockingSendWebSocket:
        def __init__(self) -> None:
            self.send_started = asyncio.Event()
            self.close_codes: list[int] = []

        async def send_json(self, _payload: dict) -> None:
            self.send_started.set()
            await asyncio.Event().wait()

        async def close(self, code: int) -> None:
            self.close_codes.append(code)

    async def measure_timeout() -> BlockingSendWebSocket:
        websocket = BlockingSendWebSocket()
        relay_app.agents.websockets["mac-china"] = websocket  # type: ignore[assignment]
        assert await relay_app.agents.measure_latency("mac-china", timeout=0.01) is None
        assert websocket.send_started.is_set()
        return websocket

    websocket = asyncio.run(measure_timeout())

    assert "mac-china" not in relay_app.agents.websockets
    assert websocket.close_codes == [1011]


def test_latency_probe_does_not_overlap_for_one_device() -> None:
    class BlockingWebSocket:
        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.send_started = asyncio.Event()
            self.allow_send = asyncio.Event()

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)
            self.send_started.set()
            await self.allow_send.wait()

    async def measure_once() -> None:
        websocket = BlockingWebSocket()
        relay_app.agents.websockets["mac-china"] = websocket  # type: ignore[assignment]
        first_probe = asyncio.create_task(
            relay_app.agents.measure_latency("mac-china", timeout=1.0)
        )
        await websocket.send_started.wait()
        assert await relay_app.agents.measure_latency("mac-china", timeout=1.0) is None
        assert len(websocket.sent) == 1
        websocket.allow_send.set()
        await asyncio.sleep(0)
        relay_app.agents.handle_agent_text(
            "mac-china",
            json.dumps({"type": "pong", "id": websocket.sent[0]["id"]}),
            websocket,  # type: ignore[arg-type]
        )
        assert isinstance(await first_probe, int)

    asyncio.run(measure_once())
