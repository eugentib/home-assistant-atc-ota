# ATC OTA over ESPHome

Experimental Home Assistant app/add-on for updating pvvx/ATC Telink thermometer firmware through an ESPHome Bluetooth Proxy.

The first release intentionally keeps the architecture simple:

- one ESPHome Bluetooth Proxy configured by address/IP;
- optional ESPHome Native API encryption key;
- BLE scan through `bleak-esphome`;
- manual upload of a Telink `.bin` firmware file;
- OTA using the pvvx Telink OTA service and packet format;
- Home Assistant Ingress web UI with progress and logs.

## Install from GitHub

After this repository is pushed to GitHub, add the repository URL to the Home Assistant Apps/Add-ons store:

`https://github.com/eugentib/home-assistant-atc-ota`

Then install **ATC OTA over ESPHome**.

See `atc_ota/DOCS.md` for configuration and usage.

## Status

Version `0.1.0` is experimental. Use it first on a thermometer that you can recover by SWire if necessary.

## Upstream projects

This project interoperates with:

- pvvx/ATC_MiThermometer
- Bluetooth-Devices/bleak-esphome
- ESPHome Bluetooth Proxy

No upstream firmware binaries are bundled in this repository.
