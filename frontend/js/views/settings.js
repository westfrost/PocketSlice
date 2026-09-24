import { api } from '../api.js';
import { $, $$, esc, reloadSettings, state, toast } from '../app.js';

export class SettingsView {
  async mount(root) {
    this.root = root;
    const s = state.settings;
    let presets = state.presets;
    try { presets = state.presets = await api.get('/api/presets'); } catch { /* ignore */ }
    const counts = presets?.counts || {};
    const opt = (type, current) => `<option value="">Last used in OrcaSlicer${presets?.last_used?.[type] ? ` (${esc(presets.last_used[type])})` : ''}</option>` +
      (presets?.[type] || []).map((p) => `<option value="${esc(p.name)}" ${p.name === current ? 'selected' : ''}>${esc(p.name)}</option>`).join('');

    root.innerHTML = `
      <form id="settings-form" class="stack">
        <div class="card">
          <h2>Printer</h2>
          <div class="stack">
            <div class="field"><label>Name</label><input class="input" name="printer_name" value="${esc(s.printer_name)}"></div>
            <div class="field"><label>Moonraker URL</label><input class="input" name="moonraker_url" inputmode="url" placeholder="http://voron.local:7125" value="${esc(s.moonraker_url)}"></div>
            <div class="field"><label>Moonraker API key ${s.moonraker_api_key_set ? '<span class="badge ok">set</span>' : '<span class="muted">(optional)</span>'}</label>
              <input class="input" name="moonraker_api_key" type="password" placeholder="${s.moonraker_api_key_set ? 'leave empty to keep' : 'only if Moonraker requires it'}"></div>
            <div class="field"><label>Upload folder on printer</label><input class="input" name="gcode_subfolder" value="${esc(s.gcode_subfolder)}" placeholder="(root)"></div>
            <button class="btn" type="button" id="test-btn">Test connection</button>
            <div id="test-result" class="small muted"></div>
          </div>
        </div>

        <div class="card">
          <h2>Webcam</h2>
          <div class="stack">
            <div class="field"><label>MJPEG stream URL</label><input class="input" name="webcam_stream_url" inputmode="url" placeholder="http://voron.local/webcam/?action=stream" value="${esc(s.webcam_stream_url)}"></div>
            <div class="field"><label>Snapshot URL (fallback)</label><input class="input" name="webcam_snapshot_url" inputmode="url" placeholder="http://voron.local/webcam/?action=snapshot" value="${esc(s.webcam_snapshot_url)}"></div>
            <div id="cam-discover" class="small muted"></div>
          </div>
        </div>

        <div class="card">
          <h2>Slicing defaults</h2>
          <div class="stack">
            <div class="field"><label>Printer preset</label><select class="input" name="default_machine">${opt('machine', s.default_machine)}</select></div>
            <div class="field"><label>Process preset</label><select class="input" name="default_process">${opt('process', s.default_process)}</select></div>
            <div class="field"><label>Filament preset</label><select class="input" name="default_filament">${opt('filament', s.default_filament)}</select></div>
            <label class="switch"><span>Auto-arrange by default</span><input type="checkbox" name="auto_arrange" ${s.auto_arrange ? 'checked' : ''}></label>
            <label class="switch"><span>Auto-orient by default</span><input type="checkbox" name="auto_orient" ${s.auto_orient ? 'checked' : ''}></label>
          </div>
        </div>

        <button class="btn primary block" type="submit">Save settings</button>
      </form>

      <div class="card" style="margin-top:12px">
        <h2>OrcaSlicer presets</h2>
        <p class="muted small">Found ${counts.machine?.user ?? 0} printer, ${counts.process?.user ?? 0} process and ${counts.filament?.user ?? 0} filament presets of yours
          (+ ${(counts.machine?.system ?? 0) + (counts.process?.system ?? 0) + (counts.filament?.system ?? 0)} system presets) in <span class="mono">${esc(presets?.profiles_dir || '')}</span>.</p>
        <p class="muted small">Keep them in sync with your PC using <span class="mono">scripts/sync-profiles.ps1</span> (Windows) or <span class="mono">scripts/sync-profiles.sh</span>, or import exported presets here.</p>
        <div class="grid2">
          <button class="btn" id="reload-btn">Rescan folder</button>
          <button class="btn" id="import-btn">Import preset…</button>
          <input type="file" id="import-input" accept=".json,.orca_printer,.orca_filament,.zip" hidden>
        </div>
        ${presets?.errors?.length ? `<pre class="log mono" style="margin-top:10px">${esc(presets.errors.join('\n'))}</pre>` : ''}
      </div>

      <div class="card" style="margin-top:12px">
        <h2>About</h2>
        <p class="muted small">PocketSlice ${esc(state.health?.version || '')} · OrcaSlicer CLI ${state.health?.orca_present ? '<span class="badge ok">found</span>' : '<span class="badge danger">missing</span>'} at <span class="mono">${esc(state.health?.orca_bin || '')}</span></p>
        ${state.health?.auth_required ? `<button class="btn sm ghost" id="logout-btn">Log out</button>` : ''}
      </div>`;

    $('#settings-form', root).onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {};
      for (const [k, v] of fd.entries()) body[k] = v;
      body.auto_arrange = fd.get('auto_arrange') === 'on';
      body.auto_orient = fd.get('auto_orient') === 'on';
      if (!body.moonraker_api_key) body.moonraker_api_key = null;
      try { await api.put('/api/settings', body); await reloadSettings(); toast('Saved', 'ok'); }
      catch (err) { toast(err.message, 'error'); }
    };
    $('#test-btn', root).onclick = async () => {
      const out = $('#test-result', root);
      out.textContent = 'Testing…';
      try {
        // save URL/key first so the test uses them
        const fd = new FormData($('#settings-form', root));
        await api.put('/api/settings', { moonraker_url: fd.get('moonraker_url'), moonraker_api_key: fd.get('moonraker_api_key') || null });
        const r = await api.get('/api/settings/test-printer');
        out.innerHTML = r.ok ? `<span style="color:var(--ok)">Connected to ${esc(r.hostname)} · Klipper ${esc(r.state)} · ${esc(r.software || '')}</span>` : `<span style="color:var(--danger)">${esc(r.error)}</span>`;
        if (r.ok) this.discoverCams();
      } catch (err) { out.innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>`; }
    };
    $('#reload-btn', root).onclick = async () => {
      try { const r = await api.post('/api/presets/reload'); toast(`Rescanned: ${r.counts.machine.user}/${r.counts.process.user}/${r.counts.filament.user} user presets`, 'ok'); this.mount(root); }
      catch (err) { toast(err.message, 'error'); }
    };
    $('#import-btn', root).onclick = () => $('#import-input', root).click();
    $('#import-input', root).onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      try { const r = await api.upload('/api/presets/import', f); toast(`Imported ${r.imported} preset file(s)`, 'ok'); this.mount(root); }
      catch (err) { toast(err.message, 'error'); }
    };
    const lo = $('#logout-btn', root);
    if (lo) lo.onclick = async () => { await api.post('/api/logout'); location.reload(); };
    this.discoverCams();
  }

  async discoverCams() {
    const box = $('#cam-discover', this.root);
    try {
      const r = await api.get('/api/printer/webcams');
      if (!r.discovered.length) return;
      box.innerHTML = 'Found in Moonraker: ' + r.discovered.map((c, i) => `<a href="#" data-cam="${i}">${esc(c.name || 'webcam')}</a>`).join(', ') + ' (tap to use)';
      $$('[data-cam]', box).forEach((a) => a.onclick = (e) => {
        e.preventDefault();
        const c = r.discovered[Number(a.dataset.cam)];
        $('[name=webcam_stream_url]', this.root).value = c.stream_url || '';
        $('[name=webcam_snapshot_url]', this.root).value = c.snapshot_url || '';
      });
    } catch { /* offline */ }
  }
}
