"""e-park sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import car_text, normalise_plate
from .const import DOMAIN
from .coordinator import EparkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    bucket = hass.data[DOMAIN][entry.entry_id]
    coordinator: EparkCoordinator = bucket["coordinator"]
    async_add_entities(
        [
            ActivePermitsSensor(coordinator, entry),
            PoolsSensor(coordinator, entry),
            MyPlatesSensor(coordinator, entry),
            HoursLeftSensor(coordinator, entry),
        ]
    )


class _Base(CoordinatorEntity[EparkCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EparkCoordinator, entry: ConfigEntry, key: str, name: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name


class ActivePermitsSensor(_Base):
    _attr_icon = "mdi:ticket-confirmation"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "active_permits", "Active guest permits")

    @property
    def native_value(self):
        permits = (self.coordinator.data or {}).get("active_permits") or []
        return len(permits)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        permits = data.get("active_permits") or []
        cars = data.get("cars") or {}
        return {
            "permits": [
                {
                    "reference": p.get("referenceNumber"),
                    "plate": p.get("licensePlate"),
                    "pool": p.get("tpoolTitle"),
                    "valid_from": p.get("validFrom"),
                    "valid_to": p.get("validTo"),
                    # the car (motor register), e.g. "VOLKSWAGEN GOLF · Blå · 2018"; None if unknown
                    "car": car_text(cars.get(normalise_plate(p.get("licensePlate")))),
                }
                for p in permits
            ]
        }


class PoolsSensor(_Base):
    _attr_icon = "mdi:parking"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "pools", "Available pools")

    @property
    def native_value(self):
        return len((self.coordinator.data or {}).get("pools") or [])

    @property
    def extra_state_attributes(self):
        pools = (self.coordinator.data or {}).get("pools") or []
        return {
            "pools": [
                {
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "description": p.get("description"),
                    "monthly_hours": p.get("monthlyHours"),
                    "max_licenses": p.get("maxLicenses"),
                    "can_stop_early": p.get("enableEarlyStop"),
                    "presets": [
                        {"id": o.get("id"), "text": o.get("text"), "period": o.get("period")}
                        for o in (p.get("defaultOptions") or [])
                    ],
                }
                for p in pools
            ]
        }


class HoursLeftSensor(_Base):
    """Guest hours left this month in the (first) pool, from e-park's own count (GET /pool/remaining/{id}), so
    permits sent from the e-park app count too. Added 2026-10-11 for the PERMIT DISPATCHER popup."""

    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "hours_left", "Guest hours left this month")

    def _remaining(self) -> dict:
        data = self.coordinator.data or {}
        pools = data.get("pools") or []
        rem = data.get("remaining") or {}
        return (rem.get(pools[0]["id"]) or {}) if pools else {}

    @property
    def native_value(self):
        minutes = self._remaining().get("monthlyMinutesRemaining")
        return None if minutes is None else round(minutes / 60, 1)

    @property
    def extra_state_attributes(self):
        r = self._remaining()
        return {
            "total_monthly_hours": r.get("totalMonthlyHours"),
            "minutes_remaining": r.get("monthlyMinutesRemaining"),
            "total_yearly_hours": r.get("totalYearlyHours"),
            "yearly_minutes_remaining": r.get("yearlyMinutesRemaining"),
            "year": r.get("year"),
            "month": r.get("month"),
        }


class MyPlatesSensor(_Base):
    _attr_icon = "mdi:car"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "my_plates", "Registered license plates")

    @property
    def native_value(self):
        return len((self.coordinator.data or {}).get("plates") or [])

    @property
    def extra_state_attributes(self):
        plates = (self.coordinator.data or {}).get("plates") or []
        return {
            "plates": [
                {
                    "plate": p.get("licensePlate"),
                    "info": p.get("carInfo"),
                    "note": p.get("note"),
                }
                for p in plates
            ]
        }
