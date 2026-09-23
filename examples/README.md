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
the response the service returns. See [docs/services.md](../docs/services.md) for what else is in there.

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

## `family_calendar.yaml` — the one in use

Two calendars, `calendar.familie` and `calendar.abfallkalender`, on an
800 × 480 panel:

- **left** the date, large, and everything on today
- **right** the six days after it, two lines each, `+2` where more follow
- **bottom** the next five collections

### What to change

1. `device_id`, to your label.
2. The two `calendar.` entity ids, if yours are named differently — in
   the `calendar.get_events` steps **and** in the template, which reads
   them back by name.
3. The `tonnen` table in the template, if your council writes the bins
   differently. It maps a keyword in the calendar entry to a short name
   and an icon, which is there because a raw entry like
   `Restmuellbehaelter` is both too wide for a chip and missing its
   umlaut.

Run it as a **script**, not as a single action: the entries come from the
two steps before the drawing one. Starting only the `drawcustom` action
draws a line telling you so rather than failing.

### Two decisions worth knowing about

**The red chip is about tomorrow, not today.** By the morning of
collection day the bin is either out or it is too late, so a warning then
is noise. The collection that has to go out tonight is labelled `MORGEN`
and filled red; everything else on the screen is black, except Sunday.
On a day with nothing to put out there is no red at all, which is what
lets red mean act.

**All-day entries repeat.** A four day holiday appears on each of its
four days, because that is what you want to see when you glance at
Wednesday. It costs one of that day's two lines.

The waste calendar is read 60 days ahead. Five collections reach that far
when one bin is fortnightly and another monthly; a week would show two.

## `family_calendar_automation.yaml` — when to send it

Every fifteen minutes, and once when Home Assistant starts. That is the
whole automation.

It can be that blunt because the integration skips a transfer that would
change nothing — see **The same picture is not sent twice** in
[../docs/services.md](../docs/services.md). Drawing costs a tenth of a second; sending costs battery and
hides the label from Bluetooth, and only happens when the picture really
differs. A moved appointment, a new entry, the date rolling over: all of
them reach the panel within a quarter of an hour, and a quiet day sends
nothing at all.

Which is also why the screen carries no clock. A ticking minute would
differ from the last render every time, and every run would transfer.

## `calendar_modern_layout.json` — the layout on its own

The same screen as `family_calendar.yaml`, frozen with example entries:
no calendars needed, so it draws the same picture every time. Send it to
see the layout on the panel, or import it into the ESPHome Designer to
move things around.

Colour says one thing at a time. The accent under HEUTE is the only
yellow. Red marks Sunday, and the one collection that has to go out
**tonight** — nothing else. On a day with nothing to put out, the whole
strip is black, so red on this screen always means act.

The bottom strip holds the next five collections, which usually reach
weeks past the seven days above it: a fortnightly bin and a monthly one
take a month and a half to add up to five. Days inside the week that have
a collection also carry a small bin next to the date, which ties the two
halves together without repeating the text.

A day in the week column takes the one or two lines it needs and the next
follows, so quiet days leave no holes, and a day with more than fits ends
in a small `+2`.

Send it as it stands to see it on the panel:

```yaml
action: esl_zhsunyco.drawcustom
data:
  device_id: <your label>
  antialias: false
  dither: false
  payload: !include calendar_modern_layout.json
```

It holds example entries, so it draws the same picture every time. The
script that fills it from real calendars comes once the layout is settled.

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
keeps small text readable on a four colour panel — see **Crisp text** in [../docs/drawcustom.md](../docs/drawcustom.md).
