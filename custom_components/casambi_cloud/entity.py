"""Base entities for the Casambi Cloud integration.

Two device layers are modelled:

- a *network* device (the Casambi network/gateway) created up-front in
  ``__init__`` so that ``via_device_id`` references from units are always valid,
  and
- per-*unit* devices (luminaires) that connect *via* the network device.

The unique-id and device-identifier schemes intentionally reproduce the
legacy integration so the existing entity and device registry survives the
upgrade untouched.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud import CasambiNetwork, CasambiUnit
from .const import DOMAIN
from .coordinator import CasambiCoordinator

MANUFACTURER: str = "Casambi"


def network_device_info(network: CasambiNetwork) -> DeviceInfo:
    """DeviceInfo for a Casambi network (the hub the units connect through)."""
    return DeviceInfo(
        identifiers={(DOMAIN, network.network_id)},
        manufacturer=MANUFACTURER,
        model="Casambi Network",
        name=network.name or "Casambi Network",
        configuration_url="https://www.casambi.com/",
    )


class CasambiNetworkEntity(CoordinatorEntity[CasambiCoordinator]):
    """Base class for network-scoped entities (diagnostics & monitoring).

    These entities stay *available* even while the Cloud link is down, so they
    can actively report the outage instead of turning ``unavailable``.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator)
        self._network_id = network_id
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, network_id)})

    @property
    def _network(self) -> CasambiNetwork | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.networks.get(self._network_id)

    @property
    def available(self) -> bool:
        # Monitoring entities must remain available during an outage; the
        # coordinator keeps publishing health snapshots rather than failing,
        # so ``last_update_success`` stays true and these entities can report
        # the problem state.
        return self.coordinator.last_update_success


class CasambiUnitEntity(CoordinatorEntity[CasambiCoordinator]):
    """Base class for entities that represent a single Casambi unit."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CasambiCoordinator, unit_key: str) -> None:
        super().__init__(coordinator)
        self._unit_key = unit_key
        unit = coordinator.unit(unit_key)
        # Identifiers are NOT lowercased: this matches the legacy device
        # registry exactly. (The light entity's unique_id is lowercased
        # separately, again matching the legacy scheme.)
        device_info = DeviceInfo(
            identifiers={(DOMAIN, unit_key)},
            manufacturer=(unit.oem or MANUFACTURER) if unit else MANUFACTURER,
            model=unit.fixture_model if unit and unit.fixture_model else None,
            name=unit.name if unit and unit.name else None,
            sw_version=unit.firmware_version
            if unit and unit.firmware_version
            else None,
        )
        via_device_id = (
            coordinator.network_device_ids.get(unit.network_id) if unit else None
        )
        if via_device_id is not None:
            device_info["via_device_id"] = via_device_id
        self._attr_device_info = device_info

    @property
    def unit(self) -> CasambiUnit | None:
        return self.coordinator.unit(self._unit_key)

    @property
    def available(self) -> bool:
        unit = self.unit
        if not self.coordinator.last_update_success or unit is None:
            return False
        # The unit is usable when it reports online *and* we have a live path
        # to it (its network's WebSocket wire is running, or a recent REST
        # poll succeeded).
        wire_running = self.coordinator.network_ws_running(unit.network_id)
        recent_rest = self.coordinator.data is not None and (
            self.coordinator.data.health.consecutive_poll_failures == 0
        )
        return unit.online and (wire_running or recent_rest)
