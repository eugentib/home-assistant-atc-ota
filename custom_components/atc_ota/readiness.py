"""Pure OTA preflight/readiness evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OtaReadiness:
    state: str
    reason: str


def evaluate_ota_readiness(
    *,
    battery: int | None,
    battery_fresh: bool,
    low_battery_threshold: int,
    gatt_proxy: str | None,
    gatt_rssi: int | None,
    gatt_failures: int | None,
    gatt_free_slots: int | None,
) -> OtaReadiness:
    """Return a stable user-facing OTA preflight summary."""
    if battery is None or not battery_fresh:
        return OtaReadiness(
            "waiting_for_battery",
            "Waiting for a fresh battery advertisement",
        )
    if battery <= low_battery_threshold:
        return OtaReadiness(
            "low_battery",
            f"Battery {battery}% is at/below the {low_battery_threshold}% OTA minimum",
        )
    if gatt_proxy is None:
        return OtaReadiness(
            "no_gatt_route",
            "No connectable Home Assistant Bluetooth route is currently available",
        )
    if gatt_free_slots == 0:
        return OtaReadiness(
            "gatt_busy",
            f"{gatt_proxy} has no free BLE connection slots",
        )
    if gatt_rssi is not None and gatt_rssi <= -90:
        return OtaReadiness(
            "poor_signal",
            f"GATT route signal is very weak ({gatt_rssi} dBm)",
        )
    if gatt_rssi is not None and gatt_rssi <= -85:
        return OtaReadiness(
            "weak_signal",
            f"GATT route signal is weak ({gatt_rssi} dBm)",
        )
    if gatt_failures is not None and gatt_failures >= 2:
        return OtaReadiness(
            "unstable_route",
            f"{gatt_proxy} has {gatt_failures} recent connection failures",
        )
    return OtaReadiness(
        "ready",
        "Battery and connectable Bluetooth route look suitable for OTA",
    )
