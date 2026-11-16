"""Config flow for e-park: set-up, re-authentication (new password) and reconfigure (default recipient)."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EparkApiError, EparkAuthError, EparkClient, Token
from .const import (
    CONF_DEFAULT_RECIPIENT_EMAIL,
    CONF_DEFAULT_RECIPIENT_PHONE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_DEFAULT_RECIPIENT_EMAIL, default=""): str,
        vol.Optional(CONF_DEFAULT_RECIPIENT_PHONE, default=""): str,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


def _recipient_schema(email: str | None, phone: str | None) -> vol.Schema:
    # suggested (not default) values, so a field can be cleared to remove that default
    return vol.Schema(
        {
            vol.Optional(CONF_DEFAULT_RECIPIENT_EMAIL, description={"suggested_value": email or ""}): str,
            vol.Optional(CONF_DEFAULT_RECIPIENT_PHONE, description={"suggested_value": phone or ""}): str,
        }
    )


class EparkConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _try_login(self, email: str, password: str, errors: dict[str, str]) -> Token | None:
        """Log in once to check the credentials; fills `errors` and returns None when that fails."""
        client = EparkClient(async_get_clientsession(self.hass))
        try:
            return await client.login(email, password)
        except EparkAuthError as e:
            _LOGGER.warning("e-park login rejected: %s", e)
            errors["base"] = "invalid_auth"
        except EparkApiError as e:
            _LOGGER.error("e-park login error: %s", e)
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001 - shown as "unknown error" in the form instead of a crashed flow
            _LOGGER.exception("unexpected error during the e-park login")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            token = await self._try_login(email, password, errors)
            if token is not None:
                data = {
                    CONF_EMAIL: email,
                    CONF_PASSWORD: password,
                    "token": token.to_dict(),
                    CONF_DEFAULT_RECIPIENT_EMAIL: user_input.get(CONF_DEFAULT_RECIPIENT_EMAIL, "").strip() or None,
                    CONF_DEFAULT_RECIPIENT_PHONE: user_input.get(CONF_DEFAULT_RECIPIENT_PHONE, "").strip() or None,
                }
                return self.async_create_entry(title=f"e-park ({email})", data=data)

        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """e-park rejected the stored password (e.g. it was changed in the app)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = await self._try_login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], errors)
            if token is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD], "token": token.to_dict()},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the default recipient (used when a service call names no e-mail / phone)."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_DEFAULT_RECIPIENT_EMAIL: user_input.get(CONF_DEFAULT_RECIPIENT_EMAIL, "").strip() or None,
                    CONF_DEFAULT_RECIPIENT_PHONE: user_input.get(CONF_DEFAULT_RECIPIENT_PHONE, "").strip() or None,
                },
                reason="reconfigure_successful",
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_recipient_schema(
                entry.data.get(CONF_DEFAULT_RECIPIENT_EMAIL), entry.data.get(CONF_DEFAULT_RECIPIENT_PHONE)
            ),
        )
