"""Config flow: set-up, re-authentication, reconfigure."""
from __future__ import annotations

from aiohttp import ClientError

from custom_components.epark.const import (
    CONF_DEFAULT_RECIPIENT_EMAIL,
    CONF_DEFAULT_RECIPIENT_PHONE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    TOKEN_URL,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import EMAIL, FakeEpark, make_entry

USER_INPUT = {
    CONF_EMAIL: f" {EMAIL} ",
    CONF_PASSWORD: "secret",
    CONF_DEFAULT_RECIPIENT_EMAIL: "",
    CONF_DEFAULT_RECIPIENT_PHONE: "12345678",
}


async def test_user_flow_creates_entry(hass: HomeAssistant, epark: FakeEpark) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"e-park ({EMAIL})"
    data = result["data"]
    assert data[CONF_EMAIL] == EMAIL
    assert data["token"]["access_token"] == "access1"
    assert data[CONF_DEFAULT_RECIPIENT_EMAIL] is None     # blank -> no default
    assert data[CONF_DEFAULT_RECIPIENT_PHONE] == "12345678"
    assert result["result"].unique_id == EMAIL


async def test_user_flow_errors(hass: HomeAssistant, aioclient_mock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    aioclient_mock.post(TOKEN_URL, status=400, text='{"error":"invalid_grant"}')
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, exc=ClientError("dns"))      # before 0.3.1: "Unknown error occurred"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, exc=RuntimeError("bug"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_already_configured(hass: HomeAssistant, epark: FakeEpark) -> None:
    make_entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(hass: HomeAssistant, epark: FakeEpark) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL

    epark.password_status = 400
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    epark.password_status = 200
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new-secret"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"
    assert entry.data["token"]["access_token"] == "access1"


async def test_reconfigure_default_recipient(hass: HomeAssistant, epark: FakeEpark) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEFAULT_RECIPIENT_EMAIL: " new@example.com "}   # phone cleared
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEFAULT_RECIPIENT_EMAIL] == "new@example.com"
    assert entry.data[CONF_DEFAULT_RECIPIENT_PHONE] is None
    assert entry.data[CONF_PASSWORD] == "secret"                     # the rest is kept
