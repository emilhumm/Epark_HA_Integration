"""e-park (unofficial) - guest parking permits from Q-Park's e-park service, for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import EparkApiError, EparkAuthError, EparkClient, Token, normalise_plate
from .const import (
    ATTR_DURATION_MINUTES,
    ATTR_EMAIL,
    ATTR_LICENSE_PLATE,
    ATTR_NOTE,
    ATTR_PHONE,
    ATTR_POOL_ID,
    ATTR_REFERENCE_NUMBER,
    CONF_DEFAULT_RECIPIENT_EMAIL,
    CONF_DEFAULT_RECIPIENT_PHONE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    SERVICE_ISSUE_PERMIT,
    SERVICE_LOOKUP_PLATE,
    SERVICE_STOP_PERMIT,
)
from .coordinator import EparkCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]

# set up from the UI only (no YAML configuration)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ISSUE_PERMIT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LICENSE_PLATE): cv.string,
        vol.Required(ATTR_DURATION_MINUTES): vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 60)),
        vol.Optional(ATTR_EMAIL): cv.string,
        vol.Optional(ATTR_PHONE): cv.string,
        vol.Optional(ATTR_NOTE): cv.string,
        vol.Optional(ATTR_POOL_ID): vol.Coerce(int),
    }
)

STOP_PERMIT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_REFERENCE_NUMBER): vol.Coerce(int),
    }
)

LOOKUP_PLATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LICENSE_PLATE): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they act on the loaded e-park account."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    token_data = entry.data.get("token")
    token = Token.from_dict(token_data) if token_data else None
    client = EparkClient(
        session, token=token, credentials=(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    )

    def _store_token(new_token: Token) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "token": new_token.to_dict()}
        )

    # every new token (login or refresh, at start-up and while running) is stored, so the next start has a refresh
    # token e-park still accepts (they are single use)
    client.on_token_update = _store_token

    # a valid token, else a refresh, else a password login (the client falls back by itself)
    try:
        await client.ensure_token()
    except EparkAuthError as e:
        # e-park rejected the stored password: Home Assistant starts the re-authentication flow
        raise ConfigEntryAuthFailed(str(e)) from e
    except EparkApiError as e:
        # e-park unreachable (network down at start-up, e-park down): Home Assistant retries the set-up later
        raise ConfigEntryNotReady(f"e-park not reachable: {e}") from e

    coordinator = EparkCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


def _account(hass: HomeAssistant) -> tuple[EparkClient, EparkCoordinator]:
    """The loaded e-park account (the first one if there are several)."""
    buckets = hass.data.get(DOMAIN) or {}
    if not buckets:
        raise ServiceValidationError("e-park is not set up, or its account is not loaded")
    bucket = next(iter(buckets.values()))
    return bucket["client"], bucket["coordinator"]


def _register_services(hass: HomeAssistant) -> None:
    async def issue_permit(call: ServiceCall) -> ServiceResponse:
        data = call.data
        client, coordinator = _account(hass)
        entry_data = coordinator.config_entry_data

        pools = coordinator.data.get("pools", []) if coordinator.data else []
        if not pools:
            raise ServiceValidationError("no guest pools available on this e-park account")

        pool_id = data.get(ATTR_POOL_ID)
        pool = (
            next((p for p in pools if p["id"] == pool_id), None)
            if pool_id is not None
            else pools[0]
        )
        if pool is None:
            raise ServiceValidationError(f"pool id {pool_id} not found (see sensor.available_pools)")

        # the service call's recipient wins, else the defaults from the config entry
        email = data.get(ATTR_EMAIL) or entry_data.get(CONF_DEFAULT_RECIPIENT_EMAIL)
        phone = data.get(ATTR_PHONE) or entry_data.get(CONF_DEFAULT_RECIPIENT_PHONE)
        if not email or not phone:
            missing = " and ".join(n for n, v in (("e-mail", email), ("phone", phone)) if not v)
            raise ServiceValidationError(
                f"recipient {missing} missing: pass it in the call, or set a default recipient "
                "(Settings > Devices & services > e-park > Reconfigure)"
            )

        try:
            result = await client.issue_guest_permit(
                license_plate=data[ATTR_LICENSE_PLATE],
                tpool_id=pool["id"],
                product_id=pool["productId"],
                email=email,
                cellphone=phone,
                duration=timedelta(minutes=int(data[ATTR_DURATION_MINUTES])),
                short_note=data.get(ATTR_NOTE),
            )
        except EparkApiError as e:
            raise HomeAssistantError(f"e-park did not issue the permit: {e}") from e
        result = result if isinstance(result, dict) else {}
        _LOGGER.debug(
            "e-park permit created ref=%s id=%s", result.get("referenceNumber"), result.get("id")
        )
        # refresh now so the active-permits and hours-left sensors show the new permit at once
        await coordinator.async_request_refresh()
        response: dict[str, Any] = {
            "license_plate": normalise_plate(data[ATTR_LICENSE_PLATE]),
            "reference_number": result.get("referenceNumber"),
        }
        return response if call.return_response else None

    async def stop_permit(call: ServiceCall) -> None:
        client, coordinator = _account(hass)
        try:
            await client.stop_permit(int(call.data[ATTR_REFERENCE_NUMBER]))
        except EparkApiError as e:
            raise HomeAssistantError(f"e-park did not stop the permit: {e}") from e
        await coordinator.async_request_refresh()

    async def lookup_plate(call: ServiceCall) -> ServiceResponse:
        """The car behind a plate (Danish motor register, via e-park), as the e-park app shows it."""
        client, _ = _account(hass)
        plate = normalise_plate(call.data[ATTR_LICENSE_PLATE])
        if not plate.isalnum():
            raise ServiceValidationError(f"not a license plate: {plate!r}")
        try:
            car = await client.dmr_lookup(plate)
        except EparkApiError as e:
            if "-> 404" in str(e) or "-> 400" in str(e):
                car = None          # unknown plate
            else:
                raise HomeAssistantError(f"e-park plate lookup failed: {e}") from e
        car = car if isinstance(car, dict) else {}
        result = {
            "license_plate": car.get("licensePlate") or plate,
            "brand": car.get("brand"),
            "model": car.get("model"),
            "variant": car.get("variant"),
            "car_body": car.get("carBody"),
            "color": car.get("color"),
            "year": car.get("year"),
        }
        result["found"] = bool(result["brand"] or result["model"])
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_ISSUE_PERMIT,
        issue_permit,
        schema=ISSUE_PERMIT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_PERMIT, stop_permit, schema=STOP_PERMIT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LOOKUP_PLATE,
        lookup_plate,
        schema=LOOKUP_PLATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
