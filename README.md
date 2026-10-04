# ATC OTA for Home Assistant — v0.2.0

A Home Assistant custom integration for pvvx/ATC Telink thermometers such as **LYWSD03MMC**.

## Why v0.2.0 is different

v0.1.x was a Home Assistant app/add-on that opened its own ESPHome API subscriptions to Bluetooth Proxy nodes. v0.2.0 is a **custom integration inside Home Assistant Core** and uses Home Assistant's existing Bluetooth manager instead.

That means:

- no `proxy_address`;
- no ESPHome encryption key handling;
- no direct/parallel subscription to ESPHome Bluetooth Proxy nodes;
- no separate Bleak scanner;
- advertisements, proxy selection and connection slots remain owned by Home Assistant;
- OTA uses the same `BLEDevice` routing Home Assistant gives other Bluetooth integrations.

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
- if battery is low, first Install attempt is rejected and asks for a second Install within 60 seconds to confirm;
- serialized metadata reads and OTA jobs;
- all GATT clients disconnect in `finally` blocks.

## Migration from v0.1.x

**Stop the old ATC OTA app/add-on before enabling v0.2.0.** The old app should not run alongside this integration.

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

The default warning threshold is **30%** and can be changed under the integration's Options. If battery is at/below the threshold, the first update attempt is intentionally rejected. A second Install within 60 seconds confirms that the user wants to proceed despite the warning.

## Bluetooth architecture

```text
ESPHome Bluetooth Proxy ─┐
ESPHome Bluetooth Proxy ─┼─> Home Assistant Bluetooth manager ─> ATC OTA
local Bluetooth adapter ─┘
```

ATC OTA never creates an ESPHome API connection itself.

## Current scope

Automatic upstream firmware selection is intentionally limited to verified **LYWSD03MMC** mappings. The classic Telink OTA framing remains compatible with the pvvx OTA service used by the v0.1.x prototype.
