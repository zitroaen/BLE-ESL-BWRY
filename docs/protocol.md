# WOLINK BLE e-ink label protocol

A complete interface description for BLE electronic shelf labels running
Wolink / Zhsunyco firmware, such as the `BLE-35BWRY`.

This is a superset of the vendor document **"BLE Display API" rev 1.5**. It
fills in what that document leaves out — byte orders, the pixel format, the
locked-state indicator, chunk sizing, timing — and corrects it where the
hardware disagrees. Section numbers in the form *(sec. 3.1)* refer to the
vendor document.

Every statement here is marked with its provenance:

| Mark | Meaning |
|---|---|
| **[M]** | Measured on a physical BLE-35BWRY |
| **[D]** | From the vendor document, consistent with observation |
| **[D?]** | From the vendor document, never exercised |
| **[A]** | Assumption, stated as such |

Measurements are reproduced in
[`hardware-verified-findings.md`](hardware-verified-findings.md), which is
the evidence behind the **[M]** marks. That document is the record of how
these facts were established; this one is the reference.

---

## 1. Identifying the device

### 1.1 Address **[D]**

The advertised address begins with the fixed bytes `66:66`, for example
`66:66:17:40:27:77`. The advertised name follows the pattern `WL` plus the
last four address bytes without separators — `WL17402777` for the address
above. **[M]**

### 1.2 Two incompatible firmware families **[M]**

Labels sold under the name "Zhsunyco" ship one of two entirely different
stacks. Check the GATT table before assuming anything else in this document
applies.

| | WOLINK (this document) | easyTag |
|---|---|---|
| Service | `30323032-4C53-4545-4C42-4B4E494C4F57` | `00001523-1212-efde-1523-785feabcd123` |
| Characteristics | five, `31…`–`35…` under that service | `…1525…` write, `…1526…` notify |
| Authentication | AES-128-ECB challenge/response | XOR key derived from the MAC |
| Commands | `0xA5xx`, 2 byte opcode | 20 byte header, 204 byte packets, CRC-16/ARC |
| Identifier | none | ASCII `easyTag` / `eTag-CO` |
| Feedback | none | notify characteristic |

