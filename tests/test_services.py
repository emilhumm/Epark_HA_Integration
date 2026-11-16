"""The services: epark.issue_permit, epark.stop_permit, epark.lookup_plate."""
from __future__ import annotations

import pytest

from custom_components.epark.const import BASE_URL, DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import FakeEpark, make_entry


async def _setup(hass: HomeAssistant, **kwargs):
    entry = make_entry(**kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_issue_permit_returns_response_and_refreshes(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "issue_permit",
        {"license_plate": "cd 45 678", "duration_minutes": "120", "phone": "87654321"},  # "120": a string is fine
        blocking=True,
        return_response=True,
    )
    assert response == {"license_plate": "CD45678", "reference_number": 777}
    body = epark.issued[0]
    assert body["licensePlate"] == "CD45678"
    assert body["period"] == "02:00:00"
    assert body["email"] == "guest@example.com"          # the default recipient e-mail
    assert body["cellphone"] == "87654321"               # the call's phone wins
    assert body["tpoolId"] == 11 and body["productId"] == 22
    assert hass.states.get("sensor.active_guest_permits").state == "2"   # refreshed at once


async def test_issue_permit_without_response(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    assert (
        await hass.services.async_call(
            DOMAIN, "issue_permit", {"license_plate": "CD45678", "duration_minutes": 60}, blocking=True
        )
        is None
    )


async def test_issue_permit_epark_error_is_a_home_assistant_error(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    epark.issue_status = 400
    with pytest.raises(HomeAssistantError, match="did not issue the permit.*pool limit reached") as err:
        await hass.services.async_call(
            DOMAIN, "issue_permit", {"license_plate": "CD45678", "duration_minutes": 60}, blocking=True
        )
    assert not isinstance(err.value, ServiceValidationError)


async def test_issue_permit_missing_recipient(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass, default_email=None)
    with pytest.raises(ServiceValidationError, match="recipient e-mail missing"):
        await hass.services.async_call(
            DOMAIN, "issue_permit", {"license_plate": "CD45678", "duration_minutes": 60}, blocking=True
        )
    assert not epark.issued


async def test_issue_permit_unknown_pool(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError, match="pool id 99 not found"):
        await hass.services.async_call(
            DOMAIN, "issue_permit", {"license_plate": "CD45678", "duration_minutes": 60, "pool_id": 99}, blocking=True
        )


async def test_services_when_account_not_loaded(hass: HomeAssistant, epark: FakeEpark) -> None:
    """Before 0.3.1 this failed with 'coroutine raised StopIteration'."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await hass.services.async_call(
            DOMAIN, "lookup_plate", {"license_plate": "AB12345"}, blocking=True, return_response=True
        )
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await hass.services.async_call(DOMAIN, "stop_permit", {"reference_number": 555}, blocking=True)


async def test_stop_permit(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, "stop_permit", {"reference_number": "555"}, blocking=True)
    assert epark.calls("put", f"{BASE_URL}/parking/license/stop-tpool-permit-now/555")

    epark.stop_status = 400
    with pytest.raises(HomeAssistantError, match="did not stop the permit"):
        await hass.services.async_call(DOMAIN, "stop_permit", {"reference_number": 555}, blocking=True)


async def test_lookup_plate(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup(hass)
    car = await hass.services.async_call(
        DOMAIN, "lookup_plate", {"license_plate": "ab 12 345"}, blocking=True, return_response=True
    )
    assert car == {
        "license_plate": "AB12345",
        "brand": "VOLKSWAGEN",
        "model": "GOLF",
        "variant": "1.5 TSI",
        "car_body": "Hatchback",
        "color": "Blå",
        "year": 2018,
        "found": True,
    }
    unknown = await hass.services.async_call(
        DOMAIN, "lookup_plate", {"license_plate": "ZZ99999"}, blocking=True, return_response=True
    )
    assert unknown["found"] is False and unknown["license_plate"] == "ZZ99999"

    with pytest.raises(ServiceValidationError, match="not a license plate"):
        await hass.services.async_call(
            DOMAIN, "lookup_plate", {"license_plate": "AB-123"}, blocking=True, return_response=True
        )
