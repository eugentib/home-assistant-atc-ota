# Changelog

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
