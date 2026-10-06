# ATC OTA for Home Assistant — v0.3.7

## Compatibility notes

Home Assistant provides `bleak-retry-connector` through its built-in Bluetooth integration, so ATC OTA does not pin or install a second copy. Config-flow imports remain lightweight and Bluetooth runtime code is loaded only after the config entry is set up.

A Home Assistant custom integration for pvvx/ATC Telink thermometers such as **LYWSD03MMC**.

## v0.3.7 visible OTA lifecycle and live progress

- makes the firmware update entity read `in_progress` and `update_percentage` directly from the manager's live OTA state instead of mirroring them through cached `_attr_` values;
- adds visible **OTA progress** and **OTA status** diagnostic sensors so the current phase remains visible even when the Home Assistant update dialog does not render a progress bar;
- keeps Home Assistant's standard second-click guard: a second install request while the first is still running is intentionally rejected as already in progress;
- recalculates OTA readiness immediately after a GATT battery refresh, fixing stale combinations such as `Battery=28%` with `OTA readiness=weak_signal`;
- uses the centralized battery freshness helper throughout OTA preflight.

## v0.3.6 real BLE advertisement freshness

- fixes battery freshness after Home Assistant restart by using the Bluetooth observation timestamp (`BluetoothServiceInfoBleak.time`) instead of re-timestamping scanner history as "now";
- stops using the native BTHome entity's `last_reported` as freshness evidence because Home Assistant's passive processor suppresses unchanged entity writes;
- a battery percentage that stays at 30%/36% may therefore show an old UI "changed" time while fresh BLE advertisements continue normally;
- scanner-history replay preserves the original observation age, so an old Battery packet stays stale after restart;
- fresh BTHome advertisement packets and GATT reads remain the only sources that satisfy the OTA battery freshness gate.

## v0.3.5 reconcile battery with native Home Assistant BTHome

- uses the already-published native BTHome Battery entity as the preferred battery source when available;
- resolves the BTHome battery entity through the entity registry/config entry instead of assuming an entity_id;
- uses Home Assistant `last_reported` to determine battery freshness, so an unchanged 30% value can still be recognized as recently reported;
- falls back to direct BTHome advertisement parsing and then GATT when native BTHome state is unavailable;
- exposes battery `source`, `age_seconds` and `last_seen` in ATC OTA diagnostics.

## v0.3.4 inclusive OTA battery minimum

- treats the configured battery threshold as the minimum allowed value;
- with the default 30% threshold, 30% is allowed and 29% is blocked;
- aligns OTA readiness, the low-battery binary sensor and firmware messages with the same rule.

## v0.3.3 align live battery parsing with Home Assistant BTHome

- uses the same `bthome-ble==3.23.4` parser as Home Assistant's native BTHome integration for live battery advertisements;
- keeps the built-in ATC parser only as a compatibility fallback;
- fixes stale ATC OTA battery values that could differ from the BTHome device page;
- after Home Assistant restart, cached battery data is no longer exposed as a live Battery/Low battery entity until a fresh BTHome or GATT reading arrives;
- adds regression coverage with a pvvx BTHome v2 payload carrying `Battery=29%`.

## v0.3.2 record the actual OTA route used

- records the scanner/proxy Home Assistant actually selected after the BLE connection succeeds, rather than inferring the route from pre-connect history;
- adds a **Last OTA route** diagnostic entity with RSSI, result, timestamp and detail;
- persists the last OTA route/result across Home Assistant restarts;
- exposes the last actual route in Firmware attributes, release notes and diagnostics;
- route inspection is best-effort only and cannot make an OTA fail if Home Assistant internals change.

## v0.3.1 route freshness and friendlier proxy visibility

- adds visible **Broadcast source** and **OTA route** diagnostic entities;
- treats a connectable-history route older than 60 seconds as `stale_gatt_route` instead of `ready`;
- checks ESPHome's runtime Bluetooth Proxy feature flags and exposes whether the selected route actually advertises `ACTIVE_CONNECTIONS`;
- reports `no_active_gatt` if an ESPHome route is present but does not support active GATT connections;
- Firmware release notes now include the live OTA preflight: readiness, battery, broadcast source, OTA/GATT route, route age, active-GATT capability, recent failures and BLE slots.

## v0.3.0 native device management and OTA readiness

v0.3.0 turns the v0.2.x OTA engine into a clearer Home Assistant device-management experience while keeping Home Assistant in full control of Bluetooth routing.

