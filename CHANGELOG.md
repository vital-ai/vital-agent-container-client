# Changelog

## 0.1.0 (unreleased)

**Breaking release.** The client now speaks the agent container's WebSocket protocol v2
(subprotocol `vital-agent.v2`) and requires **vital-agent-container-sdk 0.2.0** or later on
the server. It can't talk to older servers: `connect()` raises `ProtocolMismatchError`.
Pin the client and the server versions and upgrade them together.

### Added

- Session API: `connect()`, `request()`, `interrupt()`, `close()`, `disconnect()`, and
  `async with`. A connection carries any number of interactions, one at a time; each ends on
  `done`, `interrupted` or a close.
- `InteractionResult` (status, request URI, messages received, close, `ok`) and
  `CloseResult` / `CloseCode` (1000, 1001, 1003, 1008, 1011, 4001, 4002, 4008).
- `close_after=True` for one-shot requests; `close_on_timeout=True`.
- Automatic retry after the server's `cancelling` / `ready`; `BusyError`.
- JWT payload mode (`jwt_mode="payload"`, `jwt_auth_token`): the JWT goes on every message,
  intents included.
- `AuthenticationError` for an HTTP 401/403 handshake (JWT header mode).
- Optional handler hooks `receive_control` and `receive_other` (logged at WARNING).
- `{"aimp_chunk": ...}` objects (parts of a large reply) are delivered to
  `receive_message` and counted in `messages_received`, like AIMP lists.
- `connect_options` (constructor) and `connect(**options)`: passed to `websockets.connect`
  (e.g. `max_size`, `proxy`, `ping_interval`). Default `proxy=None`: proxy environment
  variables are ignored unless `proxy=True` is passed.
- `websocket_url()`; `py.typed`.

### Changed

- `request()` stamps a `URI` on the first object of each message if it has none.
- Server control messages no longer reach `receive_message`.
- Handler exceptions are logged and recorded on the result instead of stopping the receive
  loop.
- The client logs to `vital_agent_container_client.*` loggers instead of the root logger.
- `http://` / `https://` base URLs map to `ws://` / `wss://` by scheme only (hostnames
  containing "http" were mangled before).
- Packaging moved to `pyproject.toml`.
- Dependencies: `vital-ai-vitalsigns>=0.1.55` and `vital-ai-domain>=0.1.9` (were `>=0.1.32` and
  `>=0.1.7`); `websockets>=14.0` and `httpx>=0.28.0` now have minimum versions.

### Removed

- `open_websocket()`, `close_websocket()`, `send_message()`, `receive_messages()`,
  `handle_close()` and `wait_for_close_or_timeout()`. Migration:

  | 0.0.x | 0.1.0 |
  |---|---|
  | `open_websocket()` | `connect()` |
  | `send_message(m)` + `wait_for_close_or_timeout(t)` | `request(m, timeout=t, close_after=True)` |
  | `close_websocket()` | `close()` (graceful) or `disconnect()` |

- Unused dependencies `six` and `pyyaml`. Install them directly if you relied on getting them
  through this package.

## 0.0.8

- Optional JWT header authentication (`jwt_token`); keyword-only constructor.
