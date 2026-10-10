"""WebSocket wire contract (v2) between the client and the agent container.

Mirrors ``vital_agent_container.protocol`` in the SDK (vital-agent-container-sdk 0.2.0).
Close codes are the contract; reason strings are informational.
"""

from enum import IntEnum
from typing import Any

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


# A control message is a JSON object whose "type" is one of CONTROL_TYPES. Everything
# else the server sends is handler output: AIMP message lists, and handler-defined JSON
# values such as chunk frames ({"aimp_chunk": ...}). Classify by "type", never by "is it
# an object".
CONTROL_ERROR = "sdk.error"
CONTROL_STATUS = "sdk.status"
CONTROL_TYPES = (CONTROL_STATUS, CONTROL_ERROR)

# A large agent reply may arrive as a sequence of JSON objects carrying this key; like
# all handler output they go to receive_message, and the handler reassembles them
AIMP_CHUNK_KEY = "aimp_chunk"

ERROR_BUSY = "busy"  # a handler is running; wait for its done / interrupted
ERROR_CANCELLING = "cancelling"  # interrupted handlers still running; wait for ready
STATUS_DONE = "done"
STATUS_READY = "ready"
STATUS_INTERRUPTED = "interrupted"
STATUS_IDLE = "idle"


def is_control_message(obj: Any) -> bool:
    """True for container control messages; False for handler output."""
    return isinstance(obj, dict) and obj.get("type") in CONTROL_TYPES
