"""The Casambi Cloud integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.const import CONF_API_KEY, CONF_EMAIL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .cloud import CasambiAuthenticationError, CasambiCloudClient, CasambiCloudError
from .const import (
    CONF_NETWORK_PASSWORD,
    CONF_NETWORK_TIMEOUT,
    CONF_USER_PASSWORD,
    DEFAULT_RECONNECT_INITIAL_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_STARTUP_TIMEOUT,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPT_RECONNECT_INITIAL_DELAY,
    OPT_RECONNECT_MAX_DELAY,
    OPT_SCAN_INTERVAL,
    OPT_STARTUP_TIMEOUT,
)
from .coordinator import CasambiConfigEntry, CasambiCoordinator
from .entity import network_device_info

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Set up Casambi Cloud from a config entry."""
    session = async_get_clientsession(hass)
    # SSL context construction does blocking file I/O on first use.
    ssl_context = await hass.async_add_executor_job(get_default_context)

    client = CasambiCloudClient(
        session=session,
        api_key=entry.data[CONF_API_KEY],
        email=entry.data[CONF_EMAIL],
        user_password=entry.data.get(CONF_USER_PASSWORD),
        network_password=entry.data.get(CONF_NETWORK_PASSWORD),
        ssl_context=ssl_context,
        reconnect_initial_delay=float(
            entry.options.get(
                OPT_RECONNECT_INITIAL_DELAY, DEFAULT_RECONNECT_INITIAL_DELAY
            )
        ),
        reconnect_max_delay=float(
            entry.options.get(OPT_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY)
        ),
    )

    coordinator = CasambiCoordinator(hass, entry, client)
    client.set_callbacks(
        notify_change=coordinator.handle_client_change,
        notify_auth_failure=coordinator.handle_auth_failure,
        notify_command_rejected=coordinator.handle_command_rejected,
        notify_structure_changed=coordinator.handle_structure_change_request,
    )

    startup_timeout = float(
        entry.options.get(OPT_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT)
    )
    try:
        async with asyncio.timeout(startup_timeout):
            await client.async_login()
            await client.async_refresh_structure()
    except CasambiAuthenticationError as err:
        raise ConfigEntryAuthFailed(
            "Casambi Cloud rejected the stored credentials"
        ) from err
    except (CasambiCloudError, TimeoutError) as err:
        # Stop any partial state before Home Assistant retries setup.
        await client.async_stop()
        raise ConfigEntryNotReady(
            f"Could not reach the Casambi Cloud during startup: "
            f"{type(err).__name__}"
        ) from err

    # Create the network (hub) devices up-front and remember their device
    # ids, so units can link to them with ``via_device_id`` (``via_device``
    # is deprecated since Home Assistant 2026.8).
    device_registry = dr.async_get(hass)
    for network in client.networks.values():
        network_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **network_device_info(network),
        )
        coordinator.network_device_ids[network.network_id] = network_device.id

    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()

    # Start the per-network WebSocket supervisors as tracked background tasks.
    client.start_websockets(
        lambda coro, name: entry.async_create_background_task(hass, coro, name)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CasambiConfigEntry) -> bool:
    """Unload a config entry, tearing down tasks cleanly."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        await coordinator.client.async_stop()
        await coordinator.async_shutdown()
    return unload_ok


async def async_migrate_entry(
    hass: HomeAssistant, entry: CasambiConfigEntry
) -> bool:
    """Migrate an old config entry to the current schema."""
    _LOGGER.debug(
        "Migrating Casambi entry from version %s.%s",
        entry.version,
        entry.minor_version,
    )

    if entry.version > 2:
        # Downgrades are not supported.
        return False

    if entry.version == 1:
        data = dict(entry.data)
        options = dict(entry.options)

        # Relocate the legacy poll interval into options (validated).
        legacy_scan = data.pop(OPT_SCAN_INTERVAL, None)
        if legacy_scan is not None and OPT_SCAN_INTERVAL not in options:
            try:
                options[OPT_SCAN_INTERVAL] = max(
                    MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, int(legacy_scan))
                )
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring invalid legacy scan_interval")

        # The legacy fixed network timeout is obsolete: reconnection is now
        # bounded and jittered via dedicated options.
        data.pop(CONF_NETWORK_TIMEOUT, None)

        unique_id = entry.unique_id
        if not unique_id and data.get(CONF_EMAIL):
            unique_id = str(data[CONF_EMAIL]).lower()

        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            unique_id=unique_id,
            version=2,
            minor_version=1,
        )
        _LOGGER.info("Casambi entry migrated to version 2.1")

    return True
