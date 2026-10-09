from typing import Optional


class VitalAgentContainerClientError(Exception):
    """Base class for errors raised by the client."""


class NotConnectedError(VitalAgentContainerClientError, ConnectionError):
    """The client has no open connection."""


class AuthenticationError(VitalAgentContainerClientError):
    """The server rejected the WebSocket handshake (HTTP 401/403).

    In JWT header mode this is how a missing or invalid token is reported.
    """


class ProtocolMismatchError(VitalAgentContainerClientError):
    """The server doesn't speak the client's protocol (no ``vital-agent.v2``)."""


class BusyError(VitalAgentContainerClientError):
    """The server rejected a request because it is still busy.

    ``error`` is ``"busy"`` (another interaction is running on the connection) or
    ``"cancelling"`` (interrupted handlers are still finishing, after a retry).
    """

    def __init__(self, error: str, detail: Optional[str] = None):
        super().__init__(f"{error}: {detail}" if detail else error)
        self.error = error
        self.detail = detail
