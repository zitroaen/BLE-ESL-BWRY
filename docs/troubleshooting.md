# Troubleshooting

## Start with the diagnostic probe

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

## A command failed and the message is one word

`Timeout` or `Unknown error` on their own used to be all you got: Home
Assistant shows whatever the service raised, and several failures here
carry no text. Since 0.29.2 a failure names the label, the step and the
elapsed time, for example:

```
66:66:17:40:27:77: send_image (891 bytes) failed while writing the command
after 34s - TimeoutError: no detail
```

The step is the useful part:

| Step | What was happening |
|---|---|
| `connecting` | Waiting for the label to advertise, then opening the link |
| `reading the status` | Connected, reading the status byte before the command |
| `writing the command` | The command or the image chunks going out |
| `watching for the refresh` | Waiting for the panel to report busy and finish |

The same fields are in the diagnostics download under `last_command`, as
`phase` and `elapsed_s`.

**Settings → Devices & services → Zhsunyco ESL → Download diagnostics**
gives you a JSON file with everything the integration knows, including
`last_command` — what the last command did, and whether the panel reacted —
and `version_bytes`, the raw version field the label reports.
The download does not open a connection; use the probe button for that.

## Why the first command takes minutes

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

## It advertises, but nothing can connect to it

The device page shows a recent advertisement and every command still fails
with something like:

```
send_image (1042 bytes) failed while connecting after 252s -
BleakNotFoundError: Failed to connect after 7 attempt(s): Timeout waiting
for connect response
```

That is not a contradiction. **Advertisements carry considerably further
than a connection does** — a label can be perfectly audible and still be
unable to hold a link. Three causes, in the order worth trying:

1. **The proxy has no free connection slot.** An ESPHome proxy handles
   three connections at once by default. Restart the proxy; if it is busy
   with other devices, put a second one near the label.
2. **The label is stuck.** E-ink firmware can hang, particularly after an
   upload that was cut short. Take the battery out for a few seconds. This
   costs nothing and fixes it more often than it should.
3. **It is too far for a reliable link.** Look at the RSSI the error
   message quotes. Below roughly −80 dBm, advertisements still arrive and
   connections mostly do not.

The error message names how many connectable scanners exist and which one
heard the label last, which separates the first case from the third.

## Umlauts come out as empty boxes

Text is drawn in Roboto, which ships with the integration. If that file
is not there, drawing falls back to Pillow's own font, which covers ASCII
and nothing else — so `Müller` comes out as `M□ller`, silently, because a
missing glyph is not an error.

Check the log (Settings → System → Logs, filter `esl_zhsunyco`) for:

```
Roboto-Regular.ttf is missing, falling back to the built in font
```

Download diagnostics from the device page also lists every bundled file
under `assets` with its size, or `MISSING`.

The fix is to redownload the integration in HACS and restart Home
Assistant — an update that skipped the font files is the usual cause.

## An element ends up at the top of the panel

A bare `y` is a special case in YAML: `y: 198` can be read as `true: 198`,
which loses the position while `x` survives. The element then stacks under
whatever came before it, usually landing near the top edge.

The integration puts such a key back and logs a warning, but the way to
avoid it is to quote the key in any payload written as YAML:

```yaml
- type: text
  value: HEUTE
  x: 48
  "y": 198
```

## "Unknown device id"

`device_id` wants the device's registry id, a long hex string — not the
name the device page shows. The name is the natural thing to paste, so
the error names the right id when it recognises what you typed.

Two ways out:

```yaml
# Look it up once: Settings → Devices, open the label, and the id is the
# last part of the URL.
device_id: 9f2c1e7a4b6d8e0f1a2b3c4d5e6f7a8b

# Or let Home Assistant do it, using the name as it appears on that page.
device_id: "{{ device_id('ESL 66:66:00:00:00:00') }}"
```

The second survives a rename only if you update the name in it too, but
it saves the lookup and reads better in a script.

## Two ESL integrations at once

A label accepts only **one** connection at a time. If a second integration
talks to the same label, the two take turns and commands go missing. Disable
the other one for this label as a test.

## The LED or clear screen does nothing

1. **Check `unlock_verified` in the diagnostics.** If it is `false` the
   label rejected authentication and ignores every command by definition.
2. **Read `last_command` in the diagnostics.** `label_reacted: false` means
   the panel never became busy after the command, so it did not act on it.
3. **Check the model** in the integration options. On a model other than the
   BLE-35BWRY the command encoding has never been measured.

## All entities show as unavailable

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

## Enable debug logging

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
[`protocol.md`](protocol.md) for the other family.
