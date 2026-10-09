from abc import ABC, abstractmethod
from typing import Any, Dict, List


class AIMPMessageHandlerInf(ABC):
    """Receives what the server sends on a connection.

    Callbacks run on the client's receive loop, one at a time and in order. Keep them
    fast: while one runs the client doesn't read, and a server that can't send for 5s
    closes the connection (1008). Exceptions are logged and recorded on the
    interaction's result (``InteractionResult.handler_error``); they don't stop the
    loop.
    """

    @abstractmethod
    async def receive_message(self, message: List[Any]) -> None:
        """An AIMP message (a JSON list) from the agent."""

    async def receive_control(self, message: Dict[str, Any]) -> None:
        """Optional: a server control message (``sdk.status`` / ``sdk.error``)."""

    async def receive_text(self, text: str) -> None:
        """Optional: a text frame that is neither an AIMP list nor a control message."""
