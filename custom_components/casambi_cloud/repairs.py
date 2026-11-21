"""Repair flows for the Casambi Cloud integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

from .const import ISSUE_CONNECTION_LOST


class ConnectionLostRepairFlow(ConfirmRepairFlow):
    """Confirming the repair reloads the config entry to reconnect."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            await self.hass.config_entries.async_reload(self._entry_id)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the appropriate repair flow for an issue."""
    entry_id = (data or {}).get("entry_id")
    if issue_id.startswith(ISSUE_CONNECTION_LOST) and entry_id:
        return ConnectionLostRepairFlow(entry_id)
    # Gateway-offline (and any other) issues are informational: a plain
    # confirm acknowledges and dismisses them.
    return ConfirmRepairFlow()
