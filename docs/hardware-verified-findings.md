# Protocol findings verified on hardware (BLE-35BWRY)

This document records what was **measured on a real label**, not what the
vendor document implies. Several assumptions in the code are confirmed by
it and one central one was overturned.

Everything below was reproduced on **2026-09-06** on a physical BLE-35BWRY.

## Test setup

| | |
|---|---|
| Label | `66:66:17:40:27:77`, advertising name `WL17402777` |
| Host | Windows 11, Python 3.10.11, bleak 1.1.1 (WinRT backend) |
| Connection | **direct** BLE adapter, no ESPHome proxy, no Home Assistant |
| Distance | label next to the machine, RSSI −53 dBm |

The Home Assistant integration was disabled during the measurements so that
nothing competed for the single connection slot.

The reference script used is
[`hardware-verification/ble35bwry_reference.py`](hardware-verification/ble35bwry_reference.py).
It is deliberately minimal and free of Home Assistant dependencies so the
measurement stays reproducible.

---

## 1. GATT layout — confirmed

Full discovery dump:

```
Service 00001800-0000-1000-8000-00805f9b34fb   (Generic Access)
  Char 00002a00-…  props=['read', 'notify']
  Char 00002a01-…  props=['read']
  Char 00002a04-…  props=['read']
Service 00001801-0000-1000-8000-00805f9b34fb   (Generic Attribute)
  Char 00002a05-…  props=['indicate']
Service 30323032-4c53-4545-4c42-4b4e494c4f57   ← all payload lives here
  Char 35323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Battery
  Char 34323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Status
  Char 33323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'write']    Security
  Char 31323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'write']    Command
  Char 32323032-4c53-4545-4c42-4b4e494c4f57  props=['read', 'notify']   Version
```

**The five UUIDs in `const.py` are correct.** What matters is the structure:
these are **characteristics of a single service** `30323032-…`, not five
services of their own. Resolving them as service UUIDs (`get_service()`)
returns `None` — that was the original bug in the predecessor script and a
plausible explanation for earlier "characteristic not found" reports.

### Side finding: there is no write-without-response

The command characteristic reports `['read', 'write']` and nothing else — no
`write-without-response`. A write mode option offering it could never have
worked on this label, which is why that option no longer exists.

---

## 2. AES unlock — confirmed, the `encrypt` variant

The sequence from the document, implemented exactly and accepted:

1. **read** the 16 byte challenge from `33323032-…`
2. **encrypt** it with AES-128-ECB and the vendor key
3. **write** the result back to the same characteristic

Measured immediately after the unlock:

```
Unlocked.
Status: BUSY=0 ERR=0 (no error), battery: 2947 mV
```

`ERR=0` — not `5` (`unlock_failed`). **98 consecutive command writes** were
then accepted without the label dropping the connection.

**Conclusion:** encrypting the challenge is correct. The alternatives that
used to be kept around (`decrypt`, the reversed variants, a plain echo) are
not needed, and neither is a sweep to choose between them. Skipping the
challenge altogether — encrypting 16 zero bytes, as the pre-HACS prototype
did — is definitely wrong.

---

## 3. ⚠️ Command byte order — little endian, not big endian

**This is the central finding and it contradicted the code at the time.**

`const.py` defined the opcodes big endian:

```python
CMD_IMAGE_STORE: Final = b"\xa5\x00"
CMD_IMAGE_REFRESH_RAW: Final = b"\xa5\x01"
CMD_CLEAR: Final = b"\xa5\x04"
```

What actually worked on the device was **little endian**:

| Command | Bytes sent | Result |
|---|---|---|
| `0xA500` store image data | `00 a5` + pointer 4B LE + data | accepted, 98× |
| `0xA501` refresh uncompressed | `01 a5` + size 4B LE | the panel redraws |

Demonstrated by **three** independent, complete transfers, after each of
which the display visibly changed:

1. 8832 bytes (first attempt, still on a wrong 1 bpp assumption) → a
   checkerboard appeared over roughly half the area
2. 17664 bytes calibration image → four colour bars over the whole area
3. 17664 bytes test card → full area, correct

Log of the third transfer:

```
Connected.
Unlocked.
Status: BUSY=0 ERR=0 (no error), battery: 2947 mV
Sending image ...
  ... 3600/17664 bytes
  ... 7200/17664 bytes
  ... 10800/17664 bytes
  ... 14400/17664 bytes
  ... 17664/17664 bytes
Sent, the display is refreshing now.
```