- separates passive **Broadcast RSSI / proxy** from the preferred **GATT RSSI / proxy** used for active connections and OTA;
- exposes Home Assistant connectable-route diagnostics: route age, recent connection failures and BLE connection slots;
- adds an **OTA readiness** diagnostic entity with stable states such as `ready`, `weak_signal`, `poor_signal`, `no_gatt_route`, `gatt_busy`, `low_battery` and `unstable_route`;
- the Firmware update entity now summarizes OTA readiness and the GATT route instead of showing only the strongest broadcast RSSI;
- marks all ATC OTA entities as push-based (`should_poll = false`);
- stops persisting volatile BLE observations such as RSSI, scanner route, last-seen timestamps and runtime readiness;
- only schedules an inventory storage write when durable inventory data actually changes;
- adds pure readiness tests plus architecture regression coverage;
- retains the useful v0.1.x distinction between passive-only reception and active-GATT capability without reconnecting directly to ESPHome or handling proxy API keys itself.

Typical device view:

```text
Firmware        5.8 → 5.9
Battery         67%
Broadcast RSSI  -52 dBm       tesla-ble
GATT RSSI       -82 dBm       btproxy2
OTA readiness   ready
```

A passive scanner may therefore be the strongest broadcast source while a different Home Assistant Bluetooth Proxy is the actual GATT/OTA route.

## v0.2.13 verify OTA before reporting success

- removes the optimistic assignment that marked the target firmware as installed immediately after sending the final OTA command;
- waits for the thermometer to reboot and confirms the target version from an explicit BTHome firmware-version advertisement or a GATT metadata read;
- retries GATT verification up to three times after reboot;
- reports the update as failed/unverified if the device still reports the old version or cannot be verified;
- allows explicit BTHome firmware-version objects to refresh `current_version` after an upgrade instead of only filling it when empty.

## v0.2.12 faster Home Assistant startup

- moves catalog refresh, automatic metadata probes and post-OTA refresh work to Home Assistant background tasks so they do not hold the startup task barrier;
- replaces one inventory storage write per BLE advertisement with a 1.5-second debounced background save;
- keeps explicit user-triggered metadata/OTA persistence awaited where durability matters;
- reduces Bluetooth/GATT startup work from affecting the "Wrapping up startup" phase.

## v0.2.11 preserve Home Assistant BLE routing history

- stops clearing Home Assistant's Bluetooth advertisement/connectable history after metadata reads and OTA attempts;
- preserves the connectable route that Home Assistant needs for subsequent GATT connections;
- when no connectable route is currently cached, requests an 8-second active scan and waits briefly for the route to repopulate before failing;
- fixes follow-up OTA attempts that could report `unknown (never seen by any scanner)` immediately after a previous failed connection.

## v0.2.10 ESPHome GATT congestion handling

- handles ESP-IDF `ESP_GATT_CONGESTED` (143 / `0x8F`) during Telink OTA writes with bounded exponential backoff;
- adds a small pacing delay between firmware blocks so ESPHome Bluetooth Proxy does not receive thousands of write-without-response packets as fast as the host can enqueue them;
- applies the same congestion retry to the OTA start commands, synchronization reads and final command;
- reports congestion retries in `ota_message` instead of aborting immediately on the first transient GATT congestion error.

## v0.2.9 Home Assistant progress state and Bluetooth routing

- uses Home Assistant's native `_attr_in_progress` / `_attr_update_percentage` state so the service guard and the UI read the same progress state;
- removes custom progress properties that could diverge from Home Assistant's internal update lifecycle after a failed install;
- delegates GATT proxy/backend selection entirely to Home Assistant instead of manually iterating scanner routes;
- Home Assistant now chooses among connection-capable routes using its own scoring for RSSI, failures, active connections and free slots;
- advertisement-only scanners may still be the strongest source for BTHome broadcasts but are not used for OTA GATT connections;
- connection setup gets two attempts through Home Assistant's native routing before the OTA fails.

## v0.2.8 firmware cache and update lifecycle

- firmware binaries are cached persistently under `/config/.storage/atc_ota_firmware/` after the first successful download;
- cached images are validated before reuse; corrupt or oversized cache entries are discarded and downloaded again;
- the firmware entity exposes `firmware_source`, `firmware_cache_file` and `firmware_sha256` diagnostics;
- `in_progress` now covers the complete update operation, including battery checks, metadata/catalog refresh and firmware preparation, not only the BLE flash loop;
- `ota_message` reports preflight stages such as battery checking, cache use/download and connection/transfer progress;
- a per-device install guard keeps Home Assistant's "already in progress" state consistent with the entity state.

## v0.2.7 battery/OTA fix

