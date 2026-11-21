"""Data models and payload parsing for the Casambi Cloud client.

This module is intentionally free of I/O and third party imports so the
parsing logic can be unit tested in isolation. Payload shapes follow the
official Casambi developer documentation and the payload examples embedded
in the aiocasambi 0.285 sources (REST network information, REST network
state and WebSocket ``unitChanged`` events).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from .const import (
    CONTROL_CCT,
    CONTROL_COLOR,
    CONTROL_DIMMER,
    CONTROL_OVERHEAT,
    CONTROL_VERTICAL,
    CONTROL_WHITE,
    LIGHT_UNIT_TYPES,
)

_RGB_RE = re.compile(
    r"rgb\(\s*(?P<red>\d+)\s*,\s*(?P<green>\d+)\s*,\s*(?P<blue>\d+)\s*\)"
)


def _as_int(value: Any, default: int | None = None) -> int | None:
    """Best effort int conversion for ids arriving as int/float/str."""
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_controls(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize a Casambi ``controls`` payload to a dict keyed by type.

    The Cloud delivers controls in three shapes:
    - a flat list of dicts (WebSocket ``unitChanged``),
    - a nested list of lists (REST network information / state),
    - a dict keyed by control type (already normalized data).
    Entries without a ``type`` are ignored.
    """
    result: dict[str, dict[str, Any]] = {}

    def _consume(item: Any) -> None:
        if isinstance(item, dict):
            control_type = item.get("type")
            if isinstance(control_type, str) and control_type:
                result[control_type] = dict(item)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                _consume(sub)

    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                merged = dict(value)
                merged.setdefault("type", key)
                result[str(merged.get("type", key))] = merged
    else:
        _consume(raw)

    return result


def parse_rgb_string(value: Any) -> tuple[int, int, int] | None:
    """Parse a ``rgb(r, g, b)`` string as delivered by the Cloud."""
    if not isinstance(value, str):
        return None
    match = _RGB_RE.match(value.strip())
    if not match:
        return None
    red = min(255, int(match.group("red")))
    green = min(255, int(match.group("green")))
    blue = min(255, int(match.group("blue")))
    return (red, green, blue)


@dataclass(frozen=True)
class CasambiScene:
    """A scene stored inside a Casambi network."""

    network_id: str
    scene_id: int
    name: str
    hidden: bool = False
    scene_type: str = "REGULAR"
    position: int = 0

    @property
    def key(self) -> str:
        """Stable internal key, also the basis of the entity unique id."""
        return f"{self.network_id}-scene-{self.scene_id}"


@dataclass(frozen=True)
class CasambiUnit:
    """A device (usually a luminaire) inside a Casambi network."""

    network_id: str
    unit_id: int
    name: str = ""
    address: str = ""
    unit_type: str = ""
    fixture_id: int | None = None
    fixture_model: str = ""
    oem: str = ""
    firmware_version: str = ""
    online: bool = False
    on: bool = False
    dim_level: float = 0.0
    active_scene_id: int = 0
    controls: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = ""

    @property
    def key(self) -> str:
        """Stable key: identical to the legacy integration's unique id base.

        The legacy integration used ``{network_id}-{address}`` (falling back
        to the unit id when the hardware address is unknown). Reusing it
        preserves the entity and device registry across the upgrade.
        """
        if self.address:
            return f"{self.network_id}-{self.address}"
        return f"{self.network_id}-{self.unit_id}"

    @property
    def is_light(self) -> bool:
        return self.unit_type.lower() in LIGHT_UNIT_TYPES

    # --- capability helpers -------------------------------------------------

    @property
    def supports_brightness(self) -> bool:
        return CONTROL_DIMMER in self.controls

    @property
    def supports_color_temperature(self) -> bool:
        cct = self.controls.get(CONTROL_CCT)
        return bool(cct) and cct.get("min") is not None and cct.get("max") is not None

    @property
    def supports_rgb(self) -> bool:
        return CONTROL_COLOR in self.controls

    @property
    def supports_white(self) -> bool:
        return CONTROL_WHITE in self.controls

    @property
    def supports_vertical(self) -> bool:
        return CONTROL_VERTICAL in self.controls

    @property
    def has_overheat_control(self) -> bool:
        return CONTROL_OVERHEAT in self.controls

    # --- state helpers ------------------------------------------------------

    @property
    def brightness_1(self) -> float:
        """Current brightness in the Cloud's native 0..1 scale."""
        dimmer = self.controls.get(CONTROL_DIMMER, {})
        value = _as_float(dimmer.get("value"))
        if value is None:
            value = self.dim_level
        return max(0.0, min(1.0, value or 0.0))

    @property
    def vertical_1(self) -> float | None:
        vertical = self.controls.get(CONTROL_VERTICAL)
        if not vertical:
            return None
        value = _as_float(vertical.get("value"))
        if value is None:
            return None
        return max(0.0, min(1.0, value))

    @property
    def cct_min_kelvin(self) -> int | None:
        cct = self.controls.get(CONTROL_CCT, {})
        return _as_int(cct.get("min"))

    @property
    def cct_max_kelvin(self) -> int | None:
        cct = self.controls.get(CONTROL_CCT, {})
        return _as_int(cct.get("max"))

    @property
    def cct_kelvin(self) -> int | None:
        cct = self.controls.get(CONTROL_CCT, {})
        return _as_int(cct.get("value"))

    @property
    def rgb(self) -> tuple[int, int, int] | None:
        color = self.controls.get(CONTROL_COLOR)
        if not color:
            return None
        return parse_rgb_string(color.get("rgb"))

    @property
    def white_1(self) -> float | None:
        white = self.controls.get(CONTROL_WHITE)
        if not white:
            return None
        value = _as_float(white.get("value"))
        if value is None:
            return None
        return max(0.0, min(1.0, value))

    @property
    def overheated(self) -> bool | None:
        overheat = self.controls.get(CONTROL_OVERHEAT)
        if not overheat:
            return None
        status = str(overheat.get("status", "")).lower()
        if not status:
            return None
        return status != "ok"

    @property
    def is_on(self) -> bool:
        if self.supports_brightness:
            return self.brightness_1 > 0
        return bool(self.on)


