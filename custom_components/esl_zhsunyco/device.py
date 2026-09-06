"""Connection and state handling for a single ESL label."""

from __future__ import annotations

import asyncio
import contextlib
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
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    CONF_ADDRESS,
    CONF_LINGER_S,
    CONF_MODEL,
    CONF_SCAN_INTERVAL_MIN,
    CONF_WRITE_MODE,
    DEFAULT_LINGER_S,
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
    UUID_COMMAND,
    UUID_SECURITY,
    UUID_STATUS,
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

# The advertisement callback is the normal path. This re-reads the state
# Home Assistant already holds, so one missed callback cannot leave every
# entity blank until the next connectable poll, which may be hours away.
SEED_INTERVAL = timedelta(minutes=5)


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

    # What the last command did, so a button press is not a silent event.
    last_command: dict[str, Any] | None = None

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
        # A live connection kept between commands, plus the timer ending it.
        self._client: BleakClientWithServiceCache | None = None
        self._cancel_linger: CALLBACK_TYPE | None = None
        self._cancel_seed: CALLBACK_TYPE | None = None

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
    def linger_seconds(self) -> int:
        """How long to hold the connection open after a command."""
        return int(self.entry.options.get(CONF_LINGER_S, DEFAULT_LINGER_S))

    @property
    def connected(self) -> bool:
        """Whether a connection is currently being held open."""
        return self._client is not None and self._client.is_connected

    @callback
    def _cancel_linger_timer(self) -> None:
        """Stop a pending disconnect."""
        if self._cancel_linger is not None:
            self._cancel_linger()
            self._cancel_linger = None

    @callback
    def _schedule_linger(self) -> None:
        """Disconnect after the linger period, unless another command lands."""
        self._cancel_linger_timer()
        if self.linger_seconds <= 0:
            self.hass.async_create_task(self._async_disconnect())
            return

        def _fire(_now) -> None:
            self.hass.async_create_task(self._async_disconnect())

        self._cancel_linger = async_call_later(self.hass, self.linger_seconds, _fire)

    async def _async_disconnect(self) -> None:
        """Close the held connection."""
        async with self._lock:
            client, self._client = self._client, None
            if client is None:
                return
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001 - teardown is best effort
                _LOGGER.debug("Disconnect of %s failed: %s", self.address, err)
            else:
                _LOGGER.debug("%s disconnected after linger", self.address)

    @callback
    def _on_disconnected(self, _client) -> None:
        """Forget the client after the label or the proxy dropped the link."""
        self._client = None

    @property
    def write_response(self) -> bool | None:
        """ATT write type for commands, or None to let bleak decide."""
        mode = self.entry.options.get(CONF_WRITE_MODE, DEFAULT_WRITE_MODE)
        if mode == WRITE_MODE_RESPONSE:
            return True
        if mode == WRITE_MODE_NO_RESPONSE:
            return False
        return None

    @callback
    def _seed_from_stack(self) -> bool:
        """Take whatever the Bluetooth stack already holds for this address.

        The advertisement callback is the normal path, but relying on it alone
        means one missed registration leaves every entity blank. This is a
        cheap read of state Home Assistant already has.
        """
        service_info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=False
        )
        if service_info is None:
            return False
        self._apply_service_info(service_info)
        return True

    @callback
    def _periodic_seed(self, _now) -> None:
        """Refresh from the Bluetooth stack on a timer."""
        if self._seed_from_stack():
            self.coordinator.async_update_listeners()

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
            # connectable=False on purpose: without it the matcher defaults to
            # requiring a connectable scanner, and advertisements seen only by
            # a passive one would never reach us. Battery and version need no
            # connection, so there is nothing to gain from that restriction.
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=False),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

        self._seed_from_stack()
        self._cancel_seed = async_track_time_interval(
            self.hass, self._periodic_seed, SEED_INTERVAL
        )

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
        """Stop the passive listener and close any held connection."""
        if self._unregister_advert is not None:
            self._unregister_advert()
            self._unregister_advert = None
        if self._cancel_seed is not None:
            self._cancel_seed()
            self._cancel_seed = None
        self._cancel_linger_timer()
        await self._async_disconnect()

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

    def bluetooth_report(self) -> dict[str, Any]:
        """Report what Home Assistant's Bluetooth stack knows about this label.

        Purely passive. This separates "Home Assistant never sees the label"
        from "Home Assistant sees it but our callback is not firing", which
        are the two very different reasons entities can go unavailable.
        """
        report: dict[str, Any] = {
            "callback_registered": self._unregister_advert is not None,
            "scanners_total": bluetooth.async_scanner_count(
                self.hass, connectable=False
            ),
            "scanners_connectable": bluetooth.async_scanner_count(
                self.hass, connectable=True
            ),
            "learned_advertising_interval_s": (
                bluetooth.async_get_learned_advertising_interval(
                    self.hass, self.address
                )
            ),
        }

        for label, connectable in (("any", False), ("connectable", True)):
            info = bluetooth.async_last_service_info(
                self.hass, self.address, connectable=connectable
            )
            report[f"last_service_info_{label}"] = (
                {
                    "name": info.name,
                    "rssi": info.rssi,
                    "source": info.source,
                    "manufacturer_data": {
                        f"0x{cid:04X}": bytes(payload).hex(" ")
                        for cid, payload in info.manufacturer_data.items()
                    },
                }
                if info is not None
                else None
            )
            report[f"address_present_{label}"] = bluetooth.async_address_present(
                self.hass, self.address, connectable=connectable
            )
            report[f"ble_device_{label}"] = (
                bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=connectable
                )
                is not None
            )

        try:
            report["scanners_seeing_this_label"] = [
                {
                    "source": scanner_device.scanner.source,
                    "name": scanner_device.scanner.name,
                    "connectable": scanner_device.scanner.connectable,
                }
                for scanner_device in bluetooth.async_scanner_devices_by_address(
                    self.hass, self.address, connectable=False
                )
            ]
        except Exception as err:  # noqa: BLE001 - diagnostics must not fail
            report["scanners_seeing_this_label"] = f"{type(err).__name__}: {err}"

        return report

    # ------------------------------------------------------------------
    # Active path: connections
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True when the label was heard from recently or last poll worked."""
        # A connected peripheral stops advertising, so a held connection has
        # to count as available or the entities would drop out mid-session.
        if self.connected:
            return True
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
        # Cheap and connection free; keeps the sensors alive even if the
        # advertisement callback is not delivering for some reason.
        self._seed_from_stack()

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

    def connection(self, wait: int = ADVERTISEMENT_WAIT_S) -> _ESLConnection:
        """Return an async context manager holding an unlocked connection."""
        return _ESLConnection(self, wait)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @callback
    def _record_command(
        self, kind: str, *, ok: bool, detail: str | None = None
    ) -> None:
        """Remember how the last command went, for the diagnostics report."""
        self.state.last_command = {
            "kind": kind,
            "at": dt_util.utcnow().isoformat(),
            "result": "ok" if ok else "failed",
            "detail": detail,
        }

    async def _run_command(self, kind: str, action) -> None:
        """Run one command, recording the outcome either way."""
        try:
            async with self.connection() as client:
                await action(client)
        except Exception as err:
            self._record_command(kind, ok=False, detail=f"{type(err).__name__}: {err}")
            raise
        self._record_command(kind, ok=True)

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

        async def _send(client) -> None:
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

        await self._run_command("set_rgb", _send)

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
        await self._run_command(
            "clear_screen",
            lambda client: protocol.clear_screen(client, response=self.write_response),
        )
        _LOGGER.debug("%s screen cleared", self.address)

    async def async_send_image(self, request: ImageRequest) -> None:
        """Render and upload a full screen image (sections 3.1 - 3.2)."""
        from .imaging import render_image

        # The panel decides the default; an explicit encoding wins.
        request.pixel_format = self.pixel_format
        data = await self.hass.async_add_executor_job(
            render_image, request, self.width, self.height
        )
        await self._run_command(
            f"send_image ({len(data)} bytes)",
            lambda client: protocol.send_image(client, data, compressed=False),
        )
        _LOGGER.debug("%s image uploaded (%d bytes)", self.address, len(data))

    async def async_send_raw_command(
        self, payload: bytes, *, expect_response: bool = True
    ) -> None:
        """Write arbitrary bytes to the command characteristic.

        Only for working out an encoding the document leaves open; nothing
        in the integration uses this path.
        """
        await self.async_command_sweep([payload], response=expect_response)

    async def async_command_sweep(
        self,
        payloads: list[bytes],
        *,
        response: bool | None = None,
        settle: float = 1.5,
    ) -> dict[str, Any]:
        """Send several command encodings in one connection and watch status.

        Waking the label is the expensive part, so every candidate is tried
        inside a single connection. The status characteristic is the label's
        own feedback: if busy or the error code never move, the command was
        not understood. A disconnect right after one payload is a signal in
        itself, since the label drops writes it rejects.
        """
        if response is None:
            response = self.write_response

        results: list[dict[str, Any]] = []
        report: dict[str, Any] = {"address": self.address, "results": results}

        async def read_status_safe(client) -> dict[str, Any]:
            try:
                raw = bytes(await client.read_gatt_char(UUID_STATUS))
            except Exception as err:  # noqa: BLE001 - part of the observation
                return {"error": f"{type(err).__name__}: {err}"}
            return {
                "raw": raw[:8].hex(" "),
                "busy": bool(raw[0]) if raw else None,
                "error_code": raw[1] if len(raw) > 1 else None,
            }

        try:
            async with self.connection() as client:
                for payload in payloads:
                    entry: dict[str, Any] = {"payload": payload.hex(" ")}
                    entry["status_before"] = await read_status_safe(client)
                    try:
                        await protocol.send_command(client, payload, response=response)
                        entry["write"] = "ok"
                    except Exception as err:  # noqa: BLE001 - report, keep going
                        entry["write"] = f"{type(err).__name__}: {err}"

                    await asyncio.sleep(settle)
                    entry["status_after"] = await read_status_safe(client)
                    entry["status_changed"] = (
                        entry["status_before"] != entry["status_after"]
                    )
                    entry["connected_after"] = bool(
                        getattr(client, "is_connected", True)
                    )
                    results.append(entry)

                    if not entry["connected_after"]:
                        entry["note"] = (
                            "label dropped the connection after this payload"
                        )
                        break
        except Exception as err:  # noqa: BLE001 - the report is the deliverable
            report["error"] = f"{type(err).__name__}: {err}"

        report["any_status_changed"] = any(
            item.get("status_changed") for item in results
        )
        self.state.last_command = report
        _LOGGER.warning("ESL command sweep %s: %s", self.address, report)
        return report

    async def async_probe(self, wait: int = ADVERTISEMENT_WAIT_S) -> dict[str, Any]:
        """Collect a full diagnostic report.

        Never raises: a connection that fails is the very thing we want to
        see, and the advertisement section stays useful without one.
        """
        report: dict[str, Any] = {}
        try:
            async with self.connection(wait) as client:
                report = await protocol.probe_device(client)
            report["connection"] = "ok"
        except Exception as err:  # noqa: BLE001 - the report is the deliverable
            _LOGGER.warning("Probe of %s could not connect: %s", self.address, err)
            report["connection"] = "failed"
            report["connection_error"] = f"{type(err).__name__}: {err}"
            # Without a connection the report is much thinner than it looks,
            # and it is not obvious which answers are simply absent.
            report["sections_missing"] = {
                "reason": "these need a connection and the label was unreachable",
                "sections": [
                    "services",
                    "known_characteristics",
                    "protocol_family",
                    "unlock",
                    "reads",
                    "mtu_size",
                ],
            }

        report["address"] = self.address
        report["model"] = self.model
        report["write_mode_option"] = self.entry.options.get(
            CONF_WRITE_MODE, DEFAULT_WRITE_MODE
        )
        report["advertisement"] = {
            "battery_v": self.battery_v,
            "raw_by_company_id": dict(self.state.advert_raw),
            "decoded": dict(self.state.advert_decoded),
        }
        self.state.last_probe = report
        return report

    async def async_refresh_multi(self, index_a: int, index_b: int) -> None:
        """Switch between stored multi-screen images (section 3.10)."""
        async with self.connection() as client:
            await protocol.refresh_multi(client, index_a, index_b)


# The two characteristics every command path needs. A connection whose GATT
# table lacks them is unusable, and that happens with a stale service cache.
REQUIRED_CHARACTERISTICS = {"security": UUID_SECURITY, "command": UUID_COMMAND}


def _missing_characteristics(client: BleakClientWithServiceCache) -> list[str]:
    """Names of the characteristics this connection cannot offer."""
    return [
        name
        for name, uuid in REQUIRED_CHARACTERISTICS.items()
        if client.services.get_characteristic(uuid) is None
    ]


async def _async_close(client: BleakClientWithServiceCache, address: str) -> None:
    """Close a connection without letting teardown failures surface."""
    try:
        await client.disconnect()
    except Exception as err:  # noqa: BLE001 - teardown is best effort
        _LOGGER.debug("Disconnect of %s failed: %s", address, err)


class _ESLConnection:
    """Async context manager providing an unlocked connection.

    A single lock serialises access so entity presses, service calls and the
    coordinator poll never fight over the same label.

    The connection is NOT closed on exit. Reconnecting means waiting for the
    label to advertise again, which takes minutes and dominates everything
    else, so it is held open for the linger period instead and reused by any
    command that follows. The reference implementation for the sibling
    firmware does the same thing, keeping one connection for an entire
    transfer.
    """

    def __init__(self, device: ESLDevice, wait: int = ADVERTISEMENT_WAIT_S) -> None:
        self._device = device
        self._wait = wait

    async def __aenter__(self) -> BleakClientWithServiceCache:
        device = self._device
        await device._lock.acquire()
        client: BleakClientWithServiceCache | None = None
        try:
            device._cancel_linger_timer()

            # Reuse a live connection; the unlock is per connection and holds.
            existing = device._client
            if existing is not None and existing.is_connected:
                if not _missing_characteristics(existing):
                    _LOGGER.debug("%s reusing the open connection", device.address)
                    return existing
                # A held connection with an unusable table is worse than none.
                _LOGGER.debug(
                    "%s dropping the held connection, its GATT table is incomplete",
                    device.address,
                )
                await _async_close(existing, device.address)
            device._client = None

            # Two attempts: a cached GATT table can be stale, and the label
            # then reports "Characteristic ... was not found" for a
            # characteristic a probe has already seen. Clearing the cache and
            # rediscovering is the only way out of that.
            missing: list[str] = []
            for attempt in (1, 2):
                ble_device = await device._async_wait_for_connectable(self._wait)
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    device.address,
                    timeout=CONNECT_TIMEOUT,
                    disconnected_callback=device._on_disconnected,
                )
                if not (missing := _missing_characteristics(client)):
                    break

                _LOGGER.warning(
                    "%s: %s characteristic(s) missing on attempt %d, clearing the "
                    "service cache and rediscovering",
                    device.address,
                    ", ".join(missing),
                    attempt,
                )
                await client.clear_cache()
                await _async_close(client, device.address)
                client = None
            else:
                raise HomeAssistantError(
                    f"Label {device.address} did not expose its "
                    f"{', '.join(missing)} characteristic(s) even after the "
                    "service cache was cleared"
                )

            await protocol.unlock(client)
            device._client = client
            return client
        except BaseException:
            # BaseException, not Exception: asyncio.CancelledError is not an
            # Exception, so an outer timeout used to leave the lock held for
            # good and every later command hung. Worse, a connection opened
            # just before the cancellation stayed up, and a connected label
            # stops advertising, so the whole integration went dark.
            device._client = None
            if client is not None:
                # As a task, so it still runs while this one is cancelled.
                device.hass.async_create_task(_async_close(client, device.address))
            device._lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        device = self._device
        try:
            if exc_type is not None and device._client is not None:
                # A failed command may have left the link unusable; do not
                # hand a broken connection to whatever runs next.
                client, device._client = device._client, None
                with contextlib.suppress(Exception):
                    await client.disconnect()
            else:
                device._schedule_linger()
        finally:
            device._lock.release()
