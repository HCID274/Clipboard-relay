import asyncio
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

import app as relay_app


NEW_PASSWORD = "new-password"
OLD_PASSWORD = "old-api-key"
INITIAL_TIMESTAMP = "2026-07-11T00:00:00Z"


def record(device_id: str, timestamp: str = INITIAL_TIMESTAMP) -> dict[str, str]:
    return {
        "device_id": device_id,
        "created_at": timestamp,
        "last_active": timestamp,
    }


@pytest.fixture(autouse=True)
def reset_server_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay_app, "PASSWORD", NEW_PASSWORD)
    monkeypatch.setattr(relay_app, "API_KEY", OLD_PASSWORD)
    monkeypatch.setattr(relay_app, "MAX_DEVICES", 10)
    monkeypatch.setattr(relay_app, "DEVICES_FILE", tmp_path / "devices.json")
    monkeypatch.setattr(relay_app, "device_lock", asyncio.Lock())
    relay_app.DEVICES.clear()
    relay_app.agents.websockets.clear()
    yield
    relay_app.DEVICES.clear()
    relay_app.agents.websockets.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(relay_app.app) as test_client:
        yield test_client


def headers(password: str = NEW_PASSWORD) -> dict[str, str]:
    return {"X-API-Key": password}


def test_new_and_legacy_passwords_are_both_accepted(client: TestClient) -> None:
    new_response = client.get("/api/devices", headers=headers())
    old_response = client.get("/api/devices", headers=headers(OLD_PASSWORD))
    wrong_response = client.get("/api/devices", headers=headers("wrong"))

    assert new_response.status_code == 200
    assert old_response.status_code == 200
    assert wrong_response.status_code == 401


def test_passwords_are_trimmed_before_comparison(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relay_app, "PASSWORD", "  new-password  ")
    monkeypatch.setattr(relay_app, "API_KEY", "")

    response = client.get("/api/devices", headers=headers("  new-password  "))

    assert response.status_code == 200


def test_whitespace_only_password_configuration_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relay_app, "PASSWORD", "   ")
    monkeypatch.setattr(relay_app, "API_KEY", "")

    response = client.get("/api/devices", headers=headers(""))

    assert response.status_code == 500


def test_non_ascii_new_password_does_not_block_legacy_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relay_app, "PASSWORD", "中文密码")

    response = client.get("/api/devices", headers=headers(OLD_PASSWORD))

    assert response.status_code == 200


def test_non_ascii_candidate_is_rejected_without_server_error() -> None:
    with pytest.raises(HTTPException) as exc_info:
        relay_app.check_api_key("中文密码")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid password"


def test_registers_normalized_device_and_persists_it(client: TestClient) -> None:
    response = client.post(
        "/api/devices/register",
        headers=headers(),
        json={"device_id": "New-Laptop"},
    )

    assert response.status_code == 200
    assert response.json()["device_id"] == "new-laptop"
    stored = json.loads(relay_app.DEVICES_FILE.read_text(encoding="utf-8"))
    assert stored["devices"] == [response.json()]


def test_registering_existing_device_returns_record_without_adding_or_rewriting(
    client: TestClient,
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")

    first_response = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": "MAC-CHINA"}
    )
    second_response = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": "mac-china"}
    )

    assert first_response.status_code == 200
    assert first_response.json() == record("mac-china")
    assert second_response.json() == first_response.json()
    assert list(relay_app.DEVICES) == ["mac-china"]
    assert not relay_app.DEVICES_FILE.exists()


@pytest.mark.parametrize(
    "device_id",
    ["ab", "a" * 33, "has space", "under_score", "中文设备", None, 123],
)
def test_registration_rejects_invalid_device_id(client: TestClient, device_id) -> None:
    response = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": device_id}
    )

    assert response.status_code == 400


def test_device_limit_rejects_new_device_but_allows_existing_device(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    monkeypatch.setattr(relay_app, "MAX_DEVICES", 1)

    existing_response = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": "mac-china"}
    )
    new_response = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": "new-device"}
    )

    assert existing_response.status_code == 200
    assert new_response.status_code == 403
    assert new_response.json() == {"detail": "已达设备数上限"}


