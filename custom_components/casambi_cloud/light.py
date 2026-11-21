"""Light platform for the Casambi Cloud integration."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.restore_state import RestoreEntity

from .cloud import CasambiCloudError, commands
from .const import (
    ATTR_SERV_BRIGHTNESS,
    ATTR_SERV_DISTRIBUTION,
    DEFAULT_COMMAND_TIMEOUT,
    DOMAIN,
    OPT_COMMAND_TIMEOUT,
    SERVICE_LIGHT_TURN_ON,
)
from .coordinator import CasambiConfigEntry, CasambiCoordinator
from .entity import CasambiUnitEntity

# Used only when a tunable-white unit does not advertise its own range.
FALLBACK_MIN_KELVIN = 2000
FALLBACK_MAX_KELVIN = 6500

# State attribute used to remember the last non-zero level across restarts.
ATTR_LAST_BRIGHTNESS = "last_brightness"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CasambiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Casambi lights and add new ones as the network grows."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_lights() -> None:
        data = coordinator.data
        if data is None:
            return
        new_entities: list[CasambiLight] = []
        for key, unit in data.units.items():
            if key in known or not unit.is_light:
                continue
            known.add(key)
            new_entities.append(CasambiLight(coordinator, key))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_lights()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lights))

    # Preserve the legacy entity service for backwards-compatible automations.
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_LIGHT_TURN_ON,
        {
            vol.Required(ATTR_SERV_BRIGHTNESS): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=255)
            ),
            vol.Optional(ATTR_SERV_DISTRIBUTION): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=255)
            ),
        },
        "async_legacy_turn_on",
    )


class CasambiLight(CasambiUnitEntity, LightEntity, RestoreEntity):
    """A Casambi luminaire exposed as a Home Assistant light."""

    # The light is the primary feature of its device, so it inherits the
    # device name (matching the legacy integration).
    _attr_name = None

    def __init__(self, coordinator: CasambiCoordinator, unit_key: str) -> None:
        super().__init__(coordinator, unit_key)
        # unique_id is lowercased, exactly like the legacy integration, so the
        # entity registry entry (and any user customisation) is preserved.
        self._attr_unique_id = unit_key.lower()
        self._active_color_mode: ColorMode | None = None
        self._attr_supported_color_modes = self._compute_supported_color_modes()
        # Last non-zero dimmer level (0..1), used by a plain "turn on" so the
        # light comes back where it was instead of jumping to 100%.
        self._last_level: float | None = None

    # --- lifecycle --------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_supported_color_modes()
        self._remember_level()
        if self._last_level is None:
            last_state = await self.async_get_last_state()
            if last_state is not None:
                value = last_state.attributes.get(ATTR_LAST_BRIGHTNESS)
                if value is None:
                    value = last_state.attributes.get(ATTR_BRIGHTNESS)
                try:
                    level = float(value) / 255.0 if value is not None else 0.0
                except (TypeError, ValueError):
                    level = 0.0
                if level > 0:
                    self._last_level = min(1.0, level)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._refresh_supported_color_modes()
        self._remember_level()
        super()._handle_coordinator_update()

    def _refresh_supported_color_modes(self) -> None:
        """Recompute the colour modes from the unit's current controls.

        The capabilities used to be computed only once, when the entity was
        created. If the unit's controls were not known yet at that moment
        (luminaire without power, Cloud state not yet received, ...) the light
        stayed ``onoff`` forever, even after the Cloud reported its Dimmer.

        Capabilities are only ever upgraded: an ``onoff`` result never
        replaces richer modes that were already detected, so a unit that is
        temporarily missing or offline does not lose its slider.
        """
        computed = self._compute_supported_color_modes()
        current = self._attr_supported_color_modes or {ColorMode.ONOFF}
        if computed == current:
            return
        if computed == {ColorMode.ONOFF} and current != {ColorMode.ONOFF}:
            return
        self._attr_supported_color_modes = computed
        if self._active_color_mode not in computed:
            self._active_color_mode = None

    def _remember_level(self) -> None:
        """Store the current level while the unit is on and dimmed above 0."""
        unit = self.unit
        if unit is None or not unit.supports_brightness or not unit.is_on:
            return
        level = unit.brightness_1
        if level > 0:
            self._last_level = level

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._last_level is None:
            return None
        return {ATTR_LAST_BRIGHTNESS: round(self._last_level * 255)}

    # --- capabilities ---------------------------------------------------------

    def _compute_supported_color_modes(self) -> set[ColorMode]:
        unit = self.unit
        modes: set[ColorMode] = set()
        if unit is None:
            return {ColorMode.ONOFF}
        if unit.supports_rgb and unit.supports_white:
            modes.add(ColorMode.RGBW)
        elif unit.supports_rgb:
            modes.add(ColorMode.RGB)
        if unit.supports_color_temperature:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes:
            modes.add(
                ColorMode.BRIGHTNESS if unit.supports_brightness else ColorMode.ONOFF
            )
        return modes

    @property
    def color_mode(self) -> ColorMode:
        modes = self._attr_supported_color_modes or {ColorMode.ONOFF}
        # Honour the last explicitly-set mode when the unit supports several.
        if self._active_color_mode in modes:
            return self._active_color_mode
        unit = self.unit
        if unit is not None:
            if ColorMode.RGBW in modes and unit.rgb is not None:
                return ColorMode.RGBW
            if ColorMode.RGB in modes and unit.rgb is not None:
                return ColorMode.RGB
            if ColorMode.COLOR_TEMP in modes and unit.cct_kelvin is not None:
                return ColorMode.COLOR_TEMP
        if ColorMode.COLOR_TEMP in modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.RGBW in modes:
            return ColorMode.RGBW
        if ColorMode.RGB in modes:
            return ColorMode.RGB
        if ColorMode.BRIGHTNESS in modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF

    # --- state ----------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        unit = self.unit
        if unit is None:
            return None
        return unit.is_on

    @property
    def brightness(self) -> int | None:
        unit = self.unit
        if unit is None or not unit.supports_brightness:
            return None
        return round(unit.brightness_1 * 255)

    @property
    def color_temp_kelvin(self) -> int | None:
        unit = self.unit
        if unit is None:
            return None
        return unit.cct_kelvin

    @property
    def min_color_temp_kelvin(self) -> int:
        unit = self.unit
        if unit and unit.cct_min_kelvin:
            return unit.cct_min_kelvin
        return FALLBACK_MIN_KELVIN

    @property
    def max_color_temp_kelvin(self) -> int:
        unit = self.unit
        if unit and unit.cct_max_kelvin:
            return unit.cct_max_kelvin
        return FALLBACK_MAX_KELVIN

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        unit = self.unit
        if unit is None:
            return None
        return unit.rgb

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        unit = self.unit
        if unit is None or unit.rgb is None:
            return None
        white = unit.white_1
        white_255 = round((white if white is not None else 0.0) * 255)
        return (*unit.rgb, white_255)

    # --- commands -------------------------------------------------------------

    @property
    def _command_timeout(self) -> float:
        return float(
            self.coordinator.config_entry.options.get(
                OPT_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT
            )
        )

    async def _send(self, target_controls: dict[str, Any]) -> None:
        """Send a controlUnit message with a bounded timeout."""
        try:
            async with asyncio.timeout(self._command_timeout):
                await self.coordinator.client.async_control_unit(
                    self._unit_key, target_controls
                )
        except TimeoutError as err:
            self.coordinator.async_record_command_failure(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_timeout",
                translation_placeholders={"entity": self.entity_id or self._unit_key},
            ) from err
        except CasambiCloudError as err:
            self.coordinator.async_record_command_failure(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": type(err).__name__},
            ) from err
        else:
            self.coordinator.async_record_command_success()

    def _resume_level(self) -> float:
        """Level for a plain "turn on": keep the current one, else the last one."""
        unit = self.unit
        if unit is not None and unit.is_on and unit.brightness_1 > 0:
            return unit.brightness_1
        if self._last_level is not None and self._last_level > 0:
            return self._last_level
        return 1.0

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, applying any requested colour/brightness."""
        unit = self.unit
        controls: dict[str, Any] = {}

        if ATTR_RGBW_COLOR in kwargs and ColorMode.RGBW in (
            self._attr_supported_color_modes or set()
        ):
            red, green, blue, white = kwargs[ATTR_RGBW_COLOR]
            controls.update(commands.rgbw_controls(red, green, blue, white))
            self._active_color_mode = ColorMode.RGBW
        elif ATTR_RGB_COLOR in kwargs and ColorMode.RGB in (
            self._attr_supported_color_modes or set()
        ):
            red, green, blue = kwargs[ATTR_RGB_COLOR]
            controls.update(commands.rgb_controls(red, green, blue))
            self._active_color_mode = ColorMode.RGB
        elif ATTR_COLOR_TEMP_KELVIN in kwargs and ColorMode.COLOR_TEMP in (
            self._attr_supported_color_modes or set()
        ):
            controls.update(
                commands.color_temperature_controls(
                    kwargs[ATTR_COLOR_TEMP_KELVIN],
                    unit.cct_min_kelvin if unit else None,
                    unit.cct_max_kelvin if unit else None,
                )
            )
            self._active_color_mode = ColorMode.COLOR_TEMP

        if ATTR_BRIGHTNESS in kwargs and unit and unit.supports_brightness:
            level = kwargs[ATTR_BRIGHTNESS] / 255.0
            controls.update(commands.brightness_controls(level))
            if level > 0:
                self._last_level = min(1.0, level)
        elif unit and unit.supports_brightness:
            # No brightness requested (plain "turn on", or colour only):
            # resume the previous level instead of forcing 100%.
            controls.update(commands.brightness_controls(self._resume_level()))
        elif not controls:
            controls.update(commands.onoff_controls(True))

        await self._send(controls)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        unit = self.unit
        if unit and unit.supports_brightness:
            self._remember_level()
            await self._send(commands.brightness_controls(0.0))
        else:
            await self._send(commands.onoff_controls(False))

    async def async_legacy_turn_on(
        self, brightness: int, distribution: int | None = None
    ) -> None:
        """Legacy ``casambi_cloud.light_turn_on`` service (brightness + distribution).

        ``distribution`` maps to the unit's Vertical (light distribution)
        control, expressed on the 0..255 scale for backwards compatibility.
        """
        controls = commands.brightness_controls(brightness / 255.0)
        if brightness > 0:
            self._last_level = min(1.0, brightness / 255.0)
        unit = self.unit
        if distribution is not None and unit and unit.supports_vertical:
            controls.update(commands.vertical_controls(distribution / 255.0))
        await self._send(controls)
