import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

from vital_agent_container_client.aimp_message_handler_inf import AIMPMessageHandlerInf
from vital_agent_container_client.exceptions import (
    AuthenticationError,
    BusyError,
    NotConnectedError,
    ProtocolMismatchError,
)
from vital_agent_container_client.protocol import (
    AIMP_CHUNK_KEY,
    CONTROL_ERROR,
    CONTROL_STATUS,
    ERROR_BUSY,
    ERROR_CANCELLING,
    INTENT_CLOSE,
    INTENT_INTERRUPT,
    INTENT_PROPERTY,
    JWT_PROPERTY,
    STATUS_DONE,
    STATUS_IDLE,
    STATUS_INTERRUPTED,
    STATUS_READY,
    SUBPROTOCOL,
    URI_PROPERTY,
    CloseCode,
)
from vital_agent_container_client.results import CloseResult, InteractionResult

logger = logging.getLogger(__name__)

# A little above the server's default task deadline (WS_TASK_TIMEOUT_SECONDS, 600s), so
# the server's 4008 normally arrives first
DEFAULT_REQUEST_TIMEOUT = 630.0
# For interrupt() and close(): the server answers these at once
DEFAULT_CONTROL_TIMEOUT = 10.0

JwtMode = Literal["header", "payload"]
InterruptOutcome = Literal["interrupted", "idle", "closed"]

# Outcomes of an interaction's future
_CLOSED = "closed"
_TIMED_OUT = "timed_out"


def websocket_url(base_url: str, path: str = "/ws") -> str:
    """Turn the service's base URL into its WebSocket URL: http -> ws, https -> wss."""
    parts = urlsplit(base_url)
    schemes = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
    scheme = schemes.get(parts.scheme.lower())
    if scheme is None or not parts.netloc:
        raise ValueError(f"base_url must be an http(s) or ws(s) URL, got {base_url!r}")
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + path, "", ""))


def _new_uri() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


class _Interaction:
    """One request, from sending it until its terminal signal."""

    def __init__(self, uri: str):
        self.uri = uri
        self.outcome: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.messages_received = 0
        self.handler_error: Optional[BaseException] = None

    def resolve(self, outcome: str) -> None:
        if not self.outcome.done():
            self.outcome.set_result(outcome)


