import { api } from '../api.js';
import { $, $$, el, esc, fmtAgo, fmtBytes, fmtDuration, modal, state, toast, confirmDialog } from '../app.js';

const LS_KEY = 'pocketslice.presetChoice';

export class SliceView {
  constructor() {
    this.model = null;      // {id, name, size}
    this.job = null;
    this.viewer = null;
    this.pollTimer = null;
    this.choice = JSON.parse(localStorage.getItem(LS_KEY) || 'null') || {};
  }

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <div class="stack">
        <section id="upload-card"></section>
        <section id="preset-card" class="hidden"></section>
        <section id="job-card" class="hidden"></section>
        <section id="history-card"></section>
      </div>`;
    this.renderUpload();
    try { state.presets = await api.get('/api/presets'); } catch (e) { toast(e.message, 'error'); }
    if (state.presets && !state.presets.machine.length) this.renderNoPresets();
    // opened through the PWA share target (POST /share redirects here with ?model=)
    const params = new URLSearchParams(location.search);
    if (params.get('error') === 'unsupported') toast('Unsupported file type', 'error');
    const shared = params.get('model');
    if (shared) {
      history.replaceState(null, '', '/#slice');
      try {
        const m = (await api.get('/api/models')).find((x) => x.id === shared);
        if (m) { this.model = m; await this.renderPresets(null); }
      } catch (e) { toast(e.message, 'error'); }
    } else if (this.model && !this.job) {
      await this.renderPresets(null);
    } else if (this.job) {
      await this.renderPresets(null);
      this.renderJob();
      if (this.job.status === 'queued' || this.job.status === 'running') this.pollJob();
    }
    this.renderHistory();
  }

  unmount() {
    clearTimeout(this.pollTimer);
    if (this.viewer) { this.viewer.destroy(); this.viewer = null; }
  }

  // ---------------------------------------------------------------- upload
  renderUpload() {
    const card = $('#upload-card', this.root);
    card.innerHTML = `
      <div class="dropzone" id="drop">
        <strong>Add a model</strong>
        <span class="small">STL, 3MF, OBJ or STEP · tap to choose</span>
        <input type="file" id="file-input" accept=".stl,.3mf,.obj,.step,.stp,model/stl,application/octet-stream" hidden>
      </div>
      <div id="upload-progress" class="hidden" style="margin-top:10px">
        <div class="row between small muted"><span id="upload-name" class="ellipsis"></span><span id="upload-pct">0%</span></div>
        <div class="progress"><div style="width:0%"></div></div>
      </div>`;
    const drop = $('#drop', card), input = $('#file-input', card);
    drop.onclick = () => input.click();
    input.onchange = () => { if (input.files[0]) this.upload(input.files[0]); input.value = ''; };
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('drag'); }));
    drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) this.upload(f); });
  }

  async upload(file) {
    const prog = $('#upload-progress', this.root), bar = $('.progress > div', prog);
    prog.classList.remove('hidden');
    $('#upload-name', prog).textContent = file.name;
    try {
      const m = await api.upload('/api/models', file, (p) => {
        bar.style.width = `${Math.round(p * 100)}%`; $('#upload-pct', prog).textContent = `${Math.round(p * 100)}%`;
      });
      this.model = m;
      this.job = null;
      $('#job-card', this.root).classList.add('hidden');
      toast(`Uploaded ${m.name}`, 'ok');
      this.renderPresets(file);
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      setTimeout(() => prog.classList.add('hidden'), 600);
    }
  }

  // --------------------------------------------------------------- presets
  renderNoPresets() {
    $('#preset-card', this.root).classList.remove('hidden');
    $('#preset-card', this.root).innerHTML = `
      <div class="card">
        <h2>No OrcaSlicer presets found</h2>
        <p class="muted">Copy your OrcaSlicer configuration folder (the <span class="mono">user</span> and <span class="mono">system</span> folders)
        into <span class="mono">${esc(state.presets?.profiles_dir || '/profiles')}</span> on the server, or import exported presets under Settings.</p>
      </div>`;
  }

  async renderPresets(file) {
    const card = $('#preset-card', this.root);
    card.classList.remove('hidden');
    const p = state.presets;
    if (!p || !p.machine.length) { this.renderNoPresets(); return; }
    const pick = (type) => this.choice[type] && p[type].some((x) => x.id === this.choice[type]) ? this.choice[type] : p.defaults[type] || (p[type][0] && p[type][0].id);
    const sel = { machine: pick('machine'), process: pick('process'), filament: pick('filament') };
    const is3mf = /\.3mf$/i.test(this.model.name);
    const showAll = this.choice.showAll || false;

    const options = (type) => p[type]
      .filter((x) => showAll || x.compatible !== false || x.id === sel[type])
      .map((x) => `<option value="${x.id}" ${x.id === sel[type] ? 'selected' : ''}>${esc(x.name)}${x.compatible === false ? ' (other printer)' : ''}</option>`).join('');

    card.innerHTML = `
      <div class="card">
        <div class="card-title"><h2 class="ellipsis">${esc(this.model.name)}</h2><span class="muted small">${fmtBytes(this.model.size)}</span></div>
        <div class="viewer" id="viewer"></div>
        <div class="stack" style="margin-top:12px">
          ${is3mf ? `<label class="switch"><span>Use settings embedded in the 3MF</span><input type="checkbox" id="embedded"></label>` : ''}
          <div id="preset-fields" class="stack">
            <div class="field"><label>Printer</label><select class="input" id="sel-machine">${options('machine')}</select></div>
            <div class="field"><label>Process</label><select class="input" id="sel-process">${options('process')}</select></div>
            <div class="field"><label>Filament</label><select class="input" id="sel-filament">${options('filament')}</select></div>
            <div class="shrink-card">
              <div><div>Shrinkage compensation</div><div class="small muted" id="shrink-info">Reading preset…</div></div>
              <input type="checkbox" id="shrink" class="switch-input" ${(this.choice.shrink ?? (state.settings.shrinkage_default !== 'off')) ? 'checked' : ''}>
            </div>
            <label class="switch"><span class="muted small">Show presets for other printers${p.machine_filtered ? ' (and all printer models)' : ''}</span><input type="checkbox" id="show-all" ${showAll ? 'checked' : ''}></label>
            <details>
              <summary>Quick overrides</summary>
              <div class="stack" style="margin-top:10px" id="overrides"></div>
            </details>
            <div class="grid2">
              <label class="switch"><span>Auto-arrange</span><input type="checkbox" id="arrange" ${state.settings.auto_arrange ? 'checked' : ''}></label>
              <label class="switch"><span>Auto-orient</span><input type="checkbox" id="orient" ${state.settings.auto_orient ? 'checked' : ''}></label>
            </div>
          </div>
          <button class="btn primary block" id="slice-btn">Slice</button>
        </div>
      </div>`;

    // 3D preview (STL only; other formats show a placeholder)
    if (/\.stl$/i.test(this.model.name)) {
      try {
        const { ModelViewer } = await import('../viewer.js');
        this.viewer = new ModelViewer($('#viewer', card));
        if (file) this.viewer.loadBuffer(await file.arrayBuffer());
        else await this.viewer.loadUrl(`/api/models/${this.model.id}/file`);
      } catch (e) {
        $('#viewer', card).innerHTML = `<div class="empty small">Preview unavailable: ${esc(e.message)}</div>`;
      }
    } else {
      $('#viewer', card).innerHTML = `<div class="empty">No preview for ${esc(this.model.name.split('.').pop().toUpperCase())} files</div>`;
    }

    // overrides
    const ov = $('#overrides', card);
    for (const f of state.overrideFields) {
      let input;
      if (f.type === 'bool') input = `<select class="input" data-ov="${f.key}"><option value="">Preset default</option><option value="true">On</option><option value="false">Off</option></select>`;
      else if (f.type === 'select') input = `<select class="input" data-ov="${f.key}"><option value="">Preset default</option>${f.options.map((o) => `<option value="${o}">${o.replace(/_/g, ' ')}</option>`).join('')}</select>`;
      else input = `<input class="input" data-ov="${f.key}" type="number" inputmode="decimal" placeholder="Preset default" step="${f.step || 1}" min="${f.min ?? ''}" max="${f.max ?? ''}">`;
      ov.appendChild(el(`<div class="field"><label>${esc(f.label)}</label>${input}</div>`));
    }

    const persist = () => {
      this.choice = { machine: $('#sel-machine', card).value, process: $('#sel-process', card).value, filament: $('#sel-filament', card).value, showAll: $('#show-all', card).checked, shrink: $('#shrink', card).checked };
      localStorage.setItem(LS_KEY, JSON.stringify(this.choice));
    };
    const shrinkInfo = async () => {
      const info = $('#shrink-info', card);
      try {
        const flat = await api.get(`/api/presets/${$('#sel-filament', card).value}/flat`);
        const xy = (flat.filament_shrink || ['100%'])[0], z = (flat.filament_shrinkage_compensation_z || ['100%'])[0];
        this.presetShrink = { xy, z };
        const none = parseFloat(xy) === 100 && parseFloat(z) === 100;
        info.textContent = none ? 'Preset has no shrinkage set (100%)' : `Preset: XY ${xy}${parseFloat(z) !== 100 ? ` · Z ${z}` : ''}`;
        $('#shrink', card).disabled = none;
      } catch (e) { info.textContent = e.message; }
    };
    shrinkInfo();
    $('#sel-filament', card).addEventListener('change', shrinkInfo);
    $('#shrink', card).addEventListener('change', persist);
    $$('select', card).forEach((s) => s.addEventListener('change', persist));
    $('#sel-machine', card).addEventListener('change', async () => {
      // refresh compatibility flags for the newly chosen printer
      try { state.presets = await api.get(`/api/presets?machine=${encodeURIComponent($('#sel-machine', card).value)}&all_machines=${$('#show-all', card).checked}`); } catch { /* keep */ }
      persist(); this.renderPresets();
    });
    $('#show-all', card).addEventListener('change', async () => {
      persist();
      if (state.presets.machine_filtered || this.choice.showAll) {
        try { state.presets = await api.get(`/api/presets?all_machines=${this.choice.showAll}`); } catch { /* keep */ }
      }
      this.renderPresets();
    });
    const emb = $('#embedded', card);
    if (emb) emb.addEventListener('change', () => $('#preset-fields', card).classList.toggle('hidden', emb.checked));
    $('#slice-btn', card).onclick = () => this.slice(card);
  }

  async slice(card) {
    const embedded = $('#embedded', card)?.checked;
    const overrides = {};
    for (const i of $$('[data-ov]', card)) {
      if (i.value === '') continue;
      overrides[i.dataset.ov] = i.value === 'true' ? true : i.value === 'false' ? false : (i.type === 'number' ? Number(i.value) : i.value);
    }
    if (overrides.sparse_infill_density != null) overrides.sparse_infill_density = `${overrides.sparse_infill_density}%`;
    if (!$('#shrink', card).checked) { overrides.filament_shrink = '100%'; overrides.filament_shrinkage_compensation_z = '100%'; }
    const body = {
      model_id: this.model.id,
      machine: embedded ? null : $('#sel-machine', card).value,
      process: embedded ? null : $('#sel-process', card).value,
      filament: embedded ? null : $('#sel-filament', card).value,
      overrides, arrange: $('#arrange', card).checked, orient: $('#orient', card).checked,
    };
    $('#slice-btn', card).disabled = true;
    try {
      this.job = await api.post('/api/jobs', body);
      this.renderJob();
      this.pollJob();
      $('#job-card', this.root).scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
      toast(e.message, 'error');
    } finally {
      $('#slice-btn', card).disabled = false;
    }
  }

  // ------------------------------------------------------------------- job
  pollJob() {
    clearTimeout(this.pollTimer);
    const tick = async () => {
      try {
        this.job = await api.get(`/api/jobs/${this.job.id}`);
      } catch (e) { toast(e.message, 'error'); return; }
      this.renderJob();
      if (this.job.status === 'queued' || this.job.status === 'running') this.pollTimer = setTimeout(tick, 1000);
      else this.renderHistory();
    };
    this.pollTimer = setTimeout(tick, 800);
  }

  renderJob() {
    const j = this.job, card = $('#job-card', this.root);
    card.classList.remove('hidden');
    if (j.status === 'queued' || j.status === 'running') {
      card.innerHTML = `
        <div class="card">
          <div class="card-title"><h2>Slicing…</h2><span class="badge accent">${esc(j.phase || j.status)}</span></div>
          <div class="progress indeterminate"><div></div></div>
          <p class="muted small" style="margin:10px 0 0">${esc(j.model_name)} · ${fmtDuration(j.duration)} elapsed · ${j.log_lines} log lines</p>
        </div>`;
      return;
    }
    if (j.status === 'error') {
      card.innerHTML = `
        <div class="card">
          <div class="card-title"><h2>Slicing failed</h2><span class="badge danger">error</span></div>
          <p>${esc(j.error)}</p>
          ${j.log_tail ? `<details><summary>Slicer log</summary><pre class="log mono">${esc(j.log_tail)}</pre></details>` : ''}
          <button class="btn block" id="show-log">Full log</button>
        </div>`;
      $('#show-log', card).onclick = () => this.showLog(j);
      return;
    }
    card.innerHTML = this.jobResultHtml(j);
    this.bindJobResult(card, j);
  }

  jobResultHtml(j) {
    const m = j.meta || {};
    const sent = j.status === 'sent';
    return `
      <div class="card">
        <div class="card-title"><h2>Ready to print</h2><span class="badge ${sent ? 'info' : 'ok'}">${sent ? 'on printer' : 'sliced'}</span></div>
        <div class="row" style="align-items:flex-start">
          ${j.has_thumbnail ? `<img class="thumb lg" src="/api/jobs/${j.id}/thumbnail" alt="">` : ''}
          <div class="grow">
            <div class="ellipsis" style="font-weight:600">${esc(j.gcode_name)}</div>
            <div class="muted small">${esc(j.presets.process || 'embedded settings')}</div>
            <div class="muted small">${esc(j.presets.filament || '')}</div>
            <div class="muted small">${esc(j.presets.machine || '')}</div>
            ${j.request?.overrides?.filament_shrink ? '<span class="badge warn">shrinkage off</span>' : ''}
          </div>
        </div>
        <div class="grid3" style="margin-top:12px">
          <div class="stat"><div class="v">${fmtDuration(m.estimated_time)}</div><div class="k">Print time</div></div>
          <div class="stat"><div class="v">${m.filament_g != null ? m.filament_g.toFixed(0) + ' g' : '–'}</div><div class="k">Filament</div></div>
          <div class="stat"><div class="v">${m.layer_count ?? '–'}</div><div class="k">Layers</div></div>
        </div>
        <div class="muted small" style="margin-top:8px">${m.layer_height ? `${m.layer_height} mm layers · ` : ''}${m.filament_mm ? `${(m.filament_mm / 1000).toFixed(2)} m · ` : ''}${m.max_z ? `${m.max_z} mm tall · ` : ''}${fmtBytes(m.size)}${j.duration ? ` · sliced in ${j.duration < 60 ? Math.round(j.duration) + ' s' : fmtDuration(j.duration)}` : ''}</div>
        <div class="field" style="margin-top:12px"><label>File name on printer</label><input class="input" id="send-name" value="${esc(j.gcode_name)}"></div>
        <div class="grid2" style="margin-top:12px">
          <button class="btn" id="send-btn">Send to printer</button>
          <button class="btn primary" id="print-btn">Print now</button>
        </div>
        ${sent ? `<p class="muted small center" style="margin:10px 0 0">Uploaded as ${esc(j.printer_file)}</p>` : ''}
        <div class="row" style="margin-top:10px; justify-content:center; gap:16px">
          <a class="small" href="/api/jobs/${j.id}/gcode" download>Download G-code</a>
          <a class="small" href="#" id="log-link">Slicer log</a>
        </div>
      </div>`;
  }

  bindJobResult(card, j) {
    const send = async (print) => {
      const filename = $('#send-name', card).value.trim();
      if (print && state.printer && state.printer.state === 'printing') {
        toast('Printer is busy – file will be uploaded only', 'error'); print = false;
      }
      if (print && !(await confirmDialog('Start print?', `Send ${filename} to ${state.settings.printer_name} and start printing right away.`, { okLabel: 'Print' }))) return;
      $('#send-btn', card).disabled = $('#print-btn', card).disabled = true;
      try {
        const r = await api.post(`/api/jobs/${j.id}/send`, { print, filename });
        toast(r.print_started ? 'Print started!' : 'Sent to printer', 'ok');
        this.job = await api.get(`/api/jobs/${j.id}`);
        this.renderJob(); this.renderHistory();
        if (r.print_started) $$('.tab').find((t) => t.dataset.tab === 'printer').click();
      } catch (e) { toast(e.message, 'error'); }
      finally { const b1 = $('#send-btn', card), b2 = $('#print-btn', card); if (b1) b1.disabled = false; if (b2) b2.disabled = false; }
    };
    $('#send-btn', card).onclick = () => send(false);
    $('#print-btn', card).onclick = () => send(true);
    $('#log-link', card).onclick = (e) => { e.preventDefault(); this.showLog(j); };
  }

  async showLog(j) {
    try {
      const r = await api.get(`/api/jobs/${j.id}/log?tail=400`);
      modal(`<h2>Slicer log</h2><pre class="log mono" style="max-height:60vh">${esc(r.lines.join('\n')) || '(empty)'}</pre>`);
    } catch (e) { toast(e.message, 'error'); }
  }

  // --------------------------------------------------------------- history
  async renderHistory() {
    const card = $('#history-card', this.root);
    let jobs = [];
    try { jobs = await api.get('/api/jobs'); } catch { return; }
    jobs = jobs.filter((j) => !this.job || j.id !== this.job.id).slice(0, 15);
    if (!jobs.length) { card.innerHTML = ''; return; }
    card.innerHTML = `<div class="card"><h3>Recent slices</h3><div class="list">${jobs.map((j) => `
      <div class="list-item" data-id="${j.id}">
        ${j.has_thumbnail ? `<img class="thumb" src="/api/jobs/${j.id}/thumbnail" alt="">` : `<div class="thumb"></div>`}
        <div class="grow">
          <div class="ellipsis" style="font-weight:600">${esc(j.gcode_name || j.model_name)}</div>
          <div class="muted small">${j.status === 'error' ? `<span class="badge danger">failed</span> ` : j.status === 'sent' ? `<span class="badge info">on printer</span> ` : ''}${fmtAgo(j.created)}${j.meta?.estimated_time ? ` · ${fmtDuration(j.meta.estimated_time)}` : ''}${j.presets?.filament ? ` · ${esc(j.presets.filament)}` : ''}</div>
        </div>
        <button class="btn sm ghost icon" data-del="${j.id}" aria-label="Delete"><svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button>
      </div>`).join('')}</div></div>`;
    $$('.list-item', card).forEach((item) => item.addEventListener('click', (e) => {
      if (e.target.closest('[data-del]')) return;
      const j = jobs.find((x) => x.id === item.dataset.id);
      this.job = j; this.renderJob(); $('#job-card', this.root).scrollIntoView({ behavior: 'smooth' });
    }));
    $$('[data-del]', card).forEach((b) => b.addEventListener('click', async (e) => {
      e.stopPropagation();
      await api.del(`/api/jobs/${b.dataset.del}`); this.renderHistory();
    }));
  }
}
