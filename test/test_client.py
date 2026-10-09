import asyncio
import json

import pytest
from conftest import Peer, RecordingHandler, intent_of, uri_of

from vital_agent_container_client import (
    AuthenticationError,
    BusyError,
    CloseCode,
    NotConnectedError,
    ProtocolMismatchError,
    VitalAgentContainerClient,
    websocket_url,
)
from vital_agent_container_client.protocol import (
    AIMP_CHUNK_KEY,
    ERROR_BUSY,
    ERROR_CANCELLING,
    INTENT_CLOSE,
    INTENT_INTERRUPT,
    JWT_PROPERTY,
    STATUS_DONE,
    STATUS_IDLE,
    STATUS_INTERRUPTED,
    STATUS_READY,
    SUBPROTOCOL,
    URI_PROPERTY,
)

MESSAGE = [{"type": "test", "text": "hello"}]


def reply(text: str) -> list:
    return [{"type": "reply", "text": text}]


async def connected(
    base_url: str, handler: RecordingHandler, **kwargs
) -> VitalAgentContainerClient:
    client = VitalAgentContainerClient(base_url=base_url, handler=handler, **kwargs)
    await client.connect()
    return client


async def answer_one(peer: Peer, *replies: str) -> list:
    """Receive one request, send replies and done; return the request."""
    request = await peer.recv()
    for text in replies:
        await peer.send(reply(text))
    await peer.status(STATUS_DONE, uri_of(request))
    return request


# ── URL and construction ─────────────────────────────────────────


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("http://localhost:6006", "ws://localhost:6006/ws"),
        ("https://agent.example.com", "wss://agent.example.com/ws"),
        ("http://myhttphost:8000/", "ws://myhttphost:8000/ws"),
        ("https://host/prefix/", "wss://host/prefix/ws"),
        ("ws://host:1", "ws://host:1/ws"),
        ("wss://host", "wss://host/ws"),
    ],
)
def test_websocket_url_rewrites_the_scheme_only(base_url, expected):
    assert websocket_url(base_url) == expected


@pytest.mark.parametrize("base_url", ["localhost:6006", "ftp://host", "http://"])
def test_websocket_url_rejects_other_urls(base_url):
    with pytest.raises(ValueError):
        websocket_url(base_url)


def test_constructor_validates_jwt_settings(handler):
    with pytest.raises(ValueError):
        VitalAgentContainerClient(
            base_url="http://h", handler=handler, jwt_mode="query"
        )
    with pytest.raises(ValueError):
        VitalAgentContainerClient(
            base_url="http://h", handler=handler, jwt_mode="payload"
        )
    with pytest.raises(ValueError):
        VitalAgentContainerClient(
            base_url="http://h", handler=handler, jwt_auth_token="a"
        )


async def test_request_before_connect_raises(handler):
    client = VitalAgentContainerClient(base_url="http://localhost:1", handler=handler)
    with pytest.raises(NotConnectedError):
        await client.request(MESSAGE)


# ── Connecting ───────────────────────────────────────────────────


async def test_connect_offers_the_subprotocol_and_header_jwt(server, handler):
    url = await server.start(lambda peer: asyncio.sleep(0))
    client = await connected(url, handler, jwt_token="tok")
    assert server.request_headers["Sec-WebSocket-Protocol"] == SUBPROTOCOL
    assert server.request_headers["authorization"] == "tok"
    await client.disconnect()


async def test_server_without_v2_is_a_protocol_mismatch(server, handler):
    url = await server.start(lambda peer: asyncio.sleep(0), subprotocols=[])
    client = VitalAgentContainerClient(base_url=url, handler=handler)
    with pytest.raises(ProtocolMismatchError):
        await client.connect()
    assert not client.is_open


@pytest.mark.parametrize("status", [401, 403])
async def test_handshake_rejection_is_an_authentication_error(server, handler, status):
    url = await server.start(lambda peer: asyncio.sleep(0), reject_status=status)
    client = VitalAgentContainerClient(base_url=url, handler=handler, jwt_token="bad")
    with pytest.raises(AuthenticationError):
        await client.connect()