class VitalAgentContainerClient:
    """Client for an agent container's WebSocket protocol (contract v2).

    One connection carries any number of interactions, one at a time::

        async with VitalAgentContainerClient(base_url=url, handler=handler) as client:
            result = await client.request(message)

    Each ``request()`` ends on exactly one terminal signal from the server: ``done``,
    ``interrupted`` or a close. Replies are delivered to ``handler`` as they arrive.
    """

    def __init__(
        self,
        *,
        base_url: str,
        handler: AIMPMessageHandlerInf,
        jwt_token: Optional[str] = None,
        jwt_auth_token: Optional[str] = None,
        jwt_mode: JwtMode = "header",
    ):
        if jwt_mode not in ("header", "payload"):
            raise ValueError(
                f"jwt_mode must be 'header' or 'payload', got {jwt_mode!r}"
            )
        if jwt_mode == "header" and jwt_auth_token:
            raise ValueError("jwt_auth_token is only sent in payload mode")
        if jwt_mode == "payload" and not (jwt_token or jwt_auth_token):
            raise ValueError("payload mode needs jwt_token and/or jwt_auth_token")
        websocket_url(base_url)  # validate early

        self.base_url = base_url
        self.handler = handler
        self.jwt_token = jwt_token
        self.jwt_auth_token = jwt_auth_token
        self.jwt_mode = jwt_mode

        self._ws: Optional[ClientConnection] = None
        self._reader: Optional[asyncio.Task[None]] = None
        self._closed = asyncio.Event()
        self._ready = asyncio.Event()
        self._close_result: Optional[CloseResult] = None
        self._current: Optional[_Interaction] = None
        self._pending_interrupt: Optional[Tuple[str, asyncio.Future[str]]] = None
        self._request_lock = asyncio.Lock()
        self._interrupt_lock = asyncio.Lock()

    # ── connection ───────────────────────────────────────────────

    async def check_health(self) -> Any:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self.base_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    @property
    def close_result(self) -> Optional[CloseResult]:
        """How the last connection ended, once it has."""
        return self._close_result

    async def connect(self, path: str = "/ws", *, open_timeout: float = 10.0) -> None:
        """Open the connection and check that the server speaks ``vital-agent.v2``.

        Raises AuthenticationError if the handshake is rejected with HTTP 401/403 (JWT
        header mode reports a missing or invalid token that way), and
        ProtocolMismatchError if the server doesn't select the v2 subprotocol.
        """
        if self.is_open:
            raise RuntimeError("already connected")
        url = websocket_url(self.base_url, path)
        headers = {}
        if self.jwt_mode == "header" and self.jwt_token:
            headers["authorization"] = self.jwt_token
        try:
            ws = await connect(
                url,
                additional_headers=headers,
                subprotocols=[Subprotocol(SUBPROTOCOL)],
                open_timeout=open_timeout,
            )
        except InvalidStatus as e:
            status = e.response.status_code
            if status in (401, 403):
                raise AuthenticationError(
                    f"WebSocket handshake rejected with HTTP {status} by {url}: JWT "
                    f"missing or invalid (header mode), or no WebSocket endpoint there"
                ) from e
            raise
        if ws.subprotocol != SUBPROTOCOL:
            await ws.close(code=CloseCode.NORMAL, reason="Protocol mismatch")
            raise ProtocolMismatchError(
                f"{url} did not select subprotocol {SUBPROTOCOL!r} (selected "
                f"{ws.subprotocol!r}); the server doesn't speak protocol v2"
            )

        self._ws = ws
        self._closed = asyncio.Event()
        self._ready = asyncio.Event()
        self._close_result = None
        self._reader = asyncio.create_task(self._read_loop(ws))
        logger.info(f"Connected to {url}")

    async def close(
        self, *, timeout: float = DEFAULT_CONTROL_TIMEOUT
    ) -> Optional[CloseResult]:
        """End the connection gracefully: send the ``close`` intent, wait for the close.

        A running handler finishes first (its replies are still delivered). If the
        server doesn't close within ``timeout``, the socket is closed. Safe to call more
        than once.
        """
        if self._ws is None:
            return self._close_result
        if self.is_open:
            try:
                await self._send(self._intent(INTENT_CLOSE))
            except ConnectionClosed:
                pass
            try:
                await asyncio.wait_for(self._closed.wait(), timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Server didn't close within {timeout}s; disconnecting")
                return await self.disconnect()
        await self._wait_reader()
        return self._close_result

    async def disconnect(self) -> Optional[CloseResult]:
        """Close the socket now. The server cancels a running handler straight away."""
        if self._ws is None:
            return self._close_result
        if self.is_open:
            await self._ws.close(code=CloseCode.NORMAL, reason="Client disconnect")
        await self._wait_reader()
        return self._close_result

    async def __aenter__(self) -> "VitalAgentContainerClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # ── interactions ─────────────────────────────────────────────

    async def request(
        self,
        message: List[Dict[str, Any]],
        *,
        timeout: Optional[float] = DEFAULT_REQUEST_TIMEOUT,
        close_after: bool = False,
        close_on_timeout: bool = False,
    ) -> InteractionResult:
        """Send an AIMP message and wait until its interaction ends.

        The first object gets a ``URI`` if it has none (control messages refer to it),
        plus the JWT in payload mode; the caller's message isn't modified. Requests on
        one client are serialized.

        - ``close_after``: also send the ``close`` intent, so the server closes (1000)
          once the handler returns. For one-shot connections.
        - On ``timeout`` the handler is interrupted and the connection stays usable;
          with ``close_on_timeout`` the connection is closed instead.

        If the server is still cancelling earlier interrupted requests, the request is
        sent again once (after the server's ``ready``). Raises BusyError if the server
        rejects it as ``busy``, or as ``cancelling`` a second time.
        """
        prepared = self._prepare(message)
        uri = prepared[0][URI_PROPERTY]
        async with self._request_lock:
            self._require_open()
            loop = asyncio.get_running_loop()
            deadline = None if timeout is None else loop.time() + timeout
            retried = False
            while True:
                interaction = _Interaction(uri)
                self._current = interaction
                self._ready.clear()
                try:
                    try:
                        await self._send(prepared)
                        if close_after:
                            await self._send(self._intent(INTENT_CLOSE))
                    except ConnectionClosed:
                        await self._wait_reader()
                    try:
                        outcome = await asyncio.wait_for(
                            asyncio.shield(interaction.outcome),
                            self._remaining(deadline),
                        )
                    except asyncio.TimeoutError:
                        return await self._on_request_timeout(
                            interaction, close_on_timeout
                        )
                finally:
                    if self._current is interaction:
                        self._current = None

                if outcome == ERROR_CANCELLING and not retried:
                    retried = True
                    waited = await self._wait_ready(deadline)
                    if waited == STATUS_READY:
                        continue
                    if waited == _CLOSED:
                        close = self._close_result
                        return InteractionResult(
                            status="closed",
                            request_uri=uri,
                            messages_received=0,
                            close=close,
                            reconnect_suggested=close is not None and close.is_normal,
                        )
                    return InteractionResult(
                        status="timed_out", request_uri=uri, messages_received=0
                    )
                if outcome in (ERROR_BUSY, ERROR_CANCELLING):
                    raise BusyError(outcome)
                if outcome == STATUS_DONE and close_after:
                    # The handler finished before the server read our close intent
                    # (done, then the close): the connection's close ends this request
                    try:
                        await asyncio.wait_for(
                            self._closed.wait(), self._remaining(deadline)
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Server didn't close after done; disconnecting")
                        await self.disconnect()
                    outcome = _CLOSED
                return self._result(interaction, outcome)

    async def interrupt(
        self, *, timeout: float = DEFAULT_CONTROL_TIMEOUT
    ) -> InterruptOutcome:
        """Interrupt the running interaction. The connection stays open.

        Returns ``"interrupted"`` (a handler was cancelled; its ``request()`` returns
        ``interrupted``), ``"idle"`` (nothing was running, e.g. it had just finished) or
        ``"closed"``. Raises asyncio.TimeoutError if the server doesn't answer in time.
        """
        outcome = await self._interrupt(timeout)
        if outcome == _TIMED_OUT:
            raise asyncio.TimeoutError(f"no answer to interrupt within {timeout}s")
        return outcome  # type: ignore[return-value]

    # ── internals ────────────────────────────────────────────────

    def _require_open(self) -> ClientConnection:
        if self._ws is None or not self.is_open:
            raise NotConnectedError("WebSocket is not connected")
        return self._ws

    def _remaining(self, deadline: Optional[float]) -> Optional[float]:
        if deadline is None:
            return None
        return max(0.0, deadline - asyncio.get_running_loop().time())

    def _jwt_json(self) -> str:
        tokens = {"jwt_token": self.jwt_token, "jwt_auth_token": self.jwt_auth_token}
        return json.dumps({k: v for k, v in tokens.items() if v})

    def _prepare(self, message: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if (
            not isinstance(message, list)
            or not message
            or not isinstance(message[0], dict)
        ):
            raise ValueError("message must be a non-empty list of AIMP objects (dicts)")
        if message[0].get(INTENT_PROPERTY) in (INTENT_INTERRUPT, INTENT_CLOSE):
            raise ValueError("use interrupt() / close() to send intents")
        first = dict(message[0])
        if not first.get(URI_PROPERTY):
            first[URI_PROPERTY] = _new_uri()
        if self.jwt_mode == "payload" and not first.get(JWT_PROPERTY):
            first[JWT_PROPERTY] = self._jwt_json()
        return [first, *message[1:]]

    def _intent(self, intent: str, uri: Optional[str] = None) -> List[Dict[str, Any]]:
        obj: Dict[str, Any] = {URI_PROPERTY: uri or _new_uri(), INTENT_PROPERTY: intent}
        if self.jwt_mode == "payload":
            obj[JWT_PROPERTY] = self._jwt_json()
        return [obj]

    async def _send(self, message: List[Dict[str, Any]]) -> None:
        await self._require_open().send(json.dumps(message))

    async def _wait_reader(self) -> None:
        if self._reader is not None:
            await asyncio.shield(self._reader)

    async def _wait_ready(self, deadline: Optional[float]) -> str:
        """After ``cancelling``: wait for ``ready`` or for the connection to end."""
        try:
            await asyncio.wait_for(self._ready.wait(), self._remaining(deadline))
        except asyncio.TimeoutError:
            return _TIMED_OUT
        return _CLOSED if self._closed.is_set() else STATUS_READY

    async def _interrupt(self, timeout: float) -> str:
        async with self._interrupt_lock:
            if not self.is_open:
                return _CLOSED
            uri = _new_uri()
            answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            self._pending_interrupt = (uri, answer)
            try:
                await self._send(self._intent(INTENT_INTERRUPT, uri))
                return await asyncio.wait_for(asyncio.shield(answer), timeout)
            except asyncio.TimeoutError:
                return _TIMED_OUT
            except ConnectionClosed:
                return _CLOSED
            finally:
                self._pending_interrupt = None

    async def _on_request_timeout(
        self, interaction: _Interaction, close_on_timeout: bool
    ) -> InteractionResult:
        logger.warning(f"Request {interaction.uri} timed out")
        disconnected = close_on_timeout
        if close_on_timeout:
            await self.disconnect()
        elif await self._interrupt(DEFAULT_CONTROL_TIMEOUT) == _TIMED_OUT:
            logger.warning("No answer to the interrupt; disconnecting")
            disconnected = True
            await self.disconnect()
        # Unless we closed it ourselves, the interaction may have ended on its own
        # (done, or a server close such as 4008) while we interrupted
        if (
            not disconnected
            and interaction.outcome.done()
            and interaction.outcome.result() in (STATUS_DONE, _CLOSED)
        ):
            return self._result(interaction, interaction.outcome.result())
        return InteractionResult(
            status="timed_out",
            request_uri=interaction.uri,
            messages_received=interaction.messages_received,
            close=self._close_result,
            handler_error=interaction.handler_error,
        )

    def _result(self, interaction: _Interaction, outcome: str) -> InteractionResult:
        status = "closed" if outcome == _CLOSED else outcome
        return InteractionResult(
            status=status,  # type: ignore[arg-type]
            request_uri=interaction.uri,
            messages_received=interaction.messages_received,
            close=self._close_result if status == "closed" else None,
            handler_error=interaction.handler_error,
        )

    # ── receive loop ─────────────────────────────────────────────

    async def _read_loop(self, ws: ClientConnection) -> None:
        try:
            while True:
                try:
                    raw = await ws.recv()
                except ConnectionClosed:
                    break
                await self._dispatch(raw)
        except Exception:
            logger.exception("Receive loop failed; disconnecting")
            await ws.close(code=CloseCode.INTERNAL_ERROR, reason="Client error")
        finally:
            self._on_closed(ws)

    async def _dispatch(self, raw: Any) -> None:
        interaction = self._current
        if isinstance(raw, bytes):
            logger.warning("Ignoring a binary frame (the protocol is text only)")
            return
        try:
            data = json.loads(raw)
        except ValueError:
            await self._other(raw, interaction)
            return
        if isinstance(data, list) or (
            isinstance(data, dict) and AIMP_CHUNK_KEY in data
        ):
            if interaction is not None:
                interaction.messages_received += 1
            await self._call(self.handler.receive_message, data, interaction)
        elif isinstance(data, dict) and data.get("type") in (
            CONTROL_STATUS,
            CONTROL_ERROR,
        ):
            # Handler first, so its callbacks for an interaction finish before request()
            # returns
            await self._call(self.handler.receive_control, data, interaction)
            self._on_control(data)
        else:
            await self._other(raw, interaction)

    async def _other(self, raw: str, interaction: Optional[_Interaction]) -> None:
        preview = raw if len(raw) <= 200 else raw[:200] + "..."
        logger.warning(f"Unrecognised frame passed to receive_other: {preview}")
        await self._call(self.handler.receive_other, raw, interaction)

    async def _call(
        self,
        callback: Callable[[Any], Awaitable[None]],
        arg: Any,
        interaction: Optional[_Interaction],
    ) -> None:
        try:
            await callback(arg)
        except Exception as e:
            logger.exception(f"Message handler {callback.__name__} raised")
            if interaction is not None and interaction.handler_error is None:
                interaction.handler_error = e

    def _on_control(self, message: Dict[str, Any]) -> None:
        current = self._current
        ref = message.get("in_reply_to")
        if message.get("type") == CONTROL_STATUS:
            status = message.get("status")
            if status in (STATUS_DONE, STATUS_INTERRUPTED):
                # interrupted names the interrupted request, not the interrupt message
                if current is not None and ref == current.uri:
                    current.resolve(status)
                if status == STATUS_INTERRUPTED:
                    self._answer_interrupt(STATUS_INTERRUPTED)
            elif status == STATUS_IDLE:
                # idle names the interrupt message itself
                if (
                    self._pending_interrupt is not None
                    and ref == self._pending_interrupt[0]
                ):
                    self._answer_interrupt(STATUS_IDLE)
            elif status == STATUS_READY:
                self._ready.set()
            else:
                logger.debug(f"Ignoring unknown status {status!r}")
        else:
            error = message.get("error")
            if error in (ERROR_BUSY, ERROR_CANCELLING) and current is not None:
                if ref == current.uri:
                    current.resolve(error)
                    return
            logger.warning(f"Server error {error!r}: {message.get('detail')}")

    def _answer_interrupt(self, outcome: str) -> None:
        if self._pending_interrupt is not None:
            answer = self._pending_interrupt[1]
            if not answer.done():
                answer.set_result(outcome)

    def _on_closed(self, ws: ClientConnection) -> None:
        close = CloseResult(code=ws.close_code, reason=ws.close_reason or None)
        self._close_result = close
        level = logging.INFO
        if close.code in (CloseCode.GOING_AWAY, CloseCode.POLICY_VIOLATION):
            level = logging.WARNING
        elif close.code != CloseCode.NORMAL:
            level = logging.ERROR
        logger.log(
            level, f"Connection closed: {close.code} {close.reason or ''}".rstrip()
        )
        if self._current is not None:
            self._current.resolve(_CLOSED)
        self._answer_interrupt(_CLOSED)
        self._closed.set()
        self._ready.set()  # wake a request waiting to retry; it checks _closed
