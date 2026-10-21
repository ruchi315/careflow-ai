// Shared helpers. All user-provided text is inserted with esc() / textContent (patient text is untrusted).
const CF = {
  token: () => sessionStorage.getItem('cf_token'),
  user: () => JSON.parse(sessionStorage.getItem('cf_user') || 'null'),
  setSession(t, u) { sessionStorage.setItem('cf_token', t); sessionStorage.setItem('cf_user', JSON.stringify(u)); },
  clear() { sessionStorage.removeItem('cf_token'); sessionStorage.removeItem('cf_user'); },
  async api(path, opts = {}) {
    const headers = opts.headers || {};
    if (CF.token()) headers['Authorization'] = 'Bearer ' + CF.token();
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(opts.json); }
    const r = await fetch(path, { ...opts, headers });
    if (r.status === 401 && CF.token()) { CF.clear(); location.reload(); }
    const ct = r.headers.get('content-type') || '';
    const data = ct.includes('json') ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.detail) || r.statusText);
    return data;
  },
  langs: { 'en': 'English', 'hi': 'हिन्दी (Hindi)', 'hi-en': 'Hinglish', 'ta': 'தமிழ் (Tamil)', 'bn': 'বাংলা (Bengali)' },
};
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toast(msg) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}
function fmtWait(min) { return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} m`; }
function pill(level) { return `<span class="pill ${esc(level)}">${esc(level || '?')}</span>`; }
