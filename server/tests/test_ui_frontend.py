from pathlib import Path


UI_PAGE = Path(__file__).resolve().parents[1] / "static" / "index.html"


def test_new_ui_connection_resets_snapshot_version_before_receiving_messages() -> None:
    """新连接必须独立于旧连接的快照版本，并且拒绝旧连接的延迟消息。"""
    page = UI_PAGE.read_text(encoding="utf-8")
    connect_start = page.index("async function connectUi(connectionId)")
    reset_position = page.index("latestSnapshotVersion = -1;", connect_start)
    socket_position = page.index("const socket = new WebSocket", connect_start)

    assert reset_position < socket_position
    assert "uiSocket !== socket || connectionId !== latestUiConnectionId" in page
