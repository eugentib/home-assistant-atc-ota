# Changelog

## 0.1.10

- Read standard BLE Battery Level (`0x2A19`) during GATT device information refresh.
- Show cached/refreshed battery percentage in the inventory table and selected-device details.
- Add configurable `low_battery_warning_percent` (default 30%).
- Refresh the battery immediately before automatic or manual OTA and require explicit confirmation at or below the threshold.
- Publish per-device battery and low-battery state sensors to Home Assistant.
- Include battery information in OTA logs and update-summary attributes.

## 0.1.7

## 0.1.9

- Fixed ESPHome Bluetooth Proxy friendly-name mapping after upgrading from older proxy caches by treating BLE scanner MAC/name changes as runtime configuration changes.
- RSSI and “Strongest proxy” now come from the strongest per-proxy observation in the current scan instead of habluetooth's sticky/arbitrated BLEDevice source.
- Preserves habluetooth route source/RSSI separately for diagnostics.

## 0.1.8

- Rename the BLE table's misleading `Detected by` column to `Format`.
- Show the actual ESPHome proxy selected by habluetooth for each BLE device.
- Show all connected proxies that heard a device and their per-proxy RSSI when available.
- Persist BLE scanner provenance in the device inventory.
- Make pvvx current-version detection tolerant of swapped Firmware/Software Revision strings.

- Cache the last-known-good auto-discovered ESPHome Bluetooth Proxy configuration in `/data/proxies.json`.
- Start cached proxy connections immediately on restart, while Home Assistant discovery/probing reconciles in the background.
- Keep working cached proxies if a transient discovery/DNS failure cannot confirm a replacement.
- Reduce ESPHome probe timeout from 12 seconds to 4 seconds and probe up to eight ESPHome nodes concurrently.
- Reduce multicast mDNS browse wait to 1.5 seconds.
- Ignore obviously invalid Home Assistant configuration hosts such as `app`, `supervisor`, and `localhost`.
- Avoid reconnecting all Bluetooth Proxy managers when discovery resolves to the same effective proxy set already loaded from cache.
- Suppress expected local BlueZ/DBus adapter warnings unless debug logging is enabled.
- Prevent `websockets` DEBUG frame logging from exposing the Home Assistant Supervisor token or ESPHome API encryption keys.

## 0.1.6

- Start the Home Assistant Ingress Web UI immediately instead of blocking app startup on ESPHome proxy discovery/probing.
- Run initial proxy discovery and manual **Rediscover proxies** requests as background tasks with live status in the UI.
- Keep the UI and `/health` endpoint available while proxies are still being queried and connected.
- Probe different ESPHome nodes concurrently with a bounded concurrency of four, reducing discovery time when one unrelated node is slow or unreachable.

## 0.1.5

- Fix automatic ESPHome proxy discovery when multicast mDNS browsing is unavailable inside the Home Assistant app container.
- Resolve hosts from Home Assistant device `configuration_url` and direct `<name>.local` candidates before falling back to manual configuration.
- Verify the ESPHome API responder MAC before accepting a derived hostname.
- Show how each proxy hostname was resolved.

## 0.1.4

- Automatically discover ESPHome integrations already configured in Home Assistant.
- Read each ESPHome Native API encryption key from Home Assistant instead of requiring it to be copied into the app configuration.
- Match ESPHome config entries to live `_esphomelib._tcp.local.` mDNS services by MAC address.
- Probe `bluetooth_proxy_feature_flags` and only auto-select nodes that support active BLE/GATT connections.
- Register all usable Bluetooth Proxies simultaneously, allowing `habluetooth` to route GATT operations through the best available scanner.
- Keep `proxy_address` and `proxy_noise_psk` as a manual fallback if automatic discovery is disabled or cannot find a usable proxy.
- Add **Rediscover proxies** to the Ingress UI and show discovered node, capability, ESPHome version and runtime connection status.
- Refuse proxy reconfiguration while an OTA or full inventory scan is active.

## 0.1.3

- Add persistent device inventory in `/data/devices.json`.
- Add **Read all candidates** inventory scan: BLE advertisements are scanned first, then thermometer candidates are connected sequentially and their GAP/Device Information data is cached.
- Keep cached name, model, hardware revision, current software version, RSSI, last seen and upstream stable firmware information across app restarts.
- Mark each cached device as **update available**, **current**, or unknown.
- Publish the inventory to Home Assistant through the built-in app-to-Core API proxy when `publish_to_home_assistant` is enabled.
- Create `sensor.atc_ota_<mac>_firmware` and `binary_sensor.atc_ota_<mac>_update_available` state entities for each thermometer.
- Create `sensor.atc_ota_updates_available` summary with the number/list of devices that can be updated.
- Refresh the upstream pvvx firmware catalog periodically without waking the thermometers; default every 6 hours.
- Refresh cached device information automatically after a successful OTA.
- Serialize GATT reads and OTA operations to avoid competing for the ESPHome proxy's active BLE slots.

## 0.1.2

- Read the selected thermometer's standard GAP/Device Information characteristics over the ESPHome Bluetooth Proxy.
- Show configured BLE device name, model, hardware revision, current software version, firmware identifier and manufacturer.
- Read the current pvvx stable firmware catalog directly from upstream `firmware.json`.
- For LYWSD03MMC, resolve the correct stable pvvx image and show the latest available version.
- Add one-click **Update to latest stable pvvx**: the app downloads the `.bin` directly from the official pvvx GitHub repository, validates the Telink `KNLT` marker, then starts OTA.
- Keep manual `.bin` upload as a fallback.

## 0.1.1

- Fix thermometer discovery through ESPHome Bluetooth Proxy when the BLE local name is missing.
- Detect pvvx/ATC devices by advertising service/service-data UUIDs as well as by name:
  - `0x181A` ATC/custom format
  - `0xFE95` Xiaomi MiBeacon
  - `0xFCD2` BTHome v2
- Show the detection reason in the BLE devices table.
- Keep "Show all" available for devices using an unknown/custom advertisement format.

## 0.1.0

- Initial experimental Home Assistant app/add-on.
- Connects to one ESPHome Bluetooth Proxy via `bleak-esphome`.
- BLE discovery UI.
- Manual Telink firmware upload.
- pvvx-compatible Telink OTA framing with CRC16/MODBUS.
- OTA progress and in-memory job log.
