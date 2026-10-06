# ATC OTA for Home Assistant — v0.2.8

## Compatibility notes

Home Assistant provides `bleak-retry-connector` through its built-in Bluetooth integration, so ATC OTA does not pin or install a second copy. Config-flow imports remain lightweight and Bluetooth runtime code is loaded only after the config entry is set up.

A Home Assistant custom integration for pvvx/ATC Telink thermometers such as **LYWSD03MMC**.

## v0.2.8 firmware cache and update lifecycle

- firmware binaries are cached persistently under `/config/.storage/atc_ota_firmware/` after the first successful download;
- cached images are validated before reuse; corrupt or oversized cache entries are discarded and downloaded again;
- the firmware entity exposes `firmware_source`, `firmware_cache_file` and `firmware_sha256` diagnostics;
- `in_progress` now covers the complete update operation, including battery checks, metadata/catalog refresh and firmware preparation, not only the BLE flash loop;
- `ota_message` reports preflight stages such as battery checking, cache use/download and connection/transfer progress;
- a per-device install guard keeps Home Assistant's "already in progress" state consistent with the entity state.

## v0.2.7 battery/OTA fix

- fixes a BTHome parser bug where `Power = On` (`0x10 0x01`) followed by `Opening = Open` (`0x11 0x01`) could be misread as a fake **17%** battery value;
- removes the unsafe raw-byte battery fallback and parses battery only as a real BTHome `0x01` object;
- understands the standard BTHome binary-object range used by pvvx;
- if a pre-OTA active scan does not obtain a fresh battery advertisement, ATC OTA tries the standard Battery Level GATT characteristic before refusing the update;
- a battery value restored from persistent storage is no longer trusted for OTA until reconfirmed after restart.

## Why the v0.2.x integration is different

v0.1.x was a Home Assistant app/add-on that opened its own ESPHome API subscriptions to Bluetooth Proxy nodes. v0.2.x is a **custom integration inside Home Assistant Core** and uses Home Assistant's existing Bluetooth manager instead.

That means:

- no `proxy_address`;
- no ESPHome encryption key handling;
- no direct/parallel subscription to ESPHome Bluetooth Proxy nodes;
- no separate Bleak scanner;
- advertisements, proxy selection and connection slots remain owned by Home Assistant;
- OTA uses the same `BLEDevice` routing Home Assistant gives other Bluetooth integrations.

## v0.2.6 Home Assistant name sync

ATC OTA now looks up the existing Home Assistant/BTHome device for the same Bluetooth address and mirrors its user-assigned device name. A device renamed in Home Assistant, for example **Dormitor**, **Printer**, or **Afara**, is therefore shown with that name in ATC OTA instead of only `ATC_xxxxxx`. The firmware-advertised BLE name is kept separately for diagnostics.

The **Device Area** is mirrored as well. If the existing BTHome/Bluetooth device is assigned to an HA area such as **Afara**, **Dormitor** or **Living**, the corresponding ATC OTA device is assigned to the same area. Later area changes on the source device are picked up on the next Bluetooth advertisement (or after restart). The source HA device is treated as authoritative for area placement.

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
- OTA requires a recent battery reading; if battery is unknown/stale or at/below the configured threshold, flashing is refused before the Telink OTA session starts;
- serialized metadata reads and OTA jobs;
- all GATT clients disconnect in `finally` blocks.

## Migration from v0.1.x

**Stop the old ATC OTA app/add-on before enabling v0.2.6.** The old app should not run alongside this integration.

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

The default OTA battery minimum is **30%** and can be changed under the integration's Options. v0.2.7 requires a recent battery reading before flashing and refuses OTA when the value is unknown/stale or at/below the threshold. There is no double-click bypass; lowering the configured threshold is the explicit override.

## Bluetooth architecture

```text
ESPHome Bluetooth Proxy ─┐
ESPHome Bluetooth Proxy ─┼─> Home Assistant Bluetooth manager ─> ATC OTA
local Bluetooth adapter ─┘
```

ATC OTA never creates an ESPHome API connection itself.

## Current scope

Automatic upstream firmware selection is intentionally limited to verified **LYWSD03MMC** mappings. The classic Telink OTA framing remains compatible with the pvvx OTA service used by the v0.1.x prototype.
