"""Binary sensors for the Casambi Cloud integration.

Two families:

- per-*unit* sensors (connectivity and overheat) that reproduce the legacy
  integration's unique-id scheme so existing entities are preserved, and
- network-level *monitoring* sensors (Cloud reachability, realtime link and
  gateway presence) that stay available during an outage to actively report
  the problem.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CasambiConfigEntry, CasambiCoordinator
from .entity import CasambiNetworkEntity, CasambiUnitEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Casambi binary sensors and keep them in sync with the network."""
    coordinator = entry.runtime_data
    known_networks: set[str] = set()
    known_status: set[str] = set()
    known_overheat: set[str] = set()

    @callback
    def _sync() -> None:
        data = coordinator.data
        if data is None:
            return
        new_entities: list[BinarySensorEntity] = []

        for network_id in data.networks:
            if network_id in known_networks:
                continue
            known_networks.add(network_id)
            new_entities.append(
                CasambiCloudConnectionBinarySensor(coordinator, network_id)
            )
            new_entities.append(
                CasambiWebsocketBinarySensor(coordinator, network_id)
            )
            new_entities.append(CasambiGatewayBinarySensor(coordinator, network_id))

        for key, unit in data.units.items():
            if key not in known_status:
                known_status.add(key)
                new_entities.append(CasambiUnitStatusBinarySensor(coordinator, key))
            if unit.has_overheat_control and key not in known_overheat:
                known_overheat.add(key)
                new_entities.append(
                    CasambiUnitOverheatBinarySensor(coordinator, key)
                )

        if new_entities:
            async_add_entities(new_entities)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


# --- per-unit sensors ----------------------------------------------------------


class CasambiUnitStatusBinarySensor(CasambiUnitEntity, BinarySensorEntity):
    """Connectivity of a single unit (legacy ``..._status`` entity)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "unit_status"

    def __init__(self, coordinator: CasambiCoordinator, unit_key: str) -> None:
        super().__init__(coordinator, unit_key)
        self._attr_unique_id = f"{unit_key}_status".lower()

    @property
    def available(self) -> bool:
        # Stay available so the sensor can report "disconnected" instead of
        # disappearing when the unit drops offline.
        return self.coordinator.last_update_success and self.unit is not None

    @property
    def is_on(self) -> bool | None:
        unit = self.unit
        return None if unit is None else unit.online


class CasambiUnitOverheatBinarySensor(CasambiUnitEntity, BinarySensorEntity):
    """Overheat state of a single unit (legacy ``..._overheat`` entity)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "unit_overheat"

    def __init__(self, coordinator: CasambiCoordinator, unit_key: str) -> None:
        super().__init__(coordinator, unit_key)
        self._attr_unique_id = f"{unit_key}_overheat".lower()

    @property
    def available(self) -> bool:
        unit = self.unit
        return (
            self.coordinator.last_update_success
            and unit is not None
            and unit.overheated is not None
        )

    @property
    def is_on(self) -> bool | None:
        unit = self.unit
        return None if unit is None else unit.overheated


# --- network-level monitoring sensors ------------------------------------------


class CasambiCloudConnectionBinarySensor(CasambiNetworkEntity, BinarySensorEntity):
    """Overall reachability of the Casambi Cloud for this network."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "cloud_connection"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-cloud_connection"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None:
            return False
        running = data.websocket_states.get(self._network_id) == "running"
        gateway = data.gateway_online.get(self._network_id)
        poll_ok = data.health.consecutive_poll_failures == 0
        return running and poll_ok and gateway is not False


class CasambiWebsocketBinarySensor(CasambiNetworkEntity, BinarySensorEntity):
    """State of the realtime (WebSocket) link for this network."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "websocket"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-websocket"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None:
            return False
        return data.websocket_states.get(self._network_id) == "running"


class CasambiGatewayBinarySensor(CasambiNetworkEntity, BinarySensorEntity):
    """Whether a gateway (a peer device with cloud access) is present."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "gateway"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-gateway"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        # None => unknown (no event seen yet); surfaced as an unknown state.
        return data.gateway_online.get(self._network_id)
