import io
import json
from urllib.error import HTTPError

import pytest

import agent


def test_load_config_reuses_device_id_from_legacy_websocket_url(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )

    config = agent.load_config(config_path)

    assert config["device_id"] == "win-fukuoka"


def test_registration_prompts_with_hostname_and_persists_server_identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )
    captured_requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"device_id":"win-office"}'

    def open_request(request, timeout):
        captured_requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(agent.socket, "gethostname", lambda: "WIN OFFICE.local")
    monkeypatch.setattr("builtins.input", lambda message: "" if "win-office-local" in message else "bad")
    monkeypatch.setattr(agent, "urlopen", open_request)

    config = agent.register_configured_device(agent.load_config(config_path), config_path)

    assert config["device_id"] == "win-office"
    assert config["server_ws_url"].endswith("device_id=win-office")
    assert json.loads(config_path.read_text(encoding="utf-8"))["device_id"] == "win-office"
    assert json.loads(captured_requests[0][0].data) == {"device_id": "win-office-local"}


def test_registration_reports_server_error_detail(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "wrong-password",
                "device_id": "win-office",
            }
        ),
        encoding="utf-8",
    )
    error = HTTPError(
        "https://clip.hcid274.cn/api/devices/register",
        403,
        "Forbidden",
        {},
        io.BytesIO('{"detail":"已达设备数上限"}'.encode()),
    )
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(agent.RegistrationError, match="已达设备数上限"):
        agent.register_configured_device(agent.load_config(config_path), config_path)
