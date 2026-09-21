# Examples

## `week_calendar.yaml` — a week at a glance

A script that draws a week calendar on an 800 × 480 panel and sends it.
Today gets a wide column with the full date above it and every entry
listed; the six days after it get a narrow column each, labelled with the
weekday only.

Four calendars, told apart by how an entry is marked:

| Calendar | Marking |
|---|---|
| Mein Kalender | plain black |
| Familienkalender | red bar down the left |
| Geburtstage | red text, and a cake in today's column |
| Müllkalender | black on a yellow chip |

The weekend headers are yellow, and they move: the columns start at today,
so Saturday and Sunday are wherever they fall this week.

### Run it as a script, not as one action

The entries come from the first step, which hands them on as
`response_variable: agenda`. Starting only the `drawcustom` action — the
obvious thing to try from Developer Tools — leaves that variable
undefined. Rather than failing with `UndefinedError: 'agenda' is
undefined`, the layout then draws a line saying so, so a half run looks
like a half run.

To run the whole thing: **Developer Tools → Actions → `script.turn_on`**
with this script as the target, or the Run button in the script editor.

### What to change

1. The four `calendar.` entity ids — in the `calendar.get_events` target
   **and** in the `kalender` mapping inside the template. Both lists have
   to match.
2. `device_id`, to your label.
3. Run it from an automation on a schedule. Once an hour is plenty; every
   send costs battery and hides the label from Bluetooth while it uploads.

The script reports a failed transfer as a persistent notification, using
the response the service returns. See **Did the send work?** in the README
for what else is in there.

### How it is put together

The payload is assembled from two halves:

- **`chrome`**, a plain YAML list in a `variables:` step: the frame, the
  rules, the column separators, the HEUTE bar, the legend. Nothing
  dynamic. This is the half you can round-trip through the ESPHome
  Designer.
- **the template**, which adds what depends on the day: the date band, the
  six weekday labels with the weekend highlight, and the entries.

Keeping them apart means redesigning the frame does not mean touching
Jinja, and changing how entries look does not mean re-exporting a layout.

## `week_calendar_layout.json` — the same screen, static

The whole thing as one payload with example entries, ready to import into
the ESPHome Designer or to send as-is:

```yaml
action: esl_zhsunyco.drawcustom
data:
  device_id: <your label>
  antialias: false
  dither: false
  payload: !include week_calendar_layout.json   # or paste the contents
```

Use it to move boxes around in the Designer, then copy the frame elements
back into the script's `chrome:` block.

Both files render at exactly 800 × 480 with whole-number coordinates and
`antialias: false`, so every glyph lands on the pixel grid. That is what
keeps small text readable on a four colour panel — see **Crisp text** in
[../docs/drawcustom.md](../docs/drawcustom.md).
