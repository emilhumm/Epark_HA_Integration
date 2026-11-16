# How the e-park integration works

```
 e-park app login ──> config flow ──> config entry (email, password, token, default recipient)
                                            │
                        async_setup_entry ──┤ unreachable? retry later / password rejected? re-authenticate
                                            │
                                     EparkClient (api.py, aiohttp, Bearer token; when it expires: refresh,
                                            │       and when the refresh is rejected: log in with the password)
            EparkCoordinator (every 5 min) ─┼─> GET /pool/has-user-permission                 -> pools
                                            ├─> GET /parking/license/issued-active-tpool-permits -> active permits
                                            ├─> GET /user-license-plate                        -> plates
                                            ├─> GET /pool/remaining/{poolId}  (per pool)       -> hours left
                                            └─> GET /dmr/lookup/{plate}/visual (new plates)    -> car per permit
                                            │
                       4 sensors (sensor.py) read the coordinator's data
                                            │
   services epark.issue_permit / epark.stop_permit ──> POST /parking/license / PUT .../stop-tpool-permit-now/{ref}
                                            │          └─> refresh the coordinator at once
            service epark.lookup_plate ─────┴─> GET /dmr/lookup/{plate}/visual
```

## Files

| File | Role |
|---|---|
| `manifest.json` | domain `epark`, config flow, cloud polling, no extra requirements |
| `const.py` | API base URL, token URL, scope, option keys, update interval (5 min), service and field names |
| `api.py` | `EparkClient`: login, token refresh, one method per endpoint; `Token` dataclass; `EparkAuthError` / `EparkApiError` |
| `config_flow.py` | the setup form (logs in once to check the credentials; unique id = the e-mail, lower case), re-authentication (new password) and reconfigure (default recipient) |
| `__init__.py` | set-up / unload, token check at start, the three services (registered once in `async_setup`) |
| `coordinator.py` | `EparkCoordinator`: fetches everything every 5 minutes |
| `sensor.py` | the four sensors |
| `services.yaml`, `strings.json`, `translations/en.json` | service descriptions and UI texts (`en.json` is what Home Assistant reads for a custom integration; keep it a copy of `strings.json`) |

## Login and tokens

- **Setup:** OAuth2 *password* grant: `POST https://api.e-park.dk/connect/token` with `grant_type=password`,
  `username`, `password`, `scope=openid offline_access` (form encoded). HTTP 400/401 = wrong login. The answer
  (`access_token`, `refresh_token`, `expires_in`) becomes a `Token`, stored in the config entry with the e-mail,
  password and the default recipient.
- **Every request:** `Authorization: Bearer <access_token>`; when the token expires (60 s margin), the client first
  refreshes it (`grant_type=refresh_token`); if e-park rejects the refresh token (HTTP 400/401), the client logs in
  again with the stored password (`EparkClient.ensure_token`, at start-up and while running alike, since 0.3.1).
- **Every new token is stored** (`EparkClient.on_token_update` -> config entry): e-park's refresh tokens are single
  use, so before 0.3.0 the token kept from start-up was already spent after the first runtime refresh and every
  restart logged "The specified refresh token has already been redeemed" and fell back to the password.
- **e-park unreachable at start-up** (network error, timeout - every call has a 30 s timeout - or an HTTP error from
  the token endpoint): `ConfigEntryNotReady`, so Home Assistant retries the set-up by itself.
- **Password rejected** (at start-up or while running): `ConfigEntryAuthFailed`; Home Assistant shows a
  re-authentication form that asks for the new password and reloads the integration with it.

## Polling (coordinator, every 5 minutes)

| Call | Used for |
|---|---|
| `GET /pool/has-user-permission` | the guest pools: id, title, `productId` (needed to issue), `monthlyHours`, `maxLicenses`, `enableEarlyStop`, `defaultOptions` (presets) |
| `GET /parking/license/issued-active-tpool-permits` | permits active now: `referenceNumber`, `licensePlate`, `tpoolTitle`, `validFrom`, `validTo` |
| `GET /user-license-plate` | the account's own plates: `licensePlate`, `carInfo`, `note` |
| `GET /pool/remaining/{poolId}` | per pool: `totalMonthlyHours`, `monthlyMinutesRemaining`, `totalYearlyHours`, `yearlyMinutesRemaining`, `year`, `month`. Optional: if it fails, a warning is logged and the other sensors still update |
| `GET /dmr/lookup/{plate}/visual` | the car behind each active permit's plate, asked once per plate per run (a plate the register does not know is remembered as unknown). Optional as well |

A failure of one of the first three calls makes the whole update fail (`UpdateFailed`); the sensors keep their last
values and become unavailable only if updates keep failing.

## Sensors

| Sensor | State | Attributes (API field) |
|---|---|---|
| Active guest permits | `len(permits)` | `permits[]`: reference (`referenceNumber`), plate (`licensePlate`), pool (`tpoolTitle`), valid_from (`validFrom`), valid_to (`validTo`), car (motor register via `GET /dmr/lookup/{plate}/visual`, each plate asked once per run and remembered; `None` if unknown) |
| Guest hours left this month | `monthlyMinutesRemaining / 60`, 1 decimal, first pool | total_monthly_hours, minutes_remaining, total_yearly_hours, yearly_minutes_remaining, year, month |
| Available pools | `len(pools)` | `pools[]`: id, title, description, monthly_hours (`monthlyHours`), max_licenses (`maxLicenses`), can_stop_early (`enableEarlyStop`), presets (`defaultOptions`: id, text, period) |
| Registered license plates | `len(plates)` | `plates[]`: plate, info (`carInfo`), note |

## Issuing a permit (`epark.issue_permit`)

