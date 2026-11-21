"""Diagnostic sensors for the Casambi Cloud integration."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_COMMAND,
    ERROR_CATEGORY_CONNECTION,
    ERROR_CATEGORY_PROTOCOL,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_SESSION,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_UNKNOWN,
)
from .coordinator import CasambiConfigEntry, CasambiCoordinator
from .entity import CasambiNetworkEntity

_ERROR_STATE_NONE = "none"
_ERROR_OPTIONS = [
    _ERROR_STATE_NONE,
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_SESSION,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_CONNECTION,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_PROTOCOL,
    ERROR_CATEGORY_COMMAND,
    ERROR_CATEGORY_UNKNOWN,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Casambi diagnostic sensors per network."""
    coordinator = entry.runtime_data
    known_networks: set[str] = set()

    @callback
    def _sync() -> None:
        data = coordinator.data
        if data is None:
            return
        new_entities: list[SensorEntity] = []
        for network_id in data.networks:
            if network_id in known_networks:
                continue
            known_networks.add(network_id)
            new_entities.extend(
                (
                    CasambiLastSuccessSensor(coordinator, network_id),
                    CasambiConsecutiveFailuresSensor(coordinator, network_id),
                    CasambiReconnectCountSensor(coordinator, network_id),
                    CasambiCommandFailureSensor(coordinator, network_id),
                    CasambiLastErrorSensor(coordinator, network_id),
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class CasambiLastSuccessSensor(CasambiNetworkEntity, SensorEntity):
    """Timestamp of the last successful Cloud poll."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_success"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-last_success"

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        return data.health.last_poll_success if data else None


class CasambiConsecutiveFailuresSensor(CasambiNetworkEntity, SensorEntity):
    """Number of consecutive failed Cloud polls."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "consecutive_failures"
    _attr_native_unit_of_measurement = None

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-consecutive_failures"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return data.health.consecutive_poll_failures if data else 0


class CasambiReconnectCountSensor(CasambiNetworkEntity, SensorEntity):
    """How many times the realtime link has reconnected since setup."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "reconnect_count"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-reconnect_count"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return data.health.reconnect_count if data else 0


class CasambiCommandFailureSensor(CasambiNetworkEntity, SensorEntity):
    """How many control commands have failed since setup."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "command_failures"

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-command_failures"

    @property
    def native_value(self) -> int:
        data = self.coordinator.data
        return data.health.command_failure_count if data else 0


class CasambiLastErrorSensor(CasambiNetworkEntity, SensorEntity):
    """The most recent error category (secret-free)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_error"
    _attr_options = _ERROR_OPTIONS

    def __init__(self, coordinator: CasambiCoordinator, network_id: str) -> None:
        super().__init__(coordinator, network_id)
        self._attr_unique_id = f"{network_id}-last_error"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data is None:
            return _ERROR_STATE_NONE
        return data.health.last_error_category or _ERROR_STATE_NONE
