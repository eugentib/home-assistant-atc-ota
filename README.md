# ATC OTA over ESPHome

Experimental Home Assistant app/add-on for inventorying and updating pvvx/ATC Telink thermometers through an ESPHome Bluetooth Proxy.

Current features:

- one ESPHome Bluetooth Proxy configured by address/IP;
- optional ESPHome Native API encryption key;
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

## Home Assistant inventory entities

Version `0.1.3` can publish:

- one firmware sensor per cached thermometer;
- one update-available binary sensor per cached thermometer;
- a summary sensor with the number and list of updatable thermometers.

The inventory itself is persisted in the app data directory and is republished after app startup and periodically thereafter.

## Status

Version `0.1.3` remains experimental. Use it first on a thermometer that you can recover by SWire if necessary.

## Upstream projects

This project interoperates with:

- pvvx/ATC_MiThermometer
- Bluetooth-Devices/bleak-esphome
- ESPHome Bluetooth Proxy

No upstream firmware binaries are bundled in this repository.
