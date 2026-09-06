"""Connection and state handling for a single ESL label."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    CONF_ADDRESS,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    CONF_WRITE_MODE,
    DEFAULT_MODEL,
    DEFAULT_PIXEL_FORMAT,
    DEFAULT_RGB_OFF_MS,
    DEFAULT_RGB_ON_MS,
    DEFAULT_RGB_WORK_MS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_WRITE_MODE,
    DOMAIN,
    ERROR_CODES,
    MANUFACTURER_ID,
    MANUFACTURER_ID_ALT,
    MODELS,
    WRITE_MODE_NO_RESPONSE,
    WRITE_MODE_RESPONSE,
)

if TYPE_CHECKING:
    from .imaging import ImageRequest

_LOGGER = logging.getLogger(__name__)

# An advertisement older than this means the label is out of range.
ADVERT_TIMEOUT = timedelta(minutes=15)

CONNECT_TIMEOUT = 30.0

# An electronic shelf label sleeps between advertisements; gaps of several
# minutes are normal. A Bluetooth proxy can only open a connection to a device
# it currently has in view, so connecting to a sleeping label fails with
# BleakOutOfConnectionSlotsError no matter how often it is retried. Waiting for
# the next advertisement and connecting inside that window is what actually
# works, so commands wait rather than fail.
ADVERTISEMENT_WAIT_S = 180


@dataclass
class ESLState:
    """Everything we know about the label."""

    battery_mv: int | None = None
    pid: int | None = None
    app_version: int | None = None
    hw_version: int | None = None
    disp_version: int | None = None
    busy: bool | None = None
    error: int | None = None
    rssi: int | None = None
    last_advert: Any = None
    # Raw advertisement and probe output, only consumed by diagnostics.
    advert_raw: dict[str, str] = field(default_factory=dict)
    advert_decoded: dict[str, Any] = field(default_factory=dict)
    last_probe: dict[str, Any] | None = None
    last_connect_ok: bool = False
    last_error_message: str | None = None

    # Locally held RGB parameters, surfaced as number entities.
    rgb_on_ms: int = DEFAULT_RGB_ON_MS
    rgb_off_ms: int = DEFAULT_RGB_OFF_MS
    rgb_work_ms: int = DEFAULT_RGB_WORK_MS
    rgb_color: tuple[int, int, int] = field(default=(255, 0, 0))
    rgb_is_on: bool = False

    @property
    def error_text(self) -> str | None:
        """Human readable form of the status error code."""
        if self.error is None:
            return None
        return ERROR_CODES.get(self.error, f"unknown_{self.error}")


class ESLDevice:
    """Owns the BLE connection, the poll coordinator and the passive listener."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the device from its config entry."""
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self.model: str = entry.data.get(CONF_MODEL, DEFAULT_MODEL)

        panel = MODELS.get(self.model, MODELS[DEFAULT_MODEL])
        self.width = int(panel["width"])
        self.height = int(panel["height"])
        self.pixel_format = str(panel.get("format", DEFAULT_PIXEL_FORMAT))

        self.state = ESLState()
        self._lock = asyncio.Lock()
        self._unregister_advert: CALLBACK_TYPE | None = None

        self.coordinator: DataUpdateCoordinator[ESLState] = DataUpdateCoordinator(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {self.address}",
            update_method=self._async_poll,
            update_interval=self._poll_interval(),
        )

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    @property
    def write_response(self) -> bool | None:
        """ATT write type for commands, or None to let bleak decide."""
        mode = self.entry.options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)
        if mode == WRITE_MODE_RESPONSE:
            return True
        if mode == WRITE_MODE_NO_RESPONSE:
            return False
        return None

    def _poll_interval(self) -> timedelta | None:
        """Interval for the connectable poll, or None when disabled."""
        minutes = int(
            self.entry.options.get(
                CONF_SCAN_INTERVAL_MIN,
                self.entry.data.get(CONF_SCAN_INTERVAL_MIN, DEFAULT_SCAN_INTERVAL_MIN),
            )
        )
        return timedelta(minutes=minutes) if minutes > 0 else None

    async def async_setup(self) -> None:
        """Start the passive listener and run the first poll."""
        self._unregister_advert = bluetooth.async_register_callback(
            self.hass,
            self._advert_received,
            bluetooth.BluetoothCallbackMatcher(address=self.address),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

        # Seed from whatever the Bluetooth integration has already seen.
        if service_info := bluetooth.async_last_service_info(
            self.hass, self.address, connectable=False
        ):
            self._apply_service_info(service_info)

        # Not awaited: connecting can take minutes while the label is asleep,
        # and blocking setup on that held up Home Assistant startup for 96
        # seconds in the field. Entities come up from advertisement data and
        # the poll fills in the rest when it lands.
        self.entry.async_create_background_task(
            self.hass,
            self.coordinator.async_refresh(),
            name=f"{DOMAIN} initial refresh {self.address}",
        )

    async def async_unload(self) -> None:
        """Stop the passive listener."""
        if self._unregister_advert is not None:
            self._unregister_advert()
            self._unregister_advert = None

    def async_update_interval(self) -> None:
        """Apply a changed poll interval from the options flow."""
        self.coordinator.update_interval = self._poll_interval()

    # ------------------------------------------------------------------
    # Passive path: advertisements (section 1.2)
    # ------------------------------------------------------------------

    @callback
    def _advert_received(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle an advertisement for our address."""
        if self._apply_service_info(service_info):
            # async_update_listeners, not async_set_updated_data: the latter
            # restarts the poll timer, so frequent advertisements would keep
            # postponing the status poll forever.
            self.coordinator.async_update_listeners()

    def _apply_service_info(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> bool:
        """Merge advertisement data into the state. Returns True on change."""
        self.state.rssi = service_info.rssi
        self.state.last_advert = dt_util.utcnow()
        self.state.advert_raw = {
            f"0x{company_id:04X}": bytes(data).hex(" ")
            for company_id, data in service_info.manufacturer_data.items()
        }

        payload: bytes | None = None
        for company_id in (MANUFACTURER_ID, MANUFACTURER_ID_ALT):
            if (data := service_info.manufacturer_data.get(company_id)) is not None:
                payload = bytes(data)
                break

        if payload is None:
            return True

        self.state.advert_decoded = protocol.describe_advertisement(payload)
        parsed = protocol.parse_advertisement(payload)
        if parsed is None:
            _LOGGER.debug(
                "Advertisement from %s too short to parse: %s",
                self.address,
                payload.hex(),
            )
            return True

        version, battery_mv = parsed
        self.state.pid = version.pid
        self.state.app_version = version.app_version
        self.state.hw_version = version.hw_version
        self.state.disp_version = version.disp_version
        self.state.battery_mv = battery_mv
        return True

    # ------------------------------------------------------------------
    # Active path: connections
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when the label was heard from recently or last poll worked."""
        if (
            self.state.last_advert is not None
            and dt_util.utcnow() - self.state.last_advert < ADVERT_TIMEOUT
        ):
            return True
        return self.state.last_connect_ok

    @property
    def battery_v(self) -> float | None:
        """Battery voltage in volts."""
        if self.state.battery_mv is None:
            return None
        return round(self.state.battery_mv / 1000.0, 3)

    def _ble_device(self) -> BLEDevice | None:
        """Return the label if some adapter or proxy can reach it right now."""
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    async def _async_wait_for_connectable(
        self, timeout: int = ADVERTISEMENT_WAIT_S
    ) -> BLEDevice:
        """Return a connectable device, waiting for the label to wake if needed.

        The label is only connectable for a short window around each of its
        advertisements. Outside that window every connection attempt fails,
        which is what made the LED and clear screen look broken.
        """
        if (ble_device := self._ble_device()) is not None:
            return ble_device

        last_seen = self.state.last_advert
        _LOGGER.debug(
            "%s is asleep (last advertisement %s), waiting up to %ss for it to wake",
            self.address,
            last_seen.isoformat() if last_seen else "never",
            timeout,
        )

        try:
            await bluetooth.async_process_advertisements(
                self.hass,
                lambda service_info: True,
                bluetooth.BluetoothCallbackMatcher(
                    address=self.address, connectable=True
                ),
                bluetooth.BluetoothScanningMode.ACTIVE,
                timeout,
            )
        except TimeoutError as err:
            raise HomeAssistantError(
                f"Label {self.address} did not advertise within {timeout}s. "
                "It is asleep or out of range of every adapter and proxy."
            ) from err

        if (ble_device := self._ble_device()) is None:
            raise HomeAssistantError(
                f"Label {self.address} advertised but no adapter or proxy has a "
                "free connection slot for it."
            )
        return ble_device

    async def _async_poll(self) -> ESLState:
        """Connect, unlock and read version, battery and status."""
        try:
            async with self.connection() as client:
                version = await protocol.read_version(client)
                self.state.pid = version.pid
                self.state.app_version = version.app_version
                self.state.hw_version = version.hw_version
                self.state.disp_version = version.disp_version

                self.state.battery_mv = await protocol.read_battery_mv(client)

                status = await protocol.read_status(client)
                self.state.busy = status.busy
                self.state.error = status.error
        except UpdateFailed:
            self.state.last_connect_ok = False
            raise
        except Exception as err:  # noqa: BLE001 - surfaced through UpdateFailed
            self.state.last_connect_ok = False
            self.state.last_error_message = str(err)
            raise UpdateFailed(f"Poll of {self.address} failed: {err}") from err

        self.state.last_connect_ok = True
        self.state.last_error_message = None
        return self.state

    def connection(self) -> _ESLConnection:
        """Return an async context manager holding an unlocked connection."""
        return _ESLConnection(self)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        on_ms: int | None = None,
        off_ms: int | None = None,
        work_ms: int | None = None,
    ) -> None:
        """Drive the RGB LED (section 3.8)."""
        on_ms = self.state.rgb_on_ms if on_ms is None else on_ms
        off_ms = self.state.rgb_off_ms if off_ms is None else off_ms
        work_ms = self.state.rgb_work_ms if work_ms is None else work_ms

        async with self.connection() as client:
            await protocol.set_rgb(
                client,
                red,
                green,
                blue,
                on_ms,
                off_ms,
                work_ms,
                response=self.write_response,
            )

        self.state.rgb_color = (red, green, blue)
        self.state.rgb_is_on = work_ms > 0 and any((red, green, blue))
        _LOGGER.debug(
            "%s RGB(%d,%d,%d) on=%d off=%d work=%d",
            self.address,
            red,
            green,
            blue,
            on_ms,
            off_ms,
            work_ms,
        )
        self.coordinator.async_update_listeners()

    async def async_clear_screen(self) -> None:
        """Clear the panel (section 3.7)."""
        async with self.connection() as client:
            await protocol.clear_screen(client, response=self.write_response)
        _LOGGER.debug("%s screen cleared", self.address)

    async def async_send_image(self, request: ImageRequest) -> None:
        """Render and upload a full screen image (sections 3.1 - 3.2)."""
        from .imaging import render_image

        request.pixel_format = self.pixel_format
        data = await self.hass.async_add_executor_job(
            render_image, request, self.width, self.height
        )
        async with self.connection() as client:
            await protocol.send_image(client, data, compressed=False)
        _LOGGER.debug("%s image uploaded (%d bytes)", self.address, len(data))

    async def async_send_raw_command(
        self, payload: bytes, *, expect_response: bool = True
    ) -> None:
        """Write arbitrary bytes to the command characteristic.

        Only for working out an encoding the document leaves open; nothing
        in the integration uses this path.
        """
        async with self.connection() as client:
            await protocol.send_command(client, payload, response=expect_response)
        _LOGGER.warning(
            "Raw command sent to %s: %s (response=%s)",
            self.address,
            payload.hex(" "),
            expect_response,
        )

    async def async_probe(self) -> dict[str, Any]:
        """Collect a full diagnostic report.

        Never raises: a connection that fails is the very thing we want to
        see, and the advertisement section stays useful without one.
        """
        report: dict[str, Any] = {}
        try:
            ble_device = await self._async_wait_for_connectable()
            async with self._lock:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.address,
                    timeout=CONNECT_TIMEOUT,
                )
                try:
                    report = await protocol.probe_device(client)
                finally:
                    await client.disconnect()
            report["connection"] = "ok"
        except Exception as err:  # noqa: BLE001 - the report is the deliverable
            _LOGGER.warning("Probe of %s could not connect: %s", self.address, err)
            report["connection"] = "failed"
            report["connection_error"] = f"{type(err).__name__}: {err}"

        report["address"] = self.address
        report["model"] = self.model
        report["write_mode_option"] = self.entry.options.get(
            CONF_WRITE_MODE, DEFAULT_WRITE_MODE
        )
        report["advertisement"] = {
            "raw_by_company_id": dict(self.state.advert_raw),
            "decoded": dict(self.state.advert_decoded),
        }
        self.state.last_probe = report
        return report

    async def async_refresh_multi(self, index_a: int, index_b: int) -> None:
        """Switch between stored multi-screen images (section 3.10)."""
        async with self.connection() as client:
            await protocol.refresh_multi(client, index_a, index_b)


class _ESLConnection:
    """Async context manager that connects, unlocks and disconnects again.

    A single lock serialises access so entity presses, service calls and the
    coordinator poll never fight over the same label.
    """

    def __init__(self, device: ESLDevice) -> None:
        self._device = device
        self._client: BleakClientWithServiceCache | None = None

    async def __aenter__(self) -> BleakClientWithServiceCache:
        device = self._device
        await device._lock.acquire()
        try:
            ble_device = await device._async_wait_for_connectable()
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                device.address,
                timeout=CONNECT_TIMEOUT,
            )
        except Exception:
            device._lock.release()
            raise

        self._client = client
        try:
            await protocol.unlock(client)
        except Exception:
            await client.disconnect()
            device._lock.release()
            raise
        return client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._client is not None:
                await self._client.disconnect()
        finally:
            self._device._lock.release()
