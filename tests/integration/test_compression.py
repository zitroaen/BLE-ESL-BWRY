"""Block compression for the 0xA502 refresh path."""

from __future__ import annotations

import os
import struct
import zlib
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.esl_zhsunyco.const import CONF_COMPRESS
from custom_components.esl_zhsunyco.protocol import (
    COMPRESSION_BLOCK_SIZE,
    ESLProtocolError,
    compress_image,
    decompress_image,
)

FULL_SCREEN = 184 * 384 // 4  # 17664


def test_the_header_matches_the_documented_layout() -> None:
    """A5 A6 <block count> 02, then index, size, data per block.

    Written against the format description rather than against this
    encoder, so a change to either side shows up here.
    """
    data = bytes(FULL_SCREEN)
    payload = compress_image(data)

    assert payload[:2] == b"\xa5\xa6"
    assert payload[2] == 3, "17664 bytes is three blocks of at most 8192"
    assert payload[3] == 0x02, "fixed format marker, not a count"

    # Walk the blocks the way the label would.
    offset = 4
    rebuilt = bytearray()
    for expected_index in (1, 2, 3):
        index = payload[offset]
        (size,) = struct.unpack_from("<H", payload, offset + 1)
        offset += 3
        assert index == expected_index, "indices are 1-based and in order"
        block = payload[offset : offset + size]
        offset += size
        # Raw deflate: no zlib header, no Adler-32 trailer.
        rebuilt.extend(zlib.decompress(block, -zlib.MAX_WBITS))

    assert offset == len(payload), "no trailing bytes"
    assert bytes(rebuilt) == data


@pytest.mark.parametrize(
    "size",
    [1, 100, COMPRESSION_BLOCK_SIZE - 1, COMPRESSION_BLOCK_SIZE,
     COMPRESSION_BLOCK_SIZE + 1, FULL_SCREEN, 800 * 480 // 4],
    ids=["tiny", "small", "just under a block", "exactly a block",
         "just over a block", "3.5 inch screen", "7.5 inch screen"],
)
def test_round_trip_at_the_block_boundaries(size: int) -> None:
    """Block splitting is the part most likely to be off by one."""
    data = bytes((i * 37) % 256 for i in range(size))
    assert decompress_image(compress_image(data)) == data


def test_block_count_matches_the_uncompressed_size() -> None:
    """Blocks hold uncompressed bytes, not compressed ones."""
    for size, expected in (
        (COMPRESSION_BLOCK_SIZE, 1),
        (COMPRESSION_BLOCK_SIZE + 1, 2),
        (FULL_SCREEN, 3),
        (800 * 480 // 4, 12),  # the 7.5 inch panel
    ):
        assert compress_image(bytes(size))[2] == expected, size


def test_a_flat_image_compresses_hard() -> None:
    """The whole point: less time in the connection slot.

    Hardware runs came out at 5-6 % of the raw size for real content; a
    blank screen does far better and is the easy case to assert on.
    """
    payload = compress_image(bytes(FULL_SCREEN))
    assert len(payload) < FULL_SCREEN // 50


def test_empty_input_is_refused() -> None:
    """Nothing to send is a caller error, not an empty payload."""
    with pytest.raises(ESLProtocolError, match="nothing to compress"):
        compress_image(b"")


def test_a_corrupt_payload_is_rejected_rather_than_guessed() -> None:
    """decompress_image is the test oracle, so it must not be lenient."""
    good = compress_image(bytes(FULL_SCREEN))

    with pytest.raises(ESLProtocolError, match="not a block compressed"):
        decompress_image(b"\x00\x00\x00\x00")
    with pytest.raises(ESLProtocolError, match="format marker"):
        decompress_image(good[:3] + b"\x99" + good[4:])
    with pytest.raises(ESLProtocolError, match="truncated|ends inside"):
        decompress_image(good[:-5])


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()
    return device


async def _send_pattern(hass: HomeAssistant) -> list[tuple[bytes, bool]]:
    sent: list[tuple[bytes, bool]] = []

    async def fake_send(client, payload, *, compressed):
        sent.append((bytes(payload), compressed))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.esl_66_66_54_20_00_55_test_pattern"},
            blocking=True,
        )
    return sent


async def test_images_are_compressed_by_default(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """On by default, because the connection slot is the bottleneck."""
    await _setup(hass, config_entry)

    (payload, compressed), = await _send_pattern(hass)

    assert compressed is True
    assert len(payload) < FULL_SCREEN
    assert decompress_image(payload) != b"", "and it is the real format"


async def test_compression_can_be_turned_off(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A panel that rejects 0xA502 must not need a new release."""
    device = await _setup(hass, config_entry)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_COMPRESS: False}
    )
    await hass.async_block_till_done()
    device.state.last_advert = dt_util.utcnow()

    (payload, compressed), = await _send_pattern(hass)

    assert compressed is False
    assert len(payload) == FULL_SCREEN


async def test_incompressible_data_is_sent_raw(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Deflate can grow its input, and a longer payload is strictly worse.

    More chunks, more time holding the connection, no benefit - so the
    compressed form is only used when it actually came out shorter.
    """
    device = await _setup(hass, config_entry)
    noise = os.urandom(FULL_SCREEN)

    assert len(compress_image(noise)) > len(noise), "precondition"

    payload, compressed = device._compressed_or_raw(noise)
    assert compressed is False
    assert payload == noise


async def test_a_compressor_failure_falls_back_instead_of_failing(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Compression is an optimisation; losing it must not lose the image."""
    device = await _setup(hass, config_entry)
    data = bytes(FULL_SCREEN)

    with patch(
        "custom_components.esl_zhsunyco.device.protocol.compress_image",
        side_effect=ESLProtocolError("no"),
    ):
        payload, compressed = device._compressed_or_raw(data)

    assert compressed is False
    assert payload == data


async def test_the_response_reports_both_sizes(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An automation should be able to see what compression actually saved."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import (
        DOMAIN,
        SERVICE_SEND_TEST_PATTERN,
    )

    await _setup(hass, config_entry)
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    async def fake_send(client, payload, *, compressed):
        return None

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_TEST_PATTERN,
            {"device_id": device_entry.id},
            blocking=True,
            return_response=True,
        )

    result = response["results"][0]
    assert result["bytes"] == FULL_SCREEN, "the image is still this big"
    assert result["sent_bytes"] < FULL_SCREEN, "but less went over the air"
    assert result["compressed"] is True
