# ATC OTA v0.4 — BTHome-first design (prototype)

Status: **v0.4.0 implementation in progress**. BTHome inventory and native battery
are now used by the runtime. GATT metadata and OTA remain in the established
transport while the legacy manager is being decomposed in subsequent PRs.

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

1. **v0.4.0:** runtime uses only configured native BTHome thermometers,
   mirrors battery without another decoder, removes its duplicate battery
   entity and all automatic BLE/GATT scans. OTA controls attach to BTHome
   devices, with a fresh GATT Battery Level read before firmware transfer.
2. Validate the new BTHome inventory, GATT preflight, and same-version OTA on
   real ESPHome Bluetooth proxies.
3. Remove unused legacy BTHome callback/metadata probe code, then decompose
   the manager's metadata, connection and session services without changing
   the Telink wire protocol.
4. Keep verbose BLE diagnostics out of the default device UI.
5. Introduce HA-level runtime tests for startup timing and entity changes.

**Radio constraint:** A -90/-95 dBm advertisement is not a working GATT
connection. The app does not promise to overcome a weak bidirectional link.

**Gate:** each PR must pass real behavior tests; runtime changes additionally
require hardware validation. No automatic stable HACS release from merging.
