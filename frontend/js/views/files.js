import { api } from '../api.js';
import { $, $$, esc, fmtAgo, fmtBytes, fmtDuration, state, toast, confirmDialog } from '../app.js';

export class FilesView {
  async mount(root) {
    this.root = root;
    root.innerHTML = `<div class="card"><div class="empty">Loading files from printer…</div></div>`;
    await this.load();
  }

  async load() {
    let files;
    try { files = (await api.get('/api/printer/files')).files; }
    catch (e) { this.root.innerHTML = `<div class="card"><h2>Files on printer</h2><p class="muted">${esc(e.message)}</p></div>`; return; }
    const q = this.query || '';
    const shown = files.filter((f) => f.path.toLowerCase().includes(q.toLowerCase()));
    this.root.innerHTML = `
      <div class="card">
        <div class="card-title"><h2>Files on ${esc(state.settings.printer_name)}</h2><span class="muted small">${files.length}</span></div>
        <input class="input" id="q" placeholder="Search…" value="${esc(q)}">
        <div class="list" style="margin-top:8px">
          ${shown.length ? shown.map((f) => `
            <div class="list-item" data-path="${esc(f.path)}">
              <img class="thumb" loading="lazy" src="/api/printer/thumbnail?filename=${encodeURIComponent(f.path)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb'}))">
              <div class="grow">
                <div class="ellipsis" style="font-weight:600">${esc(f.path.split('/').pop())}</div>
                <div class="muted small">${f.path.includes('/') ? esc(f.path.split('/').slice(0, -1).join('/')) + ' · ' : ''}${fmtBytes(f.size)} · ${fmtAgo(f.modified)}</div>
              </div>
              <button class="btn sm primary" data-print="${esc(f.path)}">Print</button>
            </div>`).join('') : `<div class="empty">No G-code files</div>`}
        </div>
      </div>`;
    $('#q', this.root).oninput = (e) => { this.query = e.target.value; clearTimeout(this.t); this.t = setTimeout(() => this.load(), 200); };
    $$('[data-print]', this.root).forEach((b) => b.onclick = (e) => { e.stopPropagation(); this.print(b.dataset.print); });
    $$('.list-item', this.root).forEach((item) => item.onclick = () => this.details(item.dataset.path));
  }

  async print(path) {
    if (state.printer && state.printer.state === 'printing') { toast('Printer is busy', 'error'); return; }
    if (!(await confirmDialog('Start print?', path, { okLabel: 'Print' }))) return;
    try { await api.post('/api/printer/print', { filename: path }); toast('Print started', 'ok'); $$('.tab').find((t) => t.dataset.tab === 'printer').click(); }
    catch (e) { toast(e.message, 'error'); }
  }

  async details(path) {
    let m;
    try { m = (await api.get(`/api/printer/files/metadata?filename=${encodeURIComponent(path)}`)).metadata; }
    catch (e) { toast(e.message, 'error'); return; }
    const { modal } = await import('../app.js');
    const { root, close } = modal(`
      <div class="row" style="align-items:flex-start">
        <img class="thumb lg" src="/api/printer/thumbnail?filename=${encodeURIComponent(path)}" alt="" onerror="this.style.display='none'">
        <div class="grow"><h2 style="word-break:break-all">${esc(path.split('/').pop())}</h2>
          <div class="muted small">${esc(m.slicer || '')} ${esc(m.slicer_version || '')}</div></div>
      </div>
      <div class="grid3" style="margin:12px 0">
        <div class="stat"><div class="v">${fmtDuration(m.estimated_time)}</div><div class="k">Time</div></div>
        <div class="stat"><div class="v">${m.filament_weight_total != null ? m.filament_weight_total.toFixed(0) + ' g' : '–'}</div><div class="k">Filament</div></div>
        <div class="stat"><div class="v">${m.layer_count ?? '–'}</div><div class="k">Layers</div></div>
      </div>
      <div class="muted small">${m.filament_type ? esc(m.filament_type) + ' · ' : ''}${m.layer_height ? m.layer_height + ' mm · ' : ''}${m.first_layer_bed_temp ? `bed ${m.first_layer_bed_temp}° · ` : ''}${m.first_layer_extr_temp ? `nozzle ${m.first_layer_extr_temp}°` : ''}</div>
      <div class="grid2" style="margin-top:14px">
        <button class="btn danger" data-x="del">Delete</button>
        <button class="btn primary" data-x="print">Print</button>
      </div>`);
    root.querySelector('[data-x=print]').onclick = () => { close(); this.print(path); };
    root.querySelector('[data-x=del]').onclick = async () => {
      close();
      if (!(await confirmDialog('Delete file?', path, { okLabel: 'Delete', danger: true }))) return;
      try { await api.del(`/api/printer/files?filename=${encodeURIComponent(path)}`); toast('Deleted', 'ok'); this.load(); }
      catch (e) { toast(e.message, 'error'); }
    };
  }
}
