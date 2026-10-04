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

Address of the ESPHome proxy. Start with its IP address if `.local` name resolution does not work from the container. Example:

```text
192.168.1.45
```

or:

```text
btproxy1.local.
```

### `proxy_noise_psk`

If the ESPHome API uses encryption, paste the same `api.encryption.key` value here. Leave empty for an unencrypted API.

### `scan_seconds`

BLE scan duration in seconds. Default: `8`.

### `debug`

Enable verbose logging.

## Usage

1. Start the app and open its Web UI.
2. Wait for **Proxy connected**.
3. Press **Scan BLE**.
4. Select the thermometer or enter its MAC address manually.
5. Choose a pvvx/ATC Telink `.bin` firmware file.
6. Press **Start OTA**.
7. Keep the target close to the selected Bluetooth Proxy until the operation finishes.

The app validates the Telink firmware marker (`KNLT`) before transmitting.

## Important notes

- This first release supports one explicitly configured ESPHome proxy.
- The target firmware must use the classic Telink OTA service:
  - Service: `00010203-0405-0607-0809-0a0b0c0d1912`
  - Characteristic: `00010203-0405-0607-0809-0a0b0c0d2b12`
- If scanning works but GATT connection fails, confirm that the proxy has `bluetooth_proxy.active: true` and at least one free BLE connection slot.
- Do not interrupt power to the thermometer during OTA.
- Version `0.1.0` is experimental. Test first on a recoverable device.
