import { api } from '../api.js';
import { $, $$, esc, reloadSettings, state, toast, applyTheme } from '../app.js';
import { presetSourcesHtml, bindPresetSources } from './preset_sources.js';

export class SettingsView {
  async mount(root) {
    this.root = root;
    const s = state.settings;
    let presets = state.presets;
    try { presets = state.presets = await api.get('/api/presets'); } catch { /* ignore */ }
    const opt = (type, current) => `<option value="">Last used in OrcaSlicer${presets?.last_used?.[type] ? ` (${esc(presets.last_used[type])})` : ''}</option>` +
      (presets?.[type] || []).map((p) => `<option value="${esc(p.name)}" ${p.name === current ? 'selected' : ''}>${esc(p.name)}</option>`).join('');

    root.innerHTML = `
      <div class="stack">
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
              <div class="field"><label>Shrinkage compensation</label>
                <select class="input" name="shrinkage_default">
                  <option value="preset" ${s.shrinkage_default !== 'off' ? 'selected' : ''}>On – use the filament preset's value</option>
                  <option value="off" ${s.shrinkage_default === 'off' ? 'selected' : ''}>Off by default</option>
                </select></div>
              <label class="switch"><span>Auto-arrange by default</span><input type="checkbox" name="auto_arrange" ${s.auto_arrange ? 'checked' : ''}></label>
              <label class="switch"><span>Auto-orient by default</span><input type="checkbox" name="auto_orient" ${s.auto_orient ? 'checked' : ''}></label>
            </div>
          </div>

          <div class="card">
            <h2>Appearance</h2>
            <div class="row between">
              <span>Accent colour</span>
              <div class="chips">
                ${['#ff7a2f', '#3ecf8e', '#5aa9ff', '#c084fc', '#f5c542', '#ff5d5d'].map((c) => `<button type="button" class="swatch ${c === s.accent_color ? 'active' : ''}" data-color="${c}" style="background:${c}" aria-label="${c}"></button>`).join('')}
                <input type="color" name="accent_color" value="${esc(s.accent_color || '#ff7a2f')}" class="swatch-input" aria-label="Custom colour">
              </div>
            </div>
          </div>

          <button class="btn primary block" type="submit">Save settings</button>
        </form>

        <div class="card">
          <h2>OrcaSlicer presets</h2>
          ${presetSourcesHtml()}
        </div>

        <div class="card">
          <h2>Security</h2>
          <p class="muted small">${s.password_set ? 'A password protects this app.' : 'No password – anyone on the network can control the printer.'}</p>
          <form id="pw-form" class="stack">
            ${s.password_set ? `<input class="input" name="current_password" type="password" placeholder="Current password" autocomplete="current-password">` : ''}
            <input class="input" name="new_password" type="password" placeholder="${s.password_set ? 'New password (empty = remove)' : 'New password'}" autocomplete="new-password">
            <button class="btn block" type="submit">${s.password_set ? 'Change password' : 'Set password'}</button>
          </form>
          ${state.health?.auth_required ? `<button class="btn sm ghost" id="logout-btn" style="margin-top:8px">Log out on this device</button>` : ''}
        </div>

        <div class="card">
          <h2>About</h2>
          <p class="muted small">PocketSlice ${esc(state.health?.version || '')} · OrcaSlicer CLI ${state.health?.orca_present ? '<span class="badge ok">found</span>' : '<span class="badge danger">missing</span>'} at <span class="mono">${esc(state.health?.orca_bin || '')}</span></p>
          <p class="muted small">Presets folder: <span class="mono">${esc(presets?.profiles_dir || '')}</span></p>
          <div class="grid2">
            <button class="btn sm" id="wizard-btn">Run setup wizard</button>
            <a class="btn sm" href="/api/docs" target="_blank">API docs</a>
          </div>
        </div>
      </div>`;

    $('#settings-form', root).onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const body = {};
      for (const [k, v] of fd.entries()) body[k] = v;
      body.auto_arrange = fd.get('auto_arrange') === 'on';
      body.auto_orient = fd.get('auto_orient') === 'on';
      if (!body.moonraker_api_key) body.moonraker_api_key = null;
      try { await api.put('/api/settings', body); await reloadSettings(); applyTheme(); toast('Saved', 'ok'); }
      catch (err) { toast(err.message, 'error'); }
    };
    $$('.swatch', root).forEach((b) => b.onclick = () => {
      $('[name=accent_color]', root).value = b.dataset.color;
      $$('.swatch', root).forEach((x) => x.classList.toggle('active', x === b));
      document.documentElement.style.setProperty('--accent', b.dataset.color);
    });
    $('[name=accent_color]', root).oninput = (e) => document.documentElement.style.setProperty('--accent', e.target.value);
    $('#test-btn', root).onclick = async () => {
      const out = $('#test-result', root);
      out.textContent = 'Testing…';
      try {
        const fd = new FormData($('#settings-form', root));
        await api.put('/api/settings', { moonraker_url: fd.get('moonraker_url'), moonraker_api_key: fd.get('moonraker_api_key') || null });
        const r = await api.get('/api/settings/test-printer');
        out.innerHTML = r.ok ? `<span style="color:var(--ok)">Connected to ${esc(r.hostname)} · Klipper ${esc(r.state)} · ${esc(r.software || '')}</span>` : `<span style="color:var(--danger)">${esc(r.error)}</span>`;
        if (r.ok) this.discoverCams();
      } catch (err) { out.innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>`; }
    };
    $('#pw-form', root).onsubmit = async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        const r = await api.post('/api/settings/password', { current_password: fd.get('current_password') || '', new_password: fd.get('new_password') || '' });
        state.health.auth_required = r.auth_required;
        await reloadSettings();
        toast(r.auth_required ? 'Password saved' : 'Password removed', 'ok');
        this.mount(root);
      } catch (err) { toast(err.message, 'error'); }
    };
    $('#wizard-btn', root).onclick = () => { location.hash = '#setup'; location.reload(); };
    const lo = $('#logout-btn', root);
    if (lo) lo.onclick = async () => { await api.post('/api/logout'); location.reload(); };
    bindPresetSources(root, () => {
      const c = state.presets?.counts || {};
      const user = (c.machine?.user ?? 0) + (c.process?.user ?? 0) + (c.filament?.user ?? 0);
      $('#ps-count', root).textContent = user ? `${user} presets loaded` : 'no presets yet';
      $('#ps-detail', root).textContent = `${c.machine?.user ?? 0} printer · ${c.process?.user ?? 0} process · ${c.filament?.user ?? 0} filament`;
    });
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
