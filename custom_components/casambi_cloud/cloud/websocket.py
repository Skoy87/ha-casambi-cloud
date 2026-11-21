"""A single Casambi Cloud WebSocket wire.

One instance manages one connection attempt lifecycle for one network:
connect, OPEN the wire, keep it alive, stream events. Reconnection policy
lives in the client, which owns the per-network supervisor task.

Protocol notes (verified against https://developer.casambi.com/):
- The API key is passed as the WebSocket subprotocol.
- After connecting the client sends an ``open`` message; the server answers
  with ``wireStatus: openWireSucceed`` on success.
- If no gateway is online for the network, the server sends nothing beyond
  ``openWireSucceed``; when a gateway is available a ``peerChanged`` event
  with its online state follows.
- The server closes connections after 5 minutes without client traffic, so
  an application-level ``ping`` is sent periodically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from ssl import SSLContext
from typing import Any

import aiohttp

from .const import (
    WEBSOCKET_URL,
    WIRE_STATE_CONNECTING,
    WIRE_STATE_DISCONNECTED,
    WIRE_STATE_RUNNING,
    WIRE_STATE_STOPPED,
    WIRE_STATUS_OK,
    WS_CLIENT_TYPE_FRONTEND,
    WS_KEEPALIVE_INTERVAL,
    WS_METHOD_OPEN,
    WS_METHOD_PING,
    wire_status_is_auth_error,
)
from .errors import (
    CasambiAuthenticationError,
    CasambiCommunicationError,
    CasambiNotConnectedError,
    CasambiProtocolError,
)

_LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]

_OPEN_TIMEOUT = 30.0


class CasambiWire:
    """One WebSocket connection with one opened wire to one network."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        api_key: str,
        network_id: str,
        wire_id: int,
        session_id: str,
        ssl_context: SSLContext | None,
        on_event: EventCallback,
        keepalive_interval: float = WS_KEEPALIVE_INTERVAL,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._network_id = network_id
        self.wire_id = wire_id
        self._session_id = session_id
        self._ssl_context = ssl_context
        self._on_event = on_event
        self._keepalive_interval = keepalive_interval

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._open_confirmed: asyncio.Future[None] | None = None
        self.state = WIRE_STATE_DISCONNECTED

    # --- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect, open the wire and wait for openWireSucceed."""
        self.state = WIRE_STATE_CONNECTING
        loop = asyncio.get_running_loop()
        self._open_confirmed = loop.create_future()

        try:
            ssl_arg: SSLContext | bool = (
                self._ssl_context if self._ssl_context is not None else True
            )
            self._ws = await self._session.ws_connect(
                WEBSOCKET_URL,
                protocols=(self._api_key,),
                heartbeat=30,
                ssl=ssl_arg,
            )
        except (aiohttp.ClientError, OSError) as err:
            self.state = WIRE_STATE_DISCONNECTED
            raise CasambiCommunicationError(
                f"WebSocket connect failed: {type(err).__name__}"
            ) from err

        open_message = {
            "method": WS_METHOD_OPEN,
            "id": self._network_id,
            "session": self._session_id,
            "ref": str(uuid.uuid4()),
            "wire": self.wire_id,
            "type": WS_CLIENT_TYPE_FRONTEND,
        }
        await self._send_raw(open_message)

        # Consume messages until the open confirmation (or failure) arrives.
        try:
            async with asyncio.timeout(_OPEN_TIMEOUT):
                while not self._open_confirmed.done():
                    msg = await self._ws.receive()
                    await self._handle_ws_message(msg)
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        raise CasambiCommunicationError(
                            "WebSocket closed before the wire was opened"
                        )
                self._open_confirmed.result()
        except TimeoutError as err:
            await self.close()
            raise CasambiCommunicationError(
                "Timeout waiting for wire open confirmation"
            ) from err
        except Exception:
            await self.close()
            raise

        self.state = WIRE_STATE_RUNNING
        self._keepalive_task = asyncio.create_task(
            self._keepalive_loop(), name=f"casambi-keepalive-{self.wire_id}"
        )

    async def listen(self) -> None:
        """Stream messages until the connection ends. Returns on close."""
        if self._ws is None:
            raise CasambiNotConnectedError("listen() called before connect()")
        try:
            while True:
                msg = await self._ws.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSE,
                ):
                    _LOGGER.debug(
                        "Wire %s: connection closed by server", self.wire_id
                    )
                    return
                if msg.type == aiohttp.WSMsgType.ERROR:
                    raise CasambiCommunicationError("WebSocket transport error")
                await self._handle_ws_message(msg)
        finally:
            await self.close()

    async def close(self) -> None:
        """Tear down the connection and the keepalive task."""
        if self.state != WIRE_STATE_STOPPED:
            self.state = WIRE_STATE_DISCONNECTED
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except (aiohttp.ClientError, OSError):  # pragma: no cover
                pass
        self._ws = None

    def mark_stopped(self) -> None:
        """Flag an intentional shutdown so callers skip reconnection."""
        self.state = WIRE_STATE_STOPPED

    @property
    def connected(self) -> bool:
        return (
            self.state == WIRE_STATE_RUNNING
            and self._ws is not None
            and not self._ws.closed
        )

    # --- messaging ----------------------------------------------------------

    async def send(self, payload: dict[str, Any]) -> None:
        """Send a control payload on this wire."""
        if not self.connected or self._ws is None:
            raise CasambiNotConnectedError(
                f"Wire for network is not connected (state: {self.state})"
            )
        await self._send_raw(payload)

    async def _send_raw(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise CasambiNotConnectedError("WebSocket is closed")
        try:
            await self._ws.send_str(json.dumps(payload))
        except (aiohttp.ClientError, ConnectionError, OSError) as err:
            raise CasambiCommunicationError(
                f"WebSocket send failed: {type(err).__name__}"
            ) from err

    async def _keepalive_loop(self) -> None:
        """Send an application-level ping before the 5 minute server limit."""
        try:
            while True:
                await asyncio.sleep(self._keepalive_interval)
                if not self.connected:
                    return
                try:
                    await self._send_raw(
                        {"method": WS_METHOD_PING, "wire": self.wire_id}
                    )
                except (CasambiNotConnectedError, CasambiCommunicationError):
                    _LOGGER.debug(
                        "Wire %s: keepalive send failed; reader will observe "
                        "the closed socket",
                        self.wire_id,
                    )
                    return
        except asyncio.CancelledError:
            raise

    # --- inbound handling -----------------------------------------------------

    async def _handle_ws_message(self, msg: aiohttp.WSMessage) -> None:
        if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
            return
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            _LOGGER.debug("Wire %s: discarding non-JSON frame", self.wire_id)
            return
        if not isinstance(data, dict):
            return
        await self._dispatch(data)

    async def _dispatch(self, data: dict[str, Any]) -> None:
        wire_status = data.get("wireStatus")
        if isinstance(wire_status, str):
            await self._handle_wire_status(wire_status, data)
            return

        method = data.get("method")
        if not isinstance(method, str):
            return

        result = self._on_event(method, data)
        if result is not None:
            await result

    async def _handle_wire_status(
        self, status: str, data: dict[str, Any]
    ) -> None:
        pending = self._open_confirmed
        if status == WIRE_STATUS_OK:
            if pending is not None and not pending.done():
                pending.set_result(None)
            return

        _LOGGER.debug("Wire %s: wireStatus %s", self.wire_id, status)
        if pending is not None and not pending.done():
            if wire_status_is_auth_error(status):
                pending.set_exception(
                    CasambiAuthenticationError(f"Wire open rejected: {status}")
                )
            else:
                pending.set_exception(
                    CasambiProtocolError(f"Wire open failed: {status}")
                )
            return

        # Runtime rejection of a control message: report, keep the wire.
        result = self._on_event("wireStatusError", {"wireStatus": status})
        if result is not None:
            await result
