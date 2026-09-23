# Zhsunyco ESL — BLE e-ink label for Home Assistant

[![Validate](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml/badge.svg)](https://github.com/zitroaen/BLE-ESL-BWRY/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Put pictures on a battery-powered BLE e-ink shelf label — a calendar, a
dashboard, a name plate — and drive its LED. Works with labels running
Wolink / Zhsunyco firmware in all ten sizes the vendor sells, 1.54" to
7.5", over a local adapter or an ESPHome Bluetooth proxy.

## Install

HACS → ⋮ → **Custom repositories** → `https://github.com/zitroaen/BLE-ESL-BWRY`,
category *Integration* → download → restart. The label is then usually
discovered on its own; otherwise add it by address, which always starts
with `66:66`.

Press the **Test pattern** button on the device page to check it. The
first command after a label has been idle takes minutes — that is the
label's advertising interval, not a fault.

## What it does

| | |
|---|---|
| Send a picture | from a file or a URL, any size, any colours |
| Draw a layout | text, shapes, icons and QR codes from an automation |
| Send a test pattern | built in, no image file needed |
| Clear the screen | one button or one service call |
| See what the panel shows | an `image` entity, kept across restarts |
| Drive the RGB LED | colour, blink rate, duration |
| Read battery and status | from the advertisement, without connecting |

Sends are compressed, typically to about 5 % — and a picture the panel is
already showing is not sent at all, so an automation can run as often as
it likes. Not supported: firmware updates, and storing several images in
the label.

## Services

| Service | |
|---|---|
| `set_image` | send a picture from a file or a URL |
| `drawcustom` | draw a layout from a list of elements |
| `send_test_pattern` | send a built-in pattern |
| `clear_screen` · `set_rgb` | clear the panel, drive the LED |
| `debug_probe` · `debug_command` | for an unfamiliar label |

All of them return a result, so an automation can tell an accepted
command from one the label ignored.

```yaml
action: esl_zhsunyco.drawcustom
data:
  device_id: <your label>
  antialias: false
  dither: false
  payload:
    - type: text
      value: Hello
      x: 10
      y: 10
      size: 40
      color: red
```

The element format is OpenEPaperLink's, which is what the ESPHome
Designer exports — a design pastes across unchanged.

## Entities

| Entity | Type | Note |
|---|---|---|
| Battery | Sensor | charge in percent, estimated from the voltage |
| Battery voltage | Sensor | the actual reading, no connection needed |
| Status | Sensor | busy, or a readable error code |
| Panel | Image | what was last put on the screen |
| RGB LED | Light | colour and blink pattern |
| RGB on/off time, RGB duration | Number | blink parameters |
| Clear screen, test pattern, diagnostic probe | Button | one press each |
| Signal strength, display version, product ID | Sensor | diagnostic, off by default |

## Documentation

| | |
|---|---|
| [docs/setup.md](docs/setup.md) | installing by hand, and every option explained |
| [docs/services.md](docs/services.md) | every service, what it returns, retrying |
| [docs/drawcustom.md](docs/drawcustom.md) | the drawing elements and their properties |
| [docs/panels.md](docs/panels.md) | sizes, the orientation caveat, battery calibration |
| [docs/troubleshooting.md](docs/troubleshooting.md) | when something does not work |
| [examples/](examples/) | a week calendar, complete, with the script behind it |
| [docs/protocol.md](docs/protocol.md) | the interface itself, with what is measured and what is guessed |
| [docs/hardware-verified-findings.md](docs/hardware-verified-findings.md) | the measurements behind it |

## Development

```bash
pip install -r requirements-test.txt ruff
ruff check custom_components tests scripts
ruff format --check custom_components
python scripts/check_services.py   # services.yaml + translations
python scripts/check_links.py      # every link between the docs
python tests/test_protocol.py      # protocol, no Home Assistant needed
pytest tests/integration -q        # against a real Home Assistant
```

`protocol.py` and `imaging.py` hold no Home Assistant imports, so the wire
bytes can be checked — and driven against real hardware — from a plain
script. A release is a git tag matching `version` in `manifest.json`.

## Licence

MIT — see [LICENSE](LICENSE). The bundled fonts are Apache 2.0; see
[the NOTICE](custom_components/esl_zhsunyco/assets/NOTICE) beside them.
