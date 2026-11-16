"""Fixtures: a fake e-park API (HTTP level, through Home Assistant's aiohttp mocker) and a config entry."""
from __future__ import annotations

from datetime import timedelta
import time
from typing import Any
from urllib.parse import parse_qs

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.epark.const import (
    BASE_URL,
    CONF_DEFAULT_RECIPIENT_EMAIL,
    CONF_DEFAULT_RECIPIENT_PHONE,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
    TOKEN_URL,
)
from homeassistant.util import dt as dt_util

pytest_plugins = "pytest_homeassistant_custom_component"

JSON = {"content-type": "application/json; charset=utf-8"}
EMAIL = "user@example.com"
PASSWORD = "secret"

POOL = {
    "id": 11,
    "productId": 22,
    "title": "Guest parking",
    "description": "Guests",
    "monthlyHours": 100,
    "maxLicenses": 3,
    "enableEarlyStop": True,
    "defaultOptions": [{"id": 1, "text": "2 hours", "period": "02:00:00"}],
}
REMAINING = {
    "year": 2026,
    "month": 11,
    "totalMonthlyHours": 100,
    "monthlyMinutesRemaining": 4500,
    "totalYearlyHours": 1200,
    "yearlyMinutesRemaining": 70000,
}
CAR = {
    "licensePlate": "AB12345",
    "brand": "VOLKSWAGEN",
    "model": "GOLF",
    "variant": "1.5 TSI",
    "carBody": "Hatchback",
    "color": "Blå",
    "year": 2018,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/epark."""
    yield


def token_payload(n: int) -> dict[str, Any]:
    return {"access_token": f"access{n}", "refresh_token": f"refresh{n}", "expires_in": 3600, "token_type": "Bearer"}


def make_entry(*, expired: bool = False, default_email: str | None = "guest@example.com") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        title=f"e-park ({EMAIL})",
        data={
            CONF_EMAIL: EMAIL,
            CONF_PASSWORD: PASSWORD,
            "token": {
                "access_token": "access0",
                "refresh_token": "refresh0",
                "expires_at": time.time() + (-100 if expired else 3600),
                "token_type": "Bearer",
            },
            CONF_DEFAULT_RECIPIENT_EMAIL: default_email,
            CONF_DEFAULT_RECIPIENT_PHONE: "12345678",
        },
    )


class FakeEpark:
    """State behind the mocked endpoints; tests change the attributes to steer the answers."""

    def __init__(self, mocker: AiohttpClientMocker) -> None:
        self.mocker = mocker
        now = dt_util.now()
        self.permits: list[dict[str, Any]] = [
            {
                "referenceNumber": 555,
                "licensePlate": "AB12345",
                "tpoolTitle": "Guest parking",
                "validFrom": (now - timedelta(hours=1)).isoformat(),
                "validTo": (now + timedelta(hours=1)).isoformat(),
            }
        ]
        self.refresh_status = 200       # status of a refresh_token grant
        self.password_status = 200      # status of a password grant
        self.issue_status = 200
        self.stop_status = 200
        self.token_count = 0
        self.issued: list[dict[str, Any]] = []

        mocker.post(TOKEN_URL, side_effect=self._token)
        mocker.get(f"{BASE_URL}/pool/has-user-permission", json=[POOL], headers=JSON)
        mocker.get(f"{BASE_URL}/parking/license/issued-active-tpool-permits", side_effect=self._active)
        mocker.get(f"{BASE_URL}/user-license-plate", json=[{"licensePlate": "XY98765", "carInfo": "own car"}], headers=JSON)
        mocker.get(f"{BASE_URL}/pool/remaining/11", json=REMAINING, headers=JSON)
        mocker.get(f"{BASE_URL}/dmr/lookup/AB12345/visual", json=CAR, headers=JSON)
        mocker.get(f"{BASE_URL}/dmr/lookup/ZZ99999/visual", status=404, text="not found")
        mocker.get(f"{BASE_URL}/dmr/lookup/CD45678/visual", status=404, text="not found")
        mocker.post(f"{BASE_URL}/parking/license", side_effect=self._issue)
        mocker.put(f"{BASE_URL}/parking/license/stop-tpool-permit-now/555", side_effect=self._stop)

    def calls(self, method: str, url: str) -> list:
        return [c for c in self.mocker.mock_calls if c[0].lower() == method and str(c[1]) == url]

    async def _token(self, method, url, data):
        form = data if isinstance(data, dict) else {k: v[0] for k, v in parse_qs(str(data)).items()}
        status = self.refresh_status if form.get("grant_type") == "refresh_token" else self.password_status
        if status != 200:
            return AiohttpClientMockResponse(method, url, status=status, text='{"error":"invalid_grant"}', headers=JSON)
        self.token_count += 1
        return AiohttpClientMockResponse(method, url, json=token_payload(self.token_count), headers=JSON)

    async def _active(self, method, url, data):
        return AiohttpClientMockResponse(method, url, json=self.permits, headers=JSON)

    async def _issue(self, method, url, data):
        if self.issue_status != 200:
            return AiohttpClientMockResponse(method, url, status=self.issue_status, text="pool limit reached")
        self.issued.append(data)
        now = dt_util.now()
        self.permits = [
            *self.permits,
            {
                "referenceNumber": 777,
                "licensePlate": data["licensePlate"],
                "tpoolTitle": "Guest parking",
                "validFrom": now.isoformat(),
                "validTo": (now + timedelta(minutes=120)).isoformat(),
            },
        ]
        return AiohttpClientMockResponse(method, url, json={"referenceNumber": 777, "id": 1}, headers=JSON)

    async def _stop(self, method, url, data):
        if self.stop_status != 200:
            return AiohttpClientMockResponse(method, url, status=self.stop_status, text="early stop not allowed")
        return AiohttpClientMockResponse(method, url, text="")


@pytest.fixture
def epark(aioclient_mock: AiohttpClientMocker) -> FakeEpark:
    return FakeEpark(aioclient_mock)
