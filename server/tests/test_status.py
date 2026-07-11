from fastapi.testclient import TestClient
import pytest

import app as relay_app


VALID_API_KEY = "test-api-key"


@pytest.fixture(autouse=True)
def reset_server_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay_app, "API_KEY", VALID_API_KEY)
    relay_app.agents.websockets.clear()
    yield
    relay_app.agents.websockets.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(relay_app.app) as test_client:
        yield test_client


def status_headers() -> dict[str, str]:
    return {"X-API-Key": VALID_API_KEY}


def test_status_rejects_request_without_api_key(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid API key"}


def test_status_rejects_request_with_wrong_api_key(client: TestClient) -> None:
    response = client.get("/api/status", headers={"X-API-Key": "wrong-api-key"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid API key"}


def test_status_reports_configuration_error_when_api_key_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(relay_app, "API_KEY", "")

    response = client.get("/api/status", headers=status_headers())

    assert response.status_code == 500
    assert response.json()["detail"] == "API_KEY is not configured"
    assert response.json()["detail"] != "invalid API key"


def test_status_reports_all_devices_as_offline_by_default(client: TestClient) -> None:
    response = client.get("/api/status", headers=status_headers())

    assert response.status_code == 200
    assert response.json() == {
        "devices": {device_id: False for device_id in relay_app.DEVICES}
    }


def test_status_reports_connected_device_and_disconnect(client: TestClient) -> None:
    with client.websocket_connect(
        "/ws/agent?device_id=win-fukuoka", headers=status_headers()
    ):
        connected_response = client.get("/api/status", headers=status_headers())

        assert connected_response.status_code == 200
        assert connected_response.json()["devices"] == {
            "win-fukuoka": True,
            "mac-china": False,
        }

    disconnected_response = client.get("/api/status", headers=status_headers())

    assert disconnected_response.status_code == 200
    assert disconnected_response.json()["devices"] == {
        "win-fukuoka": False,
        "mac-china": False,
    }


def test_status_reports_two_connected_devices_independently(client: TestClient) -> None:
    with client.websocket_connect(
        "/ws/agent?device_id=win-fukuoka", headers=status_headers()
    ), client.websocket_connect(
        "/ws/agent?device_id=mac-china", headers=status_headers()
    ):
        response = client.get("/api/status", headers=status_headers())

        assert response.status_code == 200
        assert response.json()["devices"] == {
            "win-fukuoka": True,
            "mac-china": True,
        }