def test_concurrent_registration_cannot_exceed_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(relay_app, "MAX_DEVICES", 1)

    async def register_both():
        return await asyncio.gather(
            relay_app.register_device({"device_id": "device-one"}, NEW_PASSWORD),
            relay_app.register_device({"device_id": "device-two"}, NEW_PASSWORD),
            return_exceptions=True,
        )

    results = asyncio.run(register_both())

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, HTTPException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].status_code == 403
    assert len(relay_app.DEVICES) == 1


def test_list_merges_persistent_records_with_online_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    timestamps = iter(
        [
            "2026-07-11T01:00:00Z",  # 连接时持久化
            "2026-07-11T02:00:00Z",  # 在线列表
            "2026-07-11T03:00:00Z",  # 断开时持久化
        ]
    )
    monkeypatch.setattr(relay_app, "_now", lambda: next(timestamps))

    with client.websocket_connect(
        "/ws/agent?device_id=mac-china", headers=headers()
    ):
        response = client.get("/api/devices", headers=headers())

        # 连接时已持久化为 01:00；列表对在线设备返回新鲜的「当前时间」。
        assert response.json() == [
            {
                "device_id": "mac-china",
                "created_at": INITIAL_TIMESTAMP,
                "last_active": "2026-07-11T02:00:00Z",
                "online": True,
            }
        ]
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T01:00:00Z"


def test_websocket_rejects_unregistered_device(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/agent?device_id=unknown-device", headers=headers()
        ):
            pass

    assert exc_info.value.code == 1008


def test_delete_cannot_race_between_websocket_registration_check_and_connect() -> None:
    relay_app.DEVICES["race-device"] = record("race-device")
    accept_started = asyncio.Event()
    allow_accept = asyncio.Event()
    disconnected = asyncio.Event()

    class PausedWebSocket:
        headers = {"x-api-key": NEW_PASSWORD}
        query_params = {"device_id": "race-device"}

        async def accept(self) -> None:
            accept_started.set()
            await allow_accept.wait()

        async def close(self, code: int) -> None:
            disconnected.set()

        async def receive_text(self) -> str:
            await disconnected.wait()
            raise WebSocketDisconnect(code=1000)

    async def connect_and_delete() -> None:
        websocket = PausedWebSocket()
        connection_task = asyncio.create_task(relay_app.websocket_agent(websocket))
        await accept_started.wait()
        deletion_task = asyncio.create_task(
            relay_app.delete_device("race-device", NEW_PASSWORD)
        )
        await asyncio.sleep(0)
        assert not deletion_task.done()
        allow_accept.set()
        await deletion_task
        await connection_task

    asyncio.run(connect_and_delete())

    assert "race-device" not in relay_app.DEVICES
    assert "race-device" not in relay_app.agents.websockets


def test_delete_closes_online_device_and_removes_record(client: TestClient) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")

    with client.websocket_connect(
        "/ws/agent?device_id=mac-china", headers=headers()
    ) as websocket:
        response = client.delete("/api/devices/mac-china", headers=headers())

        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_text()
        assert exc_info.value.code == 1000

    assert "mac-china" not in relay_app.DEVICES
    assert "mac-china" not in relay_app.agents.websockets


def test_delete_missing_device_returns_404(client: TestClient) -> None:
    response = client.delete("/api/devices/missing-device", headers=headers())

    assert response.status_code == 404


def test_deleting_last_device_persists_empty_registry(client: TestClient) -> None:
    relay_app.DEVICES["last-device"] = record("last-device")

    response = client.delete("/api/devices/last-device", headers=headers())

    assert response.status_code == 200
    stored = json.loads(relay_app.DEVICES_FILE.read_text(encoding="utf-8"))
    assert stored == {"devices": []}


