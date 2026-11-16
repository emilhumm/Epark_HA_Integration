# e-park guest parking for Home Assistant (unofficial)

A Home Assistant custom integration for **e-park**, Q-Park's digital guest-parking permits in Denmark ("T-pool",
gæsteparkering): send guest permits to a license plate from Home Assistant, see the active permits, and see how many
guest hours are left this month. It uses your own e-park login and the same API as the e-park app (`api.e-park.dk`).

> **Unofficial community project.** Not affiliated with, endorsed by or supported by Q-Park. "Q-Park" and "e-park"
> are trademarks of their owners and are used here only to name the service this integration works with. It uses
> your own account, through the same API as the official app. Use it at your own risk: you are responsible for
> every permit it issues, and a wrongly entered plate is your risk just as in the app. If Q-Park changes the API,
> the integration can stop working.

Also in this repo: an optional **permit dispatcher** (helpers + guarded script + automation + dashboard card) that
lets anyone at a wall tablet send a permit safely: visible progress, no double sends, and a confirmation before a
second permit for a car that already has one.

| Folder / file | What it is |
|---|---|
| `custom_components/epark/` | the integration (copy this into Home Assistant) |
| `examples/epark_package.yaml` | the dispatcher: helpers, guarded script, reset automation, car lookup (one HA package file) |
| `examples/dashboard_permit_dispatcher.yaml` | a dashboard card for the dispatcher, core cards only |
| `examples/house/permit_dispatcher_popup.json` | reference: the dispatcher popup as it runs on the original install (HACS cards) |
| `docs/HOW_IT_WORKS.md` | how the integration works inside: login, polling, API endpoints, data mapping |
| `tests/` | tests (pytest with Home Assistant's test harness; e-park is faked, nothing is sent) |
| `CHANGELOG.md` | versions |

## Requirements

- Home Assistant **2024.11** or newer (no extra Python packages).
- An e-park account (the e-mail and password of the e-park app) with access to at least one guest pool.

## Install

**HACS (custom repository):** HACS > menu > Custom repositories > add `https://github.com/emilhumm/Epark_HA_Integration`,
category *Integration* > download *e-park (unofficial)* > restart Home Assistant.

**Manual:** copy the folder `custom_components/epark` to `/config/custom_components/epark` on the Home Assistant
host (Samba share, SSH or the File editor add-on) and restart Home Assistant.

## Set up

Settings > Devices & services > Add integration > **e-park (unofficial)**, then enter:

| Field | |
|---|---|
| E-mail, Password | your e-park app login |
| Default recipient e-mail / phone | where the permit receipt goes when a service call does not name one. A permit is refused when neither the call nor the defaults give both an e-mail and a phone. **The dispatcher only asks for a phone number, so it needs the default e-mail.** |

The login is checked on the spot; one entry per e-park account. Later changes:

- **Default recipient:** Settings > Devices & services > e-park > ⋮ > *Reconfigure*.
- **New password:** when e-park rejects the stored password, Home Assistant shows a *re-authenticate* prompt that
  asks for the new one.

## Entities

Entity ids come from the sensor names (there is no device), so on a new install they are:

| Entity | State | Attributes |
|---|---|---|
| `sensor.active_guest_permits` | number of permits active now | `permits`: list of `reference`, `plate`, `pool`, `valid_from`, `valid_to`, `car` (e.g. "VOLKSWAGEN GOLF · Blå · 2018", from the motor register) |
| `sensor.guest_hours_left_this_month` | hours left in the (first) pool this month, e-park's own count (permits sent from the app count too) | `total_monthly_hours`, `minutes_remaining`, `total_yearly_hours`, `yearly_minutes_remaining`, `year`, `month` |
| `sensor.available_pools` | number of guest pools on the account | `pools`: `id`, `title`, `description`, `monthly_hours`, `max_licenses`, `can_stop_early`, `presets` (the durations the pool offers) |
| `sensor.registered_license_plates` | number of plates on the account | `plates`: `plate`, `info`, `note` |

All four update every 5 minutes, and right after a permit is sent or stopped.

## Services

`epark.issue_permit` - send a guest permit:

```yaml
action: epark.issue_permit
data:
  license_plate: AB12345       # required; spaces are removed and it is upper-cased
  duration_minutes: 120        # required, 1-1440
  phone: "12345678"            # optional, else the default recipient (SMS receipt)
  email: guest@example.com     # optional, else the default recipient
  note: Visitor                # optional short note on the permit
  pool_id: 123                 # optional; default = the first pool (see sensor.available_pools)
response_variable: permit      # optional: {license_plate, reference_number}
```

`epark.stop_permit` - end an active permit early (`reference_number` from `sensor.active_guest_permits`). Only
works when the pool allows early stop (`can_stop_early: true` in `sensor.available_pools`); e-park rejects it
otherwise.

`epark.lookup_plate` - the car behind a (Danish) plate from the motor register, the way the e-park app shows it.
Returns a response, so use it with `response_variable`:

```yaml
action: epark.lookup_plate
data:
  license_plate: AB12345
response_variable: car
# car = {license_plate, brand, model, variant, car_body, color, year, found}
#        found: false for a plate the register does not know
```

Errors: a refused permit or a failed e-park call raises a Home Assistant error with e-park's answer; a missing
recipient, an unknown `pool_id` or a malformed plate raises a validation error. Both stop a script unless the step
has `continue_on_error: true`.

## Optional: the permit dispatcher

1. Enable packages in `configuration.yaml` (`homeassistant: packages: !include_dir_named packages`), copy
   `examples/epark_package.yaml` to `/config/packages/epark.yaml`, restart.
2. Add `examples/dashboard_permit_dispatcher.yaml` as a manual card to a dashboard.
3. Set a default recipient e-mail in the integration (see *Set up*): the form only asks for the phone.

How it behaves:
- the button shows the stage at once: *Dispatch permit* > *Sending...* (cannot be tapped) > *Permit sent* (plate,
  hours, valid until) or *Try again* (with the reason);
- taps while a permit is being sent are dropped;
- a car that already has an active permit, or got one in the last 2 minutes, gets a question first ("Already has a
  permit ... Send another 8 h permit?") with *Cancel* / *Send another*;
- a permit longer than the guest hours left this month is refused with a message, and a permit e-park refuses is
  shown as an error at once (the reason is in Settings > System > Logs);
- under the fields it shows the car behind the plate ("VOLKSWAGEN GOLF · Blå · 2018", exactly as the register
  returns it), or a warning when the plate is not in the motor register - a quick way to catch a typo before sending;
- the stage goes back to idle by itself (2 min after sending, 1 min after an error or an unanswered question).

On a wall tablet, two browser quirks matter if you build your own card: HA's text fields save what you type only
when they lose focus or you press Enter (a tap on a `custom:button-card` does not take the focus away, so the script
would read the previous plate), and Android's keyboard shrinks the page enough for HA popups to go fullscreen. The
reference popup in `examples/house/` handles both; see `docs/HOW_IT_WORKS.md`.

## Moving it to another Home Assistant

1. Install the integration (above) and set it up with the e-park login and a default recipient e-mail.
2. Check the four sensors appear with the entity ids above (rename them back if Home Assistant added a suffix).
3. Optional: the dispatcher package and card. Change `input_select.epark_duration_hours` to your pool's presets
   (`sensor.available_pools` > `presets`) and the phone pattern in `input_text.epark_phone` if your numbers are not
   8 digits.
4. Test with `script.epark_issue_from_form` and `dry_run: true` (everything except the e-park call).

## Good to know

- The e-park password is kept in the config entry (`.storage/core.config_entries`) so the integration can log in
  again by itself when e-park rejects its refresh token; it is in every Home Assistant backup.
- The hours-left sensor and permits without `pool_id` use the first pool on the account.
- e-park's API spec is public: `https://api.e-park.dk/swagger/v1/swagger.json`. The integration only uses endpoints
  that are not marked internal there.

## Development

The tests fake e-park at the HTTP level (nothing is sent to e-park) and run the dispatcher package end to end:

```sh
pip install -r requirements_test.txt
pytest
```

Home Assistant's test harness needs Linux or macOS (on Windows: WSL). GitHub Actions runs the tests, hassfest and
the HACS validation on every push.

## License

[MIT](LICENSE). This covers the code in this repository only, not the e-park service, its API or its trademarks.
