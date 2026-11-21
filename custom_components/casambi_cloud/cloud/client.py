"""Casambi Cloud client: REST access plus per-network WebSocket supervision.

Design goals, in order:
1. Never log or expose credentials, API keys or session identifiers.
2. Never leave orphaned tasks; every task is created through a caller
   supplied factory so Home Assistant can track and cancel it.
3. Bounded, jittered reconnection: no unbounded retry loops.
4. Fail loudly and precisely: every failure maps to a typed exception.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from ssl import SSLContext
from typing import Any

import aiohttp

from . import commands
from .const import (
    HEADER_API_KEY,
    HEADER_SESSION,
    REASONS_BY_STATUS_CODE,
    REST_BASE_URL,
    STATUS_FORBIDDEN,
    STATUS_INVALID_SESSION,
    STATUS_RATE_LIMITED,
    STATUS_UNAUTHORIZED,
    WIRE_STATE_DISCONNECTED,
    WIRE_STATE_RUNNING,
    WS_EVENT_NETWORK_UPDATED,
    WS_EVENT_PEER_CHANGED,
    WS_EVENT_UNIT_CHANGED,
)
from .errors import (
    CasambiAuthenticationError,
    CasambiCloudError,
    CasambiCommunicationError,
    CasambiNotConnectedError,
    CasambiProtocolError,
    CasambiRateLimitError,
    CasambiSessionExpiredError,
)
from .models import (
    CasambiNetwork,
    CasambiScene,
    CasambiUnit,
    apply_unit_event,
    parse_network_info,
    parse_scenes_map,
    parse_units_map,
)
from .util import ExponentialBackoff
from .websocket import CasambiWire

_LOGGER = logging.getLogger(__name__)

TaskFactory = Callable[[Coroutine[Any, Any, None], str], "asyncio.Task[None]"]


class CasambiCloudClient:
    """Owns the Cloud session, model state and per-network wires."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        api_key: str,
        email: str,
        user_password: str | None,
        network_password: str | None,
        ssl_context: SSLContext | None = None,
        request_timeout: float = 30.0,
        reconnect_initial_delay: float = 5.0,
        reconnect_max_delay: float = 300.0,
        notify_change: Callable[[], None] | None = None,
        notify_auth_failure: Callable[[], None] | None = None,
        notify_command_rejected: Callable[[str], None] | None = None,
        notify_structure_changed: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._email = email
        self._user_password = user_password or None
        self._network_password = network_password or None
        self._ssl_context = ssl_context
        self._request_timeout = request_timeout
        self._reconnect_initial_delay = reconnect_initial_delay
        self._reconnect_max_delay = reconnect_max_delay

        self._notify_change = notify_change or (lambda: None)
        self._notify_auth_failure = notify_auth_failure or (lambda: None)
        self._notify_command_rejected = notify_command_rejected or (
            lambda status: None
        )
        self._notify_structure_changed = notify_structure_changed or (lambda: None)

        self._default_session_id: str | None = None
        self._session_ids: dict[str, str] = {}
        self._login_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()

        self.networks: dict[str, CasambiNetwork] = {}
        self.units: dict[str, CasambiUnit] = {}
        self.scenes: dict[str, CasambiScene] = {}
        self.gateway_online: dict[str, bool | None] = {}

        self._wires: dict[str, CasambiWire] = {}
        self._supervisors: dict[str, asyncio.Task[None]] = {}
        self._unit_index: dict[tuple[str, int], str] = {}
        self._next_wire_id = 1
        self._stopping = False
        self.reconnect_count = 0

    # --- public: session / structure ----------------------------------------

    def set_callbacks(
        self,
        *,
        notify_change: Callable[[], None] | None = None,
        notify_auth_failure: Callable[[], None] | None = None,
        notify_command_rejected: Callable[[str], None] | None = None,
        notify_structure_changed: Callable[[], None] | None = None,
    ) -> None:
        """Attach coordinator callbacks after construction.

        This breaks the client<->coordinator construction cycle: the client
        is built first, the coordinator wraps it, then the coordinator wires
        its own callbacks here.
        """
        if notify_change is not None:
            self._notify_change = notify_change
        if notify_auth_failure is not None:
            self._notify_auth_failure = notify_auth_failure
        if notify_command_rejected is not None:
            self._notify_command_rejected = notify_command_rejected
        if notify_structure_changed is not None:
            self._notify_structure_changed = notify_structure_changed

    def secret_values(self) -> list[str]:
        """Literal secrets for redaction of free-form strings."""
        secrets = [self._api_key, self._email]
        if self._user_password:
            secrets.append(self._user_password)
        if self._network_password:
            secrets.append(self._network_password)
        if self._default_session_id:
            secrets.append(self._default_session_id)
        secrets.extend(self._session_ids.values())
        return [secret for secret in secrets if secret]

    async def async_login(self) -> None:
        """Create Cloud sessions; prefers user credentials, then network."""
        async with self._login_lock:
            errors: list[CasambiCloudError] = []
            if self._user_password:
                try:
                    await self._create_user_session()
                    return
                except CasambiAuthenticationError as err:
                    errors.append(err)
            if self._network_password:
                try:
                    await self._create_network_session()
                    return
                except CasambiAuthenticationError as err:
                    errors.append(err)
            if errors:
                raise errors[-1]
            raise CasambiAuthenticationError(
                "No user or network password configured"
            )

    async def _create_user_session(self) -> None:
        data = await self._request(
            "post",
            "/users/session",
            json_payload={"email": self._email, "password": self._user_password},
            use_session=False,
            allow_session_refresh=False,
        )
        if not isinstance(data, dict) or "sessionId" not in data:
            raise CasambiProtocolError("Unexpected user session response")
        self._default_session_id = str(data["sessionId"])
        networks = data.get("networks")
        if not isinstance(networks, dict) or not networks:
            raise CasambiProtocolError(
                "The Cloud account has no networks attached"
            )
        for raw in networks.values():
            if not isinstance(raw, dict):
                continue
            network_id = str(raw.get("id", "") or "")
            if not network_id:
                continue
            self._session_ids[network_id] = str(
                raw.get("sessionId") or self._default_session_id
            )
            self.networks.setdefault(
                network_id, parse_network_info(network_id, raw)
            )
        _LOGGER.debug(
            "User session created; %d network(s) available", len(self._session_ids)
        )

    async def _create_network_session(self) -> None:
        data = await self._request(
            "post",
            "/networks/session",
            json_payload={"email": self._email, "password": self._network_password},
            use_session=False,
            allow_session_refresh=False,
        )
        if not isinstance(data, dict) or not data:
            raise CasambiProtocolError("Unexpected network session response")
        for raw in data.values():
            if not isinstance(raw, dict):
                continue
            network_id = str(raw.get("id", "") or "")
            session_id = raw.get("sessionId")
            if not network_id or not session_id:
                continue
            self._session_ids[network_id] = str(session_id)
            if self._default_session_id is None:
                self._default_session_id = str(session_id)
            self.networks.setdefault(
                network_id, parse_network_info(network_id, raw)
            )
        if not self._session_ids:
            raise CasambiProtocolError(
                "Network session response contained no usable sessions"
            )
        _LOGGER.debug(
            "Network session created; %d network(s) available",
            len(self._session_ids),
        )

    async def async_refresh_structure(self) -> bool:
        """Fetch networks' units and scenes. Returns True when it changed."""
        async with self._refresh_lock:
            changed = False
            for network_id in list(self._session_ids):
                data = await self._request(
                    "get", f"/networks/{network_id}", network_id=network_id
                )
                if not isinstance(data, dict):
                    raise CasambiProtocolError(
                        "Unexpected network information response"
                    )
                network = parse_network_info(network_id, data)
                if self.networks.get(network_id) != network:
                    self.networks[network_id] = network
                    changed = True

                fresh_units = parse_units_map(network_id, data.get("units"))
                fresh_scenes = parse_scenes_map(network_id, data.get("scenes"))

                for key, fresh in fresh_units.items():
                    existing = self.units.get(key)
                    if existing is None:
                        self.units[key] = fresh
                        changed = True
                    else:
                        # Structure data wins for identity/fixture fields but
                        # must not clobber fresher runtime state.
                        merged_controls = dict(fresh.controls)
                        merged_controls.update(existing.controls)
                        merged = fresh.__class__(
                            **{
                                **fresh.__dict__,
                                "online": existing.online,
                                "on": existing.on,
                                "dim_level": existing.dim_level,
                                "active_scene_id": existing.active_scene_id,
                                "controls": merged_controls,
                            }
                        )
                        if merged != existing:
                            self.units[key] = merged
                            changed = True

                for key in [
                    key
                    for key, unit in self.units.items()
                    if unit.network_id == network_id and key not in fresh_units
                ]:
                    del self.units[key]
                    changed = True

                for key in [
                    key
                    for key, scene in self.scenes.items()
                    if scene.network_id == network_id and key not in fresh_scenes
                ]:
                    del self.scenes[key]
                    changed = True
                for key, scene in fresh_scenes.items():
                    if self.scenes.get(key) != scene:
                        self.scenes[key] = scene
                        changed = True

            self._rebuild_unit_index()
            return changed

    async def async_poll_state(self) -> None:
        """Fetch the live network state and merge it into the unit models."""
        for network_id in list(self._session_ids):
            data = await self._request(
                "get", f"/networks/{network_id}/state", network_id=network_id
            )
            if not isinstance(data, dict):
                raise CasambiProtocolError("Unexpected network state response")
            fresh_units = parse_units_map(network_id, data.get("units"))
            for key, fresh in fresh_units.items():
                existing = self.units.get(key)
                if existing is None:
                    # A unit we have never seen: the structure is stale.
                    self.units[key] = fresh
                    self._unit_index[(network_id, fresh.unit_id)] = key
                    self._notify_structure_changed()
                    continue
                merged_controls = dict(existing.controls)
                merged_controls.update(fresh.controls)
                self.units[key] = existing.__class__(
                    **{
                        **existing.__dict__,
                        "online": fresh.online,
                        "on": fresh.on,
                        "dim_level": fresh.dim_level,
                        "active_scene_id": fresh.active_scene_id,
                        "status": fresh.status,
                        "controls": merged_controls,
                    }
                )

    # --- public: websocket lifecycle ------------------------------------------

    def start_websockets(self, task_factory: TaskFactory) -> None:
        """Start one supervised wire per network."""
        self._stopping = False
        for network_id in self._session_ids:
            if network_id in self._supervisors:
                continue
            self._supervisors[network_id] = task_factory(
                self._supervise_network(network_id),
                f"casambi-wire-{network_id[-4:] if len(network_id) > 4 else network_id}",
            )

    async def async_stop(self) -> None:
        """Stop supervisors and close wires. Safe to call repeatedly."""
        self._stopping = True
        supervisors = list(self._supervisors.values())
        self._supervisors.clear()
        for wire in self._wires.values():
            wire.mark_stopped()
        for task in supervisors:
            task.cancel()
        for task in supervisors:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for wire in list(self._wires.values()):
            await wire.close()
        self._wires.clear()

    def websocket_states(self) -> dict[str, str]:
        return {
            network_id: self._wires[network_id].state
            if network_id in self._wires
            else WIRE_STATE_DISCONNECTED
            for network_id in self._session_ids
        }

    def all_websockets_running(self) -> bool:
        states = self.websocket_states()
        return bool(states) and all(
            state == WIRE_STATE_RUNNING for state in states.values()
        )

    async def _supervise_network(self, network_id: str) -> None:
        """Keep one wire connected with bounded, jittered backoff."""
        backoff = ExponentialBackoff(
            initial=self._reconnect_initial_delay,
            maximum=self._reconnect_max_delay,
        )
        auth_retry_available = True
        while not self._stopping:
            wire = CasambiWire(
                session=self._session,
                api_key=self._api_key,
                network_id=network_id,
                wire_id=self._allocate_wire_id(),
                session_id=self._session_ids.get(network_id, ""),
                ssl_context=self._ssl_context,
                on_event=self._make_event_handler(network_id),
            )
            self._wires[network_id] = wire
            try:
                await wire.connect()
                backoff.reset()
                auth_retry_available = True
                self._notify_change()
                _LOGGER.debug("Network %s…: wire connected", network_id[:4])
                await wire.listen()
            except CasambiAuthenticationError:
                if self._stopping:
                    return
                if auth_retry_available:
                    auth_retry_available = False
                    _LOGGER.info(
                        "Casambi Cloud session was rejected; refreshing session"
                    )
                    try:
                        await self.async_login()
                        continue
                    except CasambiAuthenticationError:
                        pass
                    except CasambiCloudError as err:
                        _LOGGER.warning(
                            "Session refresh failed (%s); will retry",
                            type(err).__name__,
                        )
                        # Transient trouble while refreshing: back off, retry.
                        self._after_disconnect(network_id)
                        await asyncio.sleep(backoff.next_delay())
                        continue
                _LOGGER.error(
                    "Casambi Cloud rejected the credentials; reauthentication "
                    "is required"
                )
                self._after_disconnect(network_id)
                self._notify_auth_failure()
                return
            except (CasambiCommunicationError, CasambiProtocolError) as err:
                _LOGGER.debug(
                    "Network %s…: wire ended (%s)",
                    network_id[:4],
                    type(err).__name__,
                )
            except asyncio.CancelledError:
                await wire.close()
                raise
            except Exception:  # noqa: BLE001 - supervisor must not die silently
                _LOGGER.exception("Unexpected error in Casambi wire supervisor")

            if self._stopping:
                return
            self._after_disconnect(network_id)
            self.reconnect_count += 1
            delay = backoff.next_delay()
            _LOGGER.debug(
                "Network %s…: reconnecting in %.1f s (attempt %d)",
                network_id[:4],
                delay,
                backoff.attempts,
            )
            self._notify_change()
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def _after_disconnect(self, network_id: str) -> None:
        """Mark push-derived knowledge stale after losing the wire."""
        self.gateway_online[network_id] = None
        self._notify_change()

    def _allocate_wire_id(self) -> int:
        wire_id = self._next_wire_id
        self._next_wire_id = self._next_wire_id % 90 + 1
        return wire_id

    # --- events -----------------------------------------------------------------

    def _make_event_handler(self, network_id: str):
        def _handle(method: str, data: dict[str, Any]) -> None:
            if method == WS_EVENT_UNIT_CHANGED:
                self._handle_unit_changed(network_id, data)
            elif method == WS_EVENT_PEER_CHANGED:
                self._handle_peer_changed(network_id, data)
            elif method == WS_EVENT_NETWORK_UPDATED:
                _LOGGER.debug("Network %s…: networkUpdated event", network_id[:4])
                self._notify_structure_changed()
            elif method == "wireStatusError":
                status = str(data.get("wireStatus", "unknown"))
                _LOGGER.warning(
                    "Casambi Cloud rejected a command (wireStatus: %s)", status
                )
                self._notify_command_rejected(status)
            else:
                _LOGGER.debug(
                    "Network %s…: unhandled event method %s",
                    network_id[:4],
                    method,
                )

        return _handle

    def _handle_unit_changed(self, network_id: str, event: dict[str, Any]) -> None:
        raw_id = event.get("id", event.get("unit"))
        try:
            unit_id = int(float(raw_id))
        except (TypeError, ValueError):
            _LOGGER.debug("Discarding unitChanged without a usable id")
            return
        key = self._unit_index.get((network_id, unit_id))
        if key is None or key not in self.units:
            _LOGGER.debug(
                "unitChanged for unknown unit %s; scheduling structure refresh",
                unit_id,
            )
            self._notify_structure_changed()
            return
        updated = apply_unit_event(self.units[key], event)
        if updated is not self.units[key]:
            self.units[key] = updated
        self._notify_change()

    def _handle_peer_changed(self, network_id: str, event: dict[str, Any]) -> None:
        online = bool(event.get("online", False))
        self.gateway_online[network_id] = online
        # Mirror the gateway state onto the units: without a gateway the
        # Cloud cannot reach the mesh, so everything is unreachable.
        for key, unit in self.units.items():
            if unit.network_id == network_id and unit.online != online:
                self.units[key] = apply_unit_event(unit, {"online": online})
        _LOGGER.debug(
            "Network %s…: gateway is %s",
            network_id[:4],
            "online" if online else "offline",
        )
        self._notify_change()

    def _rebuild_unit_index(self) -> None:
        self._unit_index = {
            (unit.network_id, unit.unit_id): key
            for key, unit in self.units.items()
        }

    # --- commands ---------------------------------------------------------------

    def _wire_for(self, network_id: str) -> CasambiWire:
        wire = self._wires.get(network_id)
        if wire is None or not wire.connected:
            raise CasambiNotConnectedError(
                "The Casambi Cloud connection for this network is not active"
            )
        return wire

    async def async_control_unit(
        self, unit_key: str, target_controls: dict[str, Any]
    ) -> None:
        unit = self.units.get(unit_key)
        if unit is None:
            raise CasambiProtocolError("Unknown Casambi unit")
        wire = self._wire_for(unit.network_id)
        await wire.send(
            commands.unit_control_message(wire.wire_id, unit.unit_id, target_controls)
        )

    async def async_control_scene(self, scene_key: str, level: float) -> None:
        scene = self.scenes.get(scene_key)
        if scene is None:
            raise CasambiProtocolError("Unknown Casambi scene")
        wire = self._wire_for(scene.network_id)
        await wire.send(
            commands.scene_control_message(wire.wire_id, scene.scene_id, level)
        )

    # --- REST plumbing ------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        network_id: str | None = None,
        use_session: bool = True,
        allow_session_refresh: bool = True,
    ) -> Any:
        headers = {
            "Content-Type": "application/json",
            HEADER_API_KEY: self._api_key,
        }
        if use_session:
            session_id = (
                self._session_ids.get(network_id) if network_id else None
            ) or self._default_session_id
            if not session_id:
                raise CasambiSessionExpiredError("No Cloud session available")
            headers[HEADER_SESSION] = session_id

        url = f"{REST_BASE_URL}{path}"
        try:
            async with self._session.request(
                method,
                url,
                json=json_payload,
                headers=headers,
                ssl=self._ssl_context if self._ssl_context is not None else True,
                timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            ) as response:
                if response.status == STATUS_INVALID_SESSION:
                    if allow_session_refresh and use_session:
                        _LOGGER.debug("Cloud session expired; renewing")
                        await self.async_login()
                        return await self._request(
                            method,
                            path,
                            json_payload=json_payload,
                            network_id=network_id,
                            use_session=use_session,
                            allow_session_refresh=False,
                        )
                    raise CasambiSessionExpiredError(
                        self._reason(response.status)
                    )
                if response.status in (STATUS_UNAUTHORIZED, STATUS_FORBIDDEN):
                    raise CasambiAuthenticationError(self._reason(response.status))
                if response.status == STATUS_RATE_LIMITED:
                    raise CasambiRateLimitError(self._reason(response.status))
                if response.status >= 500:
                    raise CasambiCommunicationError(self._reason(response.status))
                if response.status >= 400:
                    raise CasambiProtocolError(self._reason(response.status))
                if response.content_type == "application/json":
                    return await response.json()
                return await response.text()
        except TimeoutError as err:
            raise CasambiCommunicationError(
                "Timeout talking to the Casambi Cloud"
            ) from err
        except aiohttp.ClientError as err:
            raise CasambiCommunicationError(
                f"HTTP error talking to the Casambi Cloud: {type(err).__name__}"
            ) from err

    @staticmethod
    def _reason(status: int) -> str:
        reason = REASONS_BY_STATUS_CODE.get(status, "unexpected response")
        return f"Casambi Cloud returned HTTP {status}: {reason}"