def test_last_active_updates_on_connect_and_disconnect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamps = iter(
        [
            "2026-07-11T01:00:00Z",  # 注册
            "2026-07-11T02:00:00Z",  # 连接
            "2026-07-11T03:00:00Z",  # 在线列表
            "2026-07-11T04:00:00Z",  # 断开
            "2026-07-11T05:00:00Z",  # 离线列表
        ]
    )
    monkeypatch.setattr(relay_app, "_now", lambda: next(timestamps))

    registered = client.post(
        "/api/devices/register", headers=headers(), json={"device_id": "mac-china"}
    ).json()
    offline_list = client.get("/api/devices", headers=headers()).json()
    client.get("/api/status", headers=headers())

    assert registered["last_active"] == "2026-07-11T01:00:00Z"
    assert offline_list == [
        {
            "device_id": "mac-china",
            "created_at": "2026-07-11T01:00:00Z",
            "last_active": "2026-07-11T01:00:00Z",
            "online": False,
        }
    ]

    with client.websocket_connect(
        "/ws/agent?device_id=mac-china", headers=headers()
    ):
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T02:00:00Z"
        online_list = client.get("/api/devices", headers=headers()).json()
        assert online_list[0]["online"] is True
        assert online_list[0]["last_active"] == "2026-07-11T03:00:00Z"
        # 列表接口的新鲜 last_active 不得回写设备清单文件。
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T02:00:00Z"
        stored_while_online = json.loads(
            relay_app.DEVICES_FILE.read_text(encoding="utf-8")
        )
        assert stored_while_online["devices"][0]["last_active"] == "2026-07-11T02:00:00Z"

    assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T04:00:00Z"
    offline_again = client.get("/api/devices", headers=headers()).json()
    assert offline_again[0]["online"] is False
    assert offline_again[0]["last_active"] == "2026-07-11T04:00:00Z"
    stored = json.loads(relay_app.DEVICES_FILE.read_text(encoding="utf-8"))
    assert stored["devices"][0]["last_active"] == "2026-07-11T04:00:00Z"


def test_list_online_last_active_is_fresh_without_disk_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    timestamps = iter(
        [
            "2026-07-11T01:00:00Z",  # 连接时持久化
            "2026-07-11T02:00:00Z",  # 第一次列表
            "2026-07-11T03:00:00Z",  # 第二次列表
            "2026-07-11T04:00:00Z",  # 断开时持久化
        ]
    )
    monkeypatch.setattr(relay_app, "_now", lambda: next(timestamps))

    with client.websocket_connect(
        "/ws/agent?device_id=mac-china", headers=headers()
    ):
        first = client.get("/api/devices", headers=headers()).json()[0]
        second = client.get("/api/devices", headers=headers()).json()[0]

        assert first["last_active"] == "2026-07-11T02:00:00Z"
        assert second["last_active"] == "2026-07-11T03:00:00Z"
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T01:00:00Z"


def test_send_failure_closes_connection_and_updates_offline_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    monkeypatch.setattr(relay_app, "_now", lambda: "2026-07-11T01:00:00Z")

    class FailingWebSocket:
        headers = {"x-api-key": NEW_PASSWORD}
        query_params = {"device_id": "mac-china"}

        def __init__(self) -> None:
            self.connected = asyncio.Event()
            self.disconnected = asyncio.Event()
            self.close_codes: list[int] = []

        async def accept(self) -> None:
            self.connected.set()

        async def close(self, code: int) -> None:
            self.close_codes.append(code)
            self.disconnected.set()

        async def receive_text(self) -> str:
            await self.disconnected.wait()
            raise WebSocketDisconnect(code=1006)

        async def send_json(self, _payload: dict[str, str]) -> None:
            raise ConnectionError("send failed")

    async def send_and_disconnect() -> None:
        websocket = FailingWebSocket()
        connection_task = asyncio.create_task(relay_app.websocket_agent(websocket))
        await websocket.connected.wait()
        try:
            with pytest.raises(RuntimeError, match="agent offline"):
                await relay_app.agents.send_clipboard("mac-china", "hello")
            assert websocket.close_codes == [1011]
            await connection_task
        finally:
            if not connection_task.done():
                websocket.disconnected.set()
                await connection_task

    asyncio.run(send_and_disconnect())

    assert "mac-china" not in relay_app.agents.websockets
    assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T01:00:00Z"