@dataclass(frozen=True)
class CasambiNetwork:
    """A Casambi network reachable through the Cloud session."""

    network_id: str
    name: str = ""
    address: str = ""
    grade: str = ""
    revision: int | None = None


def parse_unit_info(network_id: str, raw: Any) -> CasambiUnit | None:
    """Parse one unit entry from REST network information or network state."""
    if not isinstance(raw, dict):
        return None
    unit_id = _as_int(raw.get("id"))
    if unit_id is None:
        return None

    controls = normalize_controls(raw.get("controls"))
    return CasambiUnit(
        network_id=network_id,
        unit_id=unit_id,
        name=str(raw.get("name", "") or "").strip(),
        address=str(raw.get("address", "") or ""),
        unit_type=str(raw.get("type", "") or ""),
        fixture_id=_as_int(raw.get("fixtureId")),
        fixture_model=str(raw.get("fixtureModel", raw.get("fixture_model", "")) or ""),
        oem=str(raw.get("oem", raw.get("OEM", "")) or ""),
        firmware_version=str(raw.get("firmwareVersion", "") or ""),
        online=bool(raw.get("online", False)),
        on=bool(raw.get("on", False)),
        dim_level=_as_float(raw.get("dimLevel"), 0.0) or 0.0,
        active_scene_id=_as_int(raw.get("activeSceneId"), 0) or 0,
        controls=controls,
        status=str(raw.get("status", "") or ""),
    )


def parse_units_map(network_id: str, raw: Any) -> dict[str, CasambiUnit]:
    """Parse the ``units`` mapping of a network information/state payload."""
    units: dict[str, CasambiUnit] = {}
    if not isinstance(raw, dict):
        return units
    for dict_key, entry in raw.items():
        if isinstance(entry, dict) and "id" not in entry:
            # Some payloads omit the inner id; the mapping key carries it.
            entry = {**entry, "id": dict_key}
        unit = parse_unit_info(network_id, entry)
        if unit is not None:
            units[unit.key] = unit
    return units


def parse_scenes_map(network_id: str, raw: Any) -> dict[str, CasambiScene]:
    """Parse the ``scenes`` mapping of a network information payload."""
    scenes: dict[str, CasambiScene] = {}
    if not isinstance(raw, dict):
        return scenes
    for dict_key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        scene_id = _as_int(entry.get("id"), _as_int(dict_key))
        if scene_id is None:
            continue
        name = str(entry.get("name", "") or "").strip() or f"Scene {scene_id}"
        hidden_raw = entry.get("hidden", False)
        if isinstance(hidden_raw, str):
            hidden = hidden_raw.strip().lower() in ("true", "1", "yes")
        else:
            hidden = bool(hidden_raw)
        scene = CasambiScene(
            network_id=network_id,
            scene_id=scene_id,
            name=name,
            hidden=hidden,
            scene_type=str(entry.get("type", "REGULAR") or "REGULAR"),
            position=_as_int(entry.get("position"), 0) or 0,
        )
        scenes[scene.key] = scene
    return scenes


def parse_network_info(network_id: str, raw: Any) -> CasambiNetwork:
    """Parse network level metadata from REST network information."""
    if not isinstance(raw, dict):
        raw = {}
    return CasambiNetwork(
        network_id=network_id,
        name=str(raw.get("name", "") or "").strip() or "Casambi network",
        address=str(raw.get("address", raw.get("mac", "")) or ""),
        grade=str(raw.get("grade", "") or ""),
        revision=_as_int(raw.get("revision")),
    )


def apply_unit_event(unit: CasambiUnit, event: dict[str, Any]) -> CasambiUnit:
    """Merge a WebSocket ``unitChanged`` event into an existing unit.

    Only the fields present in the event are overridden; existing details
    (fixture data, capability controls) are preserved when the event does
    not supply them.
    """
    changes: dict[str, Any] = {}

    if "online" in event:
        changes["online"] = bool(event["online"])
    if "on" in event:
        changes["on"] = bool(event["on"])
    if "dimLevel" in event:
        changes["dim_level"] = _as_float(event["dimLevel"], unit.dim_level)
    if "activeSceneId" in event:
        changes["active_scene_id"] = (
            _as_int(event["activeSceneId"], unit.active_scene_id) or 0
        )
    if "status" in event:
        changes["status"] = str(event["status"] or "")

    if "controls" in event:
        incoming = normalize_controls(event["controls"])
        if incoming:
            merged = dict(unit.controls)
            merged.update(incoming)
            changes["controls"] = merged

    details = event.get("details")
    if isinstance(details, dict):
        name = str(details.get("name", "") or "").strip()
        if name:
            changes["name"] = name
        address = str(details.get("address", "") or "")
        if address and not unit.address:
            # Never change an established address: it is the identity key.
            changes["address"] = address
        fixture_model = str(details.get("fixture_model", "") or "")
        if fixture_model:
            changes["fixture_model"] = fixture_model
        oem = str(details.get("OEM", details.get("oem", "")) or "")
        if oem:
            changes["oem"] = oem

    if not changes:
        return unit
    return replace(unit, **changes)
