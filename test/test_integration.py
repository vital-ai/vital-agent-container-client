"""End-to-end tests against the real agent container SDK (contract v2, SDK 0.2.0).

Skipped unless the SDK is importable: install it, or point
VITAL_AGENT_CONTAINER_SDK_PATH at a checkout (``make test-integration SDK_PATH=...``).
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from conftest import RecordingHandler

from vital_agent_container_client import (
    SUBPROTOCOL,
    AuthenticationError,
    CloseCode,
    VitalAgentContainerClient,
)

pytestmark = pytest.mark.integration

_sdk_path = os.environ.get("VITAL_AGENT_CONTAINER_SDK_PATH")
if _sdk_path:
    sys.path.insert(0, _sdk_path)

# The SDK writes app.log to the working directory on import, and loads .env files
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp(prefix="vacc-integration-"))
try:
    sdk_protocol = pytest.importorskip("vital_agent_container.protocol")
    if not hasattr(sdk_protocol, "SUBPROTOCOL"):
        pytest.skip("installed SDK predates contract v2", allow_module_level=True)
    jwt = pytest.importorskip("jwt")
    uvicorn = pytest.importorskip("uvicorn")
    from vital_agent_container.agent_container_app import AgentContainerApp
    from vital_agent_container.handler.aimp_message_handler_inf import (
        AIMPMessageHandlerInf as ServiceHandler,
    )
finally:
    os.chdir(_cwd)

SECRET = "integration-test-secret"


class AgentHandler(ServiceHandler):
    """Behavior is chosen by the request's "mode" field."""

    async def process_message(
        self, *, app_config, client, connection, data, jwt_payload=None, jwt_token=None
    ):
        request = json.loads(data)[0]
        mode = request.get("mode", "echo")
        if mode == "echo":
            for i in range(request.get("replies", 1)):
                await connection.send_json([{"type": "reply", "n": i}])
        elif mode == "slow":
            await connection.send_json([{"type": "reply", "n": 0}])
            await asyncio.sleep(30)
        elif mode == "slow_unwind":
            await connection.send_json([{"type": "reply", "n": 0}])
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.sleep(0.5)  # finishes within the server's unwind grace
                raise
        elif mode == "raise":
            raise RuntimeError("handler failed")
        elif mode == "close":
            await connection.send_json([{"type": "reply", "n": 0}])
            connection.request_close()
        elif mode == "whoami":
            subject = (jwt_payload or {}).get("sub") or (
                (jwt_payload or {}).get("jwt_token") or {}
            ).get("sub")
            await connection.send_json([{"type": "reply", "sub": subject}])


def jwt_config(mode):
    if mode == "disabled":
        return {"enabled": False}
    return {
        "enabled": True,
        "secret_key": SECRET,
        "algorithm": "HS256",
        "enforcement_mode": mode,
        "required_claims": ["sub", "exp", "iat"],
        "custom_claims": [],
    }


def token(sub="tester"):
    now = datetime.now(timezone.utc)
    claims = {"sub": sub, "exp": now + timedelta(hours=1), "iat": now}
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture
async def service():
    """Start the SDK app under uvicorn; call with (jwt mode, **timeouts) -> base URL."""
    servers = []

    async def start(mode="disabled", **timeouts):
        app = AgentContainerApp(
            AgentHandler(), app_config={}, jwt_config=jwt_config(mode), **timeouts
        )
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        servers.append((server, task))
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for server, task in servers:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


def msg(mode="echo", **fields):
    return [{"type": "test", "mode": mode, **fields}]


def test_contract_constants_match_the_sdk():
    assert SUBPROTOCOL == sdk_protocol.SUBPROTOCOL
    assert {c.name: int(c) for c in CloseCode} == {
        c.name: int(c) for c in sdk_protocol.CloseCode
    }


async def test_health_and_multi_turn_session(service):
    handler = RecordingHandler()
    url = await service()
    async with VitalAgentContainerClient(base_url=url, handler=handler) as client:
        assert (await client.check_health()) == {"status": "ok"}
        first = await client.request(msg(replies=2), timeout=5)
        second = await client.request(msg(), timeout=5)
        assert (first.status, first.messages_received) == ("done", 2)
        assert (second.status, second.messages_received) == ("done", 1)
    assert client.close_result.is_normal
    assert client.close_result.reason == "Closed by client request"


