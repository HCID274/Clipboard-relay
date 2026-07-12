from pathlib import Path


UI_PAGE = Path(__file__).resolve().parents[1] / "static" / "index.html"


def read_ui_page() -> str:
    return UI_PAGE.read_text(encoding="utf-8")


def test_new_ui_connection_resets_snapshot_version_before_receiving_messages() -> None:
    """新连接必须独立于旧连接的快照版本，并且拒绝旧连接的延迟消息。"""
    page = read_ui_page()
    connect_start = page.index("async function connectUi(connectionId)")
    reset_position = page.index("latestSnapshotVersion = -1;", connect_start)
    socket_position = page.index("const socket = new WebSocket", connect_start)

    assert reset_position < socket_position
    assert "uiSocket !== socket || connectionId !== latestUiConnectionId" in page


def test_password_login_gate_verifies_before_persisting_or_connecting() -> None:
    """登录遮罩必须在密码校验成功后才保存密码并建立 UI 连接。"""
    page = read_ui_page()
    submit_start = page.index('authForm.addEventListener("submit"')
    verify_position = page.index("const ticket = await confirmPassword(password);", submit_start)
    persist_position = page.index("localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);", submit_start)
    connect_position = page.index("connectUiWithTicket(++latestUiConnectionId, ticket);", submit_start)

    assert 'id="authGate"' in page
    assert 'id="authConfirm"' in page
    assert verify_position < persist_position < connect_position
    assert 'response.status === 401' in page


def test_invalid_cached_password_is_cleared_and_send_does_not_persist_password() -> None:
    """缓存密码失效时必须回到登录遮罩，发送流程不能再承担密码持久化职责。"""
    page = read_ui_page()
    send_start = page.index('sendButton.addEventListener("click"')

    assert "function clearCachedPassword(message)" in page
    assert 'INVALID_CACHED_PASSWORD_MESSAGE = "保存的密码已失效，请重新验证。"' in page
    assert page.count("clearCachedPassword(INVALID_CACHED_PASSWORD_MESSAGE)") == 3
    assert "localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);" not in page[send_start:]


def test_latency_only_refresh_does_not_rebuild_rows() -> None:
    """纯 latency 刷新只改 ms 并 tick，不能每次快照都重建整行（否则会整行抖动）。"""
    page = read_ui_page()
    assert "function updateLatenciesOnly()" in page
    assert "function structureKey()" in page
    assert "latencyTick" in page
    assert "updateLatenciesOnly();" in page
    # 快照到达仍走 render，但结构未变时应 early-return 到 latency-only 路径
    assert "lastStructureKey === key" in page
