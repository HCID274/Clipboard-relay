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


def test_load_config_rejects_non_ascii_password(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "中文密码",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        agent.load_config(config_path)


def test_config_needs_password_recognizes_placeholder_and_valid_password(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "replace-with-relay-password",
            }
        ),
        encoding="utf-8",
    )

    assert agent.config_needs_password(config_path) is True

    config_path.write_text(
        json.dumps(
            {
                "server_ws_url": "wss://clip.hcid274.cn/ws/agent",
                "password": "secret-password",
            }
        ),
        encoding="utf-8",
    )

    assert agent.config_needs_password(config_path) is False


@pytest.mark.parametrize(
    ("contents", "expected_status"),
    [
        ('{"password": "replace-with-relay-password"}', 0),
        ('{"password": "secret-password"}', 1),
        ("{not valid JSON", 2),
        ("[]", 2),
    ],
)
def test_password_setup_status_distinguishes_config_errors(
    tmp_path, contents, expected_status
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")

    assert agent.password_setup_status(config_path) == expected_status

    if expected_status == 2:
        with pytest.raises(ValueError):
            agent.config_needs_password(config_path)


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


def test_authentication_failure_uses_a_distinct_exit_code(tmp_path, monkeypatch) -> None:
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
        401,
        "Unauthorized",
        {},
        io.BytesIO('{"detail":"invalid password"}'.encode()),
    )
    monkeypatch.setattr(agent, "setup_logging", lambda: None)
    monkeypatch.setattr(agent, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(SystemExit) as exc_info:
        agent.run(register_only=True, config_path=config_path)

    assert exc_info.value.code == agent.AUTHENTICATION_FAILURE_EXIT_CODE


def test_clear_password_makes_the_installer_prompt_on_the_next_run(tmp_path) -> None:
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

    agent.clear_password(config_path)

    assert agent.password_setup_status(config_path) == 0
    assert json.loads(config_path.read_text(encoding="utf-8"))["password"] == ""
