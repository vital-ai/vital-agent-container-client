import asyncio
from vital_agent_container_client.aimp_message_handler_inf import AIMPMessageHandlerInf
from vital_agent_container_client.vital_agent_container_client import VitalAgentContainerClient


class LocalMessageHandler(AIMPMessageHandlerInf):
    async def receive_message(self, message):
        print(f"Local Handler Received message: {message}")


async def main():

    print('Test Vital Agent Container Client')

    handler = LocalMessageHandler()

    client = VitalAgentContainerClient("http://localhost:6006", handler)

    health = await client.check_health()
    print("Health:", health)

    await client.open_websocket()

    # Start receiving messages in the background
    # receive_task = asyncio.create_task(client.receive_messages())

    message = [{"type": "greeting", "content": "Hello, WebSocket!"}]

    await client.send_message(message)

    await client.wait_for_close_or_timeout(60)

    # Wait for some time to receive messages
    # await asyncio.sleep(10)

    await client.close_websocket()

if __name__ == "__main__":

    # main()
    asyncio.run(main())