1. Takes the first loaded config entry (one account), the pool given by `pool_id` or the first pool.
2. Recipient: the call's `email` / `phone`, else the default recipient (set-up or *Reconfigure*); both are required.
3. `POST /parking/license` with JSON:
   `licensePlate` (upper case, no spaces), `periodBegin` (now, ISO with offset), `period` (`HH:MM:SS` or
   `D.HH:MM:SS`), `end`, `tpoolId`, `productId`, `email`, `cellphone`, `isSmsEnabled: true`,
   `confirmationEmail`, `confirmationTextMessage`, `shortNote`, `longNote`, `supressConfirmation: false`,
   `customFields: {}`.
4. Refreshes the coordinator, so the new permit and the new hours-left value show within about a second.
5. Returns `{license_plate, reference_number}` when called with `response_variable` (optional).

Errors: a missing recipient, an unknown `pool_id` or an account that is not loaded raise `ServiceValidationError`;
an e-park failure raises `HomeAssistantError` with e-park's answer. Both stop a script unless the step has
`continue_on_error: true`; the dispatcher package uses that plus the response to show a refused permit at once.

`epark.stop_permit` calls `PUT /parking/license/stop-tpool-permit-now/{referenceNumber}` and refreshes; e-park only
allows it for pools with early stop enabled.

## Looking up a plate (`epark.lookup_plate`, 0.3.0)

`GET /dmr/lookup/{plate}/visual` (DMR = the Danish motor register) returns `MotorRegistryViewModel`:
`licensePlate`, `brand`, `model`, `variant`, `carBody`, `color`, `year`. The service removes spaces, upper-cases the
plate, refuses anything that is not letters and digits (`ServiceValidationError`), and returns those fields in
snake_case plus `found` (`brand` or `model` present). HTTP 404/400 = unknown plate (`found: false`); other failures
raise a `HomeAssistantError`, so an automation can carry on with `continue_on_error: true`. Registered with
`SupportsResponse.ONLY` (call it with `response_variable`).

Other API calls in `api.py` that the integration does not use yet: `GET /parking/license/ref/{referenceNumber}`
(one permit in full). The public spec
(`https://api.e-park.dk/swagger/v1/swagger.json`, 400+ paths) also lists usage per month
(`/pool/usage/{tpoolId}`), limits (`/tpool-limit/status/{tpoolId}`) and permits per period (`/pool-use/...`).

## The permit dispatcher (examples/)

The package keeps the dispatcher's state in two helpers so any dashboard can show it:

| `input_select.epark_stage` | `input_text.epark_message` | Shown as |
|---|---|---|
| idle | (anything) | Dispatch permit |
| sending | `PLATE|hours` | Sending... (no tap action) + "Sending 8 h for PLATE ..." |
| confirm | `PLATE|HH:MM|hours` | question "PLATE is covered until HH:MM. Send another 8 h permit?" + Cancel / Send another |
| sent | `PLATE|HH:MM|hours` | Permit sent + "PLATE · 8 h · valid until HH:MM" |
| error | error text | Try again + the text |

`script.epark_issue_from_form` (mode single, extra taps dropped silently):
1. waits 250 ms (so a text field that a tap just blurred has saved), reads plate / phone / hours from the helpers;
2. incomplete form -> error;
3. the plate (upper case, no spaces) has an active permit, or the stage is "sent" for it less than 2 minutes ago,
   and this is not a valid confirmation -> stage "confirm", nothing sent. A valid confirmation = `confirm: true`
   while the stage is "confirm" for the same plate, 1.5 to 60 s after the question appeared;
4. hours asked > `sensor.guest_hours_left_this_month` -> error "Only X h left this month";
5. stage "sending", calls `epark.issue_permit` (skipped with `dry_run: true`; `continue_on_error` + response, so a
   refused permit becomes stage "error" at once), waits up to 20 s until the new permit is in
   `sensor.active_guest_permits`, then stage "sent" with the plate, valid-until time and hours.

`automation` *e-park · Dispatcher stage reset*: sent 2 min / error 1 min / confirm 1 min -> idle; sending 30 s ->
error ("No answer from e-park ...", now only for a call that hangs); plate edited -> idle; Home Assistant start ->
idle.

### Wall-tablet details (only for custom cards / popups)

- Home Assistant's text fields (entities card rows) save on blur or Enter only. Core `button` cards take the focus
  on tap, so the field saves. A `custom:button-card` does not: the original popup gives every script button a name
  template that installs, once per page, a capture listener blurring the focused text field on `pointerdown` /
  `touchstart` (see `COMMIT_JS` in the reference JSON). Without it the script read the previous plate.
- Android's keyboard shrinks the viewport (710 -> about 400 px on a 1138 px wide tablet); browser_mod 3.2's
  `@media all and (max-width: 450px), all and (max-height: 500px)` then makes popups fullscreen. The reference
  popup's `popup_styles` adds `@media (min-width: 451px) and (max-height: 500px)` with the popup's own width
  (`!important`, because browser_mod's rules are applied later).
- Home Assistant's markdown strips `style` and `class` attributes; the reference popup styles its markdown from
  card_mod through `ha-markdown$` and uses `<ha-alert>` for status boxes.
- Car lookup while typing: because the plate field saves only on blur, the package's automation looks the car up
  when the keyboard closes. The reference popup also installs a page-level `input` listener (`LIVE_JS`, flag
  `__epkLive1`) that reads the plate field's live text and, 0.7 s after typing stops (5+ letters/digits), calls
  `epark.lookup_plate` from the browser (`hass.callService(..., returnResponse = true)`) and writes
  `input_text.epark_vehicle`; the field itself is never written, and replies to older keystrokes are dropped.
