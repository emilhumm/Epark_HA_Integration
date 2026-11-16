# Changelog

## 0.3.1 - 2026-11-16

Needs Home Assistant **2024.11** or newer (was 2024.4; the dispatcher examples already needed 2024.10).

- Start-up when e-park cannot be reached (network down after a power cut, e-park down, timeout): Home Assistant now
  retries the set-up by itself (`ConfigEntryNotReady`) instead of leaving the entry failed until a manual reload.
- A refresh token e-park rejects while running (single use: e.g. a refresh whose answer was lost) no longer stops
  the integration: the client logs in again with the stored password, as it already did at start-up.
- Re-authentication: when e-park rejects the stored password, Home Assistant asks for the new one (no more removing
  and re-adding the integration). New **Reconfigure** step to change the default recipient e-mail / phone.
- Services: e-park failures raise a Home Assistant error with e-park's message, input problems (no recipient, unknown
  pool, not a plate) a validation error, and calling a service while the account is not loaded says so instead of
  failing with `StopIteration`. `duration_minutes` also accepts `"120"`. The services are registered once at start.
- `epark.issue_permit` can return a response (`license_plate`, `reference_number`); the dispatcher package uses it
  to show a refused permit at once instead of "No answer from e-park" after 30 s.
- Every e-park call has a 30 s timeout; network errors and timeouts of the login and refresh are reported as e-park
  errors (the setup form shows "Could not reach api.e-park.dk" instead of "Unknown error").
- The coordinator gets its config entry passed explicitly (the implicit lookup is deprecated in Home Assistant).
- Translations: `translations/en.json` = `strings.json` (service texts were missing there); manifest keys sorted.
- Tests (pytest, Home Assistant test harness), GitHub Actions (hassfest, HACS validation, tests), MIT licence.
- Shown as **e-park (unofficial)** (was "e-park by Q-Park"), with a disclaimer in the README; the domain `epark`
  and all entity ids are unchanged.

## 0.3.0 - 2026-10-11

- New service **`epark.lookup_plate`**: the car behind a plate from the motor register (`GET
  /dmr/lookup/{plate}/visual`: brand, model, variant, body, colour, year), returned as a service response; `found:
  false` for an unknown plate.
- Dispatcher package: helper `input_text.epark_vehicle` + automation `epark_vehicle_lookup` (looks the plate up when
  it changes); the dashboard card shows the car, or a warning for a plate the register does not know.
- Each active permit in `sensor.active_guest_permits` carries `car` ("VOLKSWAGEN GOLF · Blå · 2018", motor register);
  every plate is looked up once and remembered.
- Token refreshes made while running are stored in the config entry (e-park refresh tokens are single use), so a
  restart no longer logs "refresh token has already been redeemed" and needs the password fallback.

## 0.2.0 - 2026-10-11

- New sensor **Guest hours left this month** (`GET /pool/remaining/{poolId}`, e-park's own count; also used by the
  dispatcher to refuse a permit longer than what is left).
- Privacy: the permit request/response (recipient e-mail and phone) is logged at debug level instead of warning,
  and the leftover diagnostics that fetched and logged the full record of the new permit and of another active
  permit after every send are gone.
- manifest: documentation / issue tracker point to this repository, code owner set.
- Examples: the permit dispatcher package (helpers, guarded script, reset automation), a core-cards dashboard card,
  and the original install's popup as a reference.

## 0.1.0 - 2026-04-20

- First version, written for one Home Assistant install: config flow (e-park login + default recipient), sensors
  for active permits, pools and registered plates, services `issue_permit` and `stop_permit`.
