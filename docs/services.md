# Services: sending, checking, reacting

Every service that changes the panel, what it returns, and how an
automation uses that. For the drawing vocabulary itself see
[drawcustom.md](drawcustom.md).

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
seconds, and anything over 8 MB is refused. A source that turns out not to
be a usable image says so, naming the URL.

You do not have to prepare the picture. It is fitted to the panel, dithered
onto the four colours the hardware can display, packed and compressed for
upload.

**Compression is on by default and matters more than it sounds.** A full
3.5" screen is 17664 bytes raw; compressed, real images came out at 830 to
1100 bytes — around 5 %. Since the label has to be awake and connected for
the whole transfer, and a connected label is invisible to everything else,
a shorter upload is the single biggest improvement available here. It
turns roughly half a minute of transfer into a couple of seconds.

If a panel refuses compressed uploads, **Compress images** in the options
turns it off. Images that would grow under compression — a photo dithered
into noise, say — are sent uncompressed automatically.

For graphics with large flat areas — text, tables, a calendar — set
`dither: false`. Dithering speckles solid colour, which looks worse than it
sounds on a small panel.

Options: `rotate` (0/90/180/270), `mirror`, `invert`, `dither`.

## Test patterns

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

## The same picture is not sent twice

A transfer is the expensive half of all this: the label has to stay
connected for it, which makes it invisible to every Bluetooth scanner
meanwhile, and it ends in a full colour refresh. Arriving at the picture
that is already on the panel is pure cost.

So a send that would change nothing is skipped. Rendering still happens —
it takes about a tenth of a second — and the result comes back with
`sent: false`:

```yaml
results:
  - address: 66:66:...
    ok: true
    sent: false
    detail: the panel is already showing this image
```

That is what lets an automation run as often as it likes:

```yaml
triggers:
  - trigger: time_pattern
    minutes: "/15"
actions:
  - action: script.my_calendar
```

Two things follow from it:

- **Keep a clock off the panel.** A line that shows the time changes every
  minute, so every render differs and every run transfers. Put the
  freshness in Home Assistant instead — the `image` entity carries the
  time of the last actual send.
- **`force: true`** sends regardless, for when the panel was cleared from
  somewhere else or a battery came out. Clearing the screen through this
  integration already does it for you: the next send goes out, because a
  blank panel is a difference.

## Did the send work?

There are **two** different failures, and they feel different:

| Case | How you notice |
|---|---|
| The transfer failed (label asleep, connection dropped) | The service raises, the automation stops |
| The transfer was accepted and the panel did nothing | **No error at all** — only `label_reacted: false` |

The second is the treacherous one: Home Assistant reports success while the
label still shows the old picture. So `set_image`, `drawcustom`,
`send_test_pattern`, `clear_screen`, `set_rgb` and `debug_command` return a
result:

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
    bytes: 17664          # the image
    sent_bytes: 891       # what actually went over the air
    compressed: true
    at: "2026-09-06T19:12:04.881+00:00"
```

**`label_reacted` is the check that matters.** `ok: true` only says the
bytes went out.

The response is optional, so automations written without
`response_variable` keep working unchanged.

## An automation that retries

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

## Without a response variable

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
| `drawcustom` | Draw a layout from a list of elements |
| `send_test_pattern` | Send a built-in pattern |
| `clear_screen` | Clear the panel |
| `set_rgb` | Drive the RGB LED |
| `debug_probe` | Connect and dump the whole GATT table |
| `debug_command` | Write raw bytes to the command characteristic |

The last two are for diagnosing an unfamiliar label; see
[`protocol.md`](protocol.md) if you need them.
