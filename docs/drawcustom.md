# Drawing a layout: the `drawcustom` payload

`esl_zhsunyco.drawcustom` takes a list of drawing elements, paints them on a
canvas the size of your panel, and sends the result the same way any other
image is sent. Nothing is stored on the label; it is an image like any
other, just one that was drawn from a description instead of loaded from a
file.

The element vocabulary is the one
[OpenEPaperLink](https://github.com/OpenEPaperLink/Home_Assistant_Integration)
uses, because that is what the [ESPHome
Designer](https://esphome.io/) exports when you pick **Home Assistant
Service Call (JSON)**. A design exported there can be pasted into an
automation here with the service name changed and nothing else.

Only the payload format is shared. No code is taken from that project.

## The call

```yaml
action: esl_zhsunyco.drawcustom
data:
  device_id: <your label>
  payload:
    - type: rectangle
      x_start: 0
      y_start: 0
      x_end: 100%
      y_end: 34
      fill: red
    - type: text
      value: Geburtstage
      x: 6
      y: 6
      size: 22
      color: white
    - type: multiline
      value: |-
        Anna   12.05.
        Bert   03.07.
      x: 6
      y: 44
      size: 18
```

| Field | Default | What it does |
|---|---|---|
| `device_id` | required | Which label to send to |
| `payload` | required | The elements. A list, an object with a `payload` key, or JSON text |
| `background` | `white` | Colour the canvas starts as |
| `rotate` | `0` | Turn the finished layout by 0, 90, 180 or 270 degrees, counter-clockwise |
| `dither` | `true` | Floyd-Steinberg dithering when reducing to the panel palette |

A rotated layout is drawn on a canvas with the panel's dimensions swapped,
so a `rotate: 90` design on a 184x384 panel is laid out 384 wide and 184
high, and percentages mean what they looked like while designing. Rotation
runs counter-clockwise; if a 90 lands upside down, use 270.

`background`, `rotate` and `dither` may also sit inside the payload object,
which is where an export puts them. A value given in the service call wins
over the one in the payload.

The service supports a response variable, exactly like `set_image`, so an
automation can tell whether the send worked. See **Did the send work?** in
the README.

### Pasting an export

The Designer has **two** exports that both look like "the JSON", and only
one of them is a drawing:

| Export | What it contains | Use it here? |
|---|---|---|
| Project file | `pages` with `widgets`, pins, deep sleep, glyphsets | No — that describes a device for a firmware build |
| Home Assistant Service Call (JSON) | a `payload` list of drawing elements | Yes |

Handing over the project file is refused with a message saying so.

To get the right one in
[ESPHomeDesigner](https://github.com/koosoli/ESPHomeDesigner): switch the
output mode to **OpenEpaperLink**, put anything in the tag entity field
(it only lands in `target`, which this integration ignores - it addresses
labels by `device_id`), and copy the JSON. The result looks roughly like
this:

```yaml
service: open_epaper_link.drawcustom
target:
  entity_id: open_epaper_link.000002ABCDEF
data:
  background: white
  rotate: 0
  payload:
    - type: text
      ...
```

Change the service to `esl_zhsunyco.drawcustom`, replace `target` with a
`device_id` in `data`, and the `data` block goes through unchanged.

There is also a shortcut that needs no editing at all: hand the copied
block over *as* the payload, and it is unwrapped on the way in. `target`
and the service name are ignored, and `background`, `rotate` and `dither`
are read out of it:

```yaml
action: esl_zhsunyco.drawcustom
data:
  device_id: <your label>
  payload: |
    {
      "service": "open_epaper_link.drawcustom",
      "target": {"entity_id": "open_epaper_link.000002ABCDEF"},
      "data": {"background": "white", "rotate": 0, "dither": 2,
               "payload": [{"type": "text", "value": "Test", "x": 40, "y": 40}]}
    }
```

That works whether you paste it as JSON text (as above) or as a YAML
block, which makes it a straight copy from the Designer into an
automation.

## Coordinates and colours

**Positions** are pixels, or a percentage of the panel written as a string:
`x_end: 100%` is the right hand edge whatever panel you have. Percentages
are the thing to use if you ever want the same layout on a second label of a
different size.

**A missing `y`** means "below whatever was drawn last", plus that element's
`y_padding` (default 10). That is what makes a stack of text lines work
without counting pixels.

**Colours** may be written as:

| | |
|---|---|
| `black` `white` `red` `yellow` | the four the panel really has |
| `b` `w` `r` `y` | the same, abbreviated |
| `accent` `a` | red |
| `half_black` `hb`, `half_red` `hr`, `half_yellow` `hy` | a mixed tone |
| `#rgb`, `#rrggbb` | anything else, snapped to the nearest of the four |

The half tones are drawn as the midpoint colour and become a dot pattern
when the image is dithered down to the panel palette, which is how a
halftone reaches an e-ink screen anyway. With `dither: false` they snap to
one flat colour instead.

Anything that is not one of the four printable colours is quantised, so a
photo or a `#336699` box will come out dithered, not rejected.

## Elements

Every element needs a `type`. Every element accepts `visible: false` to skip
it, which is useful when the value comes from a template.

### text

| Property | Default | |
|---|---|---|
| `value` | required | The string |
| `x`, `y` | `0`, auto | Position |
| `size` | `20` | Point size |
| `color` | `black` | |
| `anchor` | `lt` | Pillow anchor: `l`/`m`/`r` plus `t`/`m`/`s`/`b` |
| `max_width` | - | Wrap (or with `truncate`, cut) at this width |
| `truncate` | `false` | Cut instead of wrapping |
| `spacing` | `5` | Extra pixels between wrapped lines |
| `y_padding` | `10` | Gap above, when `y` is left out |
| `stroke_width`, `stroke_fill` | `0`, - | Outline around the glyphs |
| `parse_colors` | `false` | Read `[red]...[/red]` tags inside the value |
| `font` | built in | See **Fonts** below |

### multiline

Like `text`, and additionally:

| Property | Default | |
|---|---|---|
| `delimiter` | newline | What separates the lines |
| `offset_y` | line height | Distance from one line to the next |

### line

`x_start`, `y_start`, `x_end`, `y_end`, `fill` (`black`), `width` (`1`).

### rectangle

`x_start`, `y_start`, `x_end`, `y_end`, `fill` (none), `outline` (`black`),
`width` (`1`), `radius` (`0`), `corners` (`all`, or any of `topleft`,
`topright`, `bottomright`, `bottomleft`).

### rectangle_pattern

A grid of rectangles: `x_start`, `y_start`, `x_size`, `y_size`, `x_repeat`,
`y_repeat`, `x_offset`, `y_offset`, plus everything `rectangle` takes.

### polygon

`points` as a list of `[x, y]` pairs (at least three), `fill`, `outline`,
`width`.

### circle

`x`, `y`, `radius`, `fill`, `outline`, `width`.

### ellipse

`x_start`, `y_start`, `x_end`, `y_end`, `fill`, `outline`, `width`.

### arc

`x_start`, `y_start`, `x_end`, `y_end`, `start`, `end` (degrees, clockwise
from three o'clock), `fill`, `width`.

### progress_bar

`x_start`, `y_start`, `x_end`, `y_end`, `progress` (0-100), `direction`
(`right`, `left`, `up`, `down`), `background` (`white`), `fill` (`red`),
`outline` (`black`), `width` (`1`), `show_percentage` (`false`).

### icon, icon_sequence

`icon` draws one Material Design Icon; `icon_sequence` draws a row of them.

| Property | Default | |
|---|---|---|
| `value` / `icons` | required | `mdi:battery-50`, or the bare name. `icons` is a list |
| `x`, `y` | `0`, auto | Position |
| `size` | `20` | |
| `color` | `black` | An entry in `icons` may override it with its own `color` |
| `spacing` | `5` | Between icons in a sequence |
| `direction` | `right` | Which way a sequence runs |

The icon font is bundled with the integration, so drawing an icon never
touches the network. An unknown name is an error that names the icon.

### qrcode

`data`, `x`, `y`, `boxsize` (`2`, pixels per module), `border` (`1`,
modules), `color` (`black`), `bgcolor` (`white`).

Keep `boxsize` at 2 or more. One pixel per module is below what most phone
cameras will read off an e-ink panel.

### dlimg

Places a downloaded image: `url`, `x`, `y`, `xsize`, `ysize`, `rotate`.

The download happens before anything is drawn, and before the label is
contacted, so a dead URL fails fast. A URL starting with `/` is resolved
against Home Assistant's internal URL, so `/local/cake.png` works. The same
limits as `set_image` apply: http and https only, 30 second timeout, 8 MB.

### debug_grid

`spacing` (`50`), `color` (`red`). Draws a labelled grid so you can read
coordinates off a photo of the panel. Send it once when starting a layout.

## What is not supported

`plot` needs Home Assistant's recorder history, which this integration does
not read. Render the chart somewhere else and place it with `dlimg`.

Any other unknown `type` is an error naming the element and listing what is
supported. Nothing is skipped silently: a layout that half draws is worse
than one that refuses.

## Fonts

`text` and `multiline` use Pillow's built-in scalable font at whatever
`size` says. A `font` naming one of the fonts an OpenEPaperLink export
mentions (`ppb.ttf`, `rbm.ttf` and friends) is accepted and ignored — those
files are not shipped, and at these sizes the difference is not worth a
megabyte per face. Any other `font` is treated as a path and loaded if it
exists, so you can point at a `.ttf` in your config directory.

## Errors

A payload mistake — an unknown element, a missing property, a colour that
does not exist — is rejected before the integration goes anywhere near the
radio, and the message names the element by index:

```
element 3 (text): needs 'value'
```

Only after the payload is sound does the label get woken up.