@pytest.fixture
def connect_calls(monkeypatch):
    """Record the options the client passes to websockets.connect."""
    from vital_agent_container_client import vital_agent_container_client as module

    calls = []
    real_connect = module.connect

    def recording_connect(uri, **kwargs):
        calls.append(kwargs)
        return real_connect(uri, **kwargs)

    monkeypatch.setattr(module, "connect", recording_connect)
    return calls


async def test_connect_defaults_ignore_proxy_environment(
    server, handler, connect_calls
):
    url = await server.start(lambda peer: asyncio.sleep(0))
    client = await connected(url, handler)
    assert connect_calls[0]["proxy"] is None
    assert connect_calls[0]["open_timeout"] == 10.0
    await client.disconnect()


async def test_connect_works_with_a_dead_proxy_in_the_environment(
    server, handler, monkeypatch
):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    url = await server.start(lambda peer: asyncio.sleep(0))

    client = await connected(url, handler)  # proxy=None by default
    await client.disconnect()

    with_env_proxy = VitalAgentContainerClient(
        base_url=url, handler=handler, connect_options={"proxy": True}
    )
    with pytest.raises(OSError):
        await with_env_proxy.connect(open_timeout=2)


async def test_connect_options_precedence_and_headers(server, handler, connect_calls):
    url = await server.start(lambda peer: asyncio.sleep(0))
    client = VitalAgentContainerClient(
        base_url=url,
        handler=handler,
        jwt_token="tok",
        connect_options={"ping_interval": 5, "open_timeout": 3},
    )
    await client.connect(
        open_timeout=4, additional_headers={"x-request-source": "test"}
    )
    options = connect_calls[0]
    assert (options["ping_interval"], options["open_timeout"]) == (5, 4)
    assert options["proxy"] is None
    assert server.request_headers["x-request-source"] == "test"
    assert server.request_headers["authorization"] == "tok"
    await client.disconnect()


def test_reserved_connect_options_are_refused(handler):
    with pytest.raises(ValueError):
        VitalAgentContainerClient(
            base_url="http://h", handler=handler, connect_options={"subprotocols": []}
        )


async def test_reserved_connect_options_are_refused_on_connect(handler):
    client = VitalAgentContainerClient(base_url="http://h", handler=handler)
    with pytest.raises(ValueError):
        await client.connect(subprotocols=[])


async def test_max_size_is_configurable(server, handler):
    big = [{"type": "reply", "text": "x" * (1536 * 1024)}]  # 1.5 MiB

    async def script(peer):
        request = await peer.recv()
        await peer.send(big)
        await peer.status(STATUS_DONE, uri_of(request))

    url = await server.start(script)
    client = await connected(url, handler, connect_options={"max_size": 4 * 2**20})
    assert (await client.request(MESSAGE, timeout=5)).status == "done"
    assert handler.messages == [big]
    await client.disconnect()


async def test_default_max_size_rejects_oversized_frames(server, handler):
    async def script(peer):
        await peer.recv()
        await peer.send([{"text": "x" * (1536 * 1024)}])

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "closed" and not result.ok
    assert handler.messages == []


# ── Interactions ─────────────────────────────────────────────────


async def test_request_ends_on_done_and_delivers_replies(server, handler):
    url = await server.start(lambda peer: answer_one(peer, "one", "two"))
    client = await connected(url, handler)

    result = await client.request(MESSAGE, timeout=5)

    assert result.status == "done" and result.ok
    assert result.messages_received == 2
    assert [m[0]["text"] for m in handler.messages] == ["one", "two"]
    sent = server.received[0]
    assert result.request_uri == uri_of(sent)
    assert sent[0][URI_PROPERTY].startswith("urn:uuid:")
    assert URI_PROPERTY not in MESSAGE[0]  # the caller's message isn't modified
    assert client.is_open
    await client.disconnect()


