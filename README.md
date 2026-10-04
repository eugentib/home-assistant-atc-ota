# ATC OTA over ESPHome

Experimental Home Assistant app/add-on for inventorying and updating pvvx/ATC Telink thermometers through ESPHome Bluetooth Proxies.

Current features:

- automatic discovery of ESPHome devices already configured in Home Assistant;
- automatic retrieval of their Native API encryption keys through the Home Assistant internal WebSocket API;
- capability probing so only ESPHome nodes with Bluetooth Proxy **active GATT connections** are selected;
- simultaneous registration of all usable proxies so `habluetooth` can route BLE connections through the best available scanner;
- optional manual proxy address/encryption key fallback;
- passive BLE scan through `bleak-esphome`;
- persistent thermometer inventory with active GATT identification;
- current firmware and hardware revision detection;
- pvvx stable firmware lookup and update-available detection;
- Home Assistant state sensors for current firmware and available updates;
- one-click official pvvx firmware download for LYWSD03MMC;
- manual Telink `.bin` upload fallback;
- pvvx-compatible Telink OTA;
- Home Assistant Ingress web UI with progress and logs.

## Install from GitHub

Add this repository URL to the Home Assistant Apps/Add-ons store:

`https://github.com/eugentib/home-assistant-atc-ota`

Then install **ATC OTA over ESPHome**.

See `atc_ota/DOCS.md` for configuration and usage.

## Automatic Bluetooth Proxy discovery

Version `0.1.4` can use the ESPHome integrations already loaded in Home Assistant as its source of proxy configuration. It queries Home Assistant for ESPHome config entries and their API encryption keys, discovers the matching ESPHome Native API endpoints over mDNS, then probes `bluetooth_proxy_feature_flags` before enabling a node.

All compatible proxies with active BLE connections are registered at once. The manual `proxy_address` / `proxy_noise_psk` settings remain as a fallback if automatic discovery cannot find a usable proxy.

## Home Assistant inventory entities

The app can publish:

- one firmware sensor per cached thermometer;
- one update-available binary sensor per cached thermometer;
- a summary sensor with the number and list of updatable thermometers.

The inventory itself is persisted in the app data directory and is republished after app startup and periodically thereafter.

## Status

Version `0.1.4` remains experimental. Use it first on a thermometer that you can recover by SWire if necessary.

## Upstream projects

This project interoperates with:

- pvvx/ATC_MiThermometer
- Bluetooth-Devices/bleak-esphome
- ESPHome Bluetooth Proxy
- Home Assistant

No upstream firmware binaries are bundled in this repository.

### Automatic proxy host resolution

The app first queries Home Assistant for loaded ESPHome entries and their API encryption keys. It then resolves each node using, in order, ESPHome mDNS (when multicast is visible), the device registry `configuration_url`, and direct `<name>.local` candidates derived from Home Assistant names. Each candidate is verified against the expected ESPHome MAC before it is accepted. This avoids requiring multicast browsing inside the app container.
