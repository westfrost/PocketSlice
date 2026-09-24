// First-run setup wizard: printer → presets → webcam → password → done.
import { api } from '../api.js';
import { $, $$, esc, reloadSettings, state, toast } from '../app.js';
import { presetSourcesHtml, bindPresetSources } from './preset_sources.js';

const STEPS = ['Printer', 'Presets', 'Webcam', 'Password', 'Done'];

export class SetupView {
  constructor() { this.step = 0; }

  async mount(root) {
    this.root = root;
    this.step = 0;
    await this.render();
  }

  async render() {
    const root = this.root;
    const s = state.settings;
    if (this.step === 1 && !state.presets) { try { state.presets = await api.get('/api/presets'); } catch { /* offline */ } }
    const bar = STEPS.map((n, i) => `<div class="step ${i === this.step ? 'active' : i < this.step ? 'done' : ''}"><span>${i + 1}</span>${esc(n)}</div>`).join('');
    let body = '';
    switch (this.step) {
      case 0:
        body = `
          <h2>Connect your printer</h2>
          <p class="muted">PocketSlice talks to Klipper through Moonraker, the same address Mainsail or Fluidd use.</p>
          <div class="stack">
            <div class="field"><label>Printer name</label><input class="input" id="w-name" value="${esc(s.printer_name)}" placeholder="Voron 2.4"></div>
            <div class="field"><label>Moonraker URL</label><input class="input" id="w-url" inputmode="url" value="${esc(s.moonraker_url)}" placeholder="http://192.168.1.50:7125"></div>
            <div class="field"><label>API key <span class="muted">(only if Moonraker requires one)</span></label><input class="input" id="w-key" type="password" placeholder="${s.moonraker_api_key_set ? 'leave empty to keep' : ''}"></div>
            <button class="btn" id="w-test">Test connection</button>
            <div id="w-test-result" class="small muted"></div>
          </div>`;
        break;
      case 1:
        body = `
          <h2>Your OrcaSlicer presets</h2>
          <p class="muted">PocketSlice slices with exactly the printer, process and filament presets you use in OrcaSlicer. Pick one way to get them in:</p>
          ${presetSourcesHtml()}`;
        break;
      case 2:
        body = `
          <h2>Webcam <span class="muted">(optional)</span></h2>
          <p class="muted">An MJPEG stream shows up on the Printer tab. Cameras configured in Moonraker are listed below.</p>
          <div class="stack">
            <div class="field"><label>Stream URL</label><input class="input" id="w-stream" inputmode="url" value="${esc(s.webcam_stream_url)}" placeholder="http://192.168.1.50/webcam/?action=stream"></div>
            <div class="field"><label>Snapshot URL</label><input class="input" id="w-snap" inputmode="url" value="${esc(s.webcam_snapshot_url)}" placeholder="http://192.168.1.50/webcam/?action=snapshot"></div>
            <div id="w-cams" class="small muted">Looking for cameras…</div>
          </div>`;
        break;
      case 3:
        body = `
          <h2>Protect the app</h2>
          <p class="muted">Anyone who can open PocketSlice can start and stop prints. Set a password if the app is reachable from outside your home network (Tailscale, VPN, …).</p>
          <div class="stack">
            <div class="field"><label>Password ${s.password_set ? '<span class="badge ok">already set</span>' : ''}</label><input class="input" id="w-pw" type="password" autocomplete="new-password" placeholder="${s.password_set ? 'leave empty to keep' : 'leave empty for no password'}"></div>
          </div>`;
        break;
      default:
        body = `
          <h2>All set</h2>
          <p class="muted">Upload a model on the Slice tab, pick your presets and hit Print. Everything here can be changed later under Settings.</p>
          <ul class="muted small">
            <li>Install as an app: browser menu → <b>Add to Home Screen</b>.</li>
            <li>On Android, files can be shared straight to PocketSlice.</li>
            <li>Keep presets in sync by re-uploading the OrcaSlicer folder or connecting Orca Cloud.</li>
          </ul>`;
    }
    root.innerHTML = `
      <div class="wizard">
        <div class="steps">${bar}</div>
        <div class="card">${body}</div>
        <div class="grid2" style="margin-top:12px">
          <button class="btn" id="w-back" ${this.step === 0 ? 'disabled' : ''}>Back</button>
          <button class="btn primary" id="w-next">${this.step === STEPS.length - 1 ? 'Start slicing' : this.step === 2 || this.step === 3 ? 'Continue' : 'Next'}</button>
        </div>
        ${this.step < STEPS.length - 1 ? `<p class="center small muted" style="margin-top:10px"><a href="#" id="w-skip">Skip setup</a></p>` : ''}
      </div>`;
    $('#w-back', root).onclick = () => { this.step--; this.render(); };
    $('#w-next', root).onclick = () => this.next();
    const skip = $('#w-skip', root);
    if (skip) skip.onclick = async (e) => { e.preventDefault(); await this.finish(); };
    if (this.step === 0) this.bindPrinter();
    if (this.step === 1) bindPresetSources(root, () => this.render());
    if (this.step === 2) this.bindWebcam();
  }

  bindPrinter() {
    $('#w-test', this.root).onclick = async () => {
      const out = $('#w-test-result', this.root);
      out.textContent = 'Testing…';
      try {
        await this.savePrinter();
        const r = await api.get('/api/settings/test-printer');
        out.innerHTML = r.ok ? `<span style="color:var(--ok)">Connected to ${esc(r.hostname)} · Klipper ${esc(r.state)}</span>` : `<span style="color:var(--danger)">${esc(r.error)}</span>`;
      } catch (e) { out.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
    };
  }

  async savePrinter() {
    const key = $('#w-key', this.root).value;
    await api.put('/api/settings', { printer_name: $('#w-name', this.root).value.trim() || 'Printer', moonraker_url: $('#w-url', this.root).value.trim(), moonraker_api_key: key || null });
    await reloadSettings();
  }

  async bindWebcam() {
    const box = $('#w-cams', this.root);
    try {
      const r = await api.get('/api/printer/webcams');
      if (!r.discovered.length) { box.textContent = 'No cameras configured in Moonraker. You can leave this empty.'; return; }
      box.innerHTML = 'Found: ' + r.discovered.map((c, i) => `<a href="#" data-cam="${i}">${esc(c.name || 'webcam')}</a>`).join(', ') + ' (tap to use)';
      $$('[data-cam]', box).forEach((a) => a.onclick = (e) => {
        e.preventDefault();
        const c = r.discovered[Number(a.dataset.cam)];
        $('#w-stream', this.root).value = c.stream_url || ''; $('#w-snap', this.root).value = c.snapshot_url || '';
      });
    } catch { box.textContent = ''; }
  }

  async next() {
    try {
      if (this.step === 0) await this.savePrinter();
      if (this.step === 2) {
        await api.put('/api/settings', { webcam_stream_url: $('#w-stream', this.root).value.trim(), webcam_snapshot_url: $('#w-snap', this.root).value.trim() });
        await reloadSettings();
      }
      if (this.step === 3) {
        const pw = $('#w-pw', this.root).value;
        if (pw) { await api.post('/api/settings/password', { new_password: pw }); toast('Password set', 'ok'); }
      }
      if (this.step === STEPS.length - 1) { await this.finish(); return; }
      this.step++;
      await this.render();
      window.scrollTo(0, 0);
    } catch (e) { toast(e.message, 'error'); }
  }

  async finish() {
    await api.put('/api/settings', { setup_done: true });
    await reloadSettings();
    state.presets = null;
    $$('.tab').find((t) => t.dataset.tab === 'slice').click();
  }
}