async def test_caller_uri_is_kept(server, handler):
    url = await server.start(lambda peer: answer_one(peer))
    client = await connected(url, handler)
    result = await client.request([{"text": "x", URI_PROPERTY: "urn:mine"}], timeout=5)
    assert result.request_uri == "urn:mine" == uri_of(server.received[0])
    await client.disconnect()


async def test_several_requests_on_one_connection(server, handler):
    async def script(peer):
        for i in range(3):
            await answer_one(peer, f"r{i}")

    url = await server.start(script)
    client = await connected(url, handler)
    results = [await client.request(MESSAGE, timeout=5) for _ in range(3)]
    assert [r.status for r in results] == ["done"] * 3
    assert len({r.request_uri for r in results}) == 3
    await client.disconnect()


async def test_concurrent_requests_are_serialized(server, handler):
    async def script(peer):
        for _ in range(2):
            await answer_one(peer, "r")

    url = await server.start(script)
    client = await connected(url, handler)
    results = await asyncio.gather(
        client.request(MESSAGE, timeout=5), client.request(MESSAGE, timeout=5)
    )
    assert [r.status for r in results] == ["done", "done"]
    await client.disconnect()


async def test_done_for_another_request_is_ignored(server, handler):
    async def script(peer):
        request = await peer.recv()
        await peer.status(STATUS_DONE, "urn:someone-else")
        await peer.send(reply("late"))
        await peer.status(STATUS_DONE, uri_of(request))

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "done" and result.messages_received == 1
    await client.disconnect()


async def test_control_and_other_frames_are_routed(server, handler, caplog):
    async def script(peer):
        request = await peer.recv()
        await peer.send("not json")
        await peer.send({"unexpected": "object"})
        await peer.send(b"\x00binary")
        await peer.send(reply("ok"))
        await peer.status(STATUS_DONE, uri_of(request))

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)

    assert result.status == "done" and result.messages_received == 1
    assert handler.messages == [reply("ok")]
    assert handler.others == ["not json", json.dumps({"unexpected": "object"})]
    assert [c["status"] for c in handler.controls] == [STATUS_DONE]
    unrecognised = [r for r in caplog.records if "Unrecognised frame" in r.getMessage()]
    assert [r.levelname for r in unrecognised] == ["WARNING", "WARNING"]
    await client.disconnect()


async def test_aimp_chunks_go_to_receive_message_and_are_counted(server, handler):
    """A large reply split into aimp_chunk objects reaches the handler in order."""
    chunks = [
        {AIMP_CHUNK_KEY: {"id": "r1", "seq": i, "data": f"part{i}"}} for i in range(3)
    ]

    async def script(peer):
        request = await peer.recv()
        for chunk in chunks:
            await peer.send(chunk)
        await peer.send(reply("small"))
        await peer.status(STATUS_DONE, uri_of(request))

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)

    assert result.status == "done" and result.messages_received == 4
    assert handler.messages == [*chunks, reply("small")]
    assert handler.others == []
    await client.disconnect()


async def test_handler_error_is_recorded_and_the_loop_continues(server):
    handler = RecordingHandler(fail_on="boom")
    url = await server.start(lambda peer: answer_one(peer, "boom", "after"))
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "done" and result.messages_received == 2
    assert isinstance(result.handler_error, RuntimeError)
    await client.disconnect()


async def test_intents_and_invalid_messages_are_refused(server, handler):
    url = await server.start(lambda peer: asyncio.sleep(0))
    client = await connected(url, handler)
    for bad in (
        [],
        [1],
        "x",
        [{"http://vital.ai/ontology/vital-aimp#hasIntent": "close"}],
    ):
        with pytest.raises(ValueError):
            await client.request(bad)
    await client.disconnect()


