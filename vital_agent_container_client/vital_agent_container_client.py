import asyncio
import httpx
import websockets
import json

from vital_agent_container_client.aimp_message_handler_inf import AIMPMessageHandlerInf


class VitalAgentContainerClient:
    def __init__(self, base_url, handler: AIMPMessageHandlerInf):
        self.base_url = base_url
        self.handler = handler
        self.websocket = None

    async def check_health(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def open_websocket(self, path="/ws"):
        self.websocket = await websockets.connect(f"{self.base_url.replace('http', 'ws')}{path}")

    async def close_websocket(self):
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

    async def send_message(self, message):
        if self.websocket:
            await self.websocket.send(json.dumps(message))
        else:
            raise ConnectionError("WebSocket is not connected.")

    async def receive_messages(self):
        if self.websocket:
            async for message in self.websocket:
                data = json.loads(message)
                await self.handler.receive_message(data)
        else:
            raise ConnectionError("WebSocket is not connected.")



