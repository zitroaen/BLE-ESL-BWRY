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
    payload = struct.pack("<HHHHH", 0xAB01, 0x0102, 0x0203, 0x0304, 2950)
    report = protocol.describe_advertisement(payload)

    assert report["length"] == 10
    assert report["documented_layout_valid"] is True
    assert report["documented"]["battery_raw_le"] == 2950
    assert report["documented"]["pid"] == "0xAB01"
    # Offsets 0, 2, 4, 6, 8 must all be reported.
    assert set(report["battery_by_offset_v"]) == {
        "offset_0",
        "offset_2",
        "offset_4",
        "offset_6",
        "offset_8",
    }
    assert report["battery_by_offset_v"]["offset_8"]["le_mv"] == 2.95


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