# ── Close codes ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code, flag",
    [
        (CloseCode.HANDLER_TIMEOUT, "is_handler_timeout"),
        (CloseCode.INTERNAL_ERROR, "is_internal_error"),
        (CloseCode.UNSUPPORTED_DATA, "is_invalid_message"),
        (CloseCode.POLICY_VIOLATION, "is_client_not_reading"),
        (CloseCode.UNAUTHORIZED, "is_auth_rejected"),
        (CloseCode.UNSUPPORTED_PROTOCOL, "is_protocol_mismatch"),
        (CloseCode.GOING_AWAY, "is_server_shutdown"),
    ],
)
async def test_close_during_a_request_is_classified_by_code(
    server, handler, code, flag
):
    async def script(peer):
        await peer.recv()
        await peer.send(reply("partial"))
        await peer.close(code, "some reason")

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)

    assert result.status == "closed" and not result.ok
    assert result.close.code == code and getattr(result.close, flag)
    assert result.close.reason == "some reason"
    assert result.messages_received == 1
    assert client.close_result == result.close and not client.is_open
    with pytest.raises(NotConnectedError):
        await client.request(MESSAGE)


async def test_service_close_after_reply_is_ok(server, handler):
    async def script(peer):
        await peer.recv()
        await peer.send(reply("bye"))
        await peer.close(CloseCode.NORMAL, "Closed by service")

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "closed" and result.ok and result.close.is_normal


async def test_close_after_sends_the_close_intent(server, handler):
    async def script(peer):
        request = await peer.recv()
        close = await peer.recv()
        assert intent_of(close) == INTENT_CLOSE and uri_of(close) != uri_of(request)
        await peer.send(reply("only"))
        await peer.close(CloseCode.NORMAL, "Closed by client request")

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5, close_after=True)
    assert result.ok and result.status == "closed" and result.messages_received == 1
    assert server.script_error is None


async def test_close_after_waits_for_the_close_after_done(server, handler):
    """The handler finished before the server read the close intent: done, then 1000."""

    async def script(peer):
        request = await peer.recv()
        await peer.send(reply("only"))
        await peer.status(STATUS_DONE, uri_of(request))
        assert intent_of(await peer.recv()) == INTENT_CLOSE
        await peer.close(CloseCode.NORMAL, "Closed by client request")

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5, close_after=True)
    assert result.ok and result.status == "closed" and result.close.is_normal
    assert not client.is_open


async def test_close_sends_the_intent_and_is_idempotent(server, handler):
    async def script(peer):
        message = await peer.recv()
        assert intent_of(message) == INTENT_CLOSE
        await peer.close(CloseCode.NORMAL, "Closed by client request")

    url = await server.start(script)
    client = await connected(url, handler)
    first = await client.close()
    assert first.code == CloseCode.NORMAL and first.reason == "Closed by client request"
    assert await client.close() == first
    assert await client.disconnect() == first


async def test_close_falls_back_to_disconnect(server, handler):
    url = await server.start(lambda peer: peer.recv())  # never closes
    client = await connected(url, handler)
    result = await client.close(timeout=0.2)
    assert result is not None and not client.is_open


async def test_async_context_manager(server, handler):
    async def script(peer):
        await answer_one(peer, "x")
        assert intent_of(await peer.recv()) == INTENT_CLOSE
        await peer.close(CloseCode.NORMAL)

    url = await server.start(script)
    async with VitalAgentContainerClient(base_url=url, handler=handler) as client:
        assert (await client.request(MESSAGE, timeout=5)).ok
    assert client.close_result.is_normal


# ── Interrupt ────────────────────────────────────────────────────


async def test_interrupt_ends_the_running_request(server, handler):
    async def script(peer):
        request = await peer.recv()
        await peer.send(reply("working"))
        interrupt = await peer.recv()
        assert intent_of(interrupt) == INTENT_INTERRUPT
        await peer.status(STATUS_INTERRUPTED, uri_of(request))
        await answer_one(peer, "next")

    url = await server.start(script)
    client = await connected(url, handler)
    pending = asyncio.create_task(client.request(MESSAGE, timeout=5))
    while not handler.messages:
        await asyncio.sleep(0.01)

    assert await client.interrupt() == "interrupted"
    result = await pending
    assert result.status == "interrupted" and result.messages_received == 1
    assert (await client.request(MESSAGE, timeout=5)).status == "done"
    await client.disconnect()


