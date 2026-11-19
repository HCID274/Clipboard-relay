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


def test_device_rows_use_text_content_for_server_supplied_values() -> None:
    page = read_ui_page()

    assert "row.innerHTML" not in page
    assert "targetList.replaceChildren()" in page
    assert "deviceListEl.replaceChildren()" in page
    assert 'makeElement("span", "name", d.device_id)' in page
    assert '"最后活跃 · " + formatLastActive(d.last_active)' in page


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


def test_last_sent_cache_uses_one_expiring_session_storage_entry() -> None:
    """仅在当前标签页短期缓存最近一条发送记录。"""
    page = read_ui_page()

    assert 'LAST_SENT_STORAGE_KEY = "clipboardRelayLastSent"' in page
    assert "LAST_SENT_TTL_MS = 10 * 60 * 1000" in page
    assert page.count("sessionStorage.setItem(LAST_SENT_STORAGE_KEY") == 1
    assert "sessionStorage.getItem(LAST_SENT_STORAGE_KEY)" in page
    assert "sessionStorage.removeItem(LAST_SENT_STORAGE_KEY)" in page
    assert "cached.expiresAt <= Date.now()" in page
    assert "localStorage.setItem(LAST_SENT_STORAGE_KEY" not in page


def test_successful_send_caches_exact_text_and_target_before_clearing_input() -> None:
    page = read_ui_page()
    send_start = page.index('sendButton.addEventListener("click"')
    success_position = page.index("if (response.ok)", send_start)
    cache_position = page.index("rememberLastSent(text, sendTarget);", success_position)
    clear_position = page.index('textInput.value = "";', success_position)

    assert 'const sendTarget = target;' in page[send_start:success_position]
    assert "JSON.stringify({ target: sendTarget, text })" in page[send_start:success_position]
    assert success_position < cache_position < clear_position
    assert 'setStatus("已发送至 " + sendTarget + "。", "ok")' in page


def test_resend_action_restores_text_and_opens_target_picker() -> None:
    page = read_ui_page()
    resend_start = page.index('resendLastButton.addEventListener("click"')
    resend_end = page.index("authForm.addEventListener", resend_start)
    resend_handler = page[resend_start:resend_end]

    assert 'id="resendLast"' in page
    assert "textInput.value = lastSent.text;" in resend_handler
    assert "targetOpen = true;" in resend_handler
    assert "updateTargetOpenUI();" in resend_handler
    assert "请选择正确设备后发送" in resend_handler
    assert "clearLastSent();" in page[page.index("function clearCachedPassword"):]


def test_resend_button_has_a_mobile_friendly_touch_target() -> None:
    page = read_ui_page()

    assert "min-width: 88px" in page
    assert "min-height: 44px" in page
    assert "padding: 10px 16px" in page
