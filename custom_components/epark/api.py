"""Async e-park API client for Home Assistant."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import BASE_URL, SCOPES, TOKEN_URL

_LOGGER = logging.getLogger(__name__)

# one e-park call may take this long; a hung connection then becomes an EparkApiError instead of blocking
REQUEST_TIMEOUT = ClientTimeout(total=30)


class EparkApiError(Exception):
    """Any e-park failure: an HTTP error, a network error or a timeout."""


class EparkAuthError(EparkApiError):
    """e-park rejected the login or the refresh token (HTTP 400/401 from the token endpoint)."""


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"

    def expired(self, skew: int = 60) -> bool:
        return time.time() + skew >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Token:
        return cls(**data)

    @classmethod
    def from_response(cls, payload: dict) -> Token:
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=time.time() + int(payload.get("expires_in", 3600)),
            token_type=payload.get("token_type", "Bearer"),
        )


def _isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def _period_string(duration: timedelta) -> str:
    total = int(duration.total_seconds())
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d}.{h:02d}:{m:02d}:{s:02d}" if d else f"{h:02d}:{m:02d}:{s:02d}"


def normalise_plate(plate: Any) -> str:
    """'ab 12 345' -> 'AB12345' (how e-park stores and looks up plates)."""
    return "".join(str(plate or "").split()).upper()


def car_text(car: dict | None) -> str | None:
    """A motor-register answer as one line, values exactly as returned: 'VOLKSWAGEN GOLF · Blå · 2018'; None if empty."""
    car = car or {}
    make_model = " ".join(x for x in (car.get("brand"), car.get("model")) if x)
    text = " · ".join(str(x) for x in (make_model, car.get("color"), car.get("year")) if x)
    return text or None


class EparkClient:
    """Async e-park client backed by an aiohttp session."""

    def __init__(
        self,
        session: ClientSession,
        token: Token | None = None,
        credentials: tuple[str, str] | None = None,
    ):
        self._session = session
        self._token: Token | None = token
        # (email, password): lets the client log in again by itself when e-park rejects the refresh token
        self._credentials = credentials
        self._lock = asyncio.Lock()
        # called with the new Token after every login and refresh; the integration stores it in the config entry,
        # because e-park's refresh tokens are single use (a stale one is rejected with "already been redeemed")
        self.on_token_update: Callable[[Token], None] | None = None

    @property
    def token(self) -> Token | None:
        return self._token

    async def login(self, email: str, password: str) -> Token:
        return await self._token_request(
            {"grant_type": "password", "username": email, "password": password, "scope": SCOPES}, "login"
        )

    async def refresh(self) -> Token:
        if not self._token or not self._token.refresh_token:
            raise EparkAuthError("no refresh token available")
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": self._token.refresh_token, "scope": SCOPES}, "refresh"
        )

    async def _token_request(self, data: dict[str, str], what: str) -> Token:
        """POST to the token endpoint. HTTP 400/401 = the password or refresh token was rejected."""
        try:
            async with self._session.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.text()
                if resp.status in (400, 401):
                    raise EparkAuthError(f"{what} rejected: {resp.status} {body[:200]}")
                if resp.status >= 400:
                    raise EparkApiError(f"{what} failed: {resp.status} {body[:200]}")
                payload = await _json(resp, body)
        except (ClientError, TimeoutError) as e:
            raise EparkApiError(f"{what}: network error: {e!r}") from e
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise EparkApiError(f"{what}: unexpected answer from the token endpoint")
        self._token = Token.from_response(payload)
        if self.on_token_update is not None:
            self.on_token_update(self._token)
        return self._token

    async def ensure_token(self) -> None:
        """Make sure there is a usable access token: the current one, else a refresh, else - when the refresh token
        is rejected or missing - a new password login (needs credentials). Raises EparkAuthError when e-park rejects
        the password, EparkApiError when e-park cannot be reached."""
        async with self._lock:
            if self._token is not None and not self._token.expired():
                return
            if self._token is not None and self._token.refresh_token:
                try:
                    await self.refresh()
                    return
                except EparkAuthError as err:
                    if self._credentials is None:
                        raise
                    _LOGGER.warning("e-park refresh rejected, logging in again with the stored password: %s", err)
            elif self._credentials is None:
                raise EparkAuthError("no valid token and no credentials to log in with")
            await self.login(*self._credentials)

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        await self.ensure_token()
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"{self._token.token_type} {self._token.access_token}"
        headers.setdefault("Accept", "application/json")
        try:
            async with self._session.request(
                method, f"{BASE_URL}{path}", headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise EparkApiError(f"{method} {path} -> {resp.status}: {body[:300]}")
                if not body:
                    return None
                return await _json(resp, body)
        except (ClientError, TimeoutError) as e:
            raise EparkApiError(f"{method} {path}: network error: {e!r}") from e

    async def my_pools(self) -> list[dict]:
        return await self._request("GET", "/pool/has-user-permission") or []

    async def my_license_plates(self) -> list[dict]:
        return await self._request("GET", "/user-license-plate") or []

    async def issued_active_tpool_permits(self) -> list[dict]:
        return await self._request("GET", "/parking/license/issued-active-tpool-permits") or []

    async def pool_remaining(self, tpool_id: int) -> dict:
        """TpoolRemainingTimeViewModel for the current month: year, month, totalMonthlyHours, totalYearlyHours,
        monthlyMinutesRemaining, yearlyMinutesRemaining (public e-park API spec, 2026-10-11)."""
        return await self._request("GET", f"/pool/remaining/{tpool_id}") or {}

    async def parking_license(self, reference_number: int) -> dict:
        return await self._request("GET", f"/parking/license/ref/{reference_number}")

    async def dmr_lookup(self, plate: str) -> dict:
        return await self._request("GET", f"/dmr/lookup/{plate}/visual")

    async def issue_guest_permit(
        self,
        *,
        license_plate: str,
        tpool_id: int,
        product_id: int,
        email: str,
        cellphone: str,
        duration: timedelta,
        begin: datetime | None = None,
        short_note: str | None = None,
        long_note: str | None = None,
        suppress_confirmation: bool = False,
    ) -> dict:
        begin = begin or datetime.now(UTC).astimezone()
        body = {
            "licensePlate": normalise_plate(license_plate),
            "periodBegin": _isoformat(begin),
            "period": _period_string(duration),
            "end": _isoformat(begin + duration),
            "tpoolId": tpool_id,
            "productId": product_id,
            "email": email,
            "cellphone": cellphone,
            "isSmsEnabled": True,
            "confirmationEmail": email,
            "confirmationTextMessage": cellphone,
            "shortNote": short_note,
            "longNote": long_note,
            "supressConfirmation": suppress_confirmation,
            "customFields": {},
        }
        # the body holds the recipient's e-mail and phone: debug level only
        _LOGGER.debug("e-park issue_guest_permit request: %s", body)
        result = await self._request("POST", "/parking/license", json=body)
        _LOGGER.debug("e-park issue_guest_permit response: %s", result)
        return result

    async def stop_permit(self, reference_number: int) -> dict:
        return await self._request(
            "PUT", f"/parking/license/stop-tpool-permit-now/{reference_number}"
        )


async def _json(resp, body: str):
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return json.loads(body)
        except ValueError as e:
            raise EparkApiError(f"malformed JSON from e-park: {body[:200]}") from e
    return body
