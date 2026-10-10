# ATC OTA v0.4 — BTHome-first design (prototype)

Status: **proposal / non-runtime prototype**. Neither v0.3.18 nor any deployed
OTA behavior changes in this branch. Keep the v0.3.17/v0.3.18 rollback path.

## Scope

Initially support only the Xiaomi LYWSD03MMC / pvvx thermometers with
`A4:C1:38:xx:xx:xx` addresses that the user has *already configured in the
native Home Assistant BTHome integration*. Do not show nearby unconfigured
devices or duplicate BTHome temperature/humidity/battery entities.

## Target components

| Component | Owns | Avoids |
|---|---|---|
| Native HA BTHome | Configured-device identity, name, decoded battery and other passive sensors | ATC-specific OTA state |
| `bthome_inventory.py` | Read-only map from BTHome config entry + sensor registry to MAC/battery | BLE decoding, polling, GATT, persistence, OTA |
| HA Bluetooth + ESPHome | Connectable BLEDevice, proxy allocation, connection backend | Per-integration proxy selection |
| `metadata.py` (future) | One-shot GATT metadata read, **only when required** | Background scanning every sensor |
| `ota_transport.py` | Proven Telink OTA write protocol | HA entities and firmware download |
| `ota_session.py` (future) | Serialized preflight, connection, transfer, post-reboot verification | Multiple independent GATT retry paths |
| `firmware.py` + cache | Catalog and verified image | Own BLE state |
| Thin HA entities | Install, Refresh, OTA status/progress, availability | Independent duplicate sensor readings |

Keep one globally serialized OTA session initially, with deterministic
`idle -> checking -> connecting -> transferring -> verifying -> success/failed`
states. A refresh should fail fast with "OTA running" instead of waiting for
the global GATT lock. Don't automatically retry *after* firmware block writes
have begun. Always verify a same-version reinstallation through a **fresh**
post-OTA GATT firmware revision read.

## Native BTHome battery is NOT proof of safety freshness

The state of a BTHome battery entity can survive HA restarts and may be
unchanged for a long time. Its `last_changed` or `last_updated` timestamp
is **not proof that the battery was present in the last BLE packet**.
Therefore `bthome_inventory.py` purposely returns only a numerical level
and does NOT mark the value fresh.

Before wiring it to OTA, establish a documented battery authorization rule:
1. Use the BTHome entity as the **single source of displayed battery level**.
2. Require a recent Bluetooth observation for device presence, but do not
   misrepresent observation freshness as fresh battery sampling.
3. Where possible, read the standard Battery Level GATT characteristic during
   the *already required OTA connection* before issuing Telink start commands.
4. For devices without that characteristic, define and test a conservative
   age/freshness policy before allowing OTA. Never silently allow unknown
   or stale battery.
5. Preserve the current 30% minimum and deny OTA when safety cannot be
   established. No hidden fallback to a persisted battery value.

## Physical radio realities

Receiving BTHome advertisements at -90/-95 dBm does not demonstrate that
two-way GATT will work. HA's `connectable=True` BLE history is a candidate
route, not a successful connection. Test GATT with a nearby proxy before
attributing timeouts to the OTA protocol.

## Safe migration stages

1. **This PR:** independent, read-only BTHome inventory/battery mapping and
   behavior tests; no manager changes, no OTA changes, no release.
2. Add HA-facing integration tests for entity registry changes, disabled
   battery sensors, startup ordering and incomplete BTHome data.
3. Switch inventory/UI to configured BTHome devices and remove redundant
   battery / advertisement parsing *only after* proving correct mapping.
4. Introduce one GATT connection/session coordinator, eliminate automatic
   background metadata GATT and competing refresh tasks.
5. Move install orchestration into `ota_session.py`, keeping the
   proven `ota_transport.py` packet transport unchanged.
6. Reduce UI to Install, Refresh, Firmware, OTA status/progress, Readiness.
   Keep verbose BLE details in downloadable diagnostics, not 8+ entities.
7. Run physical same-version OTA and post-reboot GATT verification, compare
   intermediate percentages with baseline, then manually publish a release.

**Gate:** each PR must pass real behavior tests; runtime changes additionally
require hardware validation. No automatic stable HACS release from merging.
