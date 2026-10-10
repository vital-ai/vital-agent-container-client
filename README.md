# vital-agent-container-client

Python client for agent containers built on
[vital-agent-container-sdk](https://github.com/vital-ai/vital-agent-container-python). It
speaks the container's WebSocket protocol **v2** (subprotocol `vital-agent.v2`), which
requires **vital-agent-container-sdk 0.2.0** or later on the server.

## Installation

```bash
pip install vital-agent-container-client
```

Requires Python 3.12+.

## Quick start

```python
import asyncio
from vital_agent_container_client import AIMPMessageHandlerInf, VitalAgentContainerClient


class Handler(AIMPMessageHandlerInf):
    async def receive_message(self, message):
        print("reply:", message)


async def main():
    async with VitalAgentContainerClient(
        base_url="http://localhost:6006", handler=Handler()
    ) as client:
        result = await client.request([{"type": "greeting", "content": "Hello"}])
        print(result.status, result.messages_received)   # done 1


asyncio.run(main())
```

`async with` connects on entry and closes gracefully on exit. Replies arrive at the handler
while `request()` waits.

## Interactions

A connection carries any number of **interactions**, one at a time. `request()` sends one
AIMP message (a list of objects) and returns an `InteractionResult` when the server sends
the interaction's terminal signal:

| `result.status` | Meaning | Connection |
|---|---|---|
| `done` | the agent's handler returned | still open |
| `interrupted` | the handler was interrupted (`interrupt()`) | still open |
| `closed` | the connection ended; see `result.close` | closed |
| `timed_out` | the client's own `timeout` fired (see [Timeouts](#timeouts)) | open, unless `close_on_timeout=True` |

`result.ok` is true for `done`, and for a normal (1000) close, which is how a one-shot request
or an agent that ends the conversation finishes.

- The first object of each message gets a `URI` if it has none. The server refers to the
  request by that URI (`result.request_uri`). The caller's message isn't modified.
- Requests on one client are serialized: concurrent `request()` calls queue.
- **One-shot:** `request(message, close_after=True)` also sends the `close` intent, so the
  server closes (1000) once the handler has returned. The result is `closed` with `ok == True`.

```python
client = VitalAgentContainerClient(base_url=url, handler=handler)
await client.connect()
result = await client.request(message, close_after=True)
```

### Interrupting

`await client.interrupt()` cancels the running interaction. Its `request()` returns
`interrupted` and the connection stays usable, so the next request can follow straight away.
`interrupt()` returns `"interrupted"`, `"idle"` (nothing was running; for example the request
had just finished) or `"closed"`.

### Closing

- `await client.close()`: sends the `close` intent and waits for the server's 1000. A running
  handler finishes first, and its replies are still delivered. Falls back to `disconnect()`
  after `timeout`.
- `await client.disconnect()`: closes the socket now. The server cancels a running handler.
- `client.close_result`: how the connection ended (`CloseResult`).

### Busy server

The server handles one request per connection at a time. After interrupts, it may still be
finishing interrupted handlers and reply `cancelling`. `request()` then waits for the server's
`ready` and sends the request again, once. If the connection closes normally while waiting,
the result is `closed` with `reconnect_suggested == True`: open a new connection and resend.
`BusyError` is raised for a `busy` reply, or a second `cancelling`.

## Close codes

Classify on the code (`CloseResult` properties); reason strings are informational.

| Code | `CloseResult` | Meaning |
|---|---|---|
| 1000 | `is_normal` | closed on request (client `close`, agent ended the conversation), or idle timeout |
| 4008 | `is_handler_timeout` | the agent's handler exceeded the server's task deadline |
| 1011 | `is_internal_error` | the handler raised, or an unexpected server error |
| 1003 | `is_invalid_message` | not valid JSON, not an AIMP message list, or a binary frame |
| 1008 | `is_client_not_reading` | the client didn't read for 5s (keep handler callbacks fast) |
| 4001 | `is_auth_rejected` | JWT payload mode rejected a message |
| 4002 | `is_protocol_mismatch` | the client didn't offer `vital-agent.v2` |
| 1001 | `is_server_shutdown` | the server is shutting down |

## Connecting and authentication

`connect()` offers the `vital-agent.v2` subprotocol and raises `ProtocolMismatchError` if
the server doesn't select it (a server older than SDK 0.2.0).

JWT enforcement on the server has two modes:

- **Header mode** (default): `VitalAgentContainerClient(..., jwt_token=token)` sends
  `authorization: <token>` on connect. A missing or invalid token fails the handshake with
  HTTP 403, raised as `AuthenticationError`.
- **Payload mode**: `VitalAgentContainerClient(..., jwt_mode="payload", jwt_token=...,
  jwt_auth_token=...)` puts the tokens in `hasJwtJSON` on the first object of **every**
  message, including the `interrupt` and `close` intents. A message without a valid token
  closes the connection with 4001.

A `hasJwtJSON` that the caller sets on the first object is always sent unchanged. In header
mode this is supported for passing another token (for example the end user's) to the agent
alongside the connection's header token; the client adds `hasJwtJSON` itself only in
payload mode, and only when the caller hasn't set one.

**Connection options.** `connect()` passes keyword arguments to `websockets.connect`, over
`connect_options` given to the constructor (which `async with` uses):

```python
client = VitalAgentContainerClient(
    base_url=url, handler=handler, connect_options={"max_size": 16 * 2**20}
)
```

Defaults: `proxy=None` (proxy environment variables are ignored; this client talks to
internal services; pass `proxy=True` to use them) and `open_timeout=10`. `max_size` is the
largest message the client accepts (websockets' default is 1 MiB). `additional_headers` are
sent alongside the JWT header; `subprotocols` is set by the client.

## Message handler

Subclass `AIMPMessageHandlerInf`:

- `receive_message(message)` (required): handler output from the agent, i.e. every JSON
  value that isn't a control message. Usually an AIMP message (a JSON list). A large reply
  may arrive as a sequence of `{"aimp_chunk": ...}` objects, in order and before the
  interaction's `done`; reassembling them is up to the handler. Agents may also send JSON
  objects of their own. Each counts in `result.messages_received`.
- `receive_control(message)` (optional): a server control message, i.e. a JSON object whose
  `type` is `sdk.status` or `sdk.error` (`protocol.is_control_message()`, the same rule as
  the SDK).
- `receive_other(text)` (optional): a text frame that isn't JSON, as the raw text. The
  client also logs these at WARNING.

Callbacks run on the client's receive loop, one at a time and in order, and all callbacks
for an interaction finish before its `request()` returns. Exceptions are logged and recorded
as `result.handler_error`; they don't stop the loop.

**Callbacks must not block.** The client waits for each callback before it reads the next
frame. While a callback runs, nothing is read, so the server's sends back up. If the server
can't send its next control message (such as `done`) within 5 seconds, it closes the
connection with 1008 `Client not reading`. This is most likely with a large reply (many
`aimp_chunk` frames) and a callback that does slow work per frame. Keep callbacks to
collecting or queueing data, and do parsing, storage or network calls after `request()`
returns, or in a separate task fed by a queue. Never call blocking (non-async) code in a
callback.

## Timeouts

**A client timeout cancels the agent's work.** When `request()` times out, the client
interrupts the request (or, with `close_on_timeout=True`, disconnects), and either way the
server cancels the running handler. So the timeout must be longer than the longest
legitimate run of that request, not just longer than a typical one: slow but healthy work
is cut off otherwise.

- `request(timeout=630)` by default: a little above the server's default task deadline
  (`WS_TASK_TIMEOUT_SECONDS`, 600s), so the server's 4008 normally arrives first. Lower it
  per request only when you know the agent's worst case for that request; if the agent
  enforces its own deadline, stay above it so the agent can report the timeout itself.
- On timeout the result is `timed_out` and the session stays usable (it's closed with
  `close_on_timeout=True`).
- The server also closes idle connections (no handler running) after
  `WS_IDLE_TIMEOUT_SECONDS` (300s by default) with 1000.
- Keep a client timeout: the server's deadline can't stop an agent stuck in blocking
  (non-async) code.

## Development

```bash
make install-dev          # or: conda env create -f environment.yml
make test                 # unit tests; integration tests skip without the SDK
pip install -e ".[dev,integration]"   # SDK 0.2.0 for the integration tests
make test-integration     # or, against a checkout: make test-integration SDK_PATH=/path/to/sdk
make lint                 # flake8, mypy
make format               # black, isort
make check-dist           # build and twine check
```

The integration tests run the real SDK (contract v2) under uvicorn, from the `integration` extra
or from a checkout of `vital-agent-container-python` given as `SDK_PATH`.

`examples/smoke_client.py` is a manual check against a running container.

## License

Apache License 2.0. See [LICENSE](LICENSE).
