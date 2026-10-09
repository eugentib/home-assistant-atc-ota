# ATC OTA architecture (v0.3.18)

The integration relies on Home Assistant for Bluetooth scanners, connectable-route
selection, and ESPHome proxy allocations. It does not maintain its own BLE stack.

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `manager.py` | Config-entry lifecycle, device inventory, BLE observations, route diagnostics, firmware catalog, metadata refresh, install orchestration and persistence |
| `ota_transport.py` | Telink OTA handshake, firmware block writes, congestion retries, connection retry before the first block, final packet and BLE teardown |
| `protocol.py` | Pure encoding of Telink OTA protocol packets |
| `bthome.py` | Pure BTHome advertisement parsing |
| `readiness.py` | OTA readiness policy |
| `firmware.py`, `firmware_cache.py` | Firmware selection, download, validation and persistent cache |
| `entity.py`, `sensor.py`, `button.py`, `update.py` | Home Assistant entity adapters; no direct GATT operations |

## Ownership and safety contracts

- `AtcManager` owns all state and the shared `_gatt_lock` and `_ota_lock`.
- `OtaTransportMixin` has no persistent state or HA entities; it accesses its
  manager through `self`, allowing a behavior-preserving extraction.
- `async_install_latest` performs safety preflight and catalog resolution,
  invokes `_flash`, and verifies installed firmware by a post-OTA GATT read.
- `_flash` owns the GATT lock during the actual transfer. Never release the
  lock during a partially transmitted firmware image.
- Automatic retry is only permitted before firmware block transfer begins.
  Do not restart a partially flashed OTA just to improve user-visible progress.
- HA update controls remain hidden from Settings dashboard alerts; separate
  visible button and OTA status/progress entities remain the interaction path.
- Volatile BLE observations and intermediate OTA percentages are not persisted.
  Successful completed-OTA results are persisted and restore **100%** at startup
  so the visible status and progress remain consistent.

## Next refactor slices

1. Move GATT connection/metadata reads to a dedicated service after adding
   runtime tests with a fake Home Assistant BLE backend.
2. Move BLE observation parsing/route diagnostics behind a single inventory API.
3. Replace source-string tests with behavioral tests and add simulated device
   failures (unavailable GATT route, congestion, dropped OTA connection).
4. Remove legacy commentary/version changelog from the main README after
   establishing a stable documentation structure.

This PR deliberately avoids a large rewrite of the running OTA protocol.
Only one low-level boundary is extracted per behavior-validation phase.
