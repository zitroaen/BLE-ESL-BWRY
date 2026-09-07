"""Panel geometry: model presets and manual overrides."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.esl_zhsunyco.const import (
    CONF_HEIGHT,
    CONF_MODEL,
    CONF_PIXEL_FORMAT,
    CONF_WIDTH,
    MODELS,
)

from .conftest import raw_payload


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def _set_options(hass: HomeAssistant, entry, **options):
    hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("model", "width", "height"),
    [
        ("BLE-154MBWRY", 200, 200),
        ("BLE-290BWRY", 128, 296),
        ("BLE-350BWRY", 184, 384),
        ("BLE-420BWRY", 400, 300),
        ("BLE-750BWRY", 800, 480),
    ],
)
async def test_each_model_has_its_own_size(
    hass: HomeAssistant, config_entry, mock_bluetooth, model, width, height
) -> None:
    """Picking a model must set the geometry the packer works from."""
    device = await _setup(hass, config_entry)
    await _set_options(hass, config_entry, **{CONF_MODEL: model})

    assert (device.width, device.height) == (width, height)
    assert device.pixel_format == "bwry"


async def test_the_measured_model_is_unchanged(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """184 x 384 is the one measured on hardware; adding models must not move it.

    A row is 46 bytes at 2 bits per pixel and a full screen 17664, which is
    exactly what was transferred successfully.
    """
    panel = MODELS["BLE-350BWRY"]
    assert (panel["width"], panel["height"]) == (184, 384)
    assert panel["width"] * 2 // 8 == 46
    assert 46 * panel["height"] == 17664


async def test_a_size_override_beats_the_model(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The point of the override: a panel nobody has added a preset for."""
    device = await _setup(hass, config_entry)
    await _set_options(hass, config_entry, **{CONF_WIDTH: 212, CONF_HEIGHT: 104})

    assert (device.width, device.height) == (212, 104)


async def test_zero_means_take_it_from_the_model(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """0 has to mean "unset", not a zero pixel panel."""
    device = await _setup(hass, config_entry)
    await _set_options(
        hass, config_entry, **{CONF_MODEL: "BLE-290BWRY", CONF_WIDTH: 0, CONF_HEIGHT: 0}
    )

    assert (device.width, device.height) == (128, 296)


async def test_one_dimension_can_be_overridden_alone(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Correcting only the width must not blank the height."""
    device = await _setup(hass, config_entry)
    await _set_options(hass, config_entry, **{CONF_WIDTH: 200})

    assert (device.width, device.height) == (200, 384)


async def test_the_colour_depth_can_be_overridden(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A one bit panel packs eight pixels per byte, not four."""
    device = await _setup(hass, config_entry)
    await _set_options(
        hass,
        config_entry,
        **{CONF_PIXEL_FORMAT: "mono", CONF_WIDTH: 64, CONF_HEIGHT: 32},
    )

    assert device.pixel_format == "mono"

    from custom_components.esl_zhsunyco.imaging import ImageRequest, render_image

    request = ImageRequest(pattern="solid_black", pixel_format=device.pixel_format)
    rendered = render_image(request, device.width, device.height)
    assert len(rendered.payload) == 64 * 32 // 8


async def test_the_size_reaches_the_rendered_image(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The whole reason geometry is configurable: it decides the byte count."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.util import dt as dt_util

    device = await _setup(hass, config_entry)
    await _set_options(hass, config_entry, **{CONF_MODEL: "BLE-290BWRY"})
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

    assert len(raw_payload(uploaded[0])) == 128 * 296 // 4


async def test_the_model_can_be_corrected_after_setup(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Picking the wrong model must not mean deleting and re-adding the label."""
    from custom_components.esl_zhsunyco.const import (
        CONF_BATTERY_EMPTY_MV,
        CONF_BATTERY_FULL_MV,
    )

    device = await _setup(hass, config_entry)
    assert device.width == 184

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_MODEL: "BLE-750BWRY",
            CONF_BATTERY_FULL_MV: 3000,
            CONF_BATTERY_EMPTY_MV: 2200,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert (device.width, device.height) == (800, 480)


async def test_a_renamed_model_still_resolves(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An entry from an older version names a model that no longer exists.

    The vendor calls the 3.5" panel BLE-350BWRY; this integration used to
    call it BLE-35BWRY. Falling back to the default would silently give the
    wrong geometry to anyone who is not on the default model.
    """
    from custom_components.esl_zhsunyco.const import MODEL_ALIASES

    device = await _setup(hass, config_entry)
    await _set_options(hass, config_entry, **{CONF_MODEL: "BLE-35BWRY"})

    assert device.model == "BLE-350BWRY"
    assert (device.width, device.height) == (184, 384)

    # And the prototype's names map to the panels they actually were.
    assert MODEL_ALIASES["ET0290"] == "BLE-290BWRY"
    assert MODEL_ALIASES["ET0420"] == "BLE-420BWRY"


async def test_every_preset_matches_the_vendor_pixel_count(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Width x height must be the resolution on the product sheet.

    The row axis is not always the long one, so width and height can be the
    other way round from the sheet - but the pixel count cannot differ, and
    a typo in a preset would show up here.
    """
    for name, panel in MODELS.items():
        vendor = tuple(int(part) for part in str(panel["vendor"]).split("x"))
        assert sorted((panel["width"], panel["height"])) == sorted(vendor), name


async def test_a_one_bit_panel_is_offered(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The vendor sells a black and white model, so mono is not theoretical."""
    assert MODELS["BLE-213MBW-L"]["format"] == "mono"
