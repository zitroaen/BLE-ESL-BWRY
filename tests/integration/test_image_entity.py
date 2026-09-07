"""The panel preview entity: what the label is showing, in the dashboard."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import raw_payload

ENTITY = "image.esl_66_66_54_20_00_55_panel"


async def _setup(hass: HomeAssistant, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device = entry.runtime_data
    device.state.last_advert = dt_util.utcnow()
    device.coordinator.async_update_listeners()
    return device


async def _press_test_pattern(hass: HomeAssistant) -> list[bytes]:
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
    return uploaded


async def test_entity_exists_before_anything_was_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """It must appear at setup, with no picture yet rather than an error."""
    device = await _setup(hass, config_entry)

    assert hass.states.get(ENTITY) is not None
    assert device.state.last_image_png is None


async def test_the_preview_appears_after_a_send(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """One upload, one picture of what the panel now shows."""
    device = await _setup(hass, config_entry)

    uploaded = await _press_test_pattern(hass)

    assert len(uploaded) == 1
    assert device.state.last_image_png is not None
    assert device.state.last_image_png.startswith(b"\x89PNG")
    assert device.state.last_image_at is not None
    assert device.state.last_image_source == "diagnostic"


async def test_the_preview_matches_the_bytes_that_were_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The picture has to be of this upload, not a re-render of the source.

    Unpacking the payload that went to the label and comparing it with the
    preview is the only check that cannot pass if the two ever drift apart.
    """
    from io import BytesIO

    from PIL import Image

    from custom_components.esl_zhsunyco.imaging import BWRY_PALETTE

    device = await _setup(hass, config_entry)
    uploaded = await _press_test_pattern(hass)

    preview = Image.open(BytesIO(device.state.last_image_png)).convert("RGB")
    assert preview.size == (device.width, device.height)

    sent = raw_payload(uploaded[0])
    codes = [(byte >> shift) & 0b11 for byte in sent for shift in (6, 4, 2, 0)]
    assert list(preview.getdata()) == [BWRY_PALETTE[code] for code in codes]


async def test_a_failed_upload_leaves_the_old_preview(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The preview claims to show the panel, so it must not run ahead of it.

    If the upload fails the label still shows whatever it showed before,
    and a preview of the image that never arrived would be a lie.
    """
    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)
    first = device.state.last_image_png
    first_at = device.state.last_image_at
    assert first is not None

    async def failing_send_image(client, data, *, compressed=False):
        raise OSError("the label hung up")

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=failing_send_image,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
        # The failure is the point; the preview must survive it.
        contextlib.suppress(Exception),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.esl_66_66_54_20_00_55_test_pattern"},
            blocking=True,
        )

    assert device.state.last_image_png == first
    assert device.state.last_image_at == first_at


async def test_the_preview_stays_available_when_the_label_sleeps(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An ESL is out of range most of the time; the picture is still true."""
    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)

    # Nothing heard from the label for a long time.
    device.state.last_advert = None
    device.coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert device.available is False
    state = hass.states.get(ENTITY)
    assert state is not None
    assert state.state != "unavailable"


async def test_the_entity_state_is_the_last_update_time(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """An automation can watch this instead of parsing a service response.

    An image entity's state is the timestamp of its last update, so a
    state trigger on it fires exactly when a send succeeded.
    """
    await _setup(hass, config_entry)
    before = hass.states.get(ENTITY).state

    await _press_test_pattern(hass)
    await hass.async_block_till_done()

    after = hass.states.get(ENTITY).state
    assert after != before
    assert dt_util.parse_datetime(after) is not None


async def test_the_preview_survives_a_restart(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """The panel keeps its image without power, so the record must too.

    Reloading the entry throws away every bit of in-memory state, which is
    what a Home Assistant restart does to this integration.
    """
    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)
    sent = device.state.last_image_png
    sent_at = device.state.last_image_at
    assert sent is not None

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    reloaded = config_entry.runtime_data
    assert reloaded is not device, "the reload must build a fresh device"
    assert reloaded.state.last_image_png == sent
    assert reloaded.state.last_image_at == sent_at
    assert reloaded.state.last_image_source == "diagnostic"
    # And the entity serves it, not just the device object.
    assert hass.states.get(ENTITY).state == sent_at.isoformat()


async def test_nothing_is_restored_when_nothing_was_ever_sent(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A fresh install must not come up claiming to know the panel."""
    await _setup(hass, config_entry)

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.runtime_data.state.last_image_png is None


async def test_a_failed_send_does_not_reach_the_store(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """Persistence must not turn a failed upload into a permanent lie."""
    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)
    first = device.state.last_image_png

    async def failing_send_image(client, data, *, compressed=False):
        raise OSError("the label hung up")

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.send_prepared_image",
            new=failing_send_image,
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aenter__",
            new=AsyncMock(return_value=object()),
        ),
        patch(
            "custom_components.esl_zhsunyco.device._ESLConnection.__aexit__",
            new=AsyncMock(return_value=False),
        ),
        contextlib.suppress(Exception),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.esl_66_66_54_20_00_55_test_pattern"},
            blocking=True,
        )

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.runtime_data.state.last_image_png == first


async def test_clearing_the_screen_forgets_the_picture(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """After a clear the panel is not showing the image any more.

    Restoring it after a restart would then be a picture of a screen that
    is blank, which is worse than showing nothing.
    """
    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)
    assert device.state.last_image_png is not None

    with (
        patch(
            "custom_components.esl_zhsunyco.device.protocol.clear_screen",
            new=AsyncMock(),
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
            {"entity_id": "button.esl_66_66_54_20_00_55_clear_screen"},
            blocking=True,
        )

    assert device.state.last_image_png is None

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.runtime_data.state.last_image_png is None


async def test_a_corrupt_store_does_not_stop_setup(
    hass: HomeAssistant, config_entry, mock_bluetooth
) -> None:
    """A bad stored image is a reason to show nothing, not to fail setup."""
    from custom_components.esl_zhsunyco.image_store import PanelImageStore

    config_entry.add_to_hass(hass)
    store = PanelImageStore(hass, config_entry.entry_id)
    await store._store.async_save({"png_base64": "not base64!!", "at": "nonsense"})

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.runtime_data.state.last_image_png is None
    assert hass.states.get(ENTITY) is not None


async def test_what_gets_stored_is_json_serialisable(
    hass: HomeAssistant, config_entry, mock_bluetooth, hass_storage
) -> None:
    """The test store keeps objects in memory; the real one writes JSON.

    Handing a Store raw bytes passes in tests and fails on disk, so check
    the payload the way Home Assistant will actually have to write it, and
    that it decodes back to exactly the PNG that was sent.
    """
    import base64
    import json

    device = await _setup(hass, config_entry)
    await _press_test_pattern(hass)

    key = f"esl_zhsunyco.{config_entry.entry_id}.panel"
    assert key in hass_storage, sorted(hass_storage)
    stored = hass_storage[key]["data"]

    assert json.loads(json.dumps(stored)) == stored
    assert base64.b64decode(stored["png_base64"]) == device.state.last_image_png