### Why the earlier counter-claim was a measurement artefact

`protocol.py` had actively classified little endian as disproven:

```python
bytes((0x04, 0xA5)),  # 3.7, little endian opcode - known rejected
```

That verdict came out of the `clear_screen_candidates()` sweep, where
`a5 04` ran **first** and `04 a5` came later on the same connection. As the
integration's own documentation of `async_command_sweep` said, a rejected
command revokes authorisation (ATT error 0x08) and takes the connection with
it. An early candidate therefore poisons every one after it — `04 a5` was
never tested on a clean link.

### What was explicitly **not** measured here

`a5 04` versus `04 a5` for **clear screen**. Little endian was verified only
for `0xA500` and `0xA501` in this run. Section 10 closes that gap.

---

## 4. Image format and palette — confirmed and now demonstrated

`_pack_bwry()` in `imaging.py` with MSB-first ordering is **exactly right**:

- **2 bits per pixel**, 4 pixels per byte, **MSB first**, row-major
- 184 × 384 → 46 bytes per row → **17664 bytes** for a full screen

The palette order is right too. Measured with a calibration image of four
equally tall bands, each filled with one constant 2 bit code:

| Band (top → bottom) | Code | Byte value | Colour displayed |
|---|---|---|---|
| 1 | `00` | `0x00` | **black** |
| 2 | `01` | `0x55` | **white** |
| 3 | `10` | `0xAA` | **yellow** |
| 4 | `11` | `0xFF` | **red** |

That matches `BWRY_PALETTE = [black, white, yellow, red]` index for index.

Cross-check: the first attempt at 1 bpp produced runs of 20 identical bits.
Read pairwise as 2 bit codes that alternates `00` and `11` — and the display
showed exactly black and red, over only half the area, because 8832 bytes is
precisely half of 17664.

Packed 2 bpp is therefore the format for BWRY panels; separate bit planes
and LSB-first ordering are ruled out for this model.

---

## 5. Advertisement — confirmed

Captured live:

```
66:66:17:40:27:77  name=WL17402777  rssi=-53
mfg={48042: b'0\x00\x00\x0e\x030\x02\x01\x0b\x8b'}
```

- Company ID **48042 = 0xBBAA** — the matcher in `manifest.json` is correct
- Payload is **10 bytes**: `30 00 00 0e 03 30 02 01 0b 8b`, matching
  `ADV_PAYLOAD_LEN`
- Battery big endian at offset 8: `0x0b8b` = **2955 mV**

Cross-check over GATT in the same session: **2947 mV**. The mixed byte order
that `parse_advertisement()` implements — version fields little endian,
battery big endian — is thereby confirmed.

---

## 6. Advertising behaviour — the real connection hurdle

The label advertises **rarely and irregularly**. Measured with a continuous
active scan:

| Run | Scan window | Result |
|---|---|---|
| A | 10 s | not found |
| B | 12 s | not found |
| C | 20 s, targeted | not found |
| D | 45 s (callback) | **found** |
| E | 3 × 30 s | found only on the third try |
| F | 4 × 30 s (after a transfer) | **not found at all** (>120 s) |
| G | 3 × 45 s | found only on the third try |

A single scan window of 10–30 s therefore **regularly misses** the label,
even at 20 cm and −53 dBm.

Connecting is only possible in the short window around an advertisement. A
**connected** BLE device also stops advertising entirely — as long as
anything holds the connection, the label is invisible to everything else.

---

## 7. Why Home Assistant was not connecting

In order of importance:

### 7.1 Competition for the single connection slot

A linger of 60 s held the connection open after every command and reused it.
That is sensible for bursts of commands, but it makes the label invisible
for that time. Both were observed: the label was unfindable while something
else was connected, and it was gone for minutes right after our transfers
(run F above).

**For diagnostics: only ever one client at a time.**

### 7.2 A 180 s advertisement wait was marginal

Run F above stayed empty for over 120 s, and runs E and G took roughly
90–135 s each. 180 s sat right on top of what was observed, so a sleeping
label regularly ran the wait into an error.

### 7.3 A wrong opcode looks like a connection failure

Because a rejection revokes authorisation and takes the connection with it,
section 3 does not surface as a clean protocol error but as an abort, or as
"characteristic not found" on the next access. The earlier README
observation — "test image, ~75 chunks, then characteristic not found" — fits
that pattern.

