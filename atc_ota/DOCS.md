# ATC OTA over ESPHome

## Requirements

1. Home Assistant OS/Supervised with Apps/Add-ons support.
2. At least one ESP32 running ESPHome with Bluetooth Proxy enabled and active connections available:

```yaml
bluetooth_proxy:
  active: true
```

3. The ESPHome integration for the proxy should already be configured and loaded in Home Assistant for automatic discovery.
4. The target thermometer must already run pvvx/ATC firmware exposing the Telink OTA service.

## Configuration

### `auto_discover_proxies`

Default: `true`.

When enabled, the app asks Home Assistant for its loaded ESPHome integrations, obtains their Native API encryption keys through the internal Home Assistant WebSocket API, matches them with `_esphomelib._tcp.local.` mDNS services, and probes each ESPHome node.

Only nodes reporting Bluetooth Proxy support with **active GATT connections** are used for thermometer reads and OTA. Multiple compatible proxies are registered simultaneously.

### `proxy_address`

Manual fallback address. It is only used when automatic discovery is disabled or when no automatically discovered proxy is usable. Example: `192.168.1.45` or `btproxy1.local.`.

### `proxy_noise_psk`

Manual fallback ESPHome `api.encryption.key`. It is normally unnecessary when automatic discovery succeeds because the app obtains the stored key from Home Assistant. Leave empty for an unencrypted manual fallback API.

### `scan_seconds`

BLE scan duration in seconds. Default: `8`.

### `publish_to_home_assistant`

When enabled, the app publishes cached thermometer firmware/update information into Home Assistant through the internal Core API proxy. No long-lived access token is required.

### `catalog_refresh_hours`

How often the app checks the upstream pvvx firmware catalog. This does not connect to or wake the thermometers. Default: `6` hours.

### `debug`

Enable verbose logging.

## Bluetooth Proxy discovery

The Web UI lists every loaded ESPHome node that could be matched and probed. Typical statuses are:

- **active GATT** — selected and usable for OTA;
- **passive only** — Bluetooth Proxy is present, but active connections are disabled;
- **not a BT proxy** — ESPHome API is reachable but no Bluetooth Proxy feature flags are reported;
- **not-found-mdns** — Home Assistant has the ESPHome integration but the app could not match it to a current mDNS endpoint;
- **probe-failed** — endpoint/key/network probing failed.

Use **Rediscover proxies** after adding/removing/reflashing an ESPHome proxy. The app will refuse to rebuild proxy connections while an OTA or full inventory pass is running.

If automatic discovery fails, the configured manual proxy is kept as a fallback so an existing installation remains usable.

## Device inventory

**Scan BLE** is passive and only listens for advertisements through all connected proxies.

**Read all candidates** first scans advertisements, then connects to every detected ATC/Xiaomi thermometer candidate one at a time. It reads and stores:

- configured BLE device name;
- model and serial;
- hardware revision;
- pvvx software version;
- firmware identifier and manufacturer;
- RSSI and last-seen time;
- latest supported stable pvvx version;
- whether an update is available.

The inventory is persisted in `/data/devices.json` and is restored after the app restarts. Weak/out-of-range devices can fail individually without aborting the whole inventory pass.

## Home Assistant entities

With `publish_to_home_assistant: true`, the app creates state entities such as:

- `sensor.atc_ota_a4_c1_38_51_5d_77_firmware`
- `binary_sensor.atc_ota_a4_c1_38_51_5d_77_update_available`
- `sensor.atc_ota_updates_available`

The firmware sensor contains device metadata as attributes. The binary sensor is `on` when the cached current version is older than the current stable pvvx version. The summary sensor contains the count and list of updatable devices.

These are state-machine entities published by the app, not full Entity/Device Registry entries. They are suitable for dashboards and automations. A future companion Home Assistant integration can provide native device registry and `update` entities.

## Firmware update

1. Start the app and open its Web UI.
2. Confirm that at least one proxy is shown as **connected / active GATT**.
3. Run **Read all candidates**, or select one thermometer from a passive scan.
4. Select a device.
5. For LYWSD03MMC, press **Update to stable ...** to download the official upstream image and flash it through the proxy selected by `habluetooth`.
6. Manual `.bin` upload remains available as a fallback.

The downloaded/uploaded image is validated for the Telink firmware marker (`KNLT`) before transmitting.

## Important notes

- Automatic upstream firmware selection is currently limited to LYWSD03MMC. Other supported Telink devices can still use manual `.bin` upload.
- GATT device-info reads and OTA are serialized so they do not compete for active BLE slots.
- With multiple ESPHome proxies, `habluetooth` chooses a connectable scanner based on the advertisements it has seen and available connection capacity; the app does not pin a thermometer to a specific proxy.
- The target firmware must use the classic Telink OTA service:
  - Service: `00010203-0405-0607-0809-0a0b0c0d1912`
  - Characteristic: `00010203-0405-0607-0809-0a0b0c0d2b12`
- If scanning works but GATT connection fails, confirm that at least one proxy has `bluetooth_proxy.active: true` and a free BLE connection slot.
- Do not interrupt power to the thermometer during OTA.
- This project remains experimental. Test first on a recoverable device.

### Automatic proxy host resolution

The app first queries Home Assistant for loaded ESPHome entries and their API encryption keys. It then resolves each node using, in order, ESPHome mDNS (when multicast is visible), the device registry `configuration_url`, and direct `<name>.local` candidates derived from Home Assistant names. Each candidate is verified against the expected ESPHome MAC before it is accepted. This avoids requiring multicast browsing inside the app container.


## Startup behavior

The Ingress Web UI starts immediately. Automatic ESPHome Bluetooth Proxy discovery/probing continues in the background; the proxy status section updates while it runs. You do not need to restart the app while it shows `discovering`.


## Battery level and OTA warning

The app reads Battery Service characteristic `0x2A19` during active GATT inventory/device reads. `low_battery_warning_percent` controls the warning threshold and defaults to `30`. Before automatic or manual OTA, the battery is refreshed; at or below the threshold the UI asks for explicit confirmation. If battery level cannot be read, OTA remains available and the battery is shown as unknown.

Home Assistant publishing also creates a battery sensor and a low-battery binary sensor for devices that expose a battery value. Upstream pvvx documentation recommends more than 40% battery for reliable LYWSD03MMC reflashing.
