"""macOS / Windows Agent 共用的交互式输入提示。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

_UNSET: Any = object()


def _load_readline() -> ModuleType | None:
    """返回可用的 readline 兼容模块；没有则返回 None。

    macOS / Linux 使用标准库 ``readline`` 即可。
    Windows 可安装可选依赖 ``pyreadline3``，以便 ``import readline`` 成功，
    并走同一套 ``set_startup_hook`` 预填路径。
    """
    try:
        import readline
    except ImportError:
        return None
    return readline


def _is_interactive(isatty_fn: Callable[[], bool] | None) -> bool:
    if isatty_fn is not None:
        return bool(isatty_fn())
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _resolve_input(entered: str | None, suggestion: str) -> str:
    return (entered or "").strip() or suggestion


def _bracket_prompt(read_input: Callable[[str], str], suggestion: str) -> str:
    if suggestion:
        entered = read_input(f"设备名称 [{suggestion}]: ")
    else:
        entered = read_input("设备名称: ")
    return _resolve_input(entered, suggestion)


def _prompt_with_readline(
    read_input: Callable[[str], str],
    suggestion: str,
    readline_mod: Any,
) -> str | None:
    """尝试用 readline 预填建议名。失败返回 None，由调用方降级。"""

    def _startup_hook() -> None:
        readline_mod.insert_text(suggestion)
        redisplay = getattr(readline_mod, "redisplay", None)
        if not callable(redisplay):
            return
        try:
            redisplay()
        except Exception:
            pass

    try:
        readline_mod.set_startup_hook(_startup_hook)
        try:
            entered = read_input("设备名称: ")
        finally:
            readline_mod.set_startup_hook()
        return _resolve_input(entered, suggestion)
    except Exception:
        # 预填失败时降级为「括号默认值」提示。
        try:
            readline_mod.set_startup_hook()
        except Exception:
            pass
        return None


def prompt_device_id(
    suggestion: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    isatty_fn: Callable[[], bool] | None = None,
    readline_module: Any = _UNSET,
) -> str:
    """询问设备名称；在可能时把建议名预填进输入缓冲。

    * 交互式 TTY 且可用 readline（Windows 上为 pyreadline3）：建议名插入输入缓冲，
      用户可用方向键/退格编辑后回车确认。
    * 交互式 TTY 但无 readline：降级为 ``设备名称 [<suggestion>]:``（直接回车沿用建议名）。
    * 非 TTY（测试、管道、非交互安装）：不调用 ``input``，直接返回去掉首尾空白的建议名。

    返回选定的设备 id 字符串（不为 ``None``）。建议名为空且用户也提交空输入时返回空串。
    """
    suggestion = (suggestion or "").strip()
    if not _is_interactive(isatty_fn):
        return suggestion

    read_input = input if input_fn is None else input_fn
    readline_mod = _load_readline() if readline_module is _UNSET else readline_module

    if readline_mod is not None and suggestion:
        prefilled = _prompt_with_readline(read_input, suggestion, readline_mod)
        if prefilled is not None:
            return prefilled

    return _bracket_prompt(read_input, suggestion)
