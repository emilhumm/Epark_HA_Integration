"""Set-up, token handling and sensors."""
from __future__ import annotations

from aiohttp import ClientError
import pytest

from custom_components.epark.const import DOMAIN, TOKEN_URL
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import FakeEpark, make_entry


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_with_valid_token_and_sensors(hass: HomeAssistant, epark: FakeEpark) -> None:
    entry = make_entry()
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert epark.token_count == 0                      # a valid stored token is used as it is
    permits = hass.states.get("sensor.active_guest_permits")
    assert permits.state == "1"
    assert permits.attributes["permits"][0]["car"] == "VOLKSWAGEN GOLF · Blå · 2018"
    assert hass.states.get("sensor.guest_hours_left_this_month").state == "75.0"
    assert hass.states.get("sensor.available_pools").attributes["pools"][0]["id"] == 11
    assert hass.states.get("sensor.registered_license_plates").state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("exc", [ClientError("network down"), TimeoutError()])
async def test_setup_retries_when_epark_unreachable(hass: HomeAssistant, aioclient_mock, exc) -> None:
    """Expired token + no network at start-up (e.g. after a power cut): retry later, not a permanent error."""
    aioclient_mock.post(TOKEN_URL, exc=exc)
    entry = make_entry(expired=True)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_on_token_endpoint_server_error(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post(TOKEN_URL, status=503, text="maintenance")
    entry = make_entry(expired=True)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_refresh_falls_back_to_password(hass: HomeAssistant, epark: FakeEpark) -> None:
    epark.refresh_status = 400                         # "refresh token has already been redeemed"
    entry = make_entry(expired=True)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["token"]["access_token"] == "access1"   # the password login's token is stored


async def test_rejected_password_starts_reauth(hass: HomeAssistant, epark: FakeEpark) -> None:
    epark.refresh_status = 400
    epark.password_status = 400
    entry = make_entry(expired=True)
    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_runtime_refresh_rejected_relogs_and_keeps_polling(hass: HomeAssistant, epark: FakeEpark) -> None:
    """Before 0.3.1 this raised ConfigEntryAuthFailed and the coordinator stopped polling until a restart."""
    entry = make_entry()
    await _setup(hass, entry)
    bucket = hass.data[DOMAIN][entry.entry_id]
    client, coordinator = bucket["client"], bucket["coordinator"]

    client.token.expires_at = 0                       # the access token runs out ...
    epark.refresh_status = 400                        # ... and e-park rejects the refresh token
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert entry.data["token"]["access_token"] == "access1"
    assert not hass.config_entries.flow.async_progress()


async def test_runtime_password_rejected_asks_for_reauth(hass: HomeAssistant, epark: FakeEpark) -> None:
    entry = make_entry()
    await _setup(hass, entry)
    bucket = hass.data[DOMAIN][entry.entry_id]
    bucket["client"].token.expires_at = 0
    epark.refresh_status = 400
    epark.password_status = 400
    await bucket["coordinator"].async_refresh()
    await hass.async_block_till_done()

    assert not bucket["coordinator"].last_update_success
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]


async def test_every_new_token_is_stored(hass: HomeAssistant, epark: FakeEpark) -> None:
    entry = make_entry()
    await _setup(hass, entry)
    bucket = hass.data[DOMAIN][entry.entry_id]
    bucket["client"].token.expires_at = 0
    await bucket["coordinator"].async_refresh()
    assert entry.data["token"]["refresh_token"] == "refresh1"   # single-use refresh tokens: keep the newest