- fixes a BTHome parser bug where `Power = On` (`0x10 0x01`) followed by `Opening = Open` (`0x11 0x01`) could be misread as a fake **17%** battery value;
- removes the unsafe raw-byte battery fallback and parses battery only as a real BTHome `0x01` object;
- understands the standard BTHome binary-object range used by pvvx;
- if a pre-OTA active scan does not obtain a fresh battery advertisement, ATC OTA tries the standard Battery Level GATT characteristic before refusing the update;
- a battery value restored from persistent storage is no longer trusted for OTA until reconfirmed after restart.

## Why the v0.2.x integration is different

v0.1.x was a Home Assistant app/add-on that opened its own ESPHome API subscriptions to Bluetooth Proxy nodes. v0.2.x is a **custom integration inside Home Assistant Core** and uses Home Assistant's existing Bluetooth manager instead.

That means:

- no `proxy_address`;
- no ESPHome encryption key handling;
- no direct/parallel subscription to ESPHome Bluetooth Proxy nodes;
- no separate Bleak scanner;
- advertisements, proxy selection and connection slots remain owned by Home Assistant;
- OTA uses the same `BLEDevice` routing Home Assistant gives other Bluetooth integrations.

## v0.2.6 Home Assistant name sync

ATC OTA now looks up the existing Home Assistant/BTHome device for the same Bluetooth address and mirrors its user-assigned device name. A device renamed in Home Assistant, for example **Dormitor**, **Printer**, or **Afara**, is therefore shown with that name in ATC OTA instead of only `ATC_xxxxxx`. The firmware-advertised BLE name is kept separately for diagnostics.

The **Device Area** is mirrored as well. If the existing BTHome/Bluetooth device is assigned to an HA area such as **Afara**, **Dormitor** or **Living**, the corresponding ATC OTA device is assigned to the same area. Later area changes on the source device are picked up on the next Bluetooth advertisement (or after restart). The source HA device is treated as authoritative for area placement.

## Features

- automatic discovery of likely pvvx/ATC thermometers;
- passive BTHome v2 battery reading;
- strongest RSSI/proxy observation from Home Assistant's scanner history;
- GATT read of device name, model, hardware and current firmware version;
- pvvx stable firmware catalog lookup;
- native Home Assistant `update.*` firmware entity with progress;
- battery sensor and low-battery binary sensor;
- per-device **Refresh firmware info** button;
- configurable low-battery threshold (default 30%);
- OTA requires a recent battery reading; if battery is unknown/stale or below the configured threshold, flashing is refused before the Telink OTA session starts;
- serialized metadata reads and OTA jobs;
- all GATT clients disconnect in `finally` blocks.

## Migration from v0.1.x

**Stop the old ATC OTA app/add-on before enabling v0.2.6.** The old app should not run alongside this integration.

The add-on inventory cannot be imported automatically because app `/data` is isolated from Home Assistant Core. Devices are re-discovered from Home Assistant's Bluetooth history and advertisements.

## Manual installation

Copy:

```text
custom_components/atc_ota/
```

into:

```text
/config/custom_components/atc_ota/
```

Then restart Home Assistant Core and open:

**Settings → Devices & services → Add integration → ATC OTA**

The integration may also appear automatically as a discovered integration when a matching thermometer advertises.

## HACS custom repository

After pushing this repository to GitHub, add it to HACS as a **Custom repository → Integration**, then install **ATC OTA** and restart Home Assistant.

## Entities per thermometer

Typical entities:

```text
update.test1_firmware
sensor.test1_battery
sensor.test1_bluetooth_rssi
binary_sensor.test1_low_battery
button.test1_refresh_firmware_info
```

The update entity reports installed/latest versions, OTA progress, battery, strongest RSSI/proxy and the last metadata error.

## Battery safety

The default OTA battery minimum is **30%** and can be changed under the integration's Options. v0.2.7 requires a recent battery reading before flashing and refuses OTA when the value is unknown/stale or below the threshold. There is no double-click bypass; lowering the configured threshold is the explicit override.

## Bluetooth architecture

```text
ESPHome Bluetooth Proxy ─┐
ESPHome Bluetooth Proxy ─┼─> Home Assistant Bluetooth manager ─> ATC OTA
local Bluetooth adapter ─┘
```

ATC OTA never creates an ESPHome API connection itself.

## Current scope

Automatic upstream firmware selection is intentionally limited to verified **LYWSD03MMC** mappings. The classic Telink OTA framing remains compatible with the pvvx OTA service used by the v0.1.x prototype.
