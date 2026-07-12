"""macOS / Windows Agent 共用的辅助模块。"""

from clipboard_relay_shared.device import (
    build_agent_ws_url,
    registration_url,
    suggested_device_id,
)
from clipboard_relay_shared.prompt import prompt_device_id

__all__ = [
    "build_agent_ws_url",
    "prompt_device_id",
    "registration_url",
    "suggested_device_id",
]
