"""Diagnostics support for the Casambi Cloud integration.

Two layers of redaction are applied:
1. Home Assistant's ``async_redact_data`` for the known credential keys, and
2. the cloud client's recursive ``redact`` pass, which scrubs any sensitive
   *keys* and any *literal* secret values (API key, session ids, passwords)
   that might otherwise leak through free-form text.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .cloud.util import redact
from .const import CONF_NETWORK_PASSWORD, CONF_USER_PASSWORD
from .coordinator import CasambiConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    CONF_EMAIL,
    CONF_USER_PASSWORD,
    CONF_NETWORK_PASSWORD,
    "unique_id",
    "title",
}


def _base_payload(entry: CasambiConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    payload: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": coordinator.diagnostics_snapshot(),
    }
    # Final defensive pass: remove any literal secret values that could have
    # slipped into error strings or nested structures.
    return redact(payload, extra_values=coordinator.client.secret_values())


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CasambiConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return _base_payload(entry)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: CasambiConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a single device, scoped to its identifiers."""
    payload = _base_payload(entry)
    identifiers = {identifier for _, identifier in device.identifiers}
    coordinator = entry.runtime_data
    snapshot = coordinator.diagnostics_snapshot()

    scoped_units = [
        unit
        for unit in snapshot["units"]
        if unit["key"] in identifiers
        or f"{unit['key']}" in identifiers
    ]
    scoped_networks = {
        network_id: info
        for network_id, info in snapshot["networks"].items()
        if network_id in identifiers
    }
    payload["device"] = redact(
        {
            "identifiers": sorted(identifiers),
            "networks": scoped_networks,
            "units": scoped_units,
        },
        extra_values=coordinator.client.secret_values(),
    )
    return payload
