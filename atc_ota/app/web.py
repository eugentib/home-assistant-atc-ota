"""Embedded Home Assistant Ingress UI."""

INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ATC OTA</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; padding: 20px; background: var(--ha-card-background, #111); color: var(--primary-text-color, #eee); }
    .wrap { max-width: 1240px; margin: 0 auto; }
    .card { border: 1px solid #6666; border-radius: 12px; padding: 16px; margin-bottom: 16px; background: #80808010; }
    h1 { margin-top: 0; font-size: 1.5rem; }
    h2 { font-size: 1.1rem; margin-top: 0; }
    button { padding: 8px 12px; cursor: pointer; }
    button:disabled { cursor: default; opacity: .55; }
    input[type=text], input[type=file] { width: 100%; box-sizing: border-box; padding: 8px; margin: 6px 0 10px; }
    .ok { color: #43a047; }
    .bad { color: #e53935; }
    .warn { color: #f9a825; }
    .muted { opacity: .72; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; font-size:.82rem; border:1px solid #6666; }
    .pill.update { color:#f9a825; border-color:#f9a82588; }
    .pill.current { color:#43a047; border-color:#43a04788; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #6664; white-space: nowrap; }
    tr.candidate td { font-weight: 600; }
    tr.selected { background: #80808018; }
    progress { width: 100%; height: 22px; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; background: #0003; padding: 10px; border-radius: 8px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1 1 280px; }
    .info-grid { display:grid; grid-template-columns:max-content 1fr; gap:6px 14px; margin:10px 0 14px; }
    .info-grid .label { opacity:.7; }
    .separator { margin:18px 0; border:0; border-top:1px solid #6664; }
    .inventory-detail { margin-top:12px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>ATC OTA over ESPHome</h1>

  <div class="card">
    <div class="row">
      <h2 class="grow">ESPHome Bluetooth Proxies</h2>
      <button id="proxyRefreshBtn">Rediscover proxies</button>
    </div>
    <div id="proxyStatus">Loading…</div>
    <div id="proxyDiscoveryNote" class="muted" style="margin-top:6px"></div>
    <div style="overflow:auto; margin-top:8px">
      <table>
        <thead><tr><th>Name</th><th>Host</th><th>Resolved via</th><th>MAC</th><th>ESPHome</th><th>BT proxy</th><th>Runtime</th></tr></thead>
        <tbody id="proxyRows"></tbody>
      </table>
    </div>
    <div id="haStatus" class="muted" style="margin-top:8px">Home Assistant publishing: loading…</div>
  </div>

  <div class="card">
    <div class="row">
      <h2 class="grow">BLE devices</h2>
      <label><input type="checkbox" id="showAll"> Show all</label>
      <button id="scanBtn">Scan BLE</button>
      <button id="inventoryBtn">Refresh inventory</button>
    </div>
    <div id="scanInfo" class="muted">Loading saved inventory…</div>
    <div style="overflow:auto">
      <table>
        <thead><tr><th></th><th>Name</th><th>Model</th><th>Address</th><th>RSSI</th><th>Battery</th><th>HW</th><th>Current</th><th>Latest</th><th>Update</th><th>Strongest proxy</th><th>Seen by</th><th>Format</th></tr></thead>
        <tbody id="deviceRows"></tbody>
      </table>
    </div>
    <div class="inventory-detail">
      <progress id="inventoryProgress" max="100" value="0"></progress>
      <div id="inventoryState" class="muted">Inventory idle</div>
      <pre id="inventoryLog" style="display:none"></pre>
    </div>
  </div>

  <div class="card">
    <h2>Firmware update</h2>
    <label>Target BLE address</label>
    <input id="target" type="text" placeholder="A4:C1:38:xx:xx:xx">

    <div id="deviceInfo" class="muted">Select a thermometer. Cached inventory is shown immediately; selecting also refreshes its GATT information.</div>

    <div class="row">
      <button id="latestBtn" disabled>Update to latest stable pvvx</button>
      <span id="latestHint" class="muted">Select a device first.</span>
    </div>
    <div id="batteryPolicy" class="muted" style="margin-top:8px">Low-battery OTA warning threshold: 30%.</div>

    <hr class="separator">

    <label>Manual Telink firmware (.bin)</label>
    <input id="firmware" type="file" accept=".bin,application/octet-stream">
    <div class="row">
      <button id="flashBtn">Start manual OTA</button>
      <span class="muted">Manual fallback. Keep the target close to the proxy.</span>
    </div>
  </div>

  <div class="card">
    <h2>OTA progress</h2>
    <progress id="progress" max="100" value="0"></progress>
    <div id="jobState" class="muted">Idle</div>
    <pre id="jobLog"></pre>
  </div>
</div>
<script>
const byId = id => document.getElementById(id);
const apiUrl = path => new URL(path.replace(/^\//,''), new URL('.', window.location.href));
let devices = [];
let selectedAddress = '';
let selectedInfo = null;
let pollTimer = null;
let inventoryPollTimer = null;
let infoRequestSerial = 0;
let lowBatteryThreshold = 30;

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function shortVersion(v) {
  return String(v || '').trim().replace(/^V/i, '');
}

function mergeDevices(incoming) {
  const map = new Map(devices.map(d => [String(d.address || '').toUpperCase(), d]));
  for (const item of incoming || []) {
    const key = String(item.address || '').toUpperCase();
    if (!key) continue;
    map.set(key, {...(map.get(key) || {}), ...item});
  }
  devices = [...map.values()];
  devices.sort((a,b) => {
    if (!!a.update_available !== !!b.update_available) return a.update_available ? -1 : 1;
    const ar = Number.isFinite(a.rssi) ? a.rssi : -999;
    const br = Number.isFinite(b.rssi) ? b.rssi : -999;
    return br - ar;
  });
}

function renderProxyRows(status) {
  const discovered = status.proxy_discovery || [];
  const runtime = status.proxy?.proxies || [];
  const runtimeByEntry = new Map(runtime.filter(p => p.entry_id).map(p => [p.entry_id, p]));
  const runtimeByHost = new Map(runtime.map(p => [String(p.address || '').toLowerCase(), p]));

  let rows = discovered.map(p => {
    const live = runtimeByEntry.get(p.entry_id) || runtimeByHost.get(String(p.host || '').toLowerCase());
    const capability = p.usable
      ? '<span class="ok">active GATT</span>'
      : p.status === 'passive-only'
        ? '<span class="warn">passive only</span>'
        : p.status === 'not-bluetooth-proxy'
          ? '<span class="muted">not a BT proxy</span>'
          : `<span class="bad">${escapeHtml(p.status || 'unknown')}</span>`;
    const liveStatus = live
      ? (live.connected ? '<span class="ok">connected</span>' : `<span class="${live.status === 'error' ? 'bad' : 'muted'}">${escapeHtml(live.status || 'unknown')}</span>`)
      : '<span class="muted">not selected</span>';
    const title = p.error ? ` title="${escapeHtml(p.error)}"` : '';
    return `<tr${title}><td>${escapeHtml(p.name || '')}</td><td>${escapeHtml(p.host || '')}</td><td>${escapeHtml(p.resolved_via || '')}</td><td>${escapeHtml(p.mac || p.bluetooth_mac || '')}</td><td>${escapeHtml(p.esphome_version || '')}</td><td>${capability}</td><td>${liveStatus}</td></tr>`;
  });

  if (!rows.length && runtime.length) {
    rows = runtime.map(p => `<tr><td>${escapeHtml(p.name || '')}</td><td>${escapeHtml(p.address || '')}</td><td>manual config</td><td></td><td></td><td><span class="muted">manual</span></td><td>${p.connected ? '<span class="ok">connected</span>' : `<span class="${p.status === 'error' ? 'bad' : 'muted'}">${escapeHtml(p.status || '')}</span>`}</td></tr>`);
  }
  byId('proxyRows').innerHTML = rows.join('') || '<tr><td colspan="7" class="muted">No ESPHome nodes discovered yet.</td></tr>';
}

async function refreshStatus() {
  try {
    const r = await fetch(apiUrl('api/status'));
    const s = await r.json();
    const cls = s.proxy.connected ? 'ok' : (s.proxy.status === 'error' ? 'bad' : 'muted');
    const mode = s.proxy_mode || s.proxy.mode || 'unknown';
    const count = (s.proxy.proxies || []).filter(p => p.connected).length;
    byId('proxyStatus').innerHTML = `<span class="${cls}"><b>${escapeHtml(s.proxy.status)}</b></span> — mode: <b>${escapeHtml(mode)}</b> — ${count} connected${s.proxy.address ? ' — '+escapeHtml(s.proxy.address) : ''}${s.proxy.error ? '<br><span class="bad">'+escapeHtml(s.proxy.error)+'</span>' : ''}`;
    lowBatteryThreshold = Number.isFinite(Number(s.low_battery_warning_percent)) ? Number(s.low_battery_warning_percent) : 30;
    byId('batteryPolicy').textContent = `Low-battery OTA warning threshold: ${lowBatteryThreshold}%. pvvx recommends more than 40% for reliable LYWSD03MMC reflashing.`;
    const phase = s.proxy_phase || '';
    if (phase === 'discovering') {
      byId('proxyDiscoveryNote').innerHTML = `<span class="warn">${escapeHtml(s.proxy_message || 'Discovering ESPHome Bluetooth Proxies…')}</span> The Web UI is ready; this runs in the background.`;
    } else {
      byId('proxyDiscoveryNote').innerHTML = s.proxy_discovery_error
        ? `<span class="warn">Auto-discovery: ${escapeHtml(s.proxy_discovery_error)}</span>${mode.startsWith('manual') ? ' — using manual fallback.' : ''}`
        : (s.auto_discover_proxies ? 'Automatic discovery is enabled. Only ESPHome nodes with Bluetooth Proxy active GATT support are selected.' : 'Automatic discovery is disabled; using manual configuration.');
    }
    byId('proxyRefreshBtn').disabled = phase === 'discovering';
    renderProxyRows(s);
    const ha = s.home_assistant || {};
    if (!ha.publishing_enabled) {
      byId('haStatus').textContent = 'Home Assistant entities: disabled in app configuration';
    } else if (ha.api_available) {
      byId('haStatus').innerHTML = `<span class="ok">Home Assistant entities enabled</span>${ha.last_error ? ' — <span class="warn">'+escapeHtml(ha.last_error)+'</span>' : ''}`;
    } else {
      byId('haStatus').innerHTML = '<span class="bad">Home Assistant API token unavailable</span>';
    }
  } catch (e) {
    byId('proxyStatus').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
  }
}


function formatSeenBy(rows) {
  if (!Array.isArray(rows) || !rows.length) return '';
  return rows.map(item => {
    const proxy = item.proxy || item.source || '?';
    const rssi = Number.isFinite(item.rssi) ? ` ${item.rssi} dBm` : '';
    return `${proxy}${rssi}`;
  }).join(', ');
}

function batteryHtml(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '<span class="muted">—</span>';
  const low = n <= lowBatteryThreshold;
  return `<span class="${low ? 'warn' : ''}" title="${low ? `At or below ${lowBatteryThreshold}% OTA warning threshold` : 'Battery level'}">${Math.round(n)}%</span>`;
}

function cachedDevice(address) {
  return devices.find(d => String(d.address || '').toUpperCase() === String(address || '').toUpperCase()) || null;
}

function lowBatteryMessage(info, action) {
  const value = Number(info?.battery_percent);
  if (!Number.isFinite(value) || value > lowBatteryThreshold) return null;
  return `Battery is ${Math.round(value)}% (warning threshold ${lowBatteryThreshold}%).\n\n${action} may fail if battery voltage drops during flashing. pvvx recommends more than 40% for reliable LYWSD03MMC reflashing.\n\nContinue anyway?`;
}

async function postOtaWithLowBatteryRetry(url, formFactory, action) {
  let confirmedLowBattery = false;
  const cached = selectedInfo || cachedDevice(selectedAddress);
  const cachedWarning = lowBatteryMessage(cached, action);
  if (cachedWarning) {
    if (!confirm(cachedWarning)) return null;
    confirmedLowBattery = true;
  }

  for (;;) {
    const form = formFactory(confirmedLowBattery);
    const r = await fetch(apiUrl(url), {method:'POST', body:form});
    const data = await r.json();
    if (r.status === 409 && data?.detail?.code === 'low_battery' && !confirmedLowBattery) {
      const warningInfo = {battery_percent: data.detail.battery_percent};
      const msg = lowBatteryMessage(warningInfo, action) || data.detail.message || 'Low battery. Continue anyway?';
      if (!confirm(msg)) return null;
      confirmedLowBattery = true;
      continue;
    }
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data));
    return data;
  }
}

function renderDevices() {
  const showAll = byId('showAll').checked;
  const visible = devices.filter(d => showAll || d.candidate || d.model);
  const rows = visible.map(d => {
    const flag = d.update_available;
    const update = flag === true
      ? `<button class="quickUpdate" data-address="${escapeHtml(d.address)}" title="Update directly to stable ${escapeHtml(d.latest?.version || '')}">Update</button>`
      : flag === false
        ? '<span class="pill current">current</span>'
        : '<span class="muted">—</span>';
    return `
    <tr class="${d.candidate ? 'candidate' : ''} ${d.address === selectedAddress ? 'selected' : ''}">
      <td><input type="radio" name="device" data-address="${escapeHtml(d.address)}" ${d.address === selectedAddress ? 'checked' : ''}></td>
      <td>${escapeHtml(d.device_name || d.advertised_name || d.name || '(unnamed)')}</td>
      <td>${escapeHtml(d.model || '')}</td>
      <td>${escapeHtml(d.address)}</td>
      <td title="${escapeHtml(d.route_proxy && d.route_proxy !== d.best_proxy ? `habluetooth route: ${d.route_proxy}${Number.isFinite(d.route_rssi) ? ` ${d.route_rssi} dBm` : ''}` : '')}">${d.rssi ?? ''}</td>
      <td>${batteryHtml(d.battery_percent)}</td>
      <td>${escapeHtml(d.hardware_revision || '')}</td>
      <td>${escapeHtml(d.current_version || '')}</td>
      <td>${escapeHtml(d.latest?.version || '')}</td>
      <td>${update}</td>
      <td>${escapeHtml(d.best_proxy || '')}</td>
      <td>${escapeHtml(formatSeenBy(d.seen_by || []))}</td>
      <td>${escapeHtml(d.candidate_reason || '')}</td>
    </tr>`;
  }).join('');
  byId('deviceRows').innerHTML = rows || '<tr><td colspan="13" class="muted">No matching devices. Enable “Show all” to inspect every BLE advertisement.</td></tr>';
  document.querySelectorAll('input[name=device]').forEach(r => r.addEventListener('change', ev => selectDevice(ev.target.dataset.address)));
  document.querySelectorAll('.quickUpdate').forEach(btn => btn.addEventListener('click', ev => {
    ev.preventDefault();
    ev.stopPropagation();
    startLatestUpdate(ev.currentTarget.dataset.address, cachedDevice(ev.currentTarget.dataset.address));
  }));
}


function renderSelectedInfo(info) {
  const latest = info?.latest;
  byId('deviceInfo').innerHTML = `<div class="info-grid">
    <div class="label">Device name</div><div>${escapeHtml(info.device_name || info.advertised_name || '(unnamed)')}</div>
    <div class="label">Model</div><div>${escapeHtml(info.model || '')}</div>
    <div class="label">Hardware</div><div>${escapeHtml(info.hardware_revision || '')}</div>
    <div class="label">Battery</div><div>${batteryHtml(info.battery_percent)}</div>
    <div class="label">Software</div><div>${escapeHtml(info.software_revision || info.current_version || '')}</div>
    <div class="label">Firmware ID</div><div>${escapeHtml(info.firmware_revision || '')}</div>
    <div class="label">Manufacturer</div><div>${escapeHtml(info.manufacturer || '')}</div>
  </div>`;
  if (latest) {
    const current = shortVersion(info.current_version);
    const newest = shortVersion(latest.version);
    const isUpdate = info.update_available === true || (current && newest && current !== newest);
    byId('latestBtn').disabled = false;
    byId('latestBtn').textContent = isUpdate ? `Update to stable ${latest.version}` : `Reinstall stable ${latest.version}`;
    byId('latestHint').textContent = latest.filename || latest.path || '';
  } else {
    byId('latestBtn').disabled = true;
    byId('latestBtn').textContent = 'Update to latest stable pvvx';
    byId('latestHint').innerHTML = info.latest_error ? `<span class="bad">Automatic firmware unavailable:</span> ${escapeHtml(info.latest_error)}` : 'Automatic firmware unavailable for this device.';
  }
}

async function loadInventory(updateMessage=true) {
  try {
    const r = await fetch(apiUrl('api/inventory'));
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    mergeDevices(data.devices || []);
    renderDevices();
    renderInventoryJob(data.job || {});
    if (updateMessage) {
      const known = (data.devices || []).filter(d => d.candidate || d.model).length;
      byId('scanInfo').textContent = `Saved inventory: ${known} thermometer(s); ${data.updates_available || 0} update(s) available.`;
    }
  } catch(e) {
    if (updateMessage) byId('scanInfo').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
  }
}

function renderInventoryJob(job) {
  byId('inventoryProgress').value = job.progress || 0;
  byId('inventoryState').textContent = `${job.state || 'idle'}: ${job.message || 'Idle'}`;
  const log = job.log || [];
  byId('inventoryLog').style.display = log.length ? 'block' : 'none';
  byId('inventoryLog').textContent = log.join('\n');
  byId('inventoryLog').scrollTop = byId('inventoryLog').scrollHeight;
  const running = job.state === 'running';
  byId('inventoryBtn').disabled = running;
  return running;
}

async function selectDevice(address) {
  selectedAddress = address;
  selectedInfo = null;
  byId('target').value = address;
  byId('latestBtn').disabled = true;
  byId('latestBtn').textContent = 'Update to latest stable pvvx';

  const cached = devices.find(d => String(d.address).toUpperCase() === String(address).toUpperCase());
  if (cached?.current_version) renderSelectedInfo(cached);
  else byId('deviceInfo').textContent = 'Reading device information…';
  byId('latestHint').textContent = 'Refreshing through GATT…';
  renderDevices();

  const serial = ++infoRequestSerial;
  const form = new FormData();
  form.append('address', address);
  try {
    const r = await fetch(apiUrl('api/device-info'), {method:'POST', body:form});
    const info = await r.json();
    if (!r.ok) throw new Error(info.detail || JSON.stringify(info));
    if (serial !== infoRequestSerial || address !== selectedAddress) return;
    selectedInfo = info;
    mergeDevices([info]);
    renderDevices();
    renderSelectedInfo(info);
  } catch (e) {
    if (serial !== infoRequestSerial || address !== selectedAddress) return;
    byId('deviceInfo').innerHTML = `<span class="bad">Unable to refresh device information: ${escapeHtml(String(e))}</span>`;
    byId('latestHint').textContent = cached?.latest ? 'Cached firmware information remains available.' : 'Manual .bin OTA is still available.';
    if (cached?.latest) {
      selectedInfo = cached;
      renderSelectedInfo(cached);
    }
  }
}

byId('showAll').addEventListener('change', renderDevices);

byId('proxyRefreshBtn').addEventListener('click', async () => {
  const btn = byId('proxyRefreshBtn');
  btn.disabled = true;
  byId('proxyDiscoveryNote').textContent = 'Starting ESPHome proxy rediscovery…';
  try {
    const r = await fetch(apiUrl('api/proxies/refresh'), {method:'POST'});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    byId('proxyDiscoveryNote').textContent = data.status === 'already-running'
      ? 'Proxy discovery is already running in the background.'
      : 'Proxy discovery started in the background. You can keep using this page.';
    setTimeout(refreshStatus, 250);
  } catch(e) {
    byId('proxyDiscoveryNote').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
    btn.disabled = false;
  }
});

byId('scanBtn').addEventListener('click', async () => {
  const btn = byId('scanBtn');
  btn.disabled = true;
  byId('scanInfo').textContent = 'Passive BLE scan…';
  try {
    const r = await fetch(apiUrl('api/scan'), {method:'POST'});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    mergeDevices(data.devices || []);
    const candidates = (data.devices || []).filter(d => d.candidate).length;
    byId('scanInfo').textContent = `Found ${(data.devices || []).length} devices; ${candidates} look like ATC/Xiaomi thermometer candidates. “Refresh inventory” reads battery/RSSI passively and only opens GATT for devices whose metadata is still missing.`;
    renderDevices();
  } catch (e) {
    byId('scanInfo').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
  } finally {
    btn.disabled = false;
  }
});

byId('inventoryBtn').addEventListener('click', async () => {
  if (!confirm('Refresh thermometer inventory? Battery/RSSI are read passively; GATT is only used for devices whose model/version metadata is missing.')) return;
  byId('inventoryBtn').disabled = true;
  try {
    const r = await fetch(apiUrl('api/inventory/refresh'), {method:'POST'});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    startInventoryPolling();
  } catch(e) {
    byId('inventoryState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
    byId('inventoryBtn').disabled = false;
  }
});

function startInventoryPolling() {
  if (inventoryPollTimer) clearInterval(inventoryPollTimer);
  const poll = async () => {
    try {
      const r = await fetch(apiUrl('api/inventory'));
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
      mergeDevices(data.devices || []);
      renderDevices();
      const running = renderInventoryJob(data.job || {});
      byId('scanInfo').textContent = `Saved inventory: ${(data.devices || []).filter(d => d.candidate || d.model).length} thermometer(s); ${data.updates_available || 0} update(s) available.`;
      if (!running) {
        clearInterval(inventoryPollTimer); inventoryPollTimer = null;
        byId('inventoryBtn').disabled = false;
      }
    } catch(e) {
      clearInterval(inventoryPollTimer); inventoryPollTimer = null;
      byId('inventoryState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
      byId('inventoryBtn').disabled = false;
    }
  };
  poll();
  inventoryPollTimer = setInterval(poll, 1000);
}

byId('target').addEventListener('change', async () => {
  const address = byId('target').value.trim();
  if (address) await selectDevice(address);
});

async function startLatestUpdate(address, info=null) {
  if (!address) { alert('Select or enter a BLE address.'); return; }
  const cached = info || cachedDevice(address) || {};
  const version = cached?.latest?.version || 'latest stable';
  const action = `Updating ${cached.device_name || cached.advertised_name || address} to pvvx ${version}`;
  const cachedWarning = lowBatteryMessage(cached, action);
  if (!cachedWarning && !confirm(`Download pvvx ${version} directly from the upstream repository and OTA flash ${cached.device_name || cached.advertised_name || address}?`)) return;

  selectedAddress = address;
  selectedInfo = cached;
  byId('target').value = address;
  renderDevices();
  const btn = byId('latestBtn');
  btn.disabled = true;
  resetJobUi('Checking advertised battery and downloading latest stable firmware…');
  try {
    const data = await postOtaWithLowBatteryRetry('api/ota/latest', (confirmed) => {
      const form = new FormData();
      form.append('address', address);
      form.append('confirm_low_battery', confirmed ? 'true' : 'false');
      return form;
    }, action);
    if (!data) { btn.disabled = false; byId('jobState').textContent = 'OTA cancelled'; return; }
    startPolling(data.job_id);
  } catch (e) {
    byId('jobState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
    btn.disabled = false;
  }
}

byId('latestBtn').addEventListener('click', async () => {
  const address = byId('target').value.trim();
  await startLatestUpdate(address, selectedInfo || cachedDevice(address));
});

byId('flashBtn').addEventListener('click', async () => {
  const address = byId('target').value.trim();
  const file = byId('firmware').files[0];
  if (!address) { alert('Select or enter a BLE address.'); return; }
  if (!file) { alert('Select a firmware .bin file.'); return; }
  const cachedWarning = lowBatteryMessage(selectedInfo || cachedDevice(address), `Manual OTA of ${file.name} to ${address}`);
  if (!cachedWarning && !confirm(`Start manual OTA to ${address}? Do not interrupt the target during flashing.`)) return;

  const btn = byId('flashBtn');
  btn.disabled = true;
  resetJobUi('Checking battery and starting manual OTA…');
  try {
    selectedAddress = address;
    const data = await postOtaWithLowBatteryRetry('api/ota', (confirmed) => {
      const form = new FormData();
      form.append('address', address);
      form.append('firmware', file);
      form.append('confirm_low_battery', confirmed ? 'true' : 'false');
      return form;
    }, `Manual OTA of ${file.name} to ${address}`);
    if (!data) { btn.disabled = false; byId('jobState').textContent = 'OTA cancelled'; return; }
    startPolling(data.job_id);
  } catch (e) {
    byId('jobState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
    btn.disabled = false;
  }
});

function resetJobUi(message) {
  byId('jobLog').textContent = '';
  byId('jobState').textContent = message;
  byId('progress').value = 0;
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  const poll = async () => {
    try {
      const r = await fetch(apiUrl(`api/jobs/${jobId}`));
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
      byId('progress').value = j.progress;
      byId('jobState').textContent = `${j.state}: ${j.message}`;
      byId('jobLog').textContent = j.log.join('\n');
      byId('jobLog').scrollTop = byId('jobLog').scrollHeight;
      if (j.state === 'done' || j.state === 'error') {
        clearInterval(pollTimer); pollTimer = null;
        byId('flashBtn').disabled = false;
        byId('latestBtn').disabled = !selectedInfo?.latest;
        if (j.state === 'done') {
          setTimeout(() => {
            loadInventory(false);
            if (selectedAddress) selectDevice(selectedAddress);
          }, 5000);
        }
      }
    } catch(e) {
      clearInterval(pollTimer); pollTimer = null;
      byId('jobState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
      byId('flashBtn').disabled = false;
      byId('latestBtn').disabled = !selectedInfo?.latest;
    }
  };
  poll();
  pollTimer = setInterval(poll, 700);
}

refreshStatus();
loadInventory();
setInterval(refreshStatus, 5000);
</script>
</body>
</html>
'''
