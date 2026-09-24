// Shared "where do my OrcaSlicer presets come from" panel, used by the setup
// wizard and the Settings tab.
import { api } from '../api.js';
import { $, $$, esc, fmtAgo, modal, state, toast, confirmDialog } from '../app.js';

const isPhone = () => /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);

export function presetSourcesHtml() {
  const c = (state.presets && state.presets.counts) || {};
  const user = (c.machine?.user ?? 0) + (c.process?.user ?? 0) + (c.filament?.user ?? 0);
  return `
    <div id="preset-sources" class="stack">
      <div class="source">
        <div class="row between"><b>Status</b><span class="badge ${user ? 'ok' : 'warn'}" id="ps-count">${user ? `${user} presets loaded` : 'no presets yet'}</span></div>
        <div class="small muted" id="ps-detail">${user ? `${c.machine?.user ?? 0} printer · ${c.process?.user ?? 0} process · ${c.filament?.user ?? 0} filament` : 'Choose one of the options below.'}</div>
      </div>

      <div class="source">
        <b>A · Upload the OrcaSlicer folder from your PC</b> <span class="badge ok">recommended</span>
        <p class="small muted">Open this page in Chrome or Edge <b>on the PC that runs OrcaSlicer</b>, tap the button and pick the OrcaSlicer configuration folder. Nothing to install.</p>
        <div class="small mono muted" style="margin-bottom:8px">Windows: %APPDATA%\\OrcaSlicer &nbsp;·&nbsp; macOS: ~/Library/Application Support/OrcaSlicer &nbsp;·&nbsp; Linux: ~/.config/OrcaSlicer</div>
        ${isPhone() ? `<p class="small" style="color:var(--warn)">You are on a phone – do this step from the PC's browser (same address).</p>` : ''}
        <button class="btn block" id="ps-folder-btn">Choose OrcaSlicer folder…</button>
        <input type="file" id="ps-folder" webkitdirectory directory multiple hidden>
        <div id="ps-folder-progress" class="small muted"></div>
      </div>

      <div class="source">
        <b>B · Orca Cloud</b> <span class="badge">OrcaSlicer 2.4+ · experimental</span>
        <p class="small muted">If you turned on <i>Sync user presets</i> with an Orca account in OrcaSlicer, PocketSlice can pull the same presets from Orca Cloud. This uses undocumented endpoints and may break when Orca changes them.</p>
        <div id="ps-cloud">…</div>
      </div>

      <div class="source">
        <b>C · Import exported presets</b>
        <p class="small muted">In OrcaSlicer: right-click a preset → <i>Export</i>. Upload the <span class="mono">.orca_printer</span>, <span class="mono">.orca_filament</span> or <span class="mono">.json</span> file here. Works from the phone.</p>
        <button class="btn block" id="ps-import-btn">Import preset file…</button>
        <input type="file" id="ps-import" accept=".json,.orca_printer,.orca_filament,.zip" hidden>
      </div>

      <details class="source"><summary>D · Automatic sync script / network share</summary>
        <p class="small muted">Run <span class="mono">scripts/sync-profiles.ps1</span> (Windows) or <span class="mono">scripts/sync-profiles.sh</span> from the PC on a schedule, or mount the server's <span class="mono">profiles</span> folder as a share and start OrcaSlicer with <span class="mono">--datadir</span> pointing at it. See the README.</p>
        <button class="btn sm" id="ps-rescan">Rescan folder</button>
      </details>
    </div>`;
}

export function bindPresetSources(root, onChange) {
  const refresh = async () => {
    try { state.presets = await api.get('/api/presets'); } catch { /* ignore */ }
    if (onChange) onChange();
  };

  // A: folder upload
  const folderInput = $('#ps-folder', root);
  $('#ps-folder-btn', root).onclick = () => folderInput.click();
  folderInput.onchange = async () => {
    const files = [...folderInput.files].filter((f) => /(^|\/)(user|system)\/.*\.json$|(^|\/)OrcaSlicer\.conf$/i.test(f.webkitRelativePath || f.name));
    if (!files.length) { toast('That folder has no user/ or system/ presets', 'error'); return; }
    if (!(await confirmDialog('Replace presets?', `${files.length} preset files will replace the ones on the server.`, { okLabel: 'Upload' }))) return;
    const prog = $('#ps-folder-progress', root);
    prog.textContent = `Uploading ${files.length} files…`;
    const fd = new FormData();
    for (const f of files) fd.append(f.webkitRelativePath || f.name, f, f.name);
    try {
      const r = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/presets/upload-folder'); xhr.withCredentials = true;
        xhr.upload.onprogress = (e) => { if (e.lengthComputable) prog.textContent = `Uploading… ${Math.round(e.loaded / e.total * 100)}%`; };
        xhr.onload = () => { let b = xhr.responseText; try { b = JSON.parse(b); } catch { /* text */ } xhr.status < 300 ? resolve(b) : reject(new Error((b && b.detail) || xhr.statusText)); };
        xhr.onerror = () => reject(new Error('Network error'));
        xhr.send(fd);
      });
      prog.textContent = '';
      toast(`Loaded ${r.counts.machine.user} printer / ${r.counts.process.user} process / ${r.counts.filament.user} filament presets`, 'ok');
      await refresh();
    } catch (e) { prog.textContent = ''; toast(e.message, 'error'); }
    folderInput.value = '';
  };

  // C: bundle import
  $('#ps-import-btn', root).onclick = () => $('#ps-import', root).click();
  $('#ps-import', root).onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const r = await api.upload('/api/presets/import', f); toast(`Imported ${r.imported} preset file(s)`, 'ok'); await refresh(); }
    catch (err) { toast(err.message, 'error'); }
    e.target.value = '';
  };

  // D: rescan
  const rescan = $('#ps-rescan', root);
  if (rescan) rescan.onclick = async () => { try { await api.post('/api/presets/reload'); toast('Rescanned', 'ok'); await refresh(); } catch (err) { toast(err.message, 'error'); } };

  // B: Orca cloud
  renderCloud(root, refresh);
}

async function renderCloud(root, refresh) {
  const box = $('#ps-cloud', root);
  let st;
  try { st = await api.get('/api/orca-cloud/status'); } catch (e) { box.innerHTML = `<span class="small muted">${esc(e.message)}</span>`; return; }
  const minutes = state.settings.orca_cloud_auto_sync_minutes || 0;
  if (st.logged_in) {
    box.innerHTML = `
      <div class="row between"><span>Signed in as <b>${esc(st.email || st.user_id)}</b></span><button class="btn sm ghost" id="oc-logout">Sign out</button></div>
      <div class="small muted">${st.last_sync ? `Last sync ${fmtAgo(st.last_sync)} · ${st.last_sync_count} presets` : 'Not synced yet'}${st.last_error ? ` · <span style="color:var(--danger)">${esc(st.last_error)}</span>` : ''}</div>
      <div class="grid2" style="margin-top:8px">
        <button class="btn primary" id="oc-pull">Sync now</button>
        <select class="input" id="oc-auto">
          <option value="0" ${minutes == 0 ? 'selected' : ''}>Manual only</option>
          <option value="15" ${minutes == 15 ? 'selected' : ''}>Every 15 min</option>
          <option value="60" ${minutes == 60 ? 'selected' : ''}>Every hour</option>
          <option value="360" ${minutes == 360 ? 'selected' : ''}>Every 6 hours</option>
        </select>
      </div>`;
    $('#oc-pull', box).onclick = async () => {
      $('#oc-pull', box).disabled = true;
      try { const r = await api.post('/api/orca-cloud/pull'); toast(`Pulled ${r.count} presets from Orca Cloud`, 'ok'); await refresh(); renderCloud(root, refresh); }
      catch (e) { toast(e.message, 'error'); $('#oc-pull', box).disabled = false; }
    };
    $('#oc-auto', box).onchange = async (e) => { await api.put('/api/settings', { orca_cloud_auto_sync_minutes: Number(e.target.value) }); state.settings.orca_cloud_auto_sync_minutes = Number(e.target.value); toast('Saved', 'ok'); };
    $('#oc-logout', box).onclick = async () => { await api.post('/api/orca-cloud/logout'); renderCloud(root, refresh); };
    return;
  }
  box.innerHTML = `
    <form id="oc-form" class="stack">
      <div class="grid2">
        <input class="input" name="email" type="email" placeholder="Email" autocomplete="username">
        <input class="input" name="password" type="password" placeholder="Password" autocomplete="current-password">
      </div>
      <button class="btn block" type="submit">Sign in to Orca Cloud</button>
      <p class="small muted center">Signed up with Google or GitHub instead? <a href="#" id="oc-pkce">Use the browser login</a></p>
    </form>`;
  $('#oc-form', box).onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try { await api.post('/api/orca-cloud/login', { email: fd.get('email'), password: fd.get('password') }); toast('Signed in', 'ok'); renderCloud(root, refresh); }
    catch (err) { toast(err.message, 'error'); }
  };
  $('#oc-pkce', box).onclick = async (e) => {
    e.preventDefault();
    const { root: m, close } = modal(`
      <h2>Orca Cloud browser login</h2>
      <ol class="small muted" style="padding-left:18px">
        <li>Choose how you signed up and tap <b>Open login</b>. Sign in.</li>
        <li>You end up on a page at <span class="mono">localhost:8080/callback?code=…</span> that will not load – that is expected.</li>
        <li>Copy that whole address from the browser's address bar and paste it below.</li>
      </ol>
      <div class="stack">
        <select class="input" id="oc-provider"><option value="google">Google</option><option value="github">GitHub</option><option value="apple">Apple</option></select>
        <button class="btn" id="oc-open">Open login</button>
        <input class="input mono" id="oc-paste" placeholder="http://localhost:8080/callback?code=…">
        <button class="btn primary" id="oc-finish" disabled>Finish sign-in</button>
      </div>`);
    m.querySelector('#oc-open').onclick = async () => {
      try {
        const r = await api.post('/api/orca-cloud/pkce/start', { provider: m.querySelector('#oc-provider').value });
        window.open(r.url, '_blank');
        m.querySelector('#oc-finish').disabled = false;
      } catch (err) { toast(err.message, 'error'); }
    };
    m.querySelector('#oc-finish').onclick = async () => {
      try { await api.post('/api/orca-cloud/pkce/finish', { pasted: m.querySelector('#oc-paste').value }); close(); toast('Signed in', 'ok'); renderCloud(root, refresh); }
      catch (err) { toast(err.message, 'error'); }
    };
  };
}
