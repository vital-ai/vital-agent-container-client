from abc import ABC, abstractmethod
from typing import Any, Dict


class AIMPMessageHandlerInf(ABC):
    """Receives what the server sends on a connection.

    Callbacks run on the client's receive loop, one at a time and in order. Keep them
    fast: while one runs the client doesn't read, and a server that can't send for 5s
    closes the connection (1008). Exceptions are logged and recorded on the
    interaction's result (``InteractionResult.handler_error``); they don't stop the
    loop.
    """

    @abstractmethod
    async def receive_message(self, message: Any) -> None:
        """Handler output from the agent: any JSON value that isn't a control message.

        Usually an AIMP message (a JSON list). A large reply may arrive as a sequence of
        ``{"aimp_chunk": ...}`` objects, in order and before the interaction's ``done``;
        reassembling them is up to the handler. Agents may also send other JSON objects
        of their own."""

    async def receive_control(self, message: Dict[str, Any]) -> None:
        """Optional: a server control message (``sdk.status`` / ``sdk.error``)."""

    async def receive_other(self, text: str) -> None:
        """Optional: a text frame that isn't JSON, as the raw text (logged at
        WARNING)."""
