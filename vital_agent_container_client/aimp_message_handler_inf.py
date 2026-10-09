from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union


class AIMPMessageHandlerInf(ABC):
    """Receives what the server sends on a connection.

    Callbacks run on the client's receive loop, one at a time and in order. Keep them
    fast: while one runs the client doesn't read, and a server that can't send for 5s
    closes the connection (1008). Exceptions are logged and recorded on the
    interaction's result (``InteractionResult.handler_error``); they don't stop the
    loop.
    """

    @abstractmethod
    async def receive_message(self, message: Union[List[Any], Dict[str, Any]]) -> None:
        """A message from the agent: an AIMP message (a JSON list), or one part of a
        large reply split into ``{"aimp_chunk": ...}`` objects. Chunks arrive in order,
        before the interaction's ``done``; reassembling them is up to the handler."""

    async def receive_control(self, message: Dict[str, Any]) -> None:
        """Optional: a server control message (``sdk.status`` / ``sdk.error``)."""

    async def receive_other(self, text: str) -> None:
        """Optional: any other frame (not JSON, or a JSON value that is neither an AIMP
        list, an ``aimp_chunk`` object nor a control message), as the raw text. The
        client logs these at WARNING."""
