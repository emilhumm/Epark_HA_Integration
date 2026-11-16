"""DataUpdateCoordinator for e-park."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EparkApiError, EparkAuthError, EparkClient, normalise_plate
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class EparkCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: EparkClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self._client = client
        # motor-register answers per plate (normalised); {} = not in the register. Asked once per plate.
        self._cars: dict[str, dict] = {}

    @property
    def config_entry_data(self) -> dict[str, Any]:
        entry = self.config_entry
        return entry.data if entry else {}

    async def _async_update_data(self) -> dict:
        try:
            pools = await self._client.my_pools()
            active = await self._client.issued_active_tpool_permits()
            plates = await self._client.my_license_plates()
        except EparkAuthError as e:
            # only when e-park rejects the stored password too (the client logs in again by itself when just the
            # refresh token is rejected); Home Assistant then asks for the new password (re-authentication)
            raise ConfigEntryAuthFailed(str(e)) from e
        except EparkApiError as e:
            raise UpdateFailed(str(e)) from e
        # hours left this month per pool (2026-10-11, for the PERMIT DISPATCHER popup); optional - a failure here
        # must not take the other sensors down
        remaining = {}
        for pool in pools or []:
            try:
                remaining[pool["id"]] = await self._client.pool_remaining(pool["id"])
            except (EparkApiError, KeyError) as e:
                _LOGGER.warning("e-park remaining time for pool %s failed: %s", pool.get("id"), e)
        # the car behind each active permit's plate (0.3.0, motor register via e-park); optional as well
        cars = {}
        for permit in active or []:
            plate = normalise_plate(permit.get("licensePlate"))
            if not plate or plate in cars:
                continue
            if plate not in self._cars:
                try:
                    self._cars[plate] = await self._client.dmr_lookup(plate) or {}
                except EparkApiError as e:
                    if "-> 400" in str(e) or "-> 404" in str(e):
                        self._cars[plate] = {}          # not in the register: remember, do not ask again
                    else:
                        _LOGGER.debug("e-park car lookup for an active permit failed (retried next update): %s", e)
                        continue
            cars[plate] = self._cars[plate]
        return {"pools": pools, "active_permits": active, "plates": plates, "remaining": remaining, "cars": cars}
