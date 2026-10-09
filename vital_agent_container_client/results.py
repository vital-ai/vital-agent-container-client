from dataclasses import dataclass
from typing import Literal, Optional

from vital_agent_container_client.protocol import CloseCode


@dataclass(frozen=True)
class CloseResult:
    """How a connection ended. Classify on ``code``; ``reason`` is informational."""

    code: Optional[int]
    reason: Optional[str]

    @property
    def is_normal(self) -> bool:
        """1000: closed on request (service or client), or idle timeout."""
        return self.code == CloseCode.NORMAL

    @property
    def is_server_shutdown(self) -> bool:
        return self.code == CloseCode.GOING_AWAY

    @property
    def is_invalid_message(self) -> bool:
        return self.code == CloseCode.UNSUPPORTED_DATA

    @property
    def is_client_not_reading(self) -> bool:
        return self.code == CloseCode.POLICY_VIOLATION

    @property
    def is_internal_error(self) -> bool:
        """1011: the handler raised, or an unexpected server error."""
        return self.code == CloseCode.INTERNAL_ERROR

    @property
    def is_auth_rejected(self) -> bool:
        return self.code == CloseCode.UNAUTHORIZED

    @property
    def is_protocol_mismatch(self) -> bool:
        return self.code == CloseCode.UNSUPPORTED_PROTOCOL

    @property
    def is_handler_timeout(self) -> bool:
        return self.code == CloseCode.HANDLER_TIMEOUT


InteractionStatus = Literal["done", "interrupted", "closed", "timed_out"]


@dataclass(frozen=True)
class InteractionResult:
    """How one request ended.

    - ``done``: the handler returned; the connection is still open.
    - ``interrupted``: the handler was interrupted; the connection is still open.
    - ``closed``: the connection ended; see ``close``. A 1000 close is a normal end
      (``close_after=True``, or the handler requested the close).
    - ``timed_out``: the client's own timeout fired. The client interrupted the handler
      (or disconnected, with ``close_on_timeout=True``).
    """

    status: InteractionStatus
    request_uri: str
    messages_received: int
    close: Optional[CloseResult] = None
    # Set when the connection closed normally while waiting to retry a "cancelling"
    # rejection: the request never ran; open a new connection and send it again
    reconnect_suggested: bool = False
    # First exception raised by the message handler's callbacks for this interaction
    handler_error: Optional[BaseException] = None

    @property
    def ok(self) -> bool:
        """The request ran to completion: ``done``, or a normal (1000) close."""
        if self.status == "done":
            return True
        return (
            self.status == "closed"
            and self.close is not None
            and self.close.is_normal
            and not self.reconnect_suggested
        )
