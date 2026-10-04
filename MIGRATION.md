# Migration from v0.1.x app/add-on to v0.2.1 integration

1. Stop the old **ATC OTA over ESPHome** app/add-on.
2. Disable its automatic start, or uninstall it after v0.2.1 is confirmed working.
3. Install `custom_components/atc_ota`.
4. Restart Home Assistant Core.
5. Add **ATC OTA** in Settings → Devices & services.
6. Wait for advertisements. Devices with known pvvx/Xiaomi identity are created automatically.
7. For a weak device whose firmware version is unknown, press its **Refresh firmware info** button when it has a reasonable RSSI.
8. Confirm that the native `update.*_firmware` entity shows Installed and Latest versions before doing OTA.

The old app's proxy discovery and proxy cache are no longer used.
