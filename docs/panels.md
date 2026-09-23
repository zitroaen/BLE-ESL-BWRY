# Panels: size, orientation, battery

Ten models are built in, and the label never says which one it is, so
the size is configured. This is what to set and what to do when the
picture comes out wrong.

## Sizes

These labels are sold in ten sizes and the label never reports which one it
is, so the size has to be configured. All ten are built in:

| Model | Screen | Vendor resolution | Colours |
|---|---|---|---|
| `BLE-154MBWRY` | 1.54" | 200 × 200 | four |
| `BLE-213BWRY` | 2.13" | 250 × 128 | four |
| `BLE-213MBW-L` | 2.13" | 250 × 128 | black and white |
| `BLE-266BWRY` | 2.66" | 296 × 152 | four |
| `BLE-290BWRY` | 2.9" | 296 × 128 | four |
| `BLE-350BWRY` | 3.5" | 384 × 184 | four |
| `BLE-370BWRY` | 3.7" | 416 × 240 | four |
| `BLE-420BWRY` | 4.2" | 400 × 300 | four |
| `BLE-583BWRY` | 5.83" | 648 × 480 | four |
| `BLE-750BWRY` | 7.5" | 800 × 480 | four |

If yours is not listed, or a preset turns out wrong, **panel width, height
and colour depth are options in their own right** — leave width and height
at `0` to take them from the model. All of it can be changed after setup,
so picking the wrong model does not mean deleting and re-adding the label.

## One caveat worth reading

The resolutions above are the vendor's. **The orientation the data is sent
in is not always the same as the way the resolution is printed**, and that
is not documented anywhere:

| Model | Vendor prints | Pixels per row on the wire | |
|---|---|---|---|
| 3.5" | 384 × 184 | **184** | measured here |
| 2.9" | 296 × 128 | **128** | reported |
| 7.5" | 800 × 480 | **800** | measured here |

So the two smaller panels send their *short* axis as a row and the large
one its long axis. For the models where nobody has checked, the preset
follows the nearest known case — transposed up to 3.5", as printed from
3.7" up. That is an interpolation between three data points, not a rule —
but its upper end is confirmed: the 7.5" panel draws correctly at 800 × 480
with no swap.

Getting it wrong shears the picture diagonally and does nothing worse.
**The fix is to swap width and height in the options.** Send the
`diagnostic` test pattern first and it shows up immediately.

## If the picture comes out wrong

| Symptom | Cause | Fix |
|---|---|---|
| Diagonal shearing, smeared gratings | Width is the wrong axis | Swap width and height |
| Only part of the panel is drawn | Wrong height | Correct the height |
| Speckled where it should be flat | Dithering | `dither: false` |
| Mirrored or rotated | The panel's scan origin | `mirror` / `rotate` on `set_image` |

The last row is worth expecting on the 2.9" panel: the implementation this
data partly comes from applies a mirror and a 90° rotation to it, which
suggests its origin differs from the 3.5" one. Nothing is applied
automatically here, because it has not been verified.

## Battery level

The label reports a **voltage**, not a percentage — the protocol has no
percentage in it anywhere. The `Battery` sensor derives one by interpolating
linearly between two configurable voltages, so it carries Home Assistant's
battery device class and works in a low-battery automation:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.esl_66_66_17_40_27_77_battery
    below: 20
```

The defaults are **3000 mV full** and **2200 mV empty**, which suit the 3 V
lithium coin cell these labels ship with. A measured unit read 2947 mV,
which comes out as 93 %.

Two things worth knowing before you trust the number:

- **It is an estimate, not a measurement.** If you know your cell, set the
  two voltages in the integration options. The `Battery voltage` sensor
  keeps showing what the label actually reports.
- **The reading will sit high for a long time and then fall quickly.** That
  is how lithium coin cells behave — nearly flat voltage for most of their
  life, then a cliff. A straight line between two voltages cannot represent
  that, and a curve invented without knowing the cell would just be a guess
  with more decimal places. Treat a falling reading as urgent.
