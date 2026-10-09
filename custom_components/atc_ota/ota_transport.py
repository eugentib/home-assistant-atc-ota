"""Telink OTA transport: handshake, block transfer, BLE retries and finalization.

This mixin deliberately owns only the wire protocol and link lifecycle.
Firmware catalog lookup, preflight checks, persistent state and HA entity
updates remain in AtcManager. Extracted without changing OTA byte sequences.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.exceptions import HomeAssistantError

from .const import (
    OTA_BLOCK_PACING_SECONDS,
    OTA_CHAR_UUID,
    OTA_START_COMMAND_GAP_SECONDS,
    OTA_START_RECONNECT_ATTEMPTS,
    OTA_WRITE_RETRY_ATTEMPTS,
    OTA_WRITE_RETRY_BASE_SECONDS,
)
from .protocol import BLOCK_SIZE, make_block, make_finish, pad_firmware, validate_firmware

_LOGGER = logging.getLogger(__name__)


class OtaTransportMixin:
    """Bounded BLE operations and Telink OTA transfer sequence."""

    @staticmethod
    def _is_gatt_congested(exc: Exception) -> bool:
        """Return True only for the ESP-IDF GATT congestion status."""
        text = f"{type(exc).__name__}: {exc}".lower()
        return (
            "congested" in text
            or "error=143" in text
            or "error 143" in text
            or "0x8f" in text
        )

    @staticmethod
    def _is_link_lost(exc: Exception) -> bool:
        """Return True for connection loss before firmware data transfer."""
        text = f"{type(exc).__name__}: {exc}".lower()
        return any(
            token in text
            for token in (
                "not connected",
                "disconnected",
                "connection lost",
                "failed to connect",
                "unable to connect",
                "timeout waiting for connect",
            )
        )

    def _assert_battery_safe_for_ota(
        self, state: DeviceState, *, context: str
    ) -> None:
        """Hard guard battery freshness and threshold at a safety boundary."""
        self._update_ota_readiness(state)
        if state.battery is None or not self.battery_is_fresh(state):
            raise HomeAssistantError(
                f"OTA refused during {context}: a recent battery level is not available."
            )
        if state.battery < self.low_battery_threshold:
            raise HomeAssistantError(
                f"OTA refused during {context}: battery is {state.battery}% and the "
                f"configured minimum is {self.low_battery_threshold}%."
            )

    async def _ota_write(
        self,
        state: DeviceState,
        client,
        characteristic,
        payload: bytes,
        *,
        label: str,
        pacing: float = 0.0,
    ) -> None:
        """Write one OTA packet with bounded retry for ESP_GATT_CONGESTED."""
        delay = OTA_WRITE_RETRY_BASE_SECONDS
        for attempt in range(1, OTA_WRITE_RETRY_ATTEMPTS + 1):
            try:
                await client.write_gatt_char(characteristic, payload, response=False)
                if pacing > 0:
                    await asyncio.sleep(pacing)
                return
            except Exception as exc:  # noqa: BLE001
                if not self._is_gatt_congested(exc) or attempt >= OTA_WRITE_RETRY_ATTEMPTS:
                    raise
                state.ota_message = (
                    f"{label}: BLE congested, retry {attempt}/{OTA_WRITE_RETRY_ATTEMPTS}"
                )
                self._notify(state.address)
                _LOGGER.warning(
                    "BLE congestion while %s for %s; retry %s/%s after %.2fs",
                    label,
                    state.address,
                    attempt,
                    OTA_WRITE_RETRY_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 1.0)

    async def _ota_read_sync(self, state: DeviceState, client, characteristic) -> None:
        """Read the OTA characteristic, retrying only explicit BLE congestion."""
        delay = OTA_WRITE_RETRY_BASE_SECONDS
        for attempt in range(1, OTA_WRITE_RETRY_ATTEMPTS + 1):
            try:
                await client.read_gatt_char(characteristic)
                return
            except Exception as exc:  # noqa: BLE001
                if not self._is_gatt_congested(exc) or attempt >= OTA_WRITE_RETRY_ATTEMPTS:
                    raise
                state.ota_message = (
                    f"OTA sync read: BLE congested, retry {attempt}/{OTA_WRITE_RETRY_ATTEMPTS}"
                )
                self._notify(state.address)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 1.0)

    async def _flash(self, address: str, firmware: bytes, target_version: str) -> None:
        """Serialize OTA against metadata GATT reads."""
        async with self._gatt_lock:
            await self._flash_locked(address, firmware, target_version)

    async def _flash_locked(self, address: str, firmware: bytes, target_version: str) -> None:
        validate_firmware(firmware)
        padded = pad_firmware(firmware)
        block_count = len(padded) // BLOCK_SIZE
        if block_count > 0x10000:
            raise HomeAssistantError("Firmware image has too many Telink OTA blocks")

        state = self.devices[address]
        state.ota_progress = 4
        state.ota_message = "Preparing OTA"
        self._notify(address)

        client = None
        characteristic = None
        try:
            # It is safe to restart the Telink handshake before any firmware
            # block has been sent. This handles a proxy/link that connects and
            # immediately drops before or during the two start commands.
            for attempt in range(1, OTA_START_RECONNECT_ATTEMPTS + 1):
                try:
                    client = await self._establish_client(
                        address, state.name or address
                    )
                    (
                        state.last_ota_proxy,
                        state.last_ota_rssi,
                    ) = self._actual_connected_route(client, address)
                    state.last_ota_detail = (
                        f"Connected through {state.last_ota_proxy}"
                        + (
                            f" at {state.last_ota_rssi} dBm"
                            if state.last_ota_rssi is not None
                            else ""
                        )
                        if state.last_ota_proxy
                        else "Connected; exact Home Assistant route unavailable"
                    )
                    self._notify(address)

                    characteristic = client.services.get_characteristic(OTA_CHAR_UUID)
                    if characteristic is None:
                        raise HomeAssistantError(
                            "Compatible Telink OTA characteristic was not found"
                        )

                    # A fresh Battery advertisement can arrive while connection
                    # setup is in progress. Never send even the first OTA command
                    # if it has moved below the configured threshold.
                    self._assert_battery_safe_for_ota(
                        state, context="OTA start"
                    )

                    state.ota_progress = 5
                    state.ota_message = (
                        "Starting Telink OTA"
                        if attempt == 1
                        else (
                            "Starting Telink OTA "
                            f"(connection attempt {attempt}/{OTA_START_RECONNECT_ATTEMPTS})"
                        )
                    )
                    self._notify(address)

                    await asyncio.sleep(0.50)
                    state.ota_message = "Starting Telink OTA (phase 1/2)"
                    self._notify(address)
                    await self._ota_write(
                        state,
                        client,
                        characteristic,
                        b"\x00\xFF",
                        label="OTA start phase 1",
                    )
                    await asyncio.sleep(OTA_START_COMMAND_GAP_SECONDS)

                    state.ota_message = "Starting Telink OTA (phase 2/2)"
                    self._notify(address)
                    await self._ota_write(
                        state,
                        client,
                        characteristic,
                        b"\x01\xFF",
                        label="OTA start phase 2",
                    )
                    await asyncio.sleep(0.30)
                    break
                except Exception as exc:  # noqa: BLE001
                    if client is not None and client.is_connected:
                        try:
                            await asyncio.wait_for(client.disconnect(), timeout=8.0)
                        except Exception as disconnect_exc:  # noqa: BLE001
                            _LOGGER.debug(
                                "Disconnect before OTA-start retry failed for %s: %s",
                                address,
                                disconnect_exc,
                            )
                    client = None
                    characteristic = None

                    if (
                        attempt >= OTA_START_RECONNECT_ATTEMPTS
                        or not self._is_link_lost(exc)
                    ):
                        raise

                    state.ota_message = (
                        f"OTA start link lost; reconnecting "
                        f"({attempt + 1}/{OTA_START_RECONNECT_ATTEMPTS})"
                    )
                    self._notify(address)
                    _LOGGER.warning(
                        "OTA start connection lost for %s; retrying before any "
                        "firmware block was sent: %s",
                        address,
                        exc,
                    )
                    try:
                        await self.async_request_scan(2.0)
                    except Exception as scan_exc:  # noqa: BLE001
                        _LOGGER.debug(
                            "BLE scan before OTA-start retry failed for %s: %s",
                            address,
                            scan_exc,
                        )
                    await asyncio.sleep(0.50)

            if client is None or characteristic is None:
                raise HomeAssistantError("Unable to establish Telink OTA session")

            for block_number in range(block_count):
                start = block_number * BLOCK_SIZE
                packet = make_block(block_number, padded[start : start + BLOCK_SIZE])
                await self._ota_write(
                    state,
                    client,
                    characteristic,
                    packet,
                    label=f"OTA block {block_number + 1}/{block_count}",
                    pacing=OTA_BLOCK_PACING_SECONDS,
                )
                if (block_number + 1) % 8 == 0:
                    await self._ota_read_sync(state, client, characteristic)
                if (
                    block_number == 0
                    or (block_number + 1) % 32 == 0
                    or block_number + 1 == block_count
                ):
                    state.ota_progress = min(
                        98, 5 + int(((block_number + 1) / block_count) * 93)
                    )
                    state.ota_message = (
                        f"Sending block {block_number + 1}/{block_count}"
                    )
                    self._notify(address)
                await asyncio.sleep(0)

            await self._ota_write(
                state,
                client,
                characteristic,
                make_finish(block_count),
                label="OTA final command",
            )
            state.ota_progress = 99
            state.ota_message = "Final command sent; waiting for reboot"
            self._notify(address)
            await asyncio.sleep(0.5)
            state.last_metadata_error = None
        except Exception as exc:  # noqa: BLE001
            state.ota_message = f"OTA failed: {type(exc).__name__}: {exc}"
            _LOGGER.exception("OTA failed for %s", address)
            raise HomeAssistantError(state.ota_message) from exc
        finally:
            if client is not None and client.is_connected:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=8.0)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Disconnect failed after OTA for %s: %s", address, exc
                    )
            self._update_ota_readiness(state)
            self._notify(address)

