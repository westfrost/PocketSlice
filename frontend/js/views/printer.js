import { api } from '../api.js';
import { $, $$, esc, fmtDuration, onStatus, state, toast, confirmDialog, modal } from '../app.js';

const STATE_BADGE = { printing: 'accent', paused: 'warn', complete: 'ok', error: 'danger', cancelled: 'danger', standby: 'info', offline: 'danger' };

export class PrinterView {
  constructor() { this.macros = null; this.cams = null; }

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <div class="stack">
        <section id="status-card" class="card"><div class="empty">Connecting…</div></section>
        <section id="webcam-card"></section>
        <section id="temps-card" class="card"></section>
        <section id="controls-card" class="card"></section>
        <section id="macros-card"></section>
      </div>`;
    this.unsub = onStatus((s) => this.render(s));
    if (state.printer) this.render(state.printer);
    this.loadCams();
    this.loadMacros();
  }

  unmount() { if (this.unsub) this.unsub(); const img = $('#cam-img', this.root); if (img) img.src = ''; }

  // -------------------------------------------------------------- status
  render(s) {
    this.renderStatus(s);
    this.renderTemps(s);
    this.renderControls(s);
  }

  renderStatus(s) {
    const card = $('#status-card', this.root);
    if (!s.ok) {
      card.innerHTML = `<h2>${esc(state.settings.printer_name)}</h2><p class="muted">${esc(s.error || 'Offline')}</p>
        <p class="small muted">Check the Moonraker URL under Settings.</p>`;
      return;
    }
    const st = s.klippy_state && s.klippy_state !== 'ready' ? s.klippy_state : s.state;
    const pct = s.progress != null ? Math.round(s.progress * 100) : 0;
    const printing = s.state === 'printing' || s.state === 'paused';
    const r = 42, c = 2 * Math.PI * r;
    const file = s.filename || '';
    card.innerHTML = `
      <div class="card-title"><h2>${esc(state.settings.printer_name)}</h2><span class="badge ${STATE_BADGE[st] || ''}">${esc(st)}</span></div>
      ${s.klippy_state && s.klippy_state !== 'ready' ? `<p class="small" style="color:var(--danger)">${esc(s.klippy_message || '')}</p>
        <div class="grid2"><button class="btn sm" data-act="firmware_restart">Firmware restart</button></div>` : ''}
      ${printing || s.state === 'complete' ? `
        <div class="big-progress">
          <svg class="ring" viewBox="0 0 100 100"><circle class="bg" cx="50" cy="50" r="${r}"/>
            <circle class="fg" cx="50" cy="50" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct / 100)}" transform="rotate(-90 50 50)"/>
            <text x="50" y="52">${pct}%</text></svg>
          <div class="grow">
            <div class="row" style="align-items:flex-start">
              ${file ? `<img class="thumb" src="/api/printer/thumbnail?filename=${encodeURIComponent(file)}" alt="" onerror="this.style.display='none'">` : ''}
              <div class="grow ellipsis" style="font-weight:600" title="${esc(file)}">${esc(file.split('/').pop() || '–')}</div>
            </div>
            <div class="muted small" style="margin-top:6px">
              ${fmtDuration(s.print_duration)} elapsed${s.remaining != null ? ` · <b style="color:var(--text)">${fmtDuration(s.remaining)}</b> left` : ''}
              ${s.layer != null && s.total_layer ? ` · layer ${s.layer}/${s.total_layer}` : ''}
              ${s.filament_used ? ` · ${(s.filament_used / 1000).toFixed(1)} m` : ''}
            </div>
            ${s.message ? `<div class="small" style="margin-top:4px">${esc(s.message)}</div>` : ''}
          </div>
        </div>` : `<p class="muted">${s.message ? esc(s.message) : 'Idle. Slice something and hit Print.'}</p>`}
      ${printing ? `
        <div class="grid2" style="margin-top:12px">
          ${s.state === 'paused' ? `<button class="btn ok" data-act="resume">Resume</button>` : `<button class="btn" data-act="pause">Pause</button>`}
          <button class="btn danger" data-act="cancel">Cancel</button>
        </div>` : ''}`;
    $$('[data-act]', card).forEach((b) => b.onclick = () => this.action(b.dataset.act));
  }

  renderTemps(s) {
    const card = $('#temps-card', this.root);
    if (!s.ok) { card.classList.add('hidden'); return; }
    card.classList.remove('hidden');
    const names = { extruder: 'Nozzle', heater_bed: 'Bed' };
    const rows = Object.entries(s.temps || {}).sort(([a], [b]) => (a === 'extruder' ? -1 : b === 'extruder' ? 1 : a === 'heater_bed' ? -1 : b === 'heater_bed' ? 1 : a.localeCompare(b)));
    const active = document.activeElement;
    const focusedKey = active && active.dataset && active.dataset.heater;
    card.innerHTML = `<h3>Temperatures</h3>` + rows.map(([key, t]) => {
      const heater = key === 'extruder' || key === 'heater_bed' || key.startsWith('heater_generic');
      const label = names[key] || key.replace(/^(temperature_sensor|temperature_fan|heater_generic) /, '').replace(/_/g, ' ');
      return `<div class="temp-row">
        <div><div class="temp-name">${esc(label)}</div>
          <div class="temp-val">${t.actual != null ? t.actual.toFixed(1) : '–'}°${heater ? ` <span class="target">/ ${t.target ? t.target.toFixed(0) : 'off'}</span>` : ''}</div></div>
        ${heater ? `<div class="row"><input class="input temp-input" type="number" inputmode="numeric" data-heater="${esc(key)}" placeholder="${t.target || 0}" ${focusedKey === key ? 'autofocus' : ''}>
          <button class="btn sm" data-set="${esc(key)}">Set</button><button class="btn sm ghost" data-off="${esc(key)}">Off</button></div>` : ''}
      </div>`;
    }).join('');
    if (focusedKey) { const i = $(`[data-heater="${CSS.escape(focusedKey)}"]`, card); if (i) i.focus(); }
    $$('[data-set]', card).forEach((b) => b.onclick = () => {
      const v = Number($(`[data-heater="${CSS.escape(b.dataset.set)}"]`, card).value);
      if (v > 0) this.post('temperature', { heater: b.dataset.set, target: v });
    });
    $$('[data-off]', card).forEach((b) => b.onclick = () => this.post('temperature', { heater: b.dataset.off, target: 0 }));
    $$('[data-heater]', card).forEach((i) => i.addEventListener('keydown', (e) => { if (e.key === 'Enter') $(`[data-set="${CSS.escape(i.dataset.heater)}"]`, card).click(); }));
  }

  renderControls(s) {
    const card = $('#controls-card', this.root);
    if (!s.ok) { card.classList.add('hidden'); return; }
    if (card.dataset.built) { // only update sliders' labels
      $('#speed-val', card).textContent = `${Math.round((s.speed_factor ?? 1) * 100)}%`;
      $('#flow-val', card).textContent = `${Math.round((s.extrude_factor ?? 1) * 100)}%`;
      $('#fan-val', card).textContent = `${Math.round((s.fan ?? 0) * 100)}%`;
      if (!this.dragging) {
        $('#speed', card).value = Math.round((s.speed_factor ?? 1) * 100);
        $('#flow', card).value = Math.round((s.extrude_factor ?? 1) * 100);
        $('#fan', card).value = Math.round((s.fan ?? 0) * 100);
      }
      card.classList.remove('hidden');
      return;
    }
    card.dataset.built = '1';
    card.classList.remove('hidden');
    card.innerHTML = `
      <h3>Controls</h3>
      <div class="stack">
        <div class="field"><label class="row between"><span>Speed</span><span id="speed-val"></span></label><input type="range" id="speed" min="50" max="200" step="5"></div>
        <div class="field"><label class="row between"><span>Flow</span><span id="flow-val"></span></label><input type="range" id="flow" min="80" max="120" step="1"></div>
        <div class="field"><label class="row between"><span>Part fan</span><span id="fan-val"></span></label><input type="range" id="fan" min="0" max="100" step="5"></div>
        <div class="grid3">
          <button class="btn" data-act="home">Home all</button>
          <button class="btn" id="gcode-btn">G-code…</button>
          <button class="btn danger" data-act="estop">E-STOP</button>
        </div>
      </div>`;
    const bind = (id, action, scale) => {
      const i = $(`#${id}`, card);
      i.addEventListener('pointerdown', () => { this.dragging = true; });
      i.addEventListener('input', () => { $(`#${id}-val`, card).textContent = `${i.value}%`; });
      i.addEventListener('change', () => { this.dragging = false; this.post(action, { factor: Number(i.value) / scale }); });
    };
    bind('speed', 'speed', 100); bind('flow', 'flow', 100); bind('fan', 'fan', 100);
    $$('[data-act]', card).forEach((b) => b.onclick = () => this.action(b.dataset.act));
    $('#gcode-btn', card).onclick = () => {
      const { root, close } = modal(`<h2>Send G-code</h2><form id="gf" class="stack"><input class="input mono" name="script" placeholder="e.g. G28 or SET_PRESSURE_ADVANCE ADVANCE=0.04" autocapitalize="characters"><button class="btn primary block">Send</button></form>`);
      root.querySelector('#gf').onsubmit = async (e) => { e.preventDefault(); const sc = e.target.script.value.trim(); if (!sc) return; close(); await this.post('gcode', { script: sc }); };
    };
    this.renderControls(s);
  }

  // -------------------------------------------------------------- actions
  async action(act) {
    const confirms = {
      cancel: ['Cancel print?', 'The current print will be stopped.', { okLabel: 'Cancel print', danger: true }],
      estop: ['Emergency stop?', 'Klipper will halt immediately and need a firmware restart.', { okLabel: 'STOP', danger: true }],
      firmware_restart: ['Restart Klipper firmware?', 'Any running print is lost.', { okLabel: 'Restart', danger: true }],
    };
    if (confirms[act] && !(await confirmDialog(...confirms[act]))) return;
    await this.post(act);
  }

  async post(act, body) {
    try { await api.post(`/api/printer/${act}`, body || {}); toast('OK', 'ok'); }
    catch (e) { toast(e.message, 'error'); }
  }

  // --------------------------------------------------------------- webcam
  async loadCams() {
    const card = $('#webcam-card', this.root);
    let cams;
    try { cams = await api.get('/api/printer/webcams'); } catch { return; }
    const stream = cams.configured.stream_url, snap = cams.configured.snapshot_url;
    if (!stream && !snap) {
      if (cams.discovered.length) {
        card.innerHTML = `<div class="card flat small muted">Webcam found in Moonraker (${esc(cams.discovered[0].name || 'webcam')}). Enable it under Settings → Webcam.</div>`;
      }
      return;
    }
    if (stream) {
      card.innerHTML = `<img id="cam-img" class="webcam" src="/api/printer/webcam/stream?t=${Date.now()}" alt="Webcam">`;
    } else {
      card.innerHTML = `<img id="cam-img" class="webcam" src="/api/printer/webcam/snapshot?t=${Date.now()}" alt="Webcam">`;
      const img = $('#cam-img', card);
      const refresh = () => { if (!img.isConnected) return; img.src = `/api/printer/webcam/snapshot?t=${Date.now()}`; setTimeout(refresh, 3000); };
      setTimeout(refresh, 3000);
    }
    $('#cam-img', card).onerror = () => { card.innerHTML = `<div class="card flat small muted">Webcam stream unavailable.</div>`; };
  }

  async loadMacros() {
    const card = $('#macros-card', this.root);
    try {
      const r = await api.get('/api/printer/macros');
      const wanted = r.macros.filter((m) => !/^(PAUSE|RESUME|CANCEL_PRINT|START_PRINT|PRINT_START|PRINT_END|END_PRINT|M\d+|G\d+|SET_.*|_.*)$/.test(m));
      if (!wanted.length) return;
      card.innerHTML = `<div class="card"><h3>Macros</h3><div class="chips">${wanted.map((m) => `<button class="chip" data-m="${esc(m)}">${esc(m)}</button>`).join('')}</div></div>`;
      $$('[data-m]', card).forEach((b) => b.onclick = () => this.post('macro', { name: b.dataset.m }));
    } catch { /* offline */ }
  }
}