async def test_interrupt_while_idle(server, handler):
    async def script(peer):
        interrupt = await peer.recv()
        await peer.status(STATUS_IDLE, uri_of(interrupt))

    url = await server.start(script)
    client = await connected(url, handler)
    assert await client.interrupt() == "idle"
    await client.disconnect()


async def test_interrupt_crossing_done(server, handler):
    """done, then idle: the request ends on done; the interrupt finds nothing."""
    release = asyncio.Event()

    async def script(peer):
        request = await peer.recv()
        await release.wait()
        interrupt = await peer.recv()
        await peer.status(STATUS_DONE, uri_of(request))
        await peer.status(STATUS_IDLE, uri_of(interrupt))

    url = await server.start(script)
    client = await connected(url, handler)
    pending = asyncio.create_task(client.request(MESSAGE, timeout=5))
    await asyncio.sleep(0.05)
    release.set()
    assert await client.interrupt() == "idle"
    assert (await pending).status == "done"
    await client.disconnect()


async def test_close_then_interrupt(server, handler):
    """The server answers interrupted, then closes 1000."""

    async def script(peer):
        request = await peer.recv()
        assert intent_of(await peer.recv()) == INTENT_CLOSE
        assert intent_of(await peer.recv()) == INTENT_INTERRUPT
        await peer.status(STATUS_INTERRUPTED, uri_of(request))
        await peer.close(CloseCode.NORMAL, "Closed by client request")

    url = await server.start(script)
    client = await connected(url, handler)
    pending = asyncio.create_task(client.request(MESSAGE, timeout=5))
    await asyncio.sleep(0.05)
    closing = asyncio.create_task(client.close())
    await asyncio.sleep(0.05)
    assert await client.interrupt() == "interrupted"
    assert (await pending).status == "interrupted"
    assert (await closing).is_normal


# ── Timeouts ─────────────────────────────────────────────────────


async def test_timeout_interrupts_and_keeps_the_session(server, handler):
    async def script(peer):
        request = await peer.recv()
        await peer.send(reply("slow"))
        interrupt = await peer.recv()
        assert intent_of(interrupt) == INTENT_INTERRUPT
        await peer.status(STATUS_INTERRUPTED, uri_of(request))
        await answer_one(peer)

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=0.2)
    assert result.status == "timed_out" and not result.ok
    assert result.messages_received == 1 and result.close is None
    assert (await client.request(MESSAGE, timeout=5)).status == "done"
    await client.disconnect()


async def test_timeout_with_close_on_timeout_disconnects(server, handler):
    url = await server.start(lambda peer: peer.recv())
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=0.2, close_on_timeout=True)
    assert result.status == "timed_out" and result.close is not None
    assert not client.is_open


async def test_timeout_that_crosses_done_reports_done(server, handler):
    async def script(peer):
        request = await peer.recv()
        interrupt = await peer.recv()
        await peer.status(STATUS_DONE, uri_of(request))
        await peer.status(STATUS_IDLE, uri_of(interrupt))

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=0.2)
    assert result.status == "done"
    await client.disconnect()


# ── busy / cancelling / ready ────────────────────────────────────


async def test_cancelling_waits_for_ready_and_resends(server, handler):
    async def script(peer):
        first = await peer.recv()
        await peer.error(ERROR_CANCELLING, uri_of(first))
        await asyncio.sleep(0.05)
        await peer.status(STATUS_READY, None)
        second = await peer.recv()
        assert uri_of(second) == uri_of(first)
        await peer.status(STATUS_DONE, uri_of(second))

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "done" and len(server.received) == 2
    assert server.script_error is None
    await client.disconnect()


