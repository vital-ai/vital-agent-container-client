"""WebSocket wire contract (v2) between the client and the agent container.

Mirrors ``vital_agent_container.protocol`` in the SDK (vital-agent-container-sdk 0.2.0).
Close codes are the contract; reason strings are informational.
"""

from enum import IntEnum

# Subprotocol offered when connecting; the server must select it
SUBPROTOCOL = "vital-agent.v2"

# Properties on the first AIMP object of a message
URI_PROPERTY = "URI"
INTENT_PROPERTY = "http://vital.ai/ontology/vital-aimp#hasIntent"
JWT_PROPERTY = "http://vital.ai/ontology/vital-aimp#hasJwtJSON"

INTENT_INTERRUPT = "interrupt"
INTENT_CLOSE = "close"


class CloseCode(IntEnum):
    NORMAL = 1000  # closed on request (service or client), or idle timeout
    GOING_AWAY = 1001  # server shutting down
    UNSUPPORTED_DATA = 1003  # not valid JSON, not an AIMP message list, or binary
    POLICY_VIOLATION = 1008  # client stopped reading (a control message wasn't sent)
    INTERNAL_ERROR = 1011  # handler raised, or an unexpected server error
    UNAUTHORIZED = 4001  # JWT payload mode rejected a message
    UNSUPPORTED_PROTOCOL = 4002  # the client didn't offer SUBPROTOCOL
    HANDLER_TIMEOUT = 4008  # handler exceeded the server's task deadline


# Control messages are JSON objects; AIMP payloads are JSON lists
CONTROL_ERROR = "sdk.error"
CONTROL_STATUS = "sdk.status"

ERROR_BUSY = "busy"  # a handler is running; wait for its done / interrupted
ERROR_CANCELLING = "cancelling"  # interrupted handlers still running; wait for ready
STATUS_DONE = "done"
STATUS_READY = "ready"
STATUS_INTERRUPTED = "interrupted"
STATUS_IDLE = "idle"
