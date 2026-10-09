"""Manual smoke test against a running agent container (contract v2, SDK 0.2.0).

    python examples/smoke_client.py [base_url]       # default http://localhost:6006

Set JWT_TOKEN to send a token (header mode), or JWT_TOKEN plus JWT_MODE=payload.
"""

import asyncio
import logging
import os
import sys
from typing import Any, List

from vital_agent_container_client import (
    AIMPMessageHandlerInf,
    VitalAgentContainerClient,
)


class PrintingHandler(AIMPMessageHandlerInf):
    async def receive_message(self, message: List[Any]) -> None:
        print(f"Received: {message}")

    async def receive_control(self, message: Any) -> None:
        print(f"Control: {message}")


async def main(base_url: str) -> None:
    client = VitalAgentContainerClient(
        base_url=base_url,
        handler=PrintingHandler(),
        jwt_token=os.environ.get("JWT_TOKEN"),
        jwt_mode="payload" if os.environ.get("JWT_MODE") == "payload" else "header",
    )
    print("Health:", await client.check_health())

    async with client:
        message = [{"type": "greeting", "content": "Hello, WebSocket!"}]
        result = await client.request(message, timeout=60)
        print("Result:", result)
    print("Closed:", client.close_result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:6006"))
