# Zhsunyco ESL — BLE e-ink label for Home Assistant

[![Validate](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml/badge.svg)](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Home Assistant integration for BLE e-ink shelf labels running Wolink /
Zhsunyco firmware, such as the `BLE-35BWRY`.

It follows the vendor document **"BLE Display API" rev 1.5**, and every
command and characteristic in the code carries the section number it comes
from. Where the document is silent or wrong, the code says what was measured
on hardware instead — see
[`docs/hardware-verified-findings.md`](docs/hardware-verified-findings.md).

## What works

| Feature | Document | Status |
|---|---|---|
| Security unlock (challenge/response, AES-128-ECB) | sec. 2 | ✅ verified on hardware |
| Battery level, passively from the advertisement | sec. 1.2 | ✅ verified on hardware |
| Version, battery and status characteristics | sec. IV–VI | ✅ verified on hardware |
| Image upload `0xA500` / refresh `0xA501` | sec. 3.1–3.2 | ✅ verified on hardware |
| Clear screen `0xA504` | sec. 3.7 | ✅ verified on hardware |
| RGB LED `0xA508` | sec. 3.8 | ✅ verified on hardware |
| Test patterns, no image file needed | – | ✅ |
| Panel preview as an `image` entity | – | ✅ |
| Compressed refresh `0xA502` | sec. 3.3 | ❌ the compression format is undocumented |
| Multi-screen `0xA503` / `0xA509` | sec. 3.9–3.10 | ❌ not implemented |
| OTA `0xA505`–`0xA507` | sec. 3.4–3.6 | ❌ deliberately not implemented |

Works with local Bluetooth adapters **and ESPHome Bluetooth proxies**:
connections go through Home Assistant's `bluetooth` helpers and
`bleak-retry-connector`, not through `bleak` directly. Everything marked
verified above was measured through a proxy as well as a direct adapter.

## Installation through HACS

1. In Home Assistant: **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/zitroaen/BLE-ESL-BWRY`, category **Integration**
3. Add it, then download **Zhsunyco ESL**
4. Restart Home Assistant
5. **Settings → Devices & services → Add integration → Zhsunyco ESL**

Updates then arrive through HACS as usual. To publish a release, push a git
tag matching the `version` in `manifest.json`:

```bash
git tag v0.23.0 && git push origin v0.23.0
```

### Manual installation

Copy `custom_components/esl_zhsunyco/` into `<config>/custom_components/`
and restart Home Assistant.

### Upgrading from the pre-HACS prototype

A config entry created by the earlier prototype (titled like
`ESL 66:66:17:40:27:77 (BLE-35BWRY)`) is **migrated automatically** on the
first start. The address, the model and the old `battery_scan_interval` in
`HH:MM:SS` form are carried over (`12:00:00` becomes 720 minutes). There is
no need to delete and re-add the device.

## Setup

Labels that broadcast a matching manufacturer advertisement are **discovered
automatically**. You can also add one by hand — per section 1.1 of the
document the address always starts with the fixed bytes `66:66`, for example
`66:66:54:20:00:55`.

The **scan interval** only controls the connection-based poll for status and
version. Battery and version numbers arrive passively in the advertisement,
so a long interval is fine. `0` disables the poll entirely.

## Entities

| Entity | Type | Note |
|---|---|---|
| Battery voltage | Sensor | from the advertisement, no connection needed |
| Status | Sensor | BUSY/ERR per section VI, with readable error codes |
| Panel | Image | what was last put on the screen |
| Signal strength, display version, product ID | Sensor | diagnostic, disabled by default |
| RGB LED | Light | colour and blink pattern |
| RGB on/off time, RGB duration | Number | blink parameters for the LED |
| Clear screen, test pattern, diagnostic probe | Button | one press each |

## Services

```yaml
# Blink the LED red
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
| `send_test_pattern` | Send one of the built-in patterns, no file needed |
| `clear_screen` | Clear the panel |
| `set_rgb` | Drive the RGB LED |
| `debug_probe` | Connect and dump the whole GATT table |
| `debug_command` | Write raw bytes to the command characteristic |

## Sending images

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

A URL needs no allowlist — Home Assistant does not gate outgoing URLs the
way it gates file paths, and neither do its own image entities:

```yaml
action: esl_zhsunyco.set_image
data:
  device_id: <your label>
  url: https://example.com/calendar.png
```

Only `http` and `https` are accepted, the download times out after 30
seconds, and anything past 8 MB is refused rather than read into memory.

You do not have to worry about size or colours. The picture is fitted to the
panel, dithered onto the four colours it can display and packed at 2 bits
per pixel. A full screen is 17664 bytes, which is 99 chunks — expect roughly
half a minute once the connection is up.

For graphics with large flat areas — text, tables, a calendar — `dither:
false` usually looks cleaner than dithering, which speckles solid colour.

The pixel format itself is not adjustable: it is measured, not chosen, and
the panel model decides it.

### Did the send work?

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
    encoding: bwry_packed
    at: "2026-09-06T19:12:04.881+00:00"
```

**`label_reacted` is the check that matters.** `ok: true` only says the
bytes went out.

The response is optional, so automations written without
`response_variable` keep working unchanged.

#### An automation that retries

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

Be generous with the delay. The integration already waits up to 300 s for an
advertising window, and a quick second attempt only competes with the first
one for the single connection slot.

#### Without a response variable

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

The last command is also recorded in full, with status bytes and an
interpretation, under `last_command` in the diagnostics download.

### What the panel is showing

Every label has an `image` entity holding the last picture that was
uploaded. It is built **from the packed pixels**, not from the source file,
so dithering and colour reduction show up in it exactly as they do on the
panel.

```yaml
type: picture-entity
entity: image.esl_66_66_17_40_27_77_panel
```

It **survives a restart**. An e-ink panel holds its image without power, and
as long as nothing else writes to the label it is still showing exactly
that. The picture is kept per label as a base64 PNG in `.storage`, which is
about 1 kB for flat graphics and text and around 14 kB for a fully dithered
photograph.

It is updated **only after a successful transfer**:

- If the send fails, the previous preview stays — which is also what the
  panel is still showing.
- **Clearing the screen discards it.** The panel is no longer showing the
  image, and restoring it after a restart would be a picture of a blank
  screen. What a cleared panel actually displays is not documented, so this
  shows nothing rather than a guessed blank.
- Removing the label from Home Assistant deletes the stored copy.

It also stays **available** while the label sleeps: what is on the panel
does not stop being true because nobody can see it right now.

The assumption underneath all of this is that **only** this integration
writes images to the label. If something else does, the preview will be
showing a stale picture.

## Test patterns

```yaml
action: esl_zhsunyco.send_test_pattern
data:
  device_id: <your label>
  pattern: diagnostic
```

The `diagnostic` pattern is built to be read off a photograph. It shows a
closed frame, the four colour blocks, two one-pixel gratings and the panel
size as text, and each of those fails in a distinctive way:

| What you see | What it rules out |
|---|---|
| Frame closed all the way round | Width and height not swapped, whole area addressed |
| Blocks in the order black, red, yellow, white | The palette mapping is right |
| **Horizontal** one-pixel grating sharp, no smearing | The row length of 46 bytes is right |
| **Vertical** one-pixel grating sharp, no offset | The MSB-first bit order is right |

A single pixel-level packing bug is visible in those gratings and nowhere
else, which is how the packing was confirmed independently of the reference
script.

## Related but incompatible firmware

Two **completely different BLE protocols** are sold under the name
"Zhsunyco". This integration speaks only the first:

| | WOLINK (this integration) | easyTag |
|---|---|---|
| Service / characteristics | `…-4C53-4545-4C42-4B4E494C4F57` | `00001523/1525/1526-1212-efde-…` |
| Authentication | AES-128-ECB challenge/response | XOR key derived from the MAC |
| Commands | `0xA500`–`0xA509` | 20 byte header + 204 byte packets, CRC-16/ARC |
| Identifier | none | ASCII `easyTag` / `eTag-CO` |
| Feedback | none | notify characteristic |

There is a separate implementation for the easyTag variant:
[roxburghm/zhsunyco-esl](https://github.com/roxburghm/zhsunyco-esl).

The **diagnostic probe recognises both** and reports which one the label
actually speaks under `protocol_family`. If that says `easytag_xor`, this
integration is the wrong software for the device.

## What the document gets wrong

The full measurement record is in
[`docs/hardware-verified-findings.md`](docs/hardware-verified-findings.md).
The two that matter most:

**Commands go out little endian.** The document writes them as `0xA500`,
`0xA504` and so on without saying how the two bytes reach the wire. The low
byte goes first: `0xA500` is `00 a5`. Measured for `0xA500`, `0xA501`,
`0xA504` and `0xA508`.

**The advertisement mixes byte orders.** One probe captured all three
sources at once:

```
Advertisement            : 30 00 00 0e 03 30 02 01 0b 99
Version characteristic   : 30 00 00 0e 03 30 02 01
Battery characteristic   : 99 0b   -> little endian = 2969 mV
```

The first eight advertisement bytes are byte-identical to the version
characteristic, so the version fields decode the same way in both (little
endian). The last two are the byte-reversal of the battery characteristic,
so that one field alone is big endian. Reading the advertisement uniformly
is wrong in either direction.

### GATT table

All five characteristics live under the single service
`30323032-4C53-4545-4C42-4B4E494C4F57`:

| Characteristic | UUID prefix | Handle | Properties |
|---|---|---|---|
| Battery | `35323032` | 14 | notify, read |
| Status | `34323032` | 17 | notify, read |
| Security | `33323032` | 20 | read, **write** |
| Command | `31323032` | 23 | read, **write** |
| Version | `32323032` | 26 | notify, read |

They are characteristics of one service, not five separate services.
Resolving them with `get_service()` returns `None`, which is a plausible
explanation for "characteristic not found" reports elsewhere.

The command characteristic offers only **write with response** — there is no
write-without-response, so there was never a choice to configure.

All three readable characteristics can also **notify**, which the document
does not mention. Unused so far, but the obvious channel for feedback after
an upload.

MTU is **247 bytes**, and writes still go out in **180 byte slices**: that
is the size that carried three full images without a single failed write.
Larger frames have never been tried on this hardware, and a bulk upload is
the wrong place to find out. If the client reports no MTU at all, the same
size is assumed — the old fallback of 23, the ATT minimum, left 14 usable
bytes and turned one image into about 1262 writes.

### The status byte carries an undocumented lock indicator

The document defines byte 0 of the status characteristic as BUSY, "1: busy,
0: no busy", so only bit 0 is that flag. Bits 1 and 2 are not in the
document at all:

- `0x00` → the unlock was accepted
- `0x06` → the label is still locked

The integration checks this right after every connect and warns in the log,
rather than letting a rejected unlock surface minutes later as a missing
characteristic. It appears as `unlock_verified` in the diagnostics.

## Remaining unknowns

These are not specified in the vendor document and are implemented as
reasoned assumptions:

- **The pixel format for panels other than BWRY.** For BWRY it is no longer
  an assumption: 2 bits per pixel, MSB first, row-major, palette order
  black, white, yellow, red — all measured. The 1 bit packing for mono
  panels is untested; `MONO_BLACK_BIT` in `imaging.py` adjusts it.
- **Block compression** (sec. 3.3) is not described anywhere, so `0xA502`
  is not used. Everything is sent uncompressed through `0xA501`.
- **The meaning of the version fields.** `03 30` could be read as `3.48`,
  `48.3` or `3.30`. The raw bytes are in the diagnostics as `version_bytes`.
- **Panel resolutions** in `const.py` for models other than the BLE-35BWRY
  do not come from the document and may be wrong.

## Troubleshooting

### Start with the diagnostic probe

The fastest way to narrow anything down: press the **Diagnostic probe**
button on the device, or call `esl_zhsunyco.debug_probe`. It connects,
lists the whole GATT table with every characteristic and its properties,
attempts the unlock and then reads version, battery and status raw. The
result arrives as a notification and in the log.

The button works even when every other entity is unavailable — it does not
fail, it reports why under `connection_error`.

Two fields say the most:

- `connection` — `ok` means the connection and GATT access work.
- `reads.status.error_meaning` — `unlock_failed` means the challenge/response
  was rejected; anything else means the unlock worked.

**Settings → Devices & services → Zhsunyco ESL → Download diagnostics**
additionally gives you a JSON file with the raw advertisement and its
field-by-field decoding. The download does **not** open a connection; use
the probe button for that.

### The first command takes minutes

An ESL sleeps between advertisements, and gaps of several minutes are
normal. A Bluetooth proxy can only open a connection to a device it can
**currently** see, so outside that window every attempt fails no matter how
often it is retried:

```
BleakOutOfConnectionSlotsError: ... no scanner currently has it in its
discovered devices ... last advertisement 262s ago
```

So the integration waits for the next advertisement — up to 300 s — and
connects inside that window. The first button press can take noticeably
long. That is normal, not a fault.

300 s is measured, not guessed: with a continuous active scan at 20 cm and
−53 dBm, single windows of 10–30 s regularly missed the label entirely, and
one run stayed empty for over 120 s straight after a transfer.

Afterwards the connection is held open for **15 seconds** (adjustable in the
options, `0` disconnects immediately). Commands inside that window take
effect immediately because nothing has to be waited for.

It is deliberately short, because a **connected** BLE device stops
advertising altogether: every second on an open link is a second in which
the label is invisible to everything else, including Home Assistant's own
scanner.

For comparison, the reference implementation
[roxburghm/zhsunyco-esl](https://github.com/roxburghm/zhsunyco-esl) waits
the same way with `BleakScanner.find_device_by_address(..., timeout=120.0)`.
The waiting is a property of this class of device, not a quirk of this
integration.

If that does not help, the proxy is too far away or has no free connection
slots. Another
[ESPHome Bluetooth proxy](https://esphome.github.io/bluetooth-proxies/)
near the label is the fix.

### Two ESL integrations at once

A BLE label accepts only **one** connection at a time. If a second
integration (`esl_tag`, say) talks to the same label, the two take turns and
commands go missing. If both are installed, disable the other one for this
label as a test.

### All entities unavailable, no advertisements

Affects **0.9.0** only. A diagnostics download used to start a probe
automatically; if that timed out, an internal lock stayed held and an open
BLE connection stayed open. A connected BLE device sends no advertisements,
so the integration went blind and every entity dropped out.

Fixed in 0.9.1. If it still happens: **reload the integration**
(Settings → Devices & services → ⋮ → Reload).

The diagnostics section named `bluetooth` separates the two very different
cases:

| Field | Meaning |
|---|---|
| `last_service_info_any: null` | Home Assistant cannot see the label **at all** — silent, out of range, or still connected |
| `last_service_info_any` set but entities empty | HA sees it, our callback is not firing |
| `scanners_seeing_this_label: []` | no adapter or proxy is receiving it |
| `learned_advertising_interval_s` | how often HA sees the label transmit |

### The LED or clear screen does nothing

Both are verified on a BLE-35BWRY and work since the opcodes were switched
to little endian in 0.19.0. If they do nothing for you:

1. **Check the version.** Before 0.19.0 the integration sent `a5 04` instead
   of `04 a5`. The label accepts that write and does nothing.
2. **Check `unlock_verified` in the diagnostics.** If it is `false` the
   label is locked and ignores every command by definition.
3. **Read `last_command` in the diagnostics.** `label_reacted: false` means
   the panel never went busy after the command.

On a **different model** the byte order has not been measured. Try both with
`esl_zhsunyco.debug_command`, one payload at a time, on separate
connections — a rejected command revokes authorisation, so anything sent
after it on the same link proves nothing.

### Enable debug logging

```yaml
logger:
  default: warning
  logs:
    custom_components.esl_zhsunyco: debug
```

**Status reports `unlock_failed` (error code 5, section VI):** the label
rejected the challenge/response. Check whether the firmware uses the same
AES key.

**"not in range of any Bluetooth adapter or proxy":** Home Assistant cannot
see the label right now. ESPHome proxies need `bluetooth_proxy: active:
true`, otherwise only passive advertisements are possible.

## Development

```bash
pip install -r requirements-test.txt ruff

ruff check custom_components tests scripts
ruff format --check custom_components

python scripts/check_services.py   # the rules hassfest applies, run locally
python tests/test_protocol.py      # protocol, no Home Assistant needed
pytest tests/integration -q        # against a real Home Assistant
```

There are two levels of test:

- **`tests/test_protocol.py`**, `test_imaging.py` and `test_diagnostics.py`
  check the generated wire bytes against the document and against what was
  measured — the unlock challenge/response, the 13 byte RGB layout, chunk
  offsets in an upload, the pixel packing and advertisement parsing. They
  run without a Home Assistant installation, because `protocol.py` and
  `imaging.py` deliberately contain no HA imports; the same files can be
  driven against real hardware from a plain script.
- **`tests/integration/`** starts a real Home Assistant instance and covers
  the config flow, options flow, entity registration, services, the image
  entity and advertisement handling.

`scripts/check_services.py` reimplements the rules hassfest applies to
`services.yaml` and the translations, so that class of failure is caught
before pushing rather than in CI.

## Licence

MIT — see [LICENSE](LICENSE).
