"""Constants for the Zhsunyco ESL integration.

All protocol values are taken from the vendor document "BLE Display API"
(rev 1.5). Section numbers below refer to that document.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "esl_zhsunyco"

CONF_ADDRESS: Final = "address"
CONF_MODEL: Final = "model"
CONF_SCAN_INTERVAL_MIN: Final = "scan_interval_min"
CONF_LINGER_S: Final = "linger_s"

# How long the connection is held open after a command. It has to be short:
# a connected BLE device stops advertising entirely, so every second spent
# lingering is a second in which nothing - not even Home Assistant's own
# scanner - can see the label. Long enough for a follow-up command in the
# same burst, short enough not to hide the label.
# See docs/hardware-verified-findings.md sections 6 and 7.1.
DEFAULT_LINGER_S: Final = 15

# The label reports millivolts and nothing else - the protocol has no
# percentage anywhere - so a charge level has to be derived from a voltage
# range. Both ends are options because they depend on the cell fitted.
#
# The defaults suit the 3 V lithium coin cell these labels ship with: a
# fresh one settles near 3.0 V under light load, and the BLE hardware stops
# working somewhere around 2.2 V. Measured units read 2947-2969 mV, which
# puts them near the top of that range.
#
# Any percentage from this is an estimate, not a measurement. Lithium coin
# cells hold a nearly flat voltage for most of their life and then fall off
# quickly, so expect the reading to sit high for a long time and then drop.
CONF_BATTERY_FULL_MV: Final = "battery_full_mv"
CONF_BATTERY_EMPTY_MV: Final = "battery_empty_mv"
DEFAULT_BATTERY_FULL_MV: Final = 3000
DEFAULT_BATTERY_EMPTY_MV: Final = 2200
BATTERY_MV_MIN: Final = 500
BATTERY_MV_MAX: Final = 6000

# Keys used by the pre-HACS prototype, kept only for entry migration.
LEGACY_CONF_MAC: Final = "mac_address"
LEGACY_CONF_BATTERY_INTERVAL: Final = "battery_scan_interval"

DEFAULT_MODEL: Final = "BLE-350BWRY"
DEFAULT_SCAN_INTERVAL_MIN: Final = 60

# --- GATT characteristics -------------------------------------------------
# The UUIDs spell "WOLINKBLEESL2021" backwards; only the first ASCII digit
# differs per characteristic.
UUID_COMMAND: Final = "31323032-4c53-4545-4c42-4b4e494c4f57"  # sec. 3
UUID_VERSION: Final = "32323032-4c53-4545-4c42-4b4e494c4f57"  # sec. IV
UUID_SECURITY: Final = "33323032-4c53-4545-4c42-4b4e494c4f57"  # sec. 2
UUID_STATUS: Final = "34323032-4c53-4545-4c42-4b4e494c4f57"  # sec. VI
UUID_BATTERY: Final = "35323032-4c53-4545-4c42-4b4e494c4f57"  # sec. V

# --- A second, unrelated protocol family ---------------------------------
# Labels sold under the same vendor name also ship an "easyTag" firmware with
# a completely different stack: Nordic style UUIDs, a XOR key derived from the
# MAC and 20/204 byte framed packets instead of AES and 0xA5xx commands.
# Documented at https://github.com/roxburghm/zhsunyco-esl. This integration
# does not speak it, but the probe detects it so the wrong driver is obvious.
EASYTAG_SERVICE: Final = "00001523-1212-efde-1523-785feabcd123"
EASYTAG_WRITE: Final = "00001525-1212-efde-1523-785feabcd123"
EASYTAG_NOTIFY: Final = "00001526-1212-efde-1523-785feabcd123"

PROTOCOL_WOLINK: Final = "wolink_aes"
PROTOCOL_EASYTAG: Final = "easytag_xor"
PROTOCOL_UNKNOWN: Final = "unknown"

# --- Security (sec. 2) ----------------------------------------------------
AES_KEY: Final = bytes(
    (
        0x9B,
        0x60,
        0x9F,
        0x28,
        0xBC,
        0x49,
        0xE2,
        0x57,
        0x29,
        0xBD,
        0x7B,
        0x8D,
        0xF2,
        0x2B,
        0x44,
        0x20,
    )
)
CHALLENGE_LEN: Final = 16

# --- Commands (sec. 3) ----------------------------------------------------
# The document writes these as "0xA500", "0xA504" and so on without saying
# how the two bytes reach the wire. They go out LITTLE ENDIAN: the low byte
# first, so 0xA500 is 00 a5. Measured on a physical BLE-35BWRY on 2026-09-06
# over a direct adapter - 0xA500 accepted 98 consecutive chunk writes and
# 0xA501 redrew the panel, three full transfers in a row. See
# docs/hardware-verified-findings.md section 3.
#
# Written as the wire bytes rather than as ints so the low byte cannot be
# swapped back by accident at a call site.
CMD_IMAGE_STORE: Final = b"\x00\xa5"  # 3.1  + data pointer 4B + data
CMD_IMAGE_REFRESH_RAW: Final = b"\x01\xa5"  # 3.2  + picture data size
CMD_IMAGE_REFRESH_COMP: Final = b"\x02\xa5"  # 3.3  + picture data size
CMD_CLEAR: Final = b"\x04\xa5"  # 3.7  no payload
CMD_OTA_SEND: Final = b"\x05\xa5"  # 3.5  + data pointer 4B + data
CMD_OTA_UPDATE: Final = b"\x06\xa5"  # 3.6  + size 4B + crc16 2B
CMD_OTA_ERASE: Final = b"\x07\xa5"  # 3.4  no payload, wait 1s
CMD_RGB: Final = b"\x08\xa5"  # 3.8  + r + g + b + on2 + off2 + work4

# --- Advertising (sec. 1.2) ----------------------------------------------
# Manufacturer specific data, bytes 0-1 are the company identifier 0xbbaa.
# Home Assistant strips the company id and keys manufacturer_data by it, so
# the payload we receive starts at document byte 2 (PID).
MANUFACTURER_ID: Final = 0xBBAA
# Byte order of the company id is not stated in the document; accept both.
MANUFACTURER_ID_ALT: Final = 0xAABB
ADV_PAYLOAD_LEN: Final = 10  # PID, AppVer, HwVer, DispVer, BatVoltage_mv

# Section 1.1: the advertised address is prefixed with two fixed bytes.
ADDRESS_PREFIX: Final = "66:66"

# --- Status error codes (sec. VI) ----------------------------------------
ERROR_CODES: Final[dict[int, str]] = {
    0: "no_error",
    1: "epd_init_error",
    2: "epd_write_error",
    3: "decompression_error",
    4: "ota_error",
    5: "unlock_failed",
}

# --- Panels ---------------------------------------------------------------
# The vendor's product sheet gives each model's PHYSICAL resolution. That is
# not the same as the geometry the upload needs, because the row axis on the
# wire is not always the long one:
#
#   model  physical   row axis on the wire
#   350    384 x 184  184  measured here
#   290    296 x 128  128  reported
#   750    800 x 480  800  reported
#
# So the two smaller panels pack along their SHORT axis and the large one
# along its long axis. "width" below is the row axis - the number that
# decides how many bytes a row takes - and "vendor" keeps the sheet's figure
# so the two can be compared.
#
# For the models with no evidence either way, width follows the nearest
# known case: transposed up to 3.5", the sheet's orientation from 3.7" up.
# That is an interpolation between three data points, not a rule anyone has
# established. Getting it wrong shears the picture diagonally and nothing
# worse; swapping width and height in the options is the fix, and the
# diagnostic test pattern makes it obvious in one send.
#
# Reported figures come from https://github.com/shorti1996/zhsunyco-esl-wolink
# and the vendor product sheet.
MODELS: Final[dict[str, dict[str, int | str]]] = {
    "BLE-154MBWRY": {
        "width": 200,
        "height": 200,
        "format": "bwry",
        "vendor": "200x200",
        "desc": '1.54" 200x200, 4 colour (square, so orientation cannot be wrong)',
    },
    "BLE-213BWRY": {
        "width": 128,
        "height": 250,
        "format": "bwry",
        "vendor": "250x128",
        "desc": '2.13" 250x128, 4 colour (orientation unverified)',
    },
    "BLE-213MBW-L": {
        "width": 128,
        "height": 250,
        "format": "mono",
        "vendor": "250x128",
        "desc": '2.13" 250x128, black and white (unverified)',
    },
    "BLE-266BWRY": {
        "width": 152,
        "height": 296,
        "format": "bwry",
        "vendor": "296x152",
        "desc": '2.66" 296x152, 4 colour (orientation unverified)',
    },
    "BLE-290BWRY": {
        "width": 128,
        "height": 296,
        "format": "bwry",
        "vendor": "296x128",
        "desc": '2.9" 296x128, 4 colour (orientation reported)',
    },
    "BLE-350BWRY": {
        "width": 184,
        "height": 384,
        "format": "bwry",
        "vendor": "384x184",
        "desc": '3.5" 384x184, 4 colour (verified on hardware)',
    },
    "BLE-370BWRY": {
        "width": 416,
        "height": 240,
        "format": "bwry",
        "vendor": "416x240",
        "desc": '3.7" 416x240, 4 colour (orientation unverified)',
    },
    "BLE-420BWRY": {
        "width": 400,
        "height": 300,
        "format": "bwry",
        "vendor": "400x300",
        "desc": '4.2" 400x300, 4 colour (orientation unverified)',
    },
    "BLE-583BWRY": {
        "width": 648,
        "height": 480,
        "format": "bwry",
        "vendor": "648x480",
        "desc": '5.83" 648x480, 4 colour (orientation unverified)',
    },
    "BLE-750BWRY": {
        "width": 800,
        "height": 480,
        "format": "bwry",
        "vendor": "800x480",
        "desc": '7.5" 800x480, 4 colour (orientation reported)',
    },
}

# The vendor calls the 3.5" panel BLE-350BWRY; earlier versions of this
# integration called it BLE-35BWRY, and the pre-HACS prototype offered two
# easyTag style names. Resolve them rather than breaking those entries.
MODEL_ALIASES: Final[dict[str, str]] = {
    "BLE-35BWRY": "BLE-350BWRY",
    "ET0290": "BLE-290BWRY",
    "ET0420": "BLE-420BWRY",
}

# Whether to send images through the block compression 0xA502 expects.
# On by default: a compressed full screen was 5-6% of the raw size on
# hardware, and the connection slot is the bottleneck for everything here,
# so a shorter transfer is the single biggest win available. An option
# because it is verified on one panel, and a model that rejects it should
# not need a new release to work.
CONF_COMPRESS: Final = "compress"
DEFAULT_COMPRESS: Final = True

# Pixel packers imaging.py can produce.
PIXEL_FORMATS: Final = ("bwry", "mono")

# Panel geometry overrides. A label whose model is not in the list above -
# and there are many - only needs its size to work, so it can be entered
# directly instead of waiting for a preset. 0 means "take it from the
# model".
CONF_WIDTH: Final = "width"
CONF_HEIGHT: Final = "height"
CONF_PIXEL_FORMAT: Final = "pixel_format"
PANEL_PX_MIN: Final = 0
PANEL_PX_MAX: Final = 2048

DEFAULT_PIXEL_FORMAT: Final = "mono"

# RGB timing bounds, used by both the service schema and the number entities.
RGB_ON_MS_MIN: Final = 0
RGB_ON_MS_MAX: Final = 65535
RGB_OFF_MS_MIN: Final = 0
RGB_OFF_MS_MAX: Final = 65535
RGB_WORK_MS_MIN: Final = 0
RGB_WORK_MS_MAX: Final = 86_400_000

DEFAULT_RGB_ON_MS: Final = 500
DEFAULT_RGB_OFF_MS: Final = 500
DEFAULT_RGB_WORK_MS: Final = 30_000

SERVICE_SET_RGB: Final = "set_rgb"
SERVICE_CLEAR_SCREEN: Final = "clear_screen"
SERVICE_SET_IMAGE: Final = "set_image"
SERVICE_DEBUG_PROBE: Final = "debug_probe"
SERVICE_DEBUG_COMMAND: Final = "debug_command"
SERVICE_SEND_TEST_PATTERN: Final = "send_test_pattern"
