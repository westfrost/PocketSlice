import { api } from './api.js';
import { SliceView } from './views/slice.js';
import { PrinterView } from './views/printer.js';
import { FilesView } from './views/files.js';
import { SettingsView } from './views/settings.js';
import { SetupView } from './views/setup.js';

// ------------------------------------------------------------ helpers
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };

export function fmtDuration(sec) {
  if (sec == null || isNaN(sec)) return '–';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`;
}
export function fmtBytes(b) {
  if (b == null) return '–';
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${u[i]}`;
}
export function fmtAgo(ts) {
  if (!ts) return '';
  const d = Date.now() / 1000 - ts;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

let toastTimer;
export function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg; t.className = `toast ${kind}`; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'error' ? 6000 : 3000);
}

export function modal(html) {
  const m = $('#modal');
  m.innerHTML = `<div class="modal-card">${html}</div>`;
  m.hidden = false;
  const close = () => { m.hidden = true; m.innerHTML = ''; };
  m.onclick = (e) => { if (e.target === m) close(); };
  return { root: m.firstElementChild, close };
}

export function confirmDialog(title, text, { okLabel = 'OK', danger = false } = {}) {
  return new Promise((resolve) => {
    const { root, close } = modal(`
      <h2>${esc(title)}</h2>
      <p class="muted">${esc(text)}</p>
      <div class="grid2" style="margin-top:14px">
        <button class="btn" data-x="cancel">Cancel</button>
        <button class="btn ${danger ? 'danger solid' : 'primary'}" data-x="ok">${esc(okLabel)}</button>
      </div>`);
    root.querySelector('[data-x=cancel]').onclick = () => { close(); resolve(false); };
    root.querySelector('[data-x=ok]').onclick = () => { close(); resolve(true); };
  });
}

// ------------------------------------------------------------ app state
export const state = {
  health: null,
  settings: null,
  overrideFields: [],
  presets: null,
  printer: null,          // last printer status
  currentTab: 'slice',
};

const views = { slice: new SliceView(), printer: new PrinterView(), files: new FilesView(), settings: new SettingsView(), setup: new SetupView() };
let active = null;

async function showTab(name) {
  if (!views[name]) return;
  if (active && active.unmount) active.unmount();
  state.currentTab = name;
  $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  const root = $('#view');
  root.innerHTML = '';
  active = views[name];
  try { await active.mount(root); } catch (e) { root.innerHTML = `<div class="card">${esc(e.message)}</div>`; }
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
}

// ------------------------------------------------------------ login
function showLogin() {
  const root = $('#view');
  root.innerHTML = `
    <div class="login card">
      <h1 class="center">PocketSlice</h1>
      <p class="muted center">Enter the app password to continue.</p>
      <form id="login-form" class="stack">
        <input class="input" type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus>
        <button class="btn primary block" type="submit">Log in</button>
      </form>
    </div>`;
  $('#login-form').onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post('/api/login', { password: e.target.password.value });
      await boot();
    } catch (err) { toast(err.message, 'error'); }
  };
}

// ------------------------------------------------------------ printer polling (shared)
let pollTimer = null;
const statusListeners = new Set();
export function onStatus(fn) { statusListeners.add(fn); return () => statusListeners.delete(fn); }

async function pollStatus() {
  try {
    const s = await api.get('/api/printer/status');
    state.printer = s;
    setConn(true, s);
  } catch (e) {
    state.printer = { ok: false, error: e.message, state: 'offline' };
    setConn(false, state.printer);
  }
  statusListeners.forEach((fn) => { try { fn(state.printer); } catch (err) { console.error(err); } });
}

function setConn(ok, s) {
  const dot = $('#conn-dot'), txt = $('#conn-text');
  if (!ok) { dot.className = 'dot bad'; txt.textContent = 'Offline'; return; }
  const st = s.klippy_state && s.klippy_state !== 'ready' ? `Klipper ${s.klippy_state}` : s.state;
  dot.className = `dot ${s.state === 'printing' ? 'busy' : s.klippy_state === 'ready' ? 'ok' : 'bad'}`;
  txt.textContent = st;
}

function startPolling() {
  stopPolling();
  const tick = async () => {
    if (document.visibilityState === 'visible') await pollStatus();
    const busy = state.printer && state.printer.state === 'printing';
    pollTimer = setTimeout(tick, state.currentTab === 'printer' ? (busy ? 2000 : 3000) : 6000);
  };
  tick();
}
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }

// ------------------------------------------------------------ boot
export function applyTheme() {
  const c = state.settings?.accent_color;
  if (c && /^#[0-9a-f]{6}$/i.test(c)) document.documentElement.style.setProperty('--accent', c);
}

export async function reloadSettings() {
  const r = await api.get('/api/settings');
  state.settings = r.settings; state.overrideFields = r.override_fields;
  $('#printer-name').textContent = state.settings.printer_name || 'PocketSlice';
  applyTheme();
  return state.settings;
}

async function boot() {
  state.health = await api.get('/api/health');
  if (state.health.auth_required && !state.health.authed) { showLogin(); return; }
  await reloadSettings();
  startPolling();
  const hash = location.hash.replace('#', '');
  if (!state.settings.setup_done && hash !== 'setup') { await showTab('setup'); return; }
  await showTab(views[hash] ? hash : 'slice');
}

$$('.tab').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
window.addEventListener('hashchange', () => {
  const name = location.hash.replace('#', '');
  if (views[name] && name !== state.currentTab && state.settings) showTab(name);
});
window.addEventListener('auth-required', () => { stopPolling(); showLogin(); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && pollTimer) pollStatus(); });

if ('serviceWorker' in navigator && location.protocol === 'https:' || location.hostname === 'localhost') {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

boot().catch((e) => { $('#view').innerHTML = `<div class="card"><h2>Cannot reach PocketSlice</h2><p class="muted">${esc(e.message)}</p></div>`; });
