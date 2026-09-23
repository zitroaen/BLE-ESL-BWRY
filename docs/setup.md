# Installing and setting up

The short version is in the [README](../README.md); this is every
option and what it is for.

## Installing

1. In Home Assistant: **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/zitroaen/BLE-ESL-BWRY`, category **Integration**
3. Add it, then download **Zhsunyco ESL**
4. Restart Home Assistant
5. **Settings → Devices & services → Add integration → Zhsunyco ESL**

Updates then arrive through HACS as usual.

## Manually

Copy `custom_components/esl_zhsunyco/` into `<config>/custom_components/`
and restart Home Assistant.

## Upgrading from the pre-HACS prototype

A config entry created by the earlier prototype (titled like
`ESL 66:66:17:40:27:77 (BLE-35BWRY)`) is **migrated automatically** on the
first start. The address, the model and the old `battery_scan_interval` are
carried over. There is no need to delete and re-add the device.

## Adding a label

Labels are usually **discovered automatically**. To add one by hand you need
its address, which always starts with `66:66` — for example
`66:66:54:20:00:55`.

Two options are worth knowing about:

**Scan interval** only controls the connection-based poll for status and
version. Battery and version numbers arrive passively without a connection,
so a long interval is fine. `0` disables the poll entirely.

**Panel model** sets the size pictures are rendered at. If yours is not in
the list, or the list has it wrong, **Panel width / height / colour depth**
override it — see [panels.md](panels.md).

**Battery voltage when full / when empty** set the range the percentage is
interpolated between — see [panels.md](panels.md#battery-level).

**Linger** is how long the connection stays open after a command, 15 seconds
by default. Commands inside that window are instant. Longer is not better: a
connected label stops advertising, so while the link is held nothing can see
the label — not even Home Assistant.
