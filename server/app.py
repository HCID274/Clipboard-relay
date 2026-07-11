import hmac
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("API_KEY", "")
DEVICES = {
    "win-fukuoka": "福冈 Windows",
    "mac-china": "中国大陆 Mac",
}

app = FastAPI(title="Clipboard Relay")


class AgentConnections:
    def __init__(self) -> None:
        self.websockets: dict[str, WebSocket] = {}

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old_websocket = self.websockets.get(device_id)
        if old_websocket is not None:
            await old_websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        self.websockets[device_id] = websocket

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        if self.websockets.get(device_id) is websocket:
            self.websockets.pop(device_id, None)

    async def send_clipboard(self, device_id: str, text: str) -> None:
        websocket = self.websockets.get(device_id)
        if websocket is None:
            raise RuntimeError("agent offline")
        try:
            await websocket.send_json({"type": "clipboard", "text": text})
        except Exception:
            if self.websockets.get(device_id) is websocket:
                self.websockets.pop(device_id, None)
            raise RuntimeError("agent offline")


agents = AgentConnections()


def check_api_key(candidate: str | None) -> None:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY is not configured")
    if candidate is None or not hmac.compare_digest(candidate, API_KEY):
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/send")
async def send_text(
    payload: dict = Body(...),
    x_api_key: str | None = Header(default=None),
) -> dict[str, bool]:
    check_api_key(x_api_key)

    target = payload.get("target")
    text = payload.get("text")
    if not isinstance(target, str) or target not in DEVICES:
        raise HTTPException(status_code=400, detail="invalid target")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="text is empty")

    try:
        await agents.send_clipboard(target, text)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="target device is not connected")
    return {"ok": True}


@app.get("/api/status")
async def connection_status(
    x_api_key: str | None = Header(default=None),
) -> dict[str, dict[str, bool]]:
    check_api_key(x_api_key)
    return {
        "devices": {
            device_id: device_id in agents.websockets for device_id in DEVICES
        }
    }


@app.websocket("/ws/agent")
async def websocket_agent(websocket: WebSocket) -> None:
    api_key = websocket.headers.get("x-api-key")
    if not API_KEY or api_key is None or not hmac.compare_digest(api_key, API_KEY):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    device_id = websocket.query_params.get("device_id")
    if device_id not in DEVICES:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await agents.connect(device_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        agents.disconnect(device_id, websocket)
