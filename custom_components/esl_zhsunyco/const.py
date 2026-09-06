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

# Keys used by the pre-HACS prototype, kept only for entry migration.
LEGACY_CONF_MAC: Final = "mac_address"
LEGACY_CONF_BATTERY_INTERVAL: Final = "battery_scan_interval"

DEFAULT_MODEL: Final = "BLE-35BWRY"
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
# Resolutions are NOT part of the vendor document; they come from the label
# hardware itself and may need adjusting for your unit.
# "format" selects the pixel packer in imaging.py and is a best guess from the
# model name, not something the vendor document states.
MODELS: Final[dict[str, dict[str, int | str]]] = {
    "BLE-35BWRY": {
        "width": 184,
        "height": 384,
        "format": "bwry",
        "desc": '3.5" portrait (BWRY)',
    },
    "ET0420": {
        "width": 400,
        "height": 300,
        "format": "mono",
        "desc": '4.2" landscape',
    },
    "ET0290": {
        "width": 296,
        "height": 128,
        "format": "mono",
        "desc": '2.9" landscape',
    },
}

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
