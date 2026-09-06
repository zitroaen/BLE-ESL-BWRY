"""Battery decoding and probe reporting."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import load_modules  # noqa: E402

const, protocol, imaging = load_modules()


def test_reported_29_2_volt_reconstruction():
    """Reproduce the field report: 29.2 V means the raw word is 0x7210."""
    raw_word = 29200
    assert raw_word == 0x7210

    payload = struct.pack("<HHHHH", 0x0001, 0x0100, 0x0200, 0x0300, raw_word)
    candidates = protocol.decode_battery_candidates(payload, 8)

    # The documented rule is what produced the implausible reading.
    assert candidates["le_mv"] == 29.2
    # The two plausible alternatives must both be offered for comparison.
    assert candidates["be_mv"] == 4.21
    assert candidates["le_tenth_mv"] == 2.92


def test_battery_candidates_cover_every_rule():
    """Every documented candidate must produce a value for a 2 byte input."""
    candidates = protocol.decode_battery_candidates(b"\x10\x72", 0)
    assert set(candidates) == set(protocol.BATTERY_CANDIDATES)
    assert all(value is not None for value in candidates.values())


def test_battery_candidates_handle_short_input():
    """A truncated read must not raise."""
    candidates = protocol.decode_battery_candidates(b"\x10", 0)
    assert all(value is None for value in candidates.values())


def test_describe_advertisement_scans_every_offset():
    """The report must let a shifted layout be spotted by eye."""
    payload = struct.pack("<HHHH", 0xAB01, 0x0102, 0x0203, 0x0304) + struct.pack(
        ">H", 2950
    )
    report = protocol.describe_advertisement(payload)

    assert report["length"] == 10
    assert report["documented_layout_valid"] is True
    assert report["parsed"]["battery_mv"] == 2950
    assert report["parsed"]["pid"] == "0xAB01"
    # Offsets 0, 2, 4, 6, 8 must all be reported.
    assert set(report["battery_by_offset_v"]) == {
        "offset_0",
        "offset_2",
        "offset_4",
        "offset_6",
        "offset_8",
    }
    assert report["battery_by_offset_v"]["offset_8"]["be_mv"] == 2.95


def test_describe_advertisement_survives_short_payload():
    """A foreign advertisement must still produce a readable report."""
    report = protocol.describe_advertisement(b"\x01\x02\x03")
    assert report["documented_layout_valid"] is False
    assert "documented" not in report
    assert report["raw"] == "01 02 03"


class ProbeClient:
    """Fake client exposing a GATT table and canned reads."""

    class _Char:
        def __init__(self, uuid, properties, handle):
            self.uuid = uuid
            self.properties = properties
            self.handle = handle

    class _Service:
        def __init__(self, uuid, characteristics):
            self.uuid = uuid
            self.characteristics = characteristics

    class _Collection(list):
        def get_characteristic(self, uuid):
            for service in self:
                for char in service.characteristics:
                    if char.uuid == uuid:
                        return char
            return None

    def __init__(self, reads, properties=("read", "write")):
        chars = [
            self._Char(uuid, list(properties), index)
            for index, uuid in enumerate(
                (
                    const.UUID_SECURITY,
                    const.UUID_COMMAND,
                    const.UUID_VERSION,
                    const.UUID_STATUS,
                    const.UUID_BATTERY,
                )
            )
        ]
        self.services = self._Collection([self._Service("vendor", chars)])
        self.reads = reads
        self.writes = []
        self.mtu_size = 247

    async def read_gatt_char(self, uuid):
        return self.reads[uuid]

    async def write_gatt_char(self, uuid, data, response=None):
        self.writes.append((uuid, bytes(data), response))


def test_probe_reports_unlock_failure_code():
    """Error code 5 is the direct signal that the unlock was rejected."""
    import asyncio

    client = ProbeClient(
        {
            const.UUID_SECURITY: bytes(range(16)),
            const.UUID_VERSION: struct.pack("<HHHH", 1, 2, 3, 4),
            const.UUID_BATTERY: b"\x10\x72",
            const.UUID_STATUS: bytes((0, 5)),
        }
    )
    report = asyncio.run(protocol.probe_device(client))

    assert report["unlock"]["write_ok"] is True
    assert report["unlock"]["challenge_length"] == 16
    assert report["reads"]["status"]["error_code"] == 5
    assert report["reads"]["status"]["error_meaning"] == "unlock_failed"
    assert report["reads"]["battery"]["candidates_v"]["be_mv"] == 4.21
    assert report["known_characteristics"]["command"]["found"] is True
    assert report["known_characteristics"]["command"]["properties"] == ["read", "write"]
    assert report["mtu_size"] == 247


def test_probe_reports_missing_characteristics():
    """A label that lacks our UUIDs must be reported, not crash the probe."""
    import asyncio

    client = ProbeClient({const.UUID_SECURITY: bytes(range(16))})
    client.services = ProbeClient._Collection([])

    report = asyncio.run(protocol.probe_device(client))
    assert all(
        entry["found"] is False for entry in report["known_characteristics"].values()
    )
    assert "error" in report["reads"]["battery"]


def test_probe_detects_wolink_family():
    """Our own stack must be recognised as supported."""
    import asyncio

    client = ProbeClient(
        {
            const.UUID_SECURITY: bytes(range(16)),
            const.UUID_VERSION: struct.pack("<HHHH", 1, 2, 3, 4),
            const.UUID_BATTERY: b"\x10\x72",
            const.UUID_STATUS: bytes((0, 0)),
        }
    )
    report = asyncio.run(protocol.probe_device(client))

    family = report["protocol_family"]
    assert family["detected"] == const.PROTOCOL_WOLINK
    assert family["supported_by_this_integration"] is True


def test_probe_detects_easytag_family():
    """A label running the other vendor stack must be called out clearly."""
    import asyncio

    client = ProbeClient({})
    chars = [
        ProbeClient._Char(const.EASYTAG_WRITE, ["write"], 0),
        ProbeClient._Char(const.EASYTAG_NOTIFY, ["notify"], 1),
    ]
    client.services = ProbeClient._Collection(
        [ProbeClient._Service(const.EASYTAG_SERVICE, chars)]
    )

    report = asyncio.run(protocol.probe_device(client))
    family = report["protocol_family"]

    assert family["detected"] == const.PROTOCOL_EASYTAG
    assert family["supported_by_this_integration"] is False
    assert "roxburghm" in family["note"]


def test_probe_reports_unknown_family():
    """Neither stack present must not be reported as one of them."""
    import asyncio

    client = ProbeClient({})
    client.services = ProbeClient._Collection([])
    report = asyncio.run(protocol.probe_device(client))

    assert report["protocol_family"]["detected"] == const.PROTOCOL_UNKNOWN


# Captured together in one successful probe, so all three can be cross checked.
REAL_ADVERT = bytes.fromhex("30000 00e033002010b99".replace(" ", ""))
REAL_VERSION_CHARACTERISTIC = bytes.fromhex("3000000e03300201")
REAL_BATTERY_CHARACTERISTIC = bytes.fromhex("990b")
REAL_BATTERY_CHARACTERISTIC_MV = 2969


def test_real_advertisement_matches_battery_characteristic():
    """The advertisement battery must agree with the characteristic."""
    assert REAL_ADVERT.hex(" ") == "30 00 00 0e 03 30 02 01 0b 99"

    parsed = protocol.parse_advertisement(REAL_ADVERT)
    assert parsed is not None
    _version, battery_mv = parsed
    assert battery_mv == REAL_BATTERY_CHARACTERISTIC_MV
    assert round(battery_mv / 1000, 3) == 2.969


def test_advertisement_and_version_characteristic_agree():
    """The same bytes must never decode to two different versions.

    The first eight advertisement bytes are byte identical to the version
    characteristic, so parsing them differently is a bug by construction.
    """
    assert REAL_ADVERT[:8] == REAL_VERSION_CHARACTERISTIC

    class _Client:
        async def read_gatt_char(self, uuid):
            return REAL_VERSION_CHARACTERISTIC

    import asyncio

    from_characteristic = asyncio.run(protocol.read_version(_Client()))
    from_advertisement, _battery = protocol.parse_advertisement(REAL_ADVERT)

    assert from_advertisement == from_characteristic


def test_advertisement_battery_is_the_reverse_of_the_characteristic():
    """Only the battery field differs in byte order, and it is a reversal."""
    assert REAL_ADVERT[8:10] == REAL_BATTERY_CHARACTERISTIC[::-1]

    class _Client:
        async def read_gatt_char(self, uuid):
            return REAL_BATTERY_CHARACTERISTIC

    import asyncio

    assert (
        asyncio.run(protocol.read_battery_mv(_Client()))
        == REAL_BATTERY_CHARACTERISTIC_MV
    )


def test_no_little_endian_window_yields_the_true_battery():
    """Guards the reasoning: little endian cannot explain the measurement."""
    little = [
        int.from_bytes(REAL_ADVERT[offset : offset + 2], "little")
        for offset in range(len(REAL_ADVERT) - 1)
    ]
    assert REAL_BATTERY_CHARACTERISTIC_MV not in little

    big = [
        int.from_bytes(REAL_ADVERT[offset : offset + 2], "big")
        for offset in range(len(REAL_ADVERT) - 1)
    ]
    assert big.count(REAL_BATTERY_CHARACTERISTIC_MV) == 1
    assert big.index(REAL_BATTERY_CHARACTERISTIC_MV) == 8


def test_real_advertisement_report_is_labelled():
    """The report must say which byte order was used and why."""
    report = protocol.describe_advertisement(REAL_ADVERT)
    assert report["parsed"]["battery_mv"] == REAL_BATTERY_CHARACTERISTIC_MV
    assert "little endian" in report["parsed"]["byte_order"]
    assert "big endian" in report["parsed"]["byte_order"]
    assert report["parsed"]["version_bytes"] == "30 00 00 0e 03 30 02 01"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print("---")
    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)
