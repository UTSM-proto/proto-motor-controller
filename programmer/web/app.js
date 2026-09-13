'use strict';
const $ = id => document.getElementById(id);
const supplied = new URLSearchParams(location.hash.slice(1)).get('token');
if (supplied) sessionStorage.setItem('programmer-token', supplied);
history.replaceState(null, '', '/');
const token = sessionStorage.getItem('programmer-token') || '';
let config, schema, initial, group = 'Throttle', busy = false, lastJob = '', toolsReady = false, refreshing = false;
let monitorLines = [], monitorSequence = -1;
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST', headers: {'X-Programmer-Token': token, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function message(text, error = false) { $('validation').textContent = text; $('validation').className = error ? 'error' : ''; }
function save(name, text, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function summary() {
  $('startup-note').textContent = config.IDENTIFY_HALLS_ON_BOOT ? 'Programming restarts the Pico. Automatic hall calibration will move the motor once at startup. Secure the motor and release the throttle before programming.' : 'Programming restarts the Pico using your manual hall table. Release throttle to arm the controller.';
  $('flash').disabled = busy || !toolsReady || !$('device').value;
  $('build').disabled = busy || !toolsReady;
  document.querySelectorAll('[data-threshold-key]').forEach(input => {
    input.max = config.THROTTLE_INPUT_FULL_SCALE_V;
    if (document.activeElement !== input) input.value = (config[input.dataset.thresholdKey] / 4095 * config.THROTTLE_INPUT_FULL_SCALE_V).toFixed(3);
  });
}
function render() {
  $('tabs').replaceChildren();
  [...new Set(schema.map(f => f.group)), 'Manual hall table'].forEach(name => {
    const b = document.createElement('button'); b.textContent = name; b.className = group === name ? 'active' : '';
    b.onclick = () => { group = name; $('search').value = ''; render(); }; $('tabs').append(b);
  });
  $('fields').replaceChildren();
  const query = $('search').value.toLowerCase();
  const fields = schema.filter(f => query ? `${f.key} ${f.label} ${f.help}`.toLowerCase().includes(query) : f.group === group);
  fields.forEach(f => {
    const row = document.createElement('div'); row.className = 'field';
    const head = document.createElement('div'); head.className = 'field-head';
    const label = document.createElement('label'); label.htmlFor = f.key; label.textContent = f.label;
    const code = document.createElement('code'); code.textContent = f.key; label.append(code);
    const input = document.createElement(f.type === 'bool' ? 'select' : 'input'); input.id = f.key;
    if (f.type === 'bool') {
      for (const [value, text] of [['false','Off'], ['true','On']]) { const o = new Option(text, value); input.add(o); }
      input.value = String(config[f.key]);
    } else { input.type = 'number'; input.min = f.min; input.max = f.max; input.step = f.step; input.value = config[f.key]; }
    head.append(label, input); row.append(head);
    const help = document.createElement('p'); help.textContent = f.help; row.append(help);
    let range, volts;
    const change = value => { config[f.key] = value; summary(); message('Unbuilt changes in editor.'); if (volts) volts.value = (value / 4095 * config.THROTTLE_INPUT_FULL_SCALE_V).toFixed(3); };
    if (f.type !== 'bool') {
      const wrap = document.createElement('div'); wrap.className = 'range-row';
      range = document.createElement('input'); range.type = 'range'; range.min = f.min; range.max = f.max; range.step = f.step; range.value = config[f.key]; range.setAttribute('aria-label', f.label + ' slider');
      const bounds = document.createElement('span'); bounds.textContent = `${f.min} – ${f.max}`;
      range.oninput = () => { input.value = range.value; change(Number(range.value)); }; wrap.append(range, bounds); row.append(wrap);
    }
    if (['THROTTLE_LOW', 'THROTTLE_HIGH'].includes(f.key)) {
      const wrap = document.createElement('label'); wrap.className = 'voltage'; wrap.textContent = 'Or enter external volts';
      volts = document.createElement('input'); volts.type = 'number'; volts.min = 0; volts.max = config.THROTTLE_INPUT_FULL_SCALE_V; volts.step = '.001'; volts.value = (config[f.key] / 4095 * config.THROTTLE_INPUT_FULL_SCALE_V).toFixed(3);
      volts.setAttribute('aria-label', f.label + ' in volts');
      volts.dataset.thresholdKey = f.key;
      volts.oninput = () => { const value = Math.round(Number(volts.value) * 4095 / config.THROTTLE_INPUT_FULL_SCALE_V); input.value = value; range.value = value; config[f.key] = value; summary(); message('Unbuilt changes in editor.'); }; wrap.append(volts); row.append(wrap);
    }
    input.oninput = () => { const value = f.type === 'bool' ? input.value === 'true' : input.value === '' ? null : Number(input.value); if (range) range.value = value; change(value); };
    $('fields').append(row);
  });
  if (!query && group === 'Manual hall table') {
    const help = document.createElement('p'); help.textContent = 'Map each three-bit hall code to motor state 0–5. Codes 000 and 111 always disable drive. With automatic identification off, all six states must appear exactly once. Copy a verified table from USB telemetry.';
    const grid = document.createElement('div'); grid.className = 'hall-grid';
    config.HALL_TABLE.forEach((value, i) => {
      const label = document.createElement('label'); label.textContent = `Hall ${i.toString(2).padStart(3, '0')}`;
      const select = document.createElement('select'); [255,0,1,2,3,4,5].forEach(v => select.add(new Option(v === 255 ? '255 · disabled' : `State ${v}`, v)));
      select.value = value; select.disabled = i === 0 || i === 7; select.onchange = () => {config.HALL_TABLE[i] = Number(select.value); message('Unbuilt changes in editor.');}; label.append(select); grid.append(label);
    }); $('fields').append(help, grid);
  }
  summary();
}
async function refresh() {
  if (busy || refreshing) return;
  refreshing = true;
  $('refresh').disabled = true;
  try {
    const previous = $('device').value, found = await api('devices');
    $('device').replaceChildren(new Option(found.length ? 'Choose a Pico…' : 'No Pico detected', ''));
    found.forEach(d => $('device').add(new Option(`${d.serial} · ${d.mode}`, d.serial)));
    if (found.some(d => d.serial === previous)) $('device').value = previous;
    else if (found.length === 1) $('device').value = found[0].serial;
    $('device-note').textContent = found.length ? 'Programming follows this Pico through reboot on the same USB port. Close other serial monitors before programming.' : 'Connect the Pico normally by USB. Detection updates automatically.';
  } catch (e) { message(e.message, true); }
  finally { refreshing = false; $('refresh').disabled = false; summary(); }
}
async function start(flash) {
  try { await api('validate', config); busy = true; summary(); const job = await api('build', {config, flash, serial: $('device').value}); lastJob = job.id; $('download').hidden = true; message(flash ? 'Building, then programming the selected Pico…' : 'Building firmware…'); await poll(); }
  catch (e) { busy = false; summary(); message(e.message, true); }
}
async function poll() {
  try {
    const job = await api('job');
    if (!job) return;
    const signature = `${job.id}:${job.status}:${job.stage}`;
    const changed = signature !== lastJob;
    busy = job.status === 'running'; lastJob = signature;
    $('job-state').textContent = job.stage; $('log').textContent = job.log || 'Preparing build…';
    $('download').hidden = !job.artifact;
    if (!busy && changed) message(job.status === 'failed' ? 'Operation failed. See the specific error in the log below.' : job.stage + (job.cache_hit ? ' · Cached firmware reused.' : '') + (job.artifact ? ' · Configuration and checksum saved with UF2.' : ''), job.status === 'failed');
    summary();
  } catch (e) { message('Connection lost: ' + e.message, true); }
}
async function pollMonitor() {
  try {
    const state = await api('serial');
    $('serial-status').textContent = `${state.status}${state.com ? ' · ' + state.com : ''}`;
    $('monitor-start').disabled = busy || !config || !$('device').value || ['connected','connecting'].includes(state.status);
    const d = state.latest;
    $('diag-cal').textContent = d.cal ?? (d.block === 'calibration_failed' ? 'Failed' : '—');
    $('diag-block').textContent = d.block ?? '—';
    $('diag-throttle').textContent = d.throttle_adc ?? '—';
    const code = n => n === undefined ? '—' : n === 255 ? '255 (rejected)' : `${n} (${Number(n).toString(2).padStart(3,'0')})`;
    $('diag-hall').textContent = `${code(d.raw_hall)} / ${code(d.hall)}`;
    $('diag-motor').textContent = d.motor === 255 ? '255 · disabled' : d.motor ?? '—';
    $('diag-duty').textContent = `${d.duty ?? '—'} / ${d.armed === undefined ? 'unknown' : d.armed ? 'armed' : 'not armed'}`;
    const details = [];
    if (d.cal_sector !== undefined) details.push(`Calibration sector ${d.cal_sector}, code ${d.cal_code}, previous ${d.cal_previous}`);
    if (d.table) details.push('Hall table: ' + d.table.join(', '));
    if (d.build) details.push('Firmware build: ' + d.build);
    if (state.log_path) details.push('Session log: ' + state.log_path);
    if (details.length) $('serial-detail').textContent = details.join(' · ');
    if (monitorSequence !== state.sequence) {
      monitorSequence = state.sequence; monitorLines = state.lines;
      $('serial-log').textContent = monitorLines.map(r => `${r.time}  ${r.text}`).join('\n') || 'No serial data yet.';
      if ($('monitor-scroll').checked) $('serial-log').scrollTop = $('serial-log').scrollHeight;
    }
  } catch (e) { $('serial-status').textContent = 'Monitor unavailable: ' + e.message; }
}
async function monitorAction(action) {
  try { await api('serial/' + action, {serial: $('device').value}); await pollMonitor(); }
  catch (e) { $('serial-status').textContent = e.message; }
}
async function init() {
  try {
    const data = await api('config'); schema = data.fields; initial = data.defaults; config = structuredClone(initial);
    toolsReady = Object.values(data.tools).every(Boolean); $('tool-status').textContent = toolsReady ? '· Ready' : '· Setup needed';
    for (const [name, path] of Object.entries(data.tools)) { const p = document.createElement('p'); p.textContent = `${name}: ${path || 'Missing — see programmer/README.md'}`; $('tools').append(p); }
    $('search').oninput = render; $('reset').onclick = () => {config = structuredClone(initial); render(); message('Defaults restored in editor.');};
    $('export').onclick = async () => {try {await api('validate', config); save('utsm-controller-profile.json', JSON.stringify(config, null, 2));} catch(e) {message(e.message, true);}};
    $('import').onclick = () => $('file').click();
    $('file').onchange = async () => {try {const file = $('file').files[0]; if (!file) return; if (file.size > 32768) throw new Error('Profile too large.'); const values = JSON.parse(await file.text()); await api('validate', values); config = values; render(); message('Profile imported into editor.');} catch(e) {message(e.message, true);} finally {$('file').value = '';}};
    $('device').onchange = summary; $('refresh').onclick = refresh;
    $('build').onclick = () => start(false); $('flash').onclick = () => start(true);
    $('monitor-start').onclick = () => monitorAction('start');
    $('monitor-stop').onclick = () => monitorAction('stop');
    $('monitor-clear').onclick = () => monitorAction('clear');
    $('monitor-export').onclick = () => save('pico-serial.log', monitorLines.map(r => `${r.time}  ${r.text}`).join('\n'), 'text/plain');
    $('download').onclick = async () => {try {const r = await fetch('/api/artifact', {headers: {'X-Programmer-Token': token}}); if (!r.ok) throw new Error('No build available'); save('utsm-controller.uf2', await r.blob(), 'application/octet-stream');} catch(e) {message(e.message, true);}};
    render(); await refresh(); await poll(); await pollMonitor(); setInterval(poll, 1500); setInterval(refresh, 10000); setInterval(pollMonitor, 1000);
  } catch (e) { message(e.message, true); $('log').textContent = 'Start the app using Launch Programmer.cmd to open an authenticated local session.'; }
}
init();
