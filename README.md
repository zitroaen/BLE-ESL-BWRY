# Zhsunyco ESL — BLE e-ink label for Home Assistant

[![Validate](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml/badge.svg)](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Put pictures on a battery-powered BLE e-ink shelf label from Home
Assistant — a calendar, a dashboard, a name plate — and drive its LED.

Works with labels running Wolink / Zhsunyco firmware, such as the
`BLE-35BWRY` (184 × 384, black/white/red/yellow). Local Bluetooth adapters
and ESPHome Bluetooth proxies are both supported.

**This is the user manual.** The interface itself is specified separately in
[`docs/protocol.md`](docs/protocol.md), and the measurements behind it are in
[`docs/hardware-verified-findings.md`](docs/hardware-verified-findings.md).

## Quick start

1. Install through HACS as a custom repository, restart, add the integration
2. Wait for the label to be discovered, or add it by address
3. Press the **Test pattern** button on the device page

If the panel redraws within a few minutes, everything works. The first
command is always the slow one — see
[Why the first command takes minutes](#why-the-first-command-takes-minutes).

## What you can do

| | |
|---|---|
| Send a picture | from a file or a URL, any size, any colours |
| Send a test pattern | built in, no image file needed |
| Clear the screen | one button or one service call |
| See what the panel shows | an `image` entity, kept across restarts |
| Drive the RGB LED | colour, blink rate, duration |
| Read battery and status | without connecting, from the advertisement |

Not supported: firmware updates over the air, and storing several images in
the label to switch between. Compressed upload is not used because the
vendor never documented the compression scheme.

## Installation

1. In Home Assistant: **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/zitroaen/BLE-ESL-BWRY`, category **Integration**
3. Add it, then download **Zhsunyco ESL**
4. Restart Home Assistant
5. **Settings → Devices & services → Add integration → Zhsunyco ESL**

Updates then arrive through HACS as usual.

### Manually

Copy `custom_components/esl_zhsunyco/` into `<config>/custom_components/`
and restart Home Assistant.

### Upgrading from the pre-HACS prototype

A config entry created by the earlier prototype (titled like
`ESL 66:66:17:40:27:77 (BLE-35BWRY)`) is **migrated automatically** on the
first start. The address, the model and the old `battery_scan_interval` are
carried over. There is no need to delete and re-add the device.

## Setup

Labels are usually **discovered automatically**. To add one by hand you need
its address, which always starts with `66:66` — for example
`66:66:54:20:00:55`.

Two options are worth knowing about:

**Scan interval** only controls the connection-based poll for status and
version. Battery and version numbers arrive passively without a connection,
so a long interval is fine. `0` disables the poll entirely.

**Linger** is how long the connection stays open after a command, 15 seconds
by default. Commands inside that window are instant. Longer is not better: a
connected label stops advertising, so while the link is held nothing can see
the label — not even Home Assistant.

## Entities

| Entity | Type | Note |
|---|---|---|
| Battery voltage | Sensor | no connection needed |
| Status | Sensor | busy, or a readable error code |
| Panel | Image | what was last put on the screen |
| RGB LED | Light | colour and blink pattern |
| RGB on/off time, RGB duration | Number | blink parameters |
| Clear screen, test pattern, diagnostic probe | Button | one press each |
| Signal strength, display version, product ID | Sensor | diagnostic, off by default |

## Sending a picture

`set_image` takes either a local `path` or a `url`.

For a local file, Home Assistant only allows directories you have opened up,
so add this to `configuration.yaml` once:

```yaml
homeassistant:
  allowlist_external_dirs:
    - /config/www/esl
```

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <your label>
  path: /config/www/esl/calendar.png
  dither: true
```

A URL needs no allowlist:

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <your label>
  url: https://example.com/calendar.png
```

Only `http` and `https` are accepted, the download times out after 30
seconds, and anything over 8 MB is refused.

You do not have to prepare the picture. It is fitted to the panel, dithered
onto the four colours the hardware can display, and packed for upload. A
full screen takes about half a minute once the connection is up.

For graphics with large flat areas — text, tables, a calendar — set
`dither: false`. Dithering speckles solid colour, which looks worse than it
sounds on a small panel.

Options: `rotate` (0/90/180/270), `mirror`, `invert`, `dither`.

### Test patterns

```yaml
action: esl_zhsunyco.send_test_pattern
data:
  device_id: <your label>
  pattern: diagnostic
```

The `diagnostic` pattern is built to be read off a photograph, which makes
it the right first thing to send to a new label:

| What you see | What it tells you |
|---|---|
| Frame closed all the way round | The whole panel is being addressed |
| Blocks in the order black, red, yellow, white | Colours are mapped correctly |
| Both one-pixel gratings sharp, no smearing | The pixel packing is correct |

If the gratings smear or the frame is cut off, the panel is probably not the
one configured — check the model in the integration options.

Other patterns: `solid_black`, `solid_white`, `solid_red`, `solid_yellow`,
`stripes_h`, `stripes_v`, `checkerboard`, `quadrants`.

## Did the send work?

There are **two** different failures, and they feel different:

| Case | How you notice |
|---|---|
| The transfer failed (label asleep, connection dropped) | The service raises, the automation stops |
| The transfer was accepted and the panel did nothing | **No error at all** — only `label_reacted: false` |

The second is the treacherous one: Home Assistant reports success while the
label still shows the old picture. So `set_image`, `send_test_pattern`,
`clear_screen`, `set_rgb` and `debug_command` return a result:

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <your label>
  path: /config/www/esl/calendar.png
response_variable: result
```

```yaml
results:
  - address: "66:66:17:40:27:77"
    ok: true              # the write itself went through
    label_reacted: true   # the panel reported busy, so it really drew
    connection_dropped: false
    error_code: 0
    bytes: 17664
    at: "2026-09-06T19:12:04.881+00:00"
```

**`label_reacted` is the check that matters.** `ok: true` only says the
bytes went out.

The response is optional, so automations written without
`response_variable` keep working unchanged.

### An automation that retries

A sleeping label is the normal case, not the exception. Three attempts with
a real pause between them is realistic:

```yaml
- repeat:
    count: 3
    sequence:
      # Reset it: after a raised error the variable would otherwise still
      # hold the result of the previous iteration.
      - variables:
          result: null
      - action: esl_zhsunyco.set_image
        data:
          device_id: <your label>
          path: /config/www/esl/calendar.png
        response_variable: result
        continue_on_error: true
      - if:
          - condition: template
            value_template: "{{ result and result.results[0].label_reacted }}"
        then:
          - stop: "The picture is on the panel"
      - delay: "00:05:00"
- action: persistent_notification.create
  data:
    title: ESL
    message: The calendar image did not get through after three attempts.
```

Both oddities in there are load-bearing. `continue_on_error: true` keeps a
connection failure from ending the loop instead of driving it, and the
`variables:` reset is not cosmetic: without it `result` keeps its last
successful value after a raised error and the loop stops early, believing it
succeeded.

Be generous with the delay. The integration already waits up to five minutes
for the label to appear, and a quick second attempt only competes with the
first one for the single connection slot.

### Without a response variable

The `image` entity's state **is** the timestamp of the last successful
upload, which is enough to trigger on:

```yaml
triggers:
  - trigger: state
    entity_id: image.esl_66_66_17_40_27_77_panel
```

And to ask whether anything landed today:

```yaml
{{ states('image.esl_66_66_17_40_27_77_panel') | as_datetime | as_local
   > today_at('00:00') }}
```

## What the panel is showing

Every label has an `image` entity holding the last picture that was
uploaded. It is built from the pixels that were actually sent, so dithering
and colour reduction show up in it exactly as they do on the panel.

```yaml
type: picture-entity
entity: image.esl_66_66_17_40_27_77_panel
```

It **survives a restart**. An e-ink panel holds its image without power, so
as long as nothing else writes to the label it is still showing exactly
that. The picture is stored per label in `.storage`, which costs about 1 kB
for flat graphics and text and around 14 kB for a fully dithered
photograph.

It is updated **only after a successful transfer**:

- If the send fails, the previous preview stays — which is also what the
  panel is still showing.
- **Clearing the screen discards it.** The panel is no longer showing the
  image, and restoring it after a restart would be a picture of a blank
  screen.
- Removing the label from Home Assistant deletes the stored copy.

It also stays **available** while the label sleeps: what is on the panel
does not stop being true because nobody can see it right now.

This assumes **only** this integration writes to the label. If something
else does, the preview will be stale.

## Other services

```yaml
# Blink the LED red for 30 seconds
action: esl_zhsunyco.set_rgb
data:
  device_id: <your label>
  rgb_color: [255, 0, 0]
  on_ms: 500
  off_ms: 500
  work_ms: 30000
```

```yaml
action: esl_zhsunyco.clear_screen
data:
  device_id: <your label>
```

| Service | What it does |
|---|---|
| `set_image` | Send a picture from a file or a URL |
| `send_test_pattern` | Send a built-in pattern |
| `clear_screen` | Clear the panel |
| `set_rgb` | Drive the RGB LED |
| `debug_probe` | Connect and dump the whole GATT table |
| `debug_command` | Write raw bytes to the command characteristic |

The last two are for diagnosing an unfamiliar label; see
[`docs/protocol.md`](docs/protocol.md) if you need them.

## Troubleshooting

### Start with the diagnostic probe

The fastest way to narrow anything down: press the **Diagnostic probe**
button on the device page, or call `esl_zhsunyco.debug_probe`. It connects,
lists everything the label offers, tries the unlock and reads the raw
values. The result arrives as a notification and in the log.

It works even when every other entity is unavailable — it does not fail, it
reports why under `connection_error`.

Two fields say the most:

- `connection` — `ok` means the connection works.
- `reads.status.error_meaning` — `unlock_failed` means the label rejected
  authentication; anything else means that part worked.

**Settings → Devices & services → Zhsunyco ESL → Download diagnostics**
gives you a JSON file with everything the integration knows, including
`last_command` — what the last command did, and whether the panel reacted —
and `version_bytes`, the raw version field the label reports.
The download does not open a connection; use the probe button for that.

### Why the first command takes minutes

An ESL sleeps between advertisements, and gaps of several minutes are
normal. A label can only be connected to while it is briefly awake, so the
integration waits for that window — up to five minutes — and connects
inside it.

**The first button press taking a long time is normal, not a fault.**
Afterwards the connection stays open for 15 seconds, so follow-up commands
are instant.

In the log this shows up as:

```
BleakOutOfConnectionSlotsError: ... no scanner currently has it in its
discovered devices ... last advertisement 262s ago
```

If it never succeeds, the adapter or proxy is too far away or has no free
connection slots. Another
[ESPHome Bluetooth proxy](https://esphome.github.io/bluetooth-proxies/)
near the label is the fix.

### Two ESL integrations at once

A label accepts only **one** connection at a time. If a second integration
talks to the same label, the two take turns and commands go missing. Disable
the other one for this label as a test.

### The LED or clear screen does nothing

1. **Check `unlock_verified` in the diagnostics.** If it is `false` the
   label rejected authentication and ignores every command by definition.
2. **Read `last_command` in the diagnostics.** `label_reacted: false` means
   the panel never became busy after the command, so it did not act on it.
3. **Check the model** in the integration options. On a model other than the
   BLE-35BWRY the command encoding has never been measured.

### All entities show as unavailable

The label has not been heard from for a while — it is out of range, or
something else is holding a connection to it (see above). The `bluetooth`
section of the diagnostics download separates the cases:

| Field | Meaning |
|---|---|
| `last_service_info_any: null` | Home Assistant cannot see the label at all |
| `last_service_info_any` set but entities empty | It is seen, but not reaching this integration |
| `scanners_seeing_this_label: []` | No adapter or proxy is receiving it |
| `learned_advertising_interval_s` | How often Home Assistant sees it transmit |

Reloading the integration (⋮ → Reload) is a safe first step.

### Enable debug logging

```yaml
logger:
  default: warning
  logs:
    custom_components.esl_zhsunyco: debug
```

**"not in range of any Bluetooth adapter or proxy":** Home Assistant cannot
see the label right now. ESPHome proxies need `bluetooth_proxy: active:
true`, otherwise only passive advertisements are possible.

**Status reports `unlock_failed`:** the label rejected authentication. Check
whether it really runs this firmware family — `protocol_family` in the probe
output says which one it speaks. If it says `easytag_xor`, this integration
is the wrong software for that label; see
[`docs/protocol.md`](docs/protocol.md) for the other family.

## Development

```bash
pip install -r requirements-test.txt ruff

ruff check custom_components tests scripts
ruff format --check custom_components

python scripts/check_services.py   # services.yaml + translations, hassfest rules
python tests/test_protocol.py      # protocol, no Home Assistant needed
pytest tests/integration -q        # against a real Home Assistant
```

There are two levels of test. `tests/test_protocol.py`, `test_imaging.py`
and `test_diagnostics.py` check the generated wire bytes against
[`docs/protocol.md`](docs/protocol.md); they need no Home Assistant, because
`protocol.py` and `imaging.py` contain no HA imports and can be driven
against real hardware from a plain script. `tests/integration/` starts a
real Home Assistant and covers the config flow, entities, services and
advertisement handling.

To publish a release, push a git tag matching the `version` in
`manifest.json`:

```bash
git tag v0.25.0 && git push origin v0.25.0
```

## Licence

MIT — see [LICENSE](LICENSE).