def test_disconnect_write_failure_keeps_device_offline_and_updates_memory(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    monkeypatch.setattr(relay_app, "_now", lambda: "2026-07-11T01:00:00Z")
    monkeypatch.setattr(
        relay_app, "_write_devices", lambda *_args: (_ for _ in ()).throw(OSError("disk full"))
    )

    class DisconnectingWebSocket:
        headers = {"x-api-key": NEW_PASSWORD}
        query_params = {"device_id": "mac-china"}

        def __init__(self) -> None:
            self.connected = asyncio.Event()
            self.disconnected = asyncio.Event()

        async def accept(self) -> None:
            self.connected.set()

        async def close(self, _code: int) -> None:
            self.disconnected.set()

        async def receive_text(self) -> str:
            await self.disconnected.wait()
            raise WebSocketDisconnect(code=1006)

    async def disconnect_with_write_failure() -> None:
        websocket = DisconnectingWebSocket()
        connection_task = asyncio.create_task(relay_app.websocket_agent(websocket))
        await websocket.connected.wait()
        websocket.disconnected.set()
        await connection_task

    with caplog.at_level("WARNING"):
        asyncio.run(disconnect_with_write_failure())

    assert "mac-china" not in relay_app.agents.websockets
    assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T01:00:00Z"
    assert "failed to persist last_active" in caplog.text


def test_old_disconnect_cannot_update_last_active_after_same_device_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_app.DEVICES["mac-china"] = record("mac-china")
    timestamps = iter(
        [
            "2026-07-11T01:00:00Z",  # 旧连接
            "2026-07-11T02:00:00Z",  # 新连接
            "2026-07-11T03:00:00Z",  # 新断开
        ]
    )
    monkeypatch.setattr(relay_app, "_now", lambda: next(timestamps))

    class ReconnectingWebSocket:
        headers = {"x-api-key": NEW_PASSWORD}
        query_params = {"device_id": "mac-china"}

        def __init__(self, yield_on_close: bool = False) -> None:
            self.connected = asyncio.Event()
            self.disconnected = asyncio.Event()
            self.yield_on_close = yield_on_close

        async def accept(self) -> None:
            self.connected.set()

        async def close(self, code: int) -> None:
            self.disconnected.set()
            if self.yield_on_close:
                await asyncio.sleep(0)

        async def receive_text(self) -> str:
            await self.disconnected.wait()
            raise WebSocketDisconnect(code=1000)

    async def reconnect_then_disconnect() -> None:
        old_websocket = ReconnectingWebSocket(yield_on_close=True)
        old_task = asyncio.create_task(relay_app.websocket_agent(old_websocket))
        await old_websocket.connected.wait()
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T01:00:00Z"

        new_websocket = ReconnectingWebSocket()
        new_task = asyncio.create_task(relay_app.websocket_agent(new_websocket))
        await new_websocket.connected.wait()
        await old_task

        assert relay_app.agents.websockets["mac-china"] is new_websocket
        # 新连接时间优先；被替换的旧连接不得覆盖。
        # （若旧断开误写盘，会消耗下一个时间戳，使 last_active 超过新连接时间。）
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T02:00:00Z"

        new_websocket.disconnected.set()
        await new_task
        assert relay_app.DEVICES["mac-china"]["last_active"] == "2026-07-11T03:00:00Z"

    asyncio.run(reconnect_then_disconnect())


def test_corrupted_registry_loads_as_empty_and_logs_warning(tmp_path, caplog) -> None:
    path = tmp_path / "devices.json"
    path.write_text("not-json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = relay_app._load_devices(path)

    assert loaded == {}
    assert "starting empty" in caplog.text


def test_registry_records_survive_reload(tmp_path) -> None:
    path = tmp_path / "devices.json"
    expected = record("persisted-device")
    path.write_text(json.dumps({"devices": [expected]}), encoding="utf-8")

    loaded = relay_app._load_devices(path)

    assert loaded == {"persisted-device": expected}


def test_missing_registry_is_created_with_initial_devices(tmp_path) -> None:
    path = tmp_path / "devices.json"

    loaded = relay_app._load_or_create_devices(path)

    assert loaded == relay_app.INITIAL_DEVICES
    assert loaded is not relay_app.INITIAL_DEVICES
    assert all(
        loaded[device_id] is not relay_app.INITIAL_DEVICES[device_id]
        for device_id in loaded
    )
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "devices": [loaded["mac-china"], loaded["win-fukuoka"]]
    }


def test_registration_write_failure_does_not_change_in_memory_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        relay_app, "_write_devices", lambda *_args: (_ for _ in ()).throw(OSError("disk full"))
    )

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            relay_app.register_device({"device_id": "new-device"}, NEW_PASSWORD)
        )

    assert relay_app.DEVICES == {}


def test_deletion_write_failure_keeps_device_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay_app.DEVICES["kept-device"] = record("kept-device")
    monkeypatch.setattr(
        relay_app, "_write_devices", lambda *_args: (_ for _ in ()).throw(OSError("disk full"))
    )

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(relay_app.delete_device("kept-device", NEW_PASSWORD))

    assert relay_app.DEVICES == {"kept-device": record("kept-device")}
