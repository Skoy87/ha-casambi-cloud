"""Config and options flow for the Casambi Cloud integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_EMAIL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.ssl import get_default_context

from .cloud import (
    CasambiAuthenticationError,
    CasambiCloudClient,
    CasambiCommunicationError,
    CasambiRateLimitError,
)
from .const import (
    CONF_NETWORK_PASSWORD,
    CONF_USER_PASSWORD,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_ENABLE_NOTIFICATIONS,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_INCLUDE_HIDDEN_SCENES,
    DEFAULT_RECONNECT_INITIAL_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STARTUP_TIMEOUT,
    DEFAULT_STRUCTURE_REFRESH_INTERVAL,
    DOMAIN,
    MAX_COMMAND_TIMEOUT,
    MAX_FAILURE_THRESHOLD,
    MAX_RECONNECT_INITIAL_DELAY,
    MAX_RECONNECT_MAX_DELAY,
    MAX_SCAN_INTERVAL,
    MAX_STARTUP_TIMEOUT,
    MAX_STRUCTURE_REFRESH_INTERVAL,
    MIN_COMMAND_TIMEOUT,
    MIN_FAILURE_THRESHOLD,
    MIN_RECONNECT_INITIAL_DELAY,
    MIN_RECONNECT_MAX_DELAY,
    MIN_SCAN_INTERVAL,
    MIN_STARTUP_TIMEOUT,
    MIN_STRUCTURE_REFRESH_INTERVAL,
    OPT_COMMAND_TIMEOUT,
    OPT_ENABLE_NOTIFICATIONS,
    OPT_FAILURE_THRESHOLD,
    OPT_INCLUDE_HIDDEN_SCENES,
    OPT_RECONNECT_INITIAL_DELAY,
    OPT_RECONNECT_MAX_DELAY,
    OPT_SCAN_INTERVAL,
    OPT_STARTUP_TIMEOUT,
    OPT_STRUCTURE_REFRESH_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_VALIDATION_TIMEOUT = 45.0


async def _validate_credentials(
    hass, email: str, api_key: str, user_password: str, network_password: str
) -> str | None:
    """Try to log in. Return an error code, or ``None`` on success."""
    session = async_get_clientsession(hass)
    ssl_context = await hass.async_add_executor_job(get_default_context)
    client = CasambiCloudClient(
        session=session,
        api_key=api_key,
        email=email,
        user_password=user_password or None,
        network_password=network_password or None,
        ssl_context=ssl_context,
    )
    try:
        async with asyncio.timeout(_VALIDATION_TIMEOUT):
            await client.async_login()
    except CasambiAuthenticationError:
        return "invalid_auth"
    except CasambiRateLimitError:
        return "rate_limited"
    except (CasambiCommunicationError, TimeoutError):
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating Casambi credentials")
        return "unknown"
    finally:
        await client.async_stop()
    return None


class CasambiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Casambi Cloud configuration flow."""

    VERSION = 2
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._reauth_email: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry,
    ) -> CasambiOptionsFlow:
        return CasambiOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            api_key = user_input[CONF_API_KEY].strip()
            user_password = user_input.get(CONF_USER_PASSWORD, "").strip()
            network_password = user_input.get(CONF_NETWORK_PASSWORD, "").strip()

            if not user_password and not network_password:
                errors["base"] = "no_password"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                error = await _validate_credentials(
                    self.hass, email, api_key, user_password, network_password
                )
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(
                        title=email,
                        data={
                            CONF_EMAIL: email,
                            CONF_API_KEY: api_key,
                            CONF_USER_PASSWORD: user_password,
                            CONF_NETWORK_PASSWORD: network_password,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=self._credentials_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the Cloud rejects the session."""
        self._reauth_email = entry_data.get(CONF_EMAIL)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm new credentials for re-authentication."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        email = self._reauth_email or reauth_entry.data[CONF_EMAIL]

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            user_password = user_input.get(CONF_USER_PASSWORD, "").strip()
            network_password = user_input.get(CONF_NETWORK_PASSWORD, "").strip()
            if not user_password and not network_password:
                errors["base"] = "no_password"
            else:
                error = await _validate_credentials(
                    self.hass, email, api_key, user_password, network_password
                )
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates={
                            CONF_API_KEY: api_key,
                            CONF_USER_PASSWORD: user_password,
                            CONF_NETWORK_PASSWORD: network_password,
                        },
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self._credentials_schema(user_input, include_email=False),
            errors=errors,
            description_placeholders={"email": email},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow the user to change credentials without re-adding."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            api_key = user_input[CONF_API_KEY].strip()
            user_password = user_input.get(CONF_USER_PASSWORD, "").strip()
            network_password = user_input.get(CONF_NETWORK_PASSWORD, "").strip()
            if not user_password and not network_password:
                errors["base"] = "no_password"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                error = await _validate_credentials(
                    self.hass, email, api_key, user_password, network_password
                )
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        reconfigure_entry,
                        title=email,
                        data_updates={
                            CONF_EMAIL: email,
                            CONF_API_KEY: api_key,
                            CONF_USER_PASSWORD: user_password,
                            CONF_NETWORK_PASSWORD: network_password,
                        },
                    )

        defaults = user_input or dict(reconfigure_entry.data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._credentials_schema(defaults),
            errors=errors,
        )

    @staticmethod
    def _credentials_schema(
        current: dict[str, Any] | None, *, include_email: bool = True
    ) -> vol.Schema:
        current = current or {}
        fields: dict[Any, Any] = {}
        if include_email:
            fields[vol.Required(CONF_EMAIL, default=current.get(CONF_EMAIL, ""))] = str
        fields[vol.Required(CONF_API_KEY, default=current.get(CONF_API_KEY, ""))] = str
        fields[
            vol.Optional(
                CONF_USER_PASSWORD, default=current.get(CONF_USER_PASSWORD, "")
            )
        ] = str
        fields[
            vol.Optional(
                CONF_NETWORK_PASSWORD,
                default=current.get(CONF_NETWORK_PASSWORD, ""),
            )
        ] = str
        return vol.Schema(fields)


class CasambiOptionsFlow(OptionsFlowWithReload):
    """Options flow. Changing options reloads the entry automatically."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SCAN_INTERVAL,
                    default=options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Required(
                    OPT_STARTUP_TIMEOUT,
                    default=options.get(
                        OPT_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_STARTUP_TIMEOUT, max=MAX_STARTUP_TIMEOUT),
                ),
                vol.Required(
                    OPT_COMMAND_TIMEOUT,
                    default=options.get(
                        OPT_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_COMMAND_TIMEOUT, max=MAX_COMMAND_TIMEOUT),
                ),
                vol.Required(
                    OPT_RECONNECT_INITIAL_DELAY,
                    default=options.get(
                        OPT_RECONNECT_INITIAL_DELAY,
                        DEFAULT_RECONNECT_INITIAL_DELAY,
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_RECONNECT_INITIAL_DELAY,
                        max=MAX_RECONNECT_INITIAL_DELAY,
                    ),
                ),
                vol.Required(
                    OPT_RECONNECT_MAX_DELAY,
                    default=options.get(
                        OPT_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_RECONNECT_MAX_DELAY, max=MAX_RECONNECT_MAX_DELAY
                    ),
                ),
                vol.Required(
                    OPT_FAILURE_THRESHOLD,
                    default=options.get(
                        OPT_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_FAILURE_THRESHOLD, max=MAX_FAILURE_THRESHOLD
                    ),
                ),
                vol.Required(
                    OPT_STRUCTURE_REFRESH_INTERVAL,
                    default=options.get(
                        OPT_STRUCTURE_REFRESH_INTERVAL,
                        DEFAULT_STRUCTURE_REFRESH_INTERVAL,
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_STRUCTURE_REFRESH_INTERVAL,
                        max=MAX_STRUCTURE_REFRESH_INTERVAL,
                    ),
                ),
                vol.Required(
                    OPT_ENABLE_NOTIFICATIONS,
                    default=options.get(
                        OPT_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS
                    ),
                ): bool,
                vol.Required(
                    OPT_INCLUDE_HIDDEN_SCENES,
                    default=options.get(
                        OPT_INCLUDE_HIDDEN_SCENES, DEFAULT_INCLUDE_HIDDEN_SCENES
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