### 7.4 Chunk size from the fallback MTU

`_chunk_size()` in `protocol.py` used to assume an MTU of 23 when none was
known, giving `23 − 3 − 6` = **14 payload bytes per write**. A full screen
then needs ~1262 writes, which over a proxy takes minutes and is
correspondingly fragile.

For comparison: the verified runs used **180 payload bytes per write**
(186 byte frames) with `response=True` and went through without a single
failed attempt.

---

## 8. Recommended changes

Deliberately **not** implemented in the pull request that added this
document — it only documented.

| # | File | Change | Evidence |
|---|---|---|---|
| 1 | `const.py` | Opcodes to little endian (`b"\x00\xa5"`, `b"\x01\xa5"`, …) | sec. 3 |
| 2 | `protocol.py` | Drop the `known rejected` comment, reverse the sweep order | sec. 3 |
| 3 | `protocol.py` | Replace the `_FALLBACK_MTU` derivation, larger chunks | sec. 7.4 |
| 4 | `imaging.py` | Packed 2 bpp + MSB as the fixed default, palette marked verified | sec. 4 |
| 5 | `const.py` | Remove the write-without-response mode or mark it unsupported | sec. 1 |
| 6 | `device.py` | Raise the advertisement wait, lower the linger | sec. 6, 7.1, 7.2 |
| 7 | `protocol.py` | Reduce the unlock to `encrypt`, drop the sweep | sec. 2 |

**Order:** #1 first — without the right byte order nothing else can be
tested meaningfully — then #3, then the rest.

> **Addendum:** all seven were implemented in **0.19.0**. The command sweep
> (#2) was kept at the time because clear screen had not been measured in
> either byte order; section 10 closed that, and the sweep was removed in
> **0.23.0** together with the rest of the investigation scaffolding.

## 9. Open points

*State as of the original measurement run. What section 10 closed is marked
here.*

- ~~**Clear screen** (`0xA504`) was not tested in either byte order.~~
  Closed in section 10.
- **`0xA502`** (block compressed) and the compression itself are untouched —
  the vendor document does not describe the scheme.
- **Multi-screen** (`0xA503` / `0xA509`, slot header `PIC0x\0`) untested.
- ~~**RGB LED** (`0xA508`) untested.~~ Closed in section 10.
- ~~All measurements come from **one** unit over a **direct** adapter.~~
  Section 10 covers the proxy path.

---

## 10. Addendum: the same state through Home Assistant

Second measurement run, **2026-09-06**, after the changes in 0.19.0. A
different setup, and therefore independent evidence:

| | |
|---|---|
| Path | Home Assistant → **ESPHome Bluetooth proxy** → label |
| Software | this integration, v0.19.0, no helper script |
| Evidence | photograph of the panel, plus reports on the LED and clear |

### What this establishes

**The image path works through the integration end to end.** The panel
showed the `diagnostic` pattern from `patterns.py`, rendered by
`imaging.py`: frame, the four colour blocks, two one-pixel gratings and the
label `ESL 184x384`. The pattern is deliberately built to be diagnostic, so
several things can be read straight off it:

| Observed in the photograph | What it rules out |
|---|---|
| Frame closed all the way round | Width/height not swapped, whole area addressed |
| Blocks in the order black, red, yellow, white | The palette mapping is right (cross-check on sec. 4) |
| **Horizontal** one-pixel grating sharp, no smearing | The row length of 46 bytes is right |
| **Vertical** one-pixel grating sharp, no offset | The MSB-first bit order is right |

A single pixel error would be visible in exactly those gratings and nowhere
else. Both are clean, which confirms `_pack_bwry()` independently of the
reference script in section 4.

### `0xA504` and `0xA508`

Both take effect, in little endian:

- **Clear screen** `04 a5` — the last open question about byte order. The
  pattern in section 3 therefore holds for all four measured opcodes.
- **RGB LED** `08 a5` + R + G + B + on_ms 2B + off_ms 2B + work_ms 4B, all
  timing fields little endian. The 13 byte layout had been an assumption
  until this point.

### And the proxy caveat is settled

Section 9 recorded that every measurement had gone over a direct adapter and
that a proxy might differ, especially on MTU and timing. It does not: the
same 180 byte slices, the same frames, the same result.
