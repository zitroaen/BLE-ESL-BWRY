"""Downloading over a real socket, not through the request mock.

The mock hands back a whole body in one go, which is exactly the case that
works. A body arriving in several TCP segments - anything of size over a
real network - is what broke, so this file runs a real server.
"""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.esl_zhsunyco.services import MAX_DOWNLOAD_BYTES, _fetch_image


def _png(width: int, height: int) -> bytes:
    """Build a PNG big enough that it cannot arrive in one segment."""
    from PIL import Image

    image = Image.new("RGB", (width, height))
    image.putdata(
        [((x * 7) % 256, (y * 13) % 256, (x * y) % 256)
         for y in range(height) for x in range(width)]
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _Server:
    """Serves a body the way a real server does: in pieces."""

    def __init__(self, body: bytes, *, piece: int = 8192, status: int = 200):
        self.body = body
        self.piece = piece
        self.status = status
        self._runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> str:
        async def handler(request):
            if self.status != 200:
                return web.Response(status=self.status)
            response = web.StreamResponse()
            await response.prepare(request)
            for offset in range(0, len(self.body), self.piece):
                await response.write(self.body[offset : offset + self.piece])
                await asyncio.sleep(0)
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_get("/image", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/image"
        return self.url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


@pytest.fixture
async def server(socket_enabled):
    """Serve bodies from a real HTTP server on a real port."""
    started: list[_Server] = []

    async def _start(body: bytes, **kwargs) -> str:
        instance = _Server(body, **kwargs)
        started.append(instance)
        return await instance.start()

    yield _start
    for instance in started:
        await instance.stop()


async def test_a_body_in_several_pieces_arrives_whole(
    hass: HomeAssistant, server
) -> None:
    """The bug: read(n) returns what is buffered, not n bytes.

    A 100 kB PNG came back as the first 8 kB, which is not a PNG any more,
    and failed later as an unreadable image rather than as a short read.
    """
    body = _png(500, 400)
    assert len(body) > 50_000, "the source has to be worth several segments"

    data = await _fetch_image(hass, await server(body))

    assert len(data) == len(body)
    assert data == body

    from PIL import Image

    assert Image.open(BytesIO(data)).size == (500, 400)


async def test_a_small_body_still_works(hass: HomeAssistant, server) -> None:
    """The case that always worked must keep working."""
    body = _png(20, 20)
    assert await _fetch_image(hass, await server(body)) == body


async def test_the_size_cap_stops_a_long_body(hass: HomeAssistant, server) -> None:
    """Capping while reading, so an endless body cannot fill memory."""
    body = b"\x00" * (MAX_DOWNLOAD_BYTES + 4096)

    with pytest.raises(ServiceValidationError, match="larger than"):
        await _fetch_image(hass, await server(body))


async def test_an_http_error_says_the_code(hass: HomeAssistant, server) -> None:
    """A 404 has to name itself rather than surface as "Unknown error"."""
    with pytest.raises(ServiceValidationError, match="404"):
        await _fetch_image(hass, await server(b"", status=404))


async def test_an_empty_body_is_refused(hass: HomeAssistant, server) -> None:
    """Nothing downloaded is not a zero byte image."""
    with pytest.raises(ServiceValidationError, match="empty"):
        await _fetch_image(hass, await server(b""))


async def test_a_non_image_body_is_reported_as_such(
    hass: HomeAssistant, config_entry, mock_bluetooth, server
) -> None:
    """An HTML error page served with HTTP 200 must say what went wrong.

    This is the shape the truncated download took: the fetch succeeded and
    the failure only appeared later, in the renderer, as "Unknown error".
    """
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr

    from custom_components.esl_zhsunyco.const import DOMAIN, SERVICE_SET_IMAGE

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device_entry = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )[0]

    url = await server(b"<html>not an image</html>" * 500)

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_IMAGE,
            {"device_id": device_entry.id, "url": url},
            blocking=True,
        )

    message = str(caught.value)
    assert "could not read the image" in message
    assert url in message, "and it names the source"
