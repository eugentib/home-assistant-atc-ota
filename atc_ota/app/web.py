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
    .wrap { max-width: 980px; margin: 0 auto; }
    .card { border: 1px solid #6666; border-radius: 12px; padding: 16px; margin-bottom: 16px; background: #80808010; }
    h1 { margin-top: 0; font-size: 1.5rem; }
    h2 { font-size: 1.1rem; margin-top: 0; }
    button { padding: 8px 12px; cursor: pointer; }
    input[type=text], input[type=file] { width: 100%; box-sizing: border-box; padding: 8px; margin: 6px 0 10px; }
    .ok { color: #43a047; }
    .bad { color: #e53935; }
    .muted { opacity: .72; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #6664; }
    tr.candidate td { font-weight: 600; }
    progress { width: 100%; height: 22px; }
    pre { white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow: auto; background: #0003; padding: 10px; border-radius: 8px; }
    .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .grow { flex: 1 1 280px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>ATC OTA over ESPHome</h1>

  <div class="card">
    <h2>ESPHome Bluetooth Proxy</h2>
    <div id="proxyStatus">Loading…</div>
  </div>

  <div class="card">
    <div class="row">
      <h2 class="grow">BLE devices</h2>
      <label><input type="checkbox" id="showAll"> Show all</label>
      <button id="scanBtn">Scan BLE</button>
    </div>
    <div id="scanInfo" class="muted">No scan yet.</div>
    <div style="overflow:auto">
      <table>
        <thead><tr><th></th><th>Name</th><th>Address</th><th>RSSI</th><th>Detected by</th></tr></thead>
        <tbody id="deviceRows"></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>Firmware update</h2>
    <label>Target BLE address</label>
    <input id="target" type="text" placeholder="A4:C1:38:xx:xx:xx">
    <label>Telink firmware (.bin)</label>
    <input id="firmware" type="file" accept=".bin,application/octet-stream">
    <div class="row">
      <button id="flashBtn">Start OTA</button>
      <span class="muted">Experimental. Keep the target close to the proxy.</span>
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
let pollTimer = null;

async function refreshStatus() {
  try {
    const r = await fetch(apiUrl('api/status'));
    const s = await r.json();
    const cls = s.proxy.connected ? 'ok' : (s.proxy.status === 'error' ? 'bad' : 'muted');
    byId('proxyStatus').innerHTML = `<span class="${cls}"><b>${escapeHtml(s.proxy.status)}</b></span> — ${escapeHtml(s.proxy.address)}${s.proxy.error ? '<br><span class="bad">'+escapeHtml(s.proxy.error)+'</span>' : ''}`;
  } catch (e) {
    byId('proxyStatus').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
  }
}

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function renderDevices() {
  const showAll = byId('showAll').checked;
  const rows = devices.filter(d => showAll || d.candidate).map((d, i) => `
    <tr class="${d.candidate ? 'candidate' : ''}">
      <td><input type="radio" name="device" data-address="${escapeHtml(d.address)}"></td>
      <td>${escapeHtml(d.name)}</td>
      <td>${escapeHtml(d.address)}</td>
      <td>${d.rssi ?? ''}</td>
      <td>${escapeHtml(d.candidate_reason || '')}</td>
    </tr>`).join('');
  byId('deviceRows').innerHTML = rows || '<tr><td colspan="5" class="muted">No matching devices. Enable “Show all” to inspect every BLE advertisement.</td></tr>';
  document.querySelectorAll('input[name=device]').forEach(r => r.addEventListener('change', ev => {
    byId('target').value = ev.target.dataset.address;
  }));
}

byId('showAll').addEventListener('change', renderDevices);

byId('scanBtn').addEventListener('click', async () => {
  const btn = byId('scanBtn');
  btn.disabled = true;
  byId('scanInfo').textContent = 'Scanning…';
  try {
    const r = await fetch(apiUrl('api/scan'), {method:'POST'});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    devices = data.devices;
    byId('scanInfo').textContent = `Found ${devices.length} devices; ${devices.filter(d => d.candidate).length} look like ATC/Xiaomi thermometer candidates.`;
    renderDevices();
  } catch (e) {
    byId('scanInfo').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
  } finally {
    btn.disabled = false;
  }
});

byId('flashBtn').addEventListener('click', async () => {
  const address = byId('target').value.trim();
  const file = byId('firmware').files[0];
  if (!address) { alert('Select or enter a BLE address.'); return; }
  if (!file) { alert('Select a firmware .bin file.'); return; }
  if (!confirm(`Start OTA to ${address}? Do not interrupt the target during flashing.`)) return;

  const form = new FormData();
  form.append('address', address);
  form.append('firmware', file);
  const btn = byId('flashBtn');
  btn.disabled = true;
  byId('jobLog').textContent = '';
  byId('jobState').textContent = 'Starting…';
  byId('progress').value = 0;

  try {
    const r = await fetch(apiUrl('api/ota'), {method:'POST', body:form});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || JSON.stringify(data));
    startPolling(data.job_id);
  } catch (e) {
    byId('jobState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
    btn.disabled = false;
  }
});

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
      }
    } catch(e) {
      clearInterval(pollTimer); pollTimer = null;
      byId('jobState').innerHTML = `<span class="bad">${escapeHtml(String(e))}</span>`;
      byId('flashBtn').disabled = false;
    }
  };
  poll();
  pollTimer = setInterval(poll, 700);
}

refreshStatus();
setInterval(refreshStatus, 5000);
</script>
</body>
</html>
'''
