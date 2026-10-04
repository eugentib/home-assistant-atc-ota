# ATC OTA over ESPHome

## Requirements

1. Home Assistant OS/Supervised with Apps/Add-ons support.
2. An ESP32 running ESPHome with Bluetooth Proxy enabled and active connections available:

```yaml
bluetooth_proxy:
  active: true
```

3. The ESPHome Native API must be reachable from the Home Assistant app container.
4. The target thermometer must already run pvvx/ATC firmware exposing the Telink OTA service.

## Configuration

### `proxy_address`

Address of the ESPHome proxy. Start with its IP address if `.local` name resolution does not work from the container. Example `192.168.1.45` or `btproxy1.local.`.

### `proxy_noise_psk`

If the ESPHome API uses encryption, paste the same `api.encryption.key` value here. Leave empty for an unencrypted API.

### `scan_seconds`

BLE scan duration in seconds. Default: `8`.

### `publish_to_home_assistant`

When enabled, the app publishes cached thermometer firmware/update information into Home Assistant through the internal Core API proxy. No long-lived access token is required.

### `catalog_refresh_hours`

How often the app checks the upstream pvvx firmware catalog. This does not connect to or wake the thermometers. Default: `6` hours.

### `debug`

Enable verbose logging.

## Device inventory

**Scan BLE** is passive and only listens for advertisements.

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
2. Wait for **Proxy connected**.
3. Run **Read all candidates**, or select one thermometer from a passive scan.
4. Select a device.
5. For LYWSD03MMC, press **Update to stable ...** to download the official upstream image and flash it through the configured proxy.
6. Manual `.bin` upload remains available as a fallback.

The downloaded/uploaded image is validated for the Telink firmware marker (`KNLT`) before transmitting.

## Important notes

- Automatic upstream firmware selection is currently limited to LYWSD03MMC. Other supported Telink devices can still use manual `.bin` upload.
- GATT device-info reads and OTA are serialized so they do not compete for an active BLE slot.
- The target firmware must use the classic Telink OTA service:
  - Service: `00010203-0405-0607-0809-0a0b0c0d1912`
  - Characteristic: `00010203-0405-0607-0809-0a0b0c0d2b12`
- If scanning works but GATT connection fails, confirm that the proxy has `bluetooth_proxy.active: true` and at least one free BLE connection slot.
- Do not interrupt power to the thermometer during OTA.
- This project remains experimental. Test first on a recoverable device.
