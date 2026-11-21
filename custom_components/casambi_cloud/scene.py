"""Scene platform for the Casambi Cloud integration.

Scenes are activated through the Casambi Cloud's native ``controlScene``
WebSocket message (verified against the official developer documentation),
never by replaying member-light states.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .cloud import CasambiCloudError
from .const import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_INCLUDE_HIDDEN_SCENES,
    DOMAIN,
    OPT_COMMAND_TIMEOUT,
    OPT_INCLUDE_HIDDEN_SCENES,
)
from .coordinator import CasambiConfigEntry, CasambiCoordinator

_ATTR_BRIGHTNESS = "brightness"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Casambi scenes, honouring the hidden-scenes option."""
    coordinator = entry.runtime_data
    include_hidden = bool(
        entry.options.get(OPT_INCLUDE_HIDDEN_SCENES, DEFAULT_INCLUDE_HIDDEN_SCENES)
    )
    known: set[str] = set()

    def _wanted(scene_hidden: bool) -> bool:
        return include_hidden or not scene_hidden

    # Remove entity-registry entries for scenes that should no longer exist
    # (deleted in the Casambi app, or now excluded by the hidden option).
    data = coordinator.data
    if data is not None:
        desired_unique_ids = {
            scene.key.lower()
            for scene in data.scenes.values()
            if _wanted(scene.hidden)
        }
        registry = er.async_get(hass)
        for entity_entry in er.async_entries_for_config_entry(
            registry, entry.entry_id
        ):
            if (
                entity_entry.domain == "scene"
                and entity_entry.unique_id not in desired_unique_ids
            ):
                registry.async_remove(entity_entry.entity_id)

    @callback
    def _add_new_scenes() -> None:
        current = coordinator.data
        if current is None:
            return
        new_entities: list[CasambiSceneEntity] = []
        for key, scene in current.scenes.items():
            if key in known or not _wanted(scene.hidden):
                continue
            known.add(key)
            new_entities.append(CasambiSceneEntity(coordinator, key))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_scenes()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_scenes))


class CasambiSceneEntity(CoordinatorEntity[CasambiCoordinator], Scene):
    """A native Casambi scene exposed as a Home Assistant scene."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CasambiCoordinator, scene_key: str) -> None:
        super().__init__(coordinator)
        self._scene_key = scene_key
        scene = coordinator.scene(scene_key)
        self._attr_unique_id = scene_key.lower()
        self._attr_name = scene.name if scene else scene_key
        self._attr_device_info = (
            DeviceInfo(identifiers={(DOMAIN, scene.network_id)}) if scene else None
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self.coordinator.scene(self._scene_key) is not None
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the scene via the Cloud's controlScene message."""
        level = 1.0
        brightness = kwargs.get(_ATTR_BRIGHTNESS)
        if brightness is not None:
            level = max(0.0, min(1.0, float(brightness) / 255.0))

        timeout = float(
            self.coordinator.config_entry.options.get(
                OPT_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT
            )
        )
        try:
            async with asyncio.timeout(timeout):
                await self.coordinator.client.async_control_scene(
                    self._scene_key, level
                )
        except TimeoutError as err:
            self.coordinator.async_record_command_failure(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="scene_timeout",
                translation_placeholders={"scene": self._attr_name or ""},
            ) from err
        except CasambiCloudError as err:
            self.coordinator.async_record_command_failure(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="scene_failed",
                translation_placeholders={"error": type(err).__name__},
            ) from err
        else:
            self.coordinator.async_record_command_success()
