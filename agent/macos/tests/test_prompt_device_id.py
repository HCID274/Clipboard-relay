"""共享设备名提示助手的单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

# 共享包位于 agent/clipboard_relay_shared（与 macos/ 同级）。
_AGENT_ROOT = Path(__file__).resolve().parents[2]
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from clipboard_relay_shared.prompt import prompt_device_id


def test_non_tty_returns_stripped_suggestion_without_input() -> None:
    calls: list[str] = []

    result = prompt_device_id(
        "  my-mac  ",
        input_fn=lambda message: calls.append(message) or "should-not-run",
        isatty_fn=lambda: False,
    )

    assert result == "my-mac"
    assert calls == []


def test_non_tty_empty_suggestion_returns_empty_string() -> None:
    result = prompt_device_id(
        "   ",
        input_fn=lambda _message: "ignored",
        isatty_fn=lambda: False,
    )
    assert result == ""


def test_tty_with_readline_prefills_and_accepts_enter() -> None:
    hooks: list[object] = []
    inserted: list[str] = []

    class FakeReadline:
        def __init__(self) -> None:
            self._hook = None

        def set_startup_hook(self, hook=None):
            hooks.append(hook)
            self._hook = hook

        def insert_text(self, text: str) -> None:
            inserted.append(text)

        def redisplay(self) -> None:
            pass

    fake = FakeReadline()

    def fake_input(message: str) -> str:
        # 真实 readline 在开始读入时调用 startup hook。
        if fake._hook is not None:
            fake._hook()
        return "desk-mac" if message == "设备名称: " else "bad"

    result = prompt_device_id(
        "desk-mac",
        input_fn=fake_input,
        isatty_fn=lambda: True,
        readline_module=fake,
    )

    assert result == "desk-mac"
    assert inserted == ["desk-mac"]
    # hook 先安装后清除。
    assert hooks[0] is not None
    assert hooks[-1] is None


def test_tty_with_readline_user_edits_value() -> None:
    class FakeReadline:
        def set_startup_hook(self, hook=None):
            pass

        def insert_text(self, text: str) -> None:
            pass

    result = prompt_device_id(
        "desk-mac",
        input_fn=lambda _message: "  office-mac  ",
        isatty_fn=lambda: True,
        readline_module=FakeReadline(),
    )
    assert result == "office-mac"


def test_tty_with_readline_empty_input_keeps_suggestion() -> None:
    class FakeReadline:
        def set_startup_hook(self, hook=None):
            pass

        def insert_text(self, text: str) -> None:
            pass

    result = prompt_device_id(
        "desk-mac",
        input_fn=lambda _message: "   ",
        isatty_fn=lambda: True,
        readline_module=FakeReadline(),
    )
    assert result == "desk-mac"


def test_tty_without_readline_uses_bracket_prompt() -> None:
    messages: list[str] = []

    def fake_input(message: str) -> str:
        messages.append(message)
        return ""

    result = prompt_device_id(
        "desk-mac",
        input_fn=fake_input,
        isatty_fn=lambda: True,
        readline_module=None,
    )

    assert result == "desk-mac"
    assert messages == ["设备名称 [desk-mac]: "]


def test_tty_without_readline_custom_entry() -> None:
    result = prompt_device_id(
        "desk-mac",
        input_fn=lambda _message: "custom-id",
        isatty_fn=lambda: True,
        readline_module=None,
    )
    assert result == "custom-id"


def test_tty_empty_suggestion_prompts_without_brackets() -> None:
    messages: list[str] = []

    def fake_input(message: str) -> str:
        messages.append(message)
        return "typed-id"

    result = prompt_device_id(
        "",
        input_fn=fake_input,
        isatty_fn=lambda: True,
        readline_module=None,
    )

    assert result == "typed-id"
    assert messages == ["设备名称: "]


def test_readline_failure_falls_back_to_bracket_prompt() -> None:
    class BrokenReadline:
        def set_startup_hook(self, hook=None):
            if hook is not None:
                raise RuntimeError("readline unavailable")

        def insert_text(self, text: str) -> None:
            raise AssertionError("should not insert")

    messages: list[str] = []

    def fake_input(message: str) -> str:
        messages.append(message)
        return ""

    result = prompt_device_id(
        "desk-mac",
        input_fn=fake_input,
        isatty_fn=lambda: True,
        readline_module=BrokenReadline(),
    )

    assert result == "desk-mac"
    assert messages == ["设备名称 [desk-mac]: "]
