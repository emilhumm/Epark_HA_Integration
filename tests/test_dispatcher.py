"""The permit dispatcher package (examples/epark_package.yaml) end to end, against the integration and a fake e-park."""
from __future__ import annotations

from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict

from .conftest import FakeEpark, make_entry

PACKAGE = Path(__file__).parent.parent / "examples" / "epark_package.yaml"

# the package's reset automation waits 1-2 minutes in each stage (state triggers with `for:`), so its timers are
# still pending when a test ends
pytestmark = pytest.mark.parametrize("expected_lingering_timers", [True])


async def _setup_dispatcher(hass: HomeAssistant, plate: str = "CD45678") -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    package = load_yaml_dict(str(PACKAGE))
    for domain in ("input_select", "input_text", "script", "automation"):
        assert await async_setup_component(hass, domain, {domain: package[domain]})
    await hass.async_block_till_done()
    for entity_id, value in (("input_text.epark_phone", "12345678"), ("input_text.epark_plate", plate)):
        await hass.services.async_call(
            "input_text", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
        )
    await hass.async_block_till_done()


def _stage(hass: HomeAssistant) -> tuple[str, str]:
    return (
        hass.states.get("input_select.epark_stage").state,
        hass.states.get("input_text.epark_message").state,
    )


async def test_dispatcher_sends_permit(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup_dispatcher(hass)
    await hass.services.async_call("script", "epark_issue_from_form", {}, blocking=True)

    stage, message = _stage(hass)
    assert stage == "sent"
    assert message.startswith("CD45678|") and message.endswith("|2")
    assert epark.issued[0]["licensePlate"] == "CD45678"
    assert epark.issued[0]["period"] == "02:00:00"


async def test_dispatcher_shows_refused_permit_at_once(
    hass: HomeAssistant, epark: FakeEpark, caplog: pytest.LogCaptureFixture
) -> None:
    """Before 0.3.1 the script stopped in stage 'sending' and only the 30 s reset showed 'No answer from e-park'."""
    await _setup_dispatcher(hass)
    epark.issue_status = 400
    await hass.services.async_call("script", "epark_issue_from_form", {}, blocking=True)

    stage, message = _stage(hass)
    assert stage == "error"
    assert message.startswith("Permit not sent")
    assert "pool limit reached" in caplog.text         # the reason is in the log, as the message says


async def test_dispatcher_asks_before_a_second_permit(hass: HomeAssistant, epark: FakeEpark) -> None:
    await _setup_dispatcher(hass, plate="AB12345")       # already has an active permit
    await hass.services.async_call("script", "epark_issue_from_form", {}, blocking=True)

    stage, message = _stage(hass)
    assert stage == "confirm"
    assert message.startswith("AB12345|")
    assert not epark.issued
