$ErrorActionPreference = "Stop"

Write-Host "ATC OTA v0.2.0 migration cleanup"
Write-Host "Run this from the root of the existing home-assistant-atc-ota Git repository."

$legacy = @(
  "atc_ota",
  "repository.yaml",
  "init-github.ps1",
  "tests/test_inventory.py",
  "tests/test_proxy_discovery.py"
)

foreach ($path in $legacy) {
  if (Test-Path $path) {
    Write-Host "Removing legacy add-on path: $path"
    Remove-Item -Recurse -Force $path
  }
}

Write-Host "Legacy add-on files removed. Copy/extract the v0.2.0 files into this repository, then run:"
Write-Host "  git add -A"
Write-Host "  git commit -m 'Migrate ATC OTA to native Home Assistant integration v0.2.0'"
Write-Host "  git push"
