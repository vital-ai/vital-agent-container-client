import asyncio
import httpx
import websockets
import json
from vital_agent_container_client.aimp_message_handler_inf import AIMPMessageHandlerInf
import logging

class VitalAgentContainerClient:
    def __init__(self, *, base_url, handler: AIMPMessageHandlerInf, jwt_token=None):
        self.base_url = base_url
        self.handler = handler
        self.jwt_token = jwt_token
        self.websocket = None

    async def check_health(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()

    async def open_websocket(self, path="/ws"):
        ws_url = f"{self.base_url.replace('http', 'ws')}{path}"
        
        # Prepare headers for JWT authentication if token is provided
        headers = {}
        if self.jwt_token:
            headers["authorization"] = self.jwt_token
        
        self.websocket = await websockets.connect(ws_url, additional_headers=headers)

    async def close_websocket(self):
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

    async def send_message(self, message):
        if self.websocket:
            await self.websocket.send(json.dumps(message))
        else:
            raise ConnectionError("WebSocket is not connected.")

    async def handle_close(self):
        await self.websocket.wait_closed()

    async def wait_for_close_or_timeout(self, timeout):
        logger = logging.getLogger()

        try:
            await asyncio.wait_for(asyncio.gather(self.receive_messages(), self.websocket.wait_closed()), timeout)
        except asyncio.TimeoutError:
            logger.error(f"Websocket Timeout: {timeout} seconds.")
            # Handle the timeout scenario if needed
            pass

    async def receive_messages(self):
        logger = logging.getLogger()

        if self.websocket:
            try:
                async for message in self.websocket:
                    logger.info(f"Websocket Received message: {message}")
                    data = json.loads(message)
                    await self.handler.receive_message(data)
            except websockets.exceptions.ConnectionClosed as e:
                logger.info(f"Websocket connection closed with code {e.code}: {e.reason}")
        else:
            raise ConnectionError("WebSocket is not connected.")
