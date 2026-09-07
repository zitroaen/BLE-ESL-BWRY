"""Diagnostics output and the command write mode option."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import voluptuous as vol
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.esl_zhsunyco.const import (
    MANUFACTURER_ID,
)

from .conftest import raw_payload


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def test_diagnostics_include_raw_advertisement(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """The raw bytes must be downloadable so a wrong layout can be spotted."""
    device = await _setup(hass, config_entry)

    # The advertisement captured from real hardware, alongside a battery
    # characteristic that read 2963 mV.
    payload = bytes.fromhex("30000 00e033002010b93".replace(" ", ""))
    device._advert_received(
        SimpleNamespace(rssi=-55, manufacturer_data={MANUFACTURER_ID: payload}), None
    )
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    advert = result["advertisement"]
    assert advert["raw_by_company_id"]["0xBBAA"] == "30 00 00 0e 03 30 02 01 0b 93"
    assert advert["decoded"]["parsed"]["battery_mv"] == 2963
    assert advert["decoded"]["battery_by_offset_v"]["offset_8"]["be_mv"] == 2.963
    assert "battery_candidate_meanings" in result

    # The sensor must now agree with what the poll reads over GATT.
    assert hass.states.get("sensor.esl_66_66_54_20_00_55_battery_voltage").state == (
        "2.963"
    )


async def test_diagnostics_redact_address(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """The Bluetooth address must not be exposed verbatim in a shared file."""
    await _setup(hass, config_entry)
    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert result["entry"]["data"]["address"] == "**REDACTED**"


async def test_commands_always_write_with_response(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """There is nothing to configure here any more.

    The command characteristic advertises ['read', 'write'] and nothing
    else, so write-without-response was never possible on this hardware and
    the option that offered it is gone.
    """
    device = await _setup(hass, config_entry)
    assert device.write_response is True


async def test_probe_button_reports_connection_failure(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A label that cannot be reached must produce a report, not an exception."""
    from homeassistant.components import persistent_notification

    device = await _setup(hass, config_entry)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.esl_66_66_54_20_00_55_debug_probe"},
        blocking=True,
    )

    assert device.state.last_probe is not None
    assert device.state.last_probe["connection"] == "failed"

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert any("ESL probe" in item["title"] for item in notifications.values())


async def test_probe_button_is_always_pressable(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The diagnostic button must not go unavailable with the label."""
    await _setup(hass, config_entry)
    state = hass.states.get("button.esl_66_66_54_20_00_55_debug_probe")
    assert state is not None
    assert state.state != "unavailable"


async def test_probe_includes_advertisement_without_connection(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Advertisement data stays useful even when connecting fails."""
    device = await _setup(hass, config_entry)

    payload = struct.pack("<HHHH", 0xAB01, 0x0102, 0x0203, 0x0304) + struct.pack(
        ">H", 29200
    )
    device._advert_received(
        SimpleNamespace(rssi=-55, manufacturer_data={MANUFACTURER_ID: payload}), None
    )
    await hass.async_block_till_done()

    report = await device.async_probe()
    assert report["connection"] == "failed"
    assert report["advertisement"]["raw_by_company_id"]["0xBBAA"] == payload.hex(" ")


async def test_diagnostics_points_at_the_probe_button(
    hass: HomeAssistant, hass_client, config_entry, mock_bluetooth
) -> None:
    """Diagnostics must not connect, but must say how to get a probe.

    It used to run a probe automatically, which turned a passive looking
    download into an active connection attempt. See test_cancellation for the
    assertion that no connection is opened.
    """
    device = await _setup(hass, config_entry)
    assert device.state.last_probe is None

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert "Debug probe button" in result["last_probe"]


async def test_debug_command_rejects_non_hex(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A typo in the payload must be reported, not written to the label."""
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_DEBUG_COMMAND

    await _setup(hass, config_entry)
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    for bad in ("nothex", ""):
        try:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_DEBUG_COMMAND,
                {"device_id": device_entry.id, "payload": bad},
                blocking=True,
            )
        except (ServiceValidationError, vol.Invalid):
            continue
        raise AssertionError(f"expected a validation error for {bad!r}")


async def test_debug_command_sends_the_given_bytes(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Hex with separators must reach the characteristic verbatim."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_DEBUG_COMMAND

    await _setup(hass, config_entry)
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    sent: list[tuple] = []

    async def fake_send(client, payload, *, response=None):
        sent.append((bytes(payload), response))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_command", new=fake_send
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
            DOMAIN,
            SERVICE_DEBUG_COMMAND,
            {
                "device_id": device_entry.id,
                "payload": "a5 04",
                "expect_response": False,
            },
            blocking=True,
        )

    assert sent == [(b"\xa5\x04", False)]


async def test_test_pattern_button_uploads_panel_sized_data(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """One press must render and upload a full panel of bytes."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.util import dt as dt_util

    device = await _setup(hass, config_entry)
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()

    uploaded: list[bytes] = []

    async def fake_send_image(client, data, *, compressed=False):
        uploaded.append(bytes(data))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send_image,
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

    assert len(uploaded) == 1
    # BLE-350BWRY is 184x384 at two bits per pixel.
    assert len(raw_payload(uploaded[0])) == 184 * 384 // 4


async def test_the_panel_model_decides_the_packing(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """There is no encoding knob any more; the model settles it.

    A BLE-35BWRY is a four colour panel, so a full screen is two bits per
    pixel. That was measured, not chosen, which is why the caller no longer
    gets to override it.
    """
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers import device_registry as dr
    from homeassistant.util import dt as dt_util

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_SEND_TEST_PATTERN

    device = await _setup(hass, config_entry)
    device.state.last_advert = dt_util.utcnow()
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    uploaded: list[bytes] = []

    async def fake_send_image(client, data, *, compressed=False):
        uploaded.append(bytes(data))

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=fake_send_image,
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
            DOMAIN,
            SERVICE_SEND_TEST_PATTERN,
            {
                "device_id": device_entry.id,
                "pattern": "solid_black",
            },
            blocking=True,
        )

    assert len(uploaded) == 1
    assert len(raw_payload(uploaded[0])) == 184 * 384 // 4
