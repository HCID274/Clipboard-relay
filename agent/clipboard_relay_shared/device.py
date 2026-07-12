"""macOS / Windows Agent 共用的设备 id 与注册 URL 辅助函数。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

DEVICE_ID_REPLACEMENT_PATTERN = re.compile(r"[^a-z0-9-]+")


def suggested_device_id(hostname: str) -> str:
    suggestion = DEVICE_ID_REPLACEMENT_PATTERN.sub("-", hostname.lower()).strip("-")
    suggestion = suggestion[:32].rstrip("-")
    return suggestion if len(suggestion) >= 3 else "my-device"


def registration_url(server_ws_url: str) -> str:
    parsed = urlparse(server_ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, parsed.netloc, "/api/devices/register", "", "", ""))


def build_agent_ws_url(server_ws_url: str, device_id: str) -> str:
    parsed = urlparse(server_ws_url)
    query = parse_qs(parsed.query)
    query["device_id"] = [device_id]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