async def test_one_shot_close_after(service):
    url = await service()
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()
    result = await client.request(msg(replies=3), timeout=5, close_after=True)
    assert result.ok and result.status == "closed" and result.messages_received == 3
    assert not client.is_open


async def test_handler_requested_close(service):
    url = await service()
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()
    result = await client.request(msg("close"), timeout=5)
    assert result.ok and result.close.reason == "Closed by service"


async def test_interrupt_then_immediate_resend(service):
    handler = RecordingHandler()
    url = await service()
    async with VitalAgentContainerClient(base_url=url, handler=handler) as client:
        pending = asyncio.create_task(client.request(msg("slow"), timeout=10))
        while not handler.messages:
            await asyncio.sleep(0.01)
        assert await client.interrupt() == "interrupted"
        assert (await pending).status == "interrupted"
        assert (await client.request(msg(), timeout=5)).status == "done"
        assert await client.interrupt() == "idle"


async def test_overlap_cap_cancelling_then_ready(service):
    """Two interrupted handlers still unwinding: the next request waits for ready."""
    handler = RecordingHandler()
    url = await service()
    async with VitalAgentContainerClient(base_url=url, handler=handler) as client:
        for _ in range(2):
            pending = asyncio.create_task(
                client.request(msg("slow_unwind"), timeout=10)
            )
            count = len(handler.messages)
            while len(handler.messages) == count:
                await asyncio.sleep(0.01)
            assert await client.interrupt() == "interrupted"
            assert (await pending).status == "interrupted"
        result = await client.request(msg(), timeout=10)
        assert result.status == "done"
        assert any(c.get("error") == "cancelling" for c in handler.controls)
        assert any(c.get("status") == "ready" for c in handler.controls)


async def test_timeout_interrupts_and_session_continues(service):
    url = await service()
    async with VitalAgentContainerClient(base_url=url, handler=RecordingHandler()) as c:
        result = await c.request(msg("slow"), timeout=0.3)
        assert result.status == "timed_out" and result.messages_received == 1
        assert (await c.request(msg(), timeout=5)).status == "done"


async def test_server_deadline_is_4008(service):
    url = await service(ws_task_timeout=0.3)
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()
    result = await client.request(msg("slow"), timeout=5)
    assert result.status == "closed" and result.close.is_handler_timeout


async def test_handler_error_is_1011(service):
    url = await service()
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()
    result = await client.request(msg("raise"), timeout=5)
    assert result.status == "closed" and result.close.is_internal_error


async def test_idle_timeout_closes_1000(service):
    url = await service(ws_idle_timeout=0.3)
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()
    await asyncio.wait_for(client._closed.wait(), 5)
    assert (
        client.close_result.is_normal and client.close_result.reason == "Idle timeout"
    )


async def test_header_mode(service):
    handler = RecordingHandler()
    url = await service("header")
    async with VitalAgentContainerClient(
        base_url=url, handler=handler, jwt_token=token("header-user")
    ) as client:
        assert (await client.request(msg("whoami"), timeout=5)).ok
    assert handler.messages[0][0]["sub"] == "header-user"

    for bad in (None, "not-a-jwt"):
        rejected = VitalAgentContainerClient(
            base_url=url, handler=RecordingHandler(), jwt_token=bad
        )
        with pytest.raises(AuthenticationError):
            await rejected.connect()


async def test_payload_mode_attaches_the_jwt_to_requests_and_intents(service):
    handler = RecordingHandler()
    url = await service("payload")
    async with VitalAgentContainerClient(
        base_url=url,
        handler=handler,
        jwt_mode="payload",
        jwt_token=token("payload-user"),
    ) as client:
        assert (await client.request(msg("whoami"), timeout=5)).ok
        pending = asyncio.create_task(client.request(msg("slow"), timeout=10))
        while len(handler.messages) < 2:
            await asyncio.sleep(0.01)
        assert (
            await client.interrupt() == "interrupted"
        )  # a JWT-less intent would be 4001
        assert (await pending).status == "interrupted"
    assert handler.messages[0][0]["sub"] == "payload-user"
    assert client.close_result.is_normal  # the close intent carried the JWT too


async def test_payload_mode_without_a_jwt_is_4001(service):
    url = await service("payload")
    client = VitalAgentContainerClient(base_url=url, handler=RecordingHandler())
    await client.connect()  # header-mode client: no JWT in the payload
    result = await client.request(msg(), timeout=5)
    assert result.status == "closed" and result.close.is_auth_rejected
