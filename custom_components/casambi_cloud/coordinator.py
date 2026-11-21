"""DataUpdateCoordinator for the Casambi Cloud integration.

The coordinator fuses two sources into one immutable snapshot:
- periodic REST polls (authoritative health check and state resync), and
- WebSocket push events (immediate state updates between polls).

It also owns the layered alerting required by this installation:
consecutive-failure tracking, persistent notifications, Repairs issues and
their automatic resolution after recovery.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .cloud import (
    CasambiAuthenticationError,
    CasambiCloudClient,
    CasambiCloudError,
    CasambiCommunicationError,
    CasambiNetwork,
    CasambiNotConnectedError,
    CasambiProtocolError,
    CasambiRateLimitError,
    CasambiScene,
    CasambiSessionExpiredError,
    CasambiUnit,
)
from .cloud.util import redact_message
from .const import (
    DEFAULT_ENABLE_NOTIFICATIONS,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STRUCTURE_REFRESH_INTERVAL,
    DOMAIN,
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_COMMAND,
    ERROR_CATEGORY_CONNECTION,
    ERROR_CATEGORY_PROTOCOL,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_SESSION,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_UNKNOWN,
    GATEWAY_OFFLINE_GRACE_CYCLES,
    ISSUE_CONNECTION_LOST,
    ISSUE_GATEWAY_OFFLINE,
    NOTIFICATION_ID_PROBLEM,
    NOTIFICATION_ID_RECOVERED,
    OPT_ENABLE_NOTIFICATIONS,
    OPT_FAILURE_THRESHOLD,
    OPT_SCAN_INTERVAL,
    OPT_STRUCTURE_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type CasambiConfigEntry = ConfigEntry[CasambiCoordinator]

_PUSH_DEBOUNCE_SECONDS = 0.15


def categorize_error(err: Exception) -> str:
    """Map an exception to a stable, secret-free error category."""
    if isinstance(err, CasambiAuthenticationError):
        return ERROR_CATEGORY_AUTH
    if isinstance(err, CasambiSessionExpiredError):
        return ERROR_CATEGORY_SESSION
    if isinstance(err, CasambiRateLimitError):
        return ERROR_CATEGORY_RATE_LIMIT
    if isinstance(err, CasambiNotConnectedError):
        return ERROR_CATEGORY_CONNECTION
    if isinstance(err, CasambiCommunicationError):
        message = str(err).lower()
        if "timeout" in message:
            return ERROR_CATEGORY_TIMEOUT
        return ERROR_CATEGORY_CONNECTION
    if isinstance(err, CasambiProtocolError):
        return ERROR_CATEGORY_PROTOCOL
    if isinstance(err, (TimeoutError, asyncio.TimeoutError)):
        return ERROR_CATEGORY_TIMEOUT
    if isinstance(err, CasambiCloudError):
        return ERROR_CATEGORY_UNKNOWN
    return ERROR_CATEGORY_UNKNOWN


@dataclass(frozen=True)
class CasambiHealth:
    """Aggregated health of the Cloud link, safe to expose to entities."""

    operational: bool = False
    all_websockets_running: bool = False
    any_gateway_offline: bool = False
    last_poll_success: datetime | None = None
    consecutive_poll_failures: int = 0
    websocket_down_cycles: int = 0
    gateway_offline_cycles: int = 0
    reconnect_count: int = 0
    command_failure_count: int = 0
    last_error_category: str | None = None
    last_error_message: str | None = None


@dataclass(frozen=True)
class CasambiData:
    """Immutable snapshot delivered to entities."""

    networks: dict[str, CasambiNetwork] = field(default_factory=dict)
    units: dict[str, CasambiUnit] = field(default_factory=dict)
    scenes: dict[str, CasambiScene] = field(default_factory=dict)
    websocket_states: dict[str, str] = field(default_factory=dict)
    gateway_online: dict[str, bool | None] = field(default_factory=dict)
    health: CasambiHealth = field(default_factory=CasambiHealth)


class CasambiCoordinator(DataUpdateCoordinator[CasambiData]):
    """Coordinates data, health and alerting for one config entry."""

    config_entry: CasambiConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CasambiConfigEntry,
        client: CasambiCloudClient,
    ) -> None:
        scan_interval = int(
            entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        # Home Assistant device ids of the network (hub) devices, keyed by
        # Casambi network id. Filled in by async_setup_entry; units link to
        # their network through ``via_device_id``.
        self.network_device_ids: dict[str, str] = {}
        self._failure_threshold = int(
            entry.options.get(OPT_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD)
        )
        self._notifications_enabled = bool(
            entry.options.get(OPT_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS)
        )
        self._structure_refresh_interval = int(
            entry.options.get(
                OPT_STRUCTURE_REFRESH_INTERVAL, DEFAULT_STRUCTURE_REFRESH_INTERVAL
            )
        )

        self._health = CasambiHealth()
        self._last_structure_refresh = dt_util.utcnow()
        self._structure_refresh_pending = False
        self._structure_refresh_running = False
        self._push_flush_handle: asyncio.TimerHandle | None = None
        self._active_issues: set[str] = set()
        self._problem_notified = False
        self._shutting_down = False

    # --- client callbacks (all invoked on the event loop) ---------------------

    @callback
    def handle_client_change(self) -> None:
        """A push event changed client state; publish a debounced snapshot."""
        if self._shutting_down:
            return
        if self._push_flush_handle is not None:
            return
        self._push_flush_handle = self.hass.loop.call_later(
            _PUSH_DEBOUNCE_SECONDS, self._flush_push_update
        )

    @callback
    def _flush_push_update(self) -> None:
        self._push_flush_handle = None
        if self._shutting_down:
            return
        self.async_set_updated_data(self._build_snapshot())

    @callback
    def handle_auth_failure(self) -> None:
        """The Cloud permanently rejected the session credentials."""
        if self._shutting_down:
            return
        self._health = replace(
            self._health,
            operational=False,
            last_error_category=ERROR_CATEGORY_AUTH,
            last_error_message="Authentication rejected by the Casambi Cloud",
        )
        self.async_set_updated_data(self._build_snapshot())
        self.config_entry.async_start_reauth(self.hass)

    @callback
    def handle_command_rejected(self, status: str) -> None:
        """The Cloud rejected a control message with a wireStatus error."""
        self.async_record_command_failure(
            CasambiProtocolError(f"wireStatus: {status}"),
            category=ERROR_CATEGORY_COMMAND,
        )

    @callback
    def handle_structure_change_request(self) -> None:
        """A push event referenced unknown structure; refresh it soon."""
        if self._shutting_down or self._structure_refresh_pending:
            return
        self._structure_refresh_pending = True
        self.config_entry.async_create_background_task(
            self.hass,
            self._async_refresh_structure_task(),
            name="casambi-structure-refresh",
        )

    async def _async_refresh_structure_task(self) -> None:
        # Small delay: Casambi app edits emit bursts of events.
        await asyncio.sleep(2)
        self._structure_refresh_pending = False
        if self._structure_refresh_running or self._shutting_down:
            return
        self._structure_refresh_running = True
        try:
            changed = await self.client.async_refresh_structure()
            self._last_structure_refresh = dt_util.utcnow()
            if changed:
                _LOGGER.debug("Casambi network structure changed; snapshot updated")
        except CasambiCloudError as err:
            _LOGGER.debug(
                "Deferred structure refresh failed (%s); the next poll retries",
                type(err).__name__,
            )
        finally:
            self._structure_refresh_running = False
        self.async_set_updated_data(self._build_snapshot())

    # --- command bookkeeping ----------------------------------------------------

    @callback
    def async_record_command_success(self) -> None:
        """Commands succeeding proves the wire works; clear command errors."""
        if self._health.last_error_category == ERROR_CATEGORY_COMMAND:
            self._health = replace(
                self._health, last_error_category=None, last_error_message=None
            )
            self.handle_client_change()

    @callback
    def async_record_command_failure(
        self, err: Exception, *, category: str | None = None
    ) -> None:
        self._health = replace(
            self._health,
            command_failure_count=self._health.command_failure_count + 1,
            last_error_category=category or categorize_error(err),
            last_error_message=self._redact(str(err))[:250],
        )
        self.handle_client_change()

    def _redact(self, message: str) -> str:
        return redact_message(message, self.client.secret_values())

    # --- polling -------------------------------------------------------------------

    async def _async_update_data(self) -> CasambiData:
        """Poll the Cloud. This is also the integration's health heartbeat."""
        try:
            if (
                dt_util.utcnow() - self._last_structure_refresh
            ).total_seconds() >= self._structure_refresh_interval:
                await self.client.async_refresh_structure()
                self._last_structure_refresh = dt_util.utcnow()
            await self.client.async_poll_state()
        except CasambiAuthenticationError as err:
            # DataUpdateCoordinator turns this into Home Assistant's
            # built-in reauthentication flow and repair.
            raise ConfigEntryAuthFailed(
                "Casambi Cloud rejected the credentials"
            ) from err
        except CasambiCloudError as err:
            return self._handle_poll_failure(err)
        except Exception as err:  # noqa: BLE001 - keep the heartbeat alive
            return self._handle_poll_failure(err)

        return self._handle_poll_success()

    def _handle_poll_success(self) -> CasambiData:
        self._health = replace(
            self._health,
            last_poll_success=dt_util.utcnow(),
            consecutive_poll_failures=0,
            last_error_category=None
            if self._health.last_error_category != ERROR_CATEGORY_COMMAND
            else self._health.last_error_category,
            last_error_message=None
            if self._health.last_error_category != ERROR_CATEGORY_COMMAND
            else self._health.last_error_message,
        )
        snapshot = self._build_snapshot()
        self._evaluate_alerts(snapshot)
        return snapshot

    def _handle_poll_failure(self, err: Exception) -> CasambiData:
        category = categorize_error(err)
        message = self._redact(str(err))[:250]
        failures = self._health.consecutive_poll_failures + 1
        self._health = replace(
            self._health,
            consecutive_poll_failures=failures,
            last_error_category=category,
            last_error_message=message,
        )
        _LOGGER.warning(
            "Casambi Cloud poll failed (%s, consecutive failures: %d): %s",
            category,
            failures,
            message,
        )
        if self.data is None:
            # First refresh: let Home Assistant retry setup with backoff.
            raise UpdateFailed(f"Initial Casambi Cloud poll failed: {message}")
        snapshot = self._build_snapshot()
        self._evaluate_alerts(snapshot)
        return snapshot

    # --- snapshot / health --------------------------------------------------------

    def _build_snapshot(self) -> CasambiData:
        websocket_states = self.client.websocket_states()
        gateway_online = dict(self.client.gateway_online)
        all_ws = self.client.all_websockets_running()
        any_gateway_offline = any(
            state is False for state in gateway_online.values()
        )

        ws_down_cycles = self._health.websocket_down_cycles
        gw_off_cycles = self._health.gateway_offline_cycles

        rest_ok = self._health.consecutive_poll_failures == 0 and (
            self._health.last_poll_success is not None
        )
        operational = rest_ok and all_ws and not any_gateway_offline

        self._health = replace(
            self._health,
            operational=operational,
            all_websockets_running=all_ws,
            any_gateway_offline=any_gateway_offline,
            websocket_down_cycles=ws_down_cycles,
            gateway_offline_cycles=gw_off_cycles,
            reconnect_count=self.client.reconnect_count,
        )
        return CasambiData(
            networks=dict(self.client.networks),
            units=dict(self.client.units),
            scenes=dict(self.client.scenes),
            websocket_states=websocket_states,
            gateway_online=gateway_online,
            health=self._health,
        )

    def _evaluate_alerts(self, snapshot: CasambiData) -> None:
        """Threshold based alerting, deduplicated, with auto-recovery."""
        health = snapshot.health

        ws_down_cycles = (
            0 if health.all_websockets_running else health.websocket_down_cycles + 1
        )
        gw_off_cycles = (
            health.gateway_offline_cycles + 1 if health.any_gateway_offline else 0
        )
        self._health = replace(
            self._health,
            websocket_down_cycles=ws_down_cycles,
            gateway_offline_cycles=gw_off_cycles,
        )

        connection_problem = (
            health.consecutive_poll_failures >= self._failure_threshold
            or ws_down_cycles >= self._failure_threshold
        )
        gateway_problem = gw_off_cycles >= GATEWAY_OFFLINE_GRACE_CYCLES

        if connection_problem:
            self._raise_issue(
                ISSUE_CONNECTION_LOST,
                severity=ir.IssueSeverity.ERROR,
                is_fixable=True,
                placeholders={
                    "error": health.last_error_message or "unknown",
                    "failures": str(
                        max(health.consecutive_poll_failures, ws_down_cycles)
                    ),
                },
            )
        else:
            self._clear_issue(ISSUE_CONNECTION_LOST)

        if gateway_problem:
            self._raise_issue(
                ISSUE_GATEWAY_OFFLINE,
                severity=ir.IssueSeverity.WARNING,
                is_fixable=False,
                placeholders={},
            )
        else:
            self._clear_issue(ISSUE_GATEWAY_OFFLINE)

        if connection_problem or gateway_problem:
            self._notify_problem(connection_problem, gateway_problem, health)
        else:
            self._notify_recovered()

    def _raise_issue(
        self,
        issue_id: str,
        *,
        severity: ir.IssueSeverity,
        is_fixable: bool,
        placeholders: dict[str, str],
    ) -> None:
        scoped_id = f"{issue_id}_{self.config_entry.entry_id}"
        if scoped_id in self._active_issues:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            scoped_id,
            is_fixable=is_fixable,
            severity=severity,
            translation_key=issue_id,
            translation_placeholders=placeholders,
            data={"entry_id": self.config_entry.entry_id},
        )
        self._active_issues.add(scoped_id)

    def _clear_issue(self, issue_id: str) -> None:
        scoped_id = f"{issue_id}_{self.config_entry.entry_id}"
        if scoped_id not in self._active_issues:
            return
        ir.async_delete_issue(self.hass, DOMAIN, scoped_id)
        self._active_issues.discard(scoped_id)

    def _notify_problem(
        self, connection_problem: bool, gateway_problem: bool, health: CasambiHealth
    ) -> None:
        if self._problem_notified:
            return
        self._problem_notified = True
        if not self._notifications_enabled:
            return
        persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID_RECOVERED)
        lines = [f"Casambi Cloud ({self.config_entry.title}) reported a problem."]
        if connection_problem:
            lines.append(
                "The Cloud connection is failing "
                f"(last error: {health.last_error_category or 'unknown'})."
            )
        if gateway_problem:
            lines.append(
                "The Casambi gateway for your network appears to be offline. "
                "Check the gateway device (power, Wi-Fi/Ethernet, Bluetooth "
                "range to the luminaires)."
            )
        lines.append(
            "Details: Settings > Devices & Services > Casambi, and "
            "Settings > System > Repairs."
        )
        persistent_notification.async_create(
            self.hass,
            "\n\n".join(lines),
            title="Casambi Cloud problem",
            notification_id=NOTIFICATION_ID_PROBLEM,
        )

    def _notify_recovered(self) -> None:
        if not self._problem_notified:
            return
        self._problem_notified = False
        persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID_PROBLEM)
        if not self._notifications_enabled:
            return
        persistent_notification.async_create(
            self.hass,
            f"The Casambi Cloud connection ({self.config_entry.title}) "
            "has recovered and is fully operational again.",
            title="Casambi Cloud recovered",
            notification_id=NOTIFICATION_ID_RECOVERED,
        )

    # --- shutdown ---------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Stop timers and clear alerting state on unload."""
        self._shutting_down = True
        if self._push_flush_handle is not None:
            self._push_flush_handle.cancel()
            self._push_flush_handle = None
        for scoped_id in list(self._active_issues):
            ir.async_delete_issue(self.hass, DOMAIN, scoped_id)
        self._active_issues.clear()
        persistent_notification.async_dismiss(self.hass, NOTIFICATION_ID_PROBLEM)
        await super().async_shutdown()

    # --- helpers for entities ------------------------------------------------------

    def unit(self, unit_key: str) -> CasambiUnit | None:
        if self.data is None:
            return None
        return self.data.units.get(unit_key)

    def scene(self, scene_key: str) -> CasambiScene | None:
        if self.data is None:
            return None
        return self.data.scenes.get(scene_key)

    def network_ws_running(self, network_id: str) -> bool:
        if self.data is None:
            return False
        return self.data.websocket_states.get(network_id) == "running"

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Structured, redaction-ready view of the coordinator state."""
        data = self.data or CasambiData()
        health = data.health
        return {
            "health": {
                "operational": health.operational,
                "all_websockets_running": health.all_websockets_running,
                "any_gateway_offline": health.any_gateway_offline,
                "last_poll_success": health.last_poll_success.isoformat()
                if health.last_poll_success
                else None,
                "consecutive_poll_failures": health.consecutive_poll_failures,
                "reconnect_count": health.reconnect_count,
                "command_failure_count": health.command_failure_count,
                "last_error_category": health.last_error_category,
                "last_error_message": health.last_error_message,
            },
            "websocket_states": dict(data.websocket_states),
            "gateway_online": dict(data.gateway_online),
            "networks": {
                network_id: {
                    "name": network.name,
                    "grade": network.grade,
                    "revision": network.revision,
                }
                for network_id, network in data.networks.items()
            },
            "units": [
                {
                    "key": unit.key,
                    "unit_id": unit.unit_id,
                    "name": unit.name,
                    "type": unit.unit_type,
                    "fixture_model": unit.fixture_model,
                    "oem": unit.oem,
                    "firmware_version": unit.firmware_version,
                    "online": unit.online,
                    "is_light": unit.is_light,
                    "controls": sorted(unit.controls),
                    "supports": {
                        "brightness": unit.supports_brightness,
                        "color_temperature": unit.supports_color_temperature,
                        "rgb": unit.supports_rgb,
                        "white": unit.supports_white,
                        "vertical": unit.supports_vertical,
                    },
                }
                for unit in data.units.values()
            ],
            "scenes": [
                {
                    "key": scene.key,
                    "scene_id": scene.scene_id,
                    "name": scene.name,
                    "hidden": scene.hidden,
                    "type": scene.scene_type,
                }
                for scene in data.scenes.values()
            ],
        }