The easyTag family is documented at
[roxburghm/zhsunyco-esl](https://github.com/roxburghm/zhsunyco-esl).

---

## 2. Advertising and connection behaviour

### 2.1 Manufacturer data (sec. 1.2)

Company identifier `0xBBAA`, followed by a 10 byte payload:

```
offset  0   1   2   3   4   5   6   7   8   9
       PID     AppVer  HwVer   DispVer BatVoltage_mv
```

**The byte order is mixed, and the document does not mention it.** **[M]**

| Field | Offset | Width | Byte order |
|---|---|---|---|
| PID | 0 | 2 | little endian |
| AppVer | 2 | 2 | little endian |
| HwVer | 4 | 2 | little endian |
| DispVer | 6 | 2 | little endian |
| BatVoltage_mv | 8 | 2 | **big endian** |

This is not a guess between two readings. One capture took all three sources
in the same session:

```
Advertisement            : 30 00 00 0e 03 30 02 01 0b 99
Version characteristic   : 30 00 00 0e 03 30 02 01
Battery characteristic   : 99 0b   -> little endian = 2969 mV
```

The first eight advertisement bytes are byte-identical to the version
characteristic, so the version fields decode identically in both. The last
two are the byte-reversal of the battery characteristic, so that field alone
is big endian. Decoding the payload uniformly is wrong in either direction.

Implementations that receive manufacturer data through a host stack usually
get the company identifier stripped and keyed separately, so the payload
starts at offset 0 above rather than at document byte 2.

### 2.2 Advertising interval **[M]**

The label advertises rarely and irregularly. With a continuous active scan
at 20 cm and −53 dBm, single windows of 10–30 s **regularly fail to see it
at all**, and one run after a transfer stayed empty for over 120 s. Budget
at least 300 s before declaring a label unreachable.

### 2.3 Connection window **[M]**

A connection can only be opened in the short window around an advertisement.
Outside it, a proxy or scanner-backed stack fails with "no scanner currently
has it in its discovered devices" no matter how often it retries. Retrying
blindly does not help; waiting for the next advertisement and connecting
inside that window does.

### 2.4 A connected label is invisible **[M]**

Like any BLE peripheral, the label stops advertising entirely while
connected. Holding the connection open therefore hides it from every other
scanner, including the one that would notice the next advertisement. Hold
the link only as long as a burst of commands needs it.

Only one client can be connected at a time.

---

## 3. GATT layout **[M]**

All payload characteristics live under a **single service**. They are not
services of their own; resolving them with a service lookup returns nothing.

Service `30323032-4C53-4545-4C42-4B4E494C4F57`

| Characteristic | UUID | Sec. | Properties |
|---|---|---|---|
| Command | `31323032-4C53-4545-4C42-4B4E494C4F57` | 3 | read, write |
| Version | `32323032-4C53-4545-4C42-4B4E494C4F57` | IV | read, notify |
| Security | `33323032-4C53-4545-4C42-4B4E494C4F57` | 2 | read, write |
| Status | `34323032-4C53-4545-4C42-4B4E494C4F57` | VI | read, notify |
| Battery | `35323032-4C53-4545-4C42-4B4E494C4F57` | V | read, notify |

The UUIDs spell `WOLINKBLEESL2021` backwards; only the leading ASCII digit
differs per characteristic.

Notes:

- **Write with response only.** Neither writable characteristic advertises
  write-without-response, so there is no choice to make. **[M]**
- All three readable characteristics also support **notify**, which the
  document does not mention. Unused by this implementation. **[M]**
- Negotiated MTU on the measured unit was **247 bytes**. **[M]**

---

## 4. Unlock (sec. 2) **[M]**

Until a connection is unlocked, the label accepts writes and does nothing
with them, and per the document *"writing other services will be
disconnected immediately"*.

```
1. read  16 bytes from the Security characteristic   -> challenge
2. token = AES-128-ECB-encrypt(challenge, KEY)
3. write token to the Security characteristic
```

Key:

```
9B 60 9F 28 BC 49 E2 57 29 BD 7B 8D F2 2B 44 20
```

The challenge is fresh for every connection. Encryption is the correct
direction; decryption, byte-reversed variants and echoing the challenge are
all rejected. **[M]**

### 4.1 Verifying the unlock without sending a command **[M]**

Byte 0 of the Status characteristic carries an **undocumented lock
indicator**. Reading it directly after the unlock write says whether it was
accepted, with no need to risk a command:

| Byte 0 | Meaning |
|---|---|
| `0x00` | unlocked |
| `0x06` | still locked (bits 1 and 2 set) |

This is not in the vendor document, which defines byte 0 only as BUSY,
"1: busy, 0: no busy" — so only bit 0 is that flag. Reading the whole byte
as a boolean misreports a locked label as busy.

Error code 5 (`unlock_failed`) in status byte 1 is the documented signal for
the same condition (sec. VI).

---

## 5. Read characteristics

### 5.1 Version (sec. IV) **[M]**

8 bytes, all fields **little endian**:

```
offset  0   1   2   3   4   5   6   7
       PID     AppVer  HwVer   DispVer
```

How to render a version word is not specified. `03 30` can be read as
`3.48`, `48.3` or `3.30`; keep the raw bytes available. **[A]**

### 5.2 Battery (sec. V) **[M]**

2 bytes, **little endian**, millivolts. Note the contrast with the
advertisement, where the same value is big endian (§2.1).

**There is no charge percentage anywhere in the protocol**, and no cell
chemistry or capacity is reported either. A percentage can only be derived
from the voltage, which requires knowing the cell. Measured units read
2947–2969 mV, consistent with a 3 V lithium coin cell. **[M]**

### 5.3 Status (sec. VI) **[M]**

The measured unit returns **32 bytes** where the document describes 2. The
remainder is zero and can be ignored.

```
byte 0: bit 0 = BUSY        [D]
        bits 1,2 = locked   [M], undocumented
byte 1: error code          [D]
```

Error codes (sec. VI) **[D]**:

| Code | Meaning |
|---|---|
| 0 | no error |
| 1 | EPD initialisation error |
| 2 | EPD write error |
| 3 | decompression error |
| 4 | OTA error |
| 5 | unlock failed |

BUSY goes high while the panel is physically refreshing, which takes
seconds. Polling it across a command is the only feedback the protocol
offers: a write that is accepted but never makes the panel go busy was not
acted on. **[M]**

---

## 6. Commands (sec. 3)

### 6.1 Opcode byte order — little endian **[M]**

The document writes commands as `0xA500`, `0xA504` and so on without saying
how the two bytes reach the wire. **The low byte goes first.** `0xA500` is
`00 a5` on the wire.

Verified for `0xA500`, `0xA501`, `0xA504` and `0xA508` — every opcode this
implementation uses. The remaining opcodes are assumed to follow the same
rule. **[A]**

Multi-byte parameters are **little endian** throughout. **[M]** for the
image pointer, the image size and the RGB timing fields.

### 6.2 Command table

| Opcode | Wire | Sec. | Payload after the opcode | Status |
|---|---|---|---|---|
| `0xA500` | `00 a5` | 3.1 | pointer 4B + data | **[M]** |
| `0xA501` | `01 a5` | 3.2 | picture size 4B | **[M]** |
| `0xA502` | `02 a5` | 3.3 | picture size 4B | **[D?]** |
| `0xA503` | `03 a5` | 3.9 | pointer 4B + data | **[D?]** |
| `0xA504` | `04 a5` | 3.7 | none | **[M]** |
| `0xA505` | `05 a5` | 3.5 | pointer 4B + data | **[D?]** |
| `0xA506` | `06 a5` | 3.6 | size 4B + CRC-16 2B | **[D?]** |
| `0xA507` | `07 a5` | 3.4 | none, then wait 1 s | **[D?]** |
| `0xA508` | `08 a5` | 3.8 | R + G + B + on 2B + off 2B + work 4B | **[M]** |
| `0xA509` | `09 a5` | 3.10 | index A 1B + index B 1B, signed | **[D?]** |

`0xA504` is named "Unbind Clear Screen" in the document. It clears the
panel. **[M]**

`0xA505`–`0xA507` are OTA firmware update and are deliberately not
implemented here.

### 6.3 RGB LED, `0xA508` (sec. 3.8) **[M]**

13 bytes total:

```
08 a5 | R 1B | G 1B | B 1B | on_ms 2B LE | off_ms 2B LE | work_ms 4B LE
```

`on_ms` and `off_ms` are the blink cycle, `work_ms` how long the whole
pattern runs. `work_ms = 0` turns the LED off.

### 6.4 Multi-screen, `0xA509` (sec. 3.10) **[D?]**

Two signed index bytes select what each plane shows:

| Value | Wire | Meaning |
|---|---|---|
| −2 | `0xFE` | clear the screen |
| −1 | `0xFF` | leave unchanged |
| 0…10 | `0x00`…`0x0A` | show the image stored in that slot |

Slots are filled with `0xA503`, whose payload is prefixed with a six byte
header the document writes as `PIC0x\0`. That form only fits a single digit;
two-digit formatting (`PIC00`…`PIC10`) is the reading that keeps the length
at six for every slot. **[A]** — none of this has been exercised.

### 6.5 Rejection behaviour **[M]**

A command the label refuses does not produce a protocol-level error. It
**revokes the authorisation granted by the unlock**. The next plain read on
the same connection then fails with ATT error `0x08`, insufficient
authorization, and the link goes down.

The practical consequences:

- Anything sent after a rejected command on the same connection proves
  nothing.
- A wrong opcode presents as a connection failure or as "characteristic not
  found" on the next access, not as a clear rejection.
- Probing an unknown opcode requires a **fresh connection per candidate**.

A command the label merely does not understand behaves differently: the
write is accepted, the link survives, and the panel never goes busy.
Distinguishing those two outcomes is what makes the status byte worth
polling.

---

## 7. Image format

### 7.1 The panel size is not on the wire **[M]**

Nothing in the protocol reports the panel's resolution. The version
characteristic carries a product ID, but no mapping from that to a
resolution is known, so an implementation has to be told the size.

The vendor's product sheet gives these, all IP65, 175 degree viewing angle:

| Model | Screen | Resolution | Colours |
|---|---|---|---|
| BLE-154MBWRY | 1.54" | 200 x 200 | BWRY |
| BLE-213BWRY | 2.13" | 250 x 128 | BWRY |
| BLE-213MBW-L | 2.13" | 250 x 128 | BW |
| BLE-266BWRY | 2.66" | 296 x 152 | BWRY |
| BLE-290BWRY | 2.9" | 296 x 128 | BWRY |
| BLE-350BWRY | 3.5" | 384 x 184 | BWRY |
| BLE-370BWRY | 3.7" | 416 x 240 | BWRY |
| BLE-420BWRY | 4.2" | 400 x 300 | BWRY |
| BLE-583BWRY | 5.83" | 648 x 480 | BWRY |
| BLE-750BWRY | 7.5" | 800 x 480 | BWRY |

**The row axis on the wire is not always the long one.** This matters more
than the resolution itself, because it decides the row stride:

| Model | Sheet | Pixels per row | Provenance |
|---|---|---|---|
| BLE-350BWRY | 384 x 184 | 184 | **[M]** |
| BLE-290BWRY | 296 x 128 | 128 | reported |
| BLE-750BWRY | 800 x 480 | 800 | reported |

The two smaller panels transmit their short axis as a row; the large one
transmits its long axis. Nothing is known about the others, and nothing in
the vendor document addresses it. The pixel count is unaffected either way,
so a wrong choice shears the image diagonally rather than truncating it.

The reported figures come from
[shorti1996/zhsunyco-esl-wolink](https://github.com/shorti1996/zhsunyco-esl-wolink),
which describes the 3.5" panel as 384 x 184 packed column-major - the same
byte sequence as 184 x 384 packed row-major, so it agrees with the
measurement here. It also applies a mirror and a 90 degree rotation to the
2.9" panel, which suggests that panel's scan origin differs; unverified.

### 7.2 Four colour panels (BWRY) **[M]**

- **2 bits per pixel**, 4 pixels per byte, **MSB first**, row-major
- Rows are padded to whole bytes
- 184 × 384 → 46 bytes per row → **17664 bytes** per full screen

Palette, by the 2 bit code:

| Code | Byte of 4 identical pixels | Colour |
|---|---|---|
| `00` | `0x00` | black |
| `01` | `0x55` | white |
| `10` | `0xAA` | yellow |
| `11` | `0xFF` | red |

Separate bit planes — high plane then low plane — are **not** the format
this panel uses, and LSB-first ordering is wrong. Both were ruled out by
measurement. **[M]**

### 7.3 One bit panels **[A]**

1 bit per pixel, 8 per byte, MSB first, rows padded to whole bytes. Which
bit value means black has not been verified on hardware; in this
implementation it is the `MONO_BLACK_BIT` constant in `imaging.py`.

### 7.4 Upload procedure (sec. 3.1–3.2) **[M]**

```
for each chunk:
    0xA500 | offset 4B LE | chunk bytes
then:
    0xA501 | total size 4B LE
```

The offset is the position of the chunk within the complete image, counted
from zero. Chunks are written in order.

**Chunk size.** 180 payload bytes per write carried three full images with
no failed write. That makes a 186 byte frame, within the 247 byte MTU. A
full screen is 99 writes. **[M]**

Do not derive the chunk size from the ATT minimum MTU of 23: that leaves 14
payload bytes and turns one image into roughly 1262 writes, which over a
proxy takes minutes and is correspondingly fragile.

**Timing.** These delays come from the sibling easyTag driver, since the
document gives none. They are sufficient, not proven minimal. **[A]**

| Delay | Value | Where |
|---|---|---|
| After the unlock, before the first chunk | 500 ms | once |
| Between chunks | 20 ms | every chunk |
| Additional | 3 ms | every 5th chunk |
| Before the refresh command | 500 ms | once |

**After the refresh** the panel goes busy for several seconds and routinely
drops the connection during the physical refresh. That is normal and not an
error. **[M]**

### 7.5 Compressed upload, `0xA502` (sec. 3.3) **[D?]**

The document names a block compression scheme but does not describe it, so
it cannot be implemented from the document alone. Uncompressed upload
through `0xA501` is unaffected.

---

## 8. A minimal working sequence

```
1.  scan until the label advertises          (up to 300 s, §2.2)
2.  connect inside that window               (§2.3)
3.  read 16 bytes from Security              -> challenge
4.  write AES-128-ECB(challenge, KEY)        (§4)
5.  read Status; byte 0 must be 0x00         (§4.1)
6.  sleep 500 ms
7.  for each 180 byte chunk:
        write 00 a5 | offset 4B LE | chunk   (§7.4)
        sleep 20 ms  (+3 ms every 5th)
8.  sleep 500 ms
9.  write 01 a5 | size 4B LE
10. poll Status; BUSY rises, then falls      (§5.3)
11. disconnect promptly                      (§2.4)
```

Steps 5 and 10 are what turn "the writes were accepted" into "the label
acted on them". Without them a silent failure is indistinguishable from
success.

---

## 9. What remains unknown

| Area | State |
|---|---|
| Block compression, `0xA502` | Scheme not described anywhere |
| Multi-screen, `0xA503` / `0xA509` | Implementable from the document, never exercised |
| OTA, `0xA505`–`0xA507` | Documented, deliberately not implemented |
| Version field rendering | `03 30` could be `3.48`, `48.3` or `3.30` |
| 1 bit pixel packing | Assumed, no mono panel measured |
| Panel resolutions | On the vendor sheet, but never reported by the label |
| Row axis per model | Known for three of ten; not derivable from the resolution |
| PID to model mapping | One PID known; not enough to map models |
| Notify on the readable characteristics | Present, purpose unknown, unused |
| Battery cell type and discharge curve | Not reported; voltage is all there is |
| Minimum viable upload timing | Working values known, lower bound not probed |
