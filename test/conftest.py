import asyncio
import json
from http import HTTPStatus
from typing import Any, Awaitable, Callable, List, Optional

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.typing import Subprotocol

from vital_agent_container_client import AIMPMessageHandlerInf
from vital_agent_container_client.protocol import (
    CONTROL_ERROR,
    CONTROL_STATUS,
    INTENT_PROPERTY,
    SUBPROTOCOL,
    URI_PROPERTY,
)

Script = Callable[["Peer"], Awaitable[None]]


class Peer:
    """Server side of one scripted connection."""

    def __init__(self, ws: ServerConnection, received: List[Any]):
        self.ws = ws
        self.received = received

    async def recv(self) -> List[Any]:
        message = json.loads(await asyncio.wait_for(self.ws.recv(), 5))
        self.received.append(message)
        return message

    async def send(self, obj: Any) -> None:
        await self.ws.send(obj if isinstance(obj, (str, bytes)) else json.dumps(obj))

    async def status(self, status: str, in_reply_to: Optional[str]) -> None:
        await self.send(
            {"type": CONTROL_STATUS, "status": status, "in_reply_to": in_reply_to}
        )

    async def error(self, error: str, in_reply_to: Optional[str]) -> None:
        await self.send(
            {
                "type": CONTROL_ERROR,
                "error": error,
                "detail": "",
                "in_reply_to": in_reply_to,
            }
        )

    async def close(self, code: int, reason: str = "") -> None:
        await self.ws.close(code=code, reason=reason)


def uri_of(message: List[Any]) -> str:
    return message[0][URI_PROPERTY]


def intent_of(message: List[Any]) -> Optional[str]:
    return message[0].get(INTENT_PROPERTY)


class ScriptedServer:
    def __init__(self) -> None:
        self.received: List[Any] = []
        self.request_headers: Any = None
        self.script_error: Optional[BaseException] = None
        self._server: Any = None

    async def start(
        self,
        script: Script,
        *,
        subprotocols: Optional[List[str]] = None,
        reject_status: Optional[int] = None,
    ) -> str:
        offered = [SUBPROTOCOL] if subprotocols is None else subprotocols

        async def handler(ws: ServerConnection) -> None:
            self.request_headers = ws.request.headers if ws.request else None
            try:
                await script(Peer(ws, self.received))
            except Exception as e:  # surfaced by the test via script_error
                self.script_error = e
                raise
            # Keep the connection until the client is done with it
            await ws.wait_closed()

        def process_request(connection: Any, request: Any) -> Any:
            self.request_headers = request.headers
            if reject_status is not None:
                return connection.respond(HTTPStatus(reject_status), "rejected\n")
            return None

        self._server = await serve(
            handler,
            "127.0.0.1",
            0,
            subprotocols=[Subprotocol(p) for p in offered] or None,
            process_request=process_request,
        )
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


@pytest.fixture
async def server():
    s = ScriptedServer()
    yield s
    await s.stop()


class RecordingHandler(AIMPMessageHandlerInf):
    def __init__(self, fail_on: Optional[str] = None):
        self.messages: List[Any] = []
        self.controls: List[Any] = []
        self.texts: List[str] = []
        self.fail_on = fail_on

    async def receive_message(self, message: List[Any]) -> None:
        self.messages.append(message)
        if self.fail_on is not None and message[0].get("text") == self.fail_on:
            raise RuntimeError("handler failed")

    async def receive_control(self, message: Any) -> None:
        self.controls.append(message)

    async def receive_text(self, text: str) -> None:
        self.texts.append(text)


@pytest.fixture
def handler() -> RecordingHandler:
    return RecordingHandler()
