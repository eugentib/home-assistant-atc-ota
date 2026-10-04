# Changelog

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