async def test_ready_before_the_cancelling_is_handled_is_not_lost(server, handler):
    async def script(peer):
        first = await peer.recv()
        await peer.error(ERROR_CANCELLING, uri_of(first))
        await peer.status(STATUS_READY, None)
        await answer_one(peer)

    url = await server.start(script)
    client = await connected(url, handler)
    assert (await client.request(MESSAGE, timeout=5)).status == "done"
    await client.disconnect()


async def test_idle_close_while_waiting_for_ready_suggests_reconnect(server, handler):
    async def script(peer):
        first = await peer.recv()
        await peer.error(ERROR_CANCELLING, uri_of(first))
        await peer.close(CloseCode.NORMAL, "Idle timeout")

    url = await server.start(script)
    client = await connected(url, handler)
    result = await client.request(MESSAGE, timeout=5)
    assert result.status == "closed" and result.reconnect_suggested and not result.ok


async def test_second_cancelling_raises(server, handler):
    async def script(peer):
        for _ in range(2):
            message = await peer.recv()
            await peer.error(ERROR_CANCELLING, uri_of(message))
            await peer.status(STATUS_READY, None)

    url = await server.start(script)
    client = await connected(url, handler)
    with pytest.raises(BusyError) as raised:
        await client.request(MESSAGE, timeout=5)
    assert raised.value.error == ERROR_CANCELLING
    await client.disconnect()


async def test_busy_raises(server, handler):
    async def script(peer):
        message = await peer.recv()
        await peer.error(ERROR_BUSY, uri_of(message))

    url = await server.start(script)
    client = await connected(url, handler)
    with pytest.raises(BusyError) as raised:
        await client.request(MESSAGE, timeout=5)
    assert raised.value.error == ERROR_BUSY
    await client.disconnect()


# ── Payload-mode JWT ─────────────────────────────────────────────


async def test_payload_mode_puts_the_jwt_on_every_message(server, handler):
    async def script(peer):
        request = await peer.recv()
        interrupt = await peer.recv()
        await peer.status(STATUS_INTERRUPTED, uri_of(request))
        close = await peer.recv()
        assert [intent_of(m) for m in (interrupt, close)] == [
            INTENT_INTERRUPT,
            INTENT_CLOSE,
        ]
        await peer.close(CloseCode.NORMAL)

    url = await server.start(script)
    client = await connected(
        url, handler, jwt_mode="payload", jwt_token="transport", jwt_auth_token="user"
    )
    pending = asyncio.create_task(client.request(MESSAGE, timeout=5))
    await asyncio.sleep(0.05)
    await client.interrupt()
    await pending
    await client.close()

    assert "authorization" not in server.request_headers
    assert len(server.received) == 3
    for message in server.received:
        assert json.loads(message[0][JWT_PROPERTY]) == {
            "jwt_token": "transport",
            "jwt_auth_token": "user",
        }


async def test_payload_mode_keeps_a_caller_jwt(server, handler):
    url = await server.start(lambda peer: answer_one(peer))
    client = await connected(url, handler, jwt_mode="payload", jwt_token="t")
    await client.request([{JWT_PROPERTY: "caller"}], timeout=5)
    assert server.received[0][0][JWT_PROPERTY] == "caller"
    await client.disconnect()


async def test_header_mode_passes_a_caller_jwt_through(server, handler):
    """Header mode: a caller's own hasJwtJSON (e.g. a user token) is sent unchanged."""
    user_jwt = json.dumps({"jwt_auth_token": "user-token"})
    url = await server.start(lambda peer: answer_one(peer))
    client = await connected(url, handler, jwt_token="transport")
    await client.request([{"type": "test", JWT_PROPERTY: user_jwt}], timeout=5)
    assert server.received[0][0][JWT_PROPERTY] == user_jwt
    assert server.request_headers["authorization"] == "transport"
    await client.disconnect()


async def test_header_mode_adds_no_payload_jwt(server, handler):
    url = await server.start(lambda peer: answer_one(peer))
    client = await connected(url, handler, jwt_token="transport")
    await client.request(MESSAGE, timeout=5)
    assert JWT_PROPERTY not in server.received[0][0]
    await client.disconnect()
