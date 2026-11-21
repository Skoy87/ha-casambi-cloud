"""Payload builders for Casambi Cloud control messages.

Pure functions so every wire payload can be unit tested byte-for-byte.

Formats verified against the official developer documentation
(``controlUnit``, ``controlScene``) and the aiocasambi 0.285 sources
(``targetControls`` shapes for Dimmer, ColorTemperature, RGB, White and
Vertical, including the "snap Kelvin to the nearest 50" behaviour of the
Casambi app).
"""

from __future__ import annotations

from typing import Any

from .const import WS_METHOD_CONTROL_SCENE, WS_METHOD_CONTROL_UNIT


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def unit_control_message(
    wire_id: int, unit_id: int, target_controls: dict[str, Any]
) -> dict[str, Any]:
    """Wrap targetControls in a controlUnit message."""
    return {
        "wire": wire_id,
        "method": WS_METHOD_CONTROL_UNIT,
        "id": int(unit_id),
        "targetControls": target_controls,
    }


def scene_control_message(
    wire_id: int, scene_id: int, level: float
) -> dict[str, Any]:
    """Build the official controlScene message (level 0..1)."""
    return {
        "wire": wire_id,
        "method": WS_METHOD_CONTROL_SCENE,
        "id": int(scene_id),
        "level": _clamp01(level),
    }


def brightness_controls(level: float) -> dict[str, Any]:
    """Dimmer level in the Cloud's native 0..1 scale."""
    return {"Dimmer": {"value": _clamp01(level)}}


def onoff_controls(turn_on: bool) -> dict[str, Any]:
    return {"Dimmer": {"value": 1 if turn_on else 0}}


def snap_kelvin(kelvin: int, minimum: int | None, maximum: int | None) -> int:
    """Snap to the nearest 50 K (upward, matching the Casambi app) and clamp."""
    value = int(kelvin)
    if value % 50 != 0:
        value = int(value / 50) * 50 + 50
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def color_temperature_controls(
    kelvin: int, minimum: int | None, maximum: int | None
) -> dict[str, Any]:
    """ColorTemperature value is expressed in Kelvin on the wire."""
    return {
        "ColorTemperature": {"value": snap_kelvin(kelvin, minimum, maximum)},
        "Colorsource": {"source": "TW"},
    }


def rgb_controls(red: int, green: int, blue: int) -> dict[str, Any]:
    return {
        "RGB": {"rgb": f"rgb({int(red)}, {int(green)}, {int(blue)})"},
        "Colorsource": {"source": "RGB"},
    }


def rgbw_controls(
    red: int, green: int, blue: int, white: int
) -> dict[str, Any]:
    """RGBW: RGB plus a White channel scaled to 0..1."""
    controls = rgb_controls(red, green, blue)
    controls["White"] = {"value": _clamp01(white / 255.0)}
    return controls


def vertical_controls(level: float) -> dict[str, Any]:
    """Vertical (light distribution) level in 0..1."""
    return {"Vertical": {"value": _clamp01(level)}}
