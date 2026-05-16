// ── Auth state (shared across pages) ───────────────────────────────
let authToken = localStorage.getItem('geohan_token') || '';
let currentUser = JSON.parse(localStorage.getItem('geohan_user') || 'null');

function isAuthed() { return Boolean(authToken && currentUser); }

async function apiFetch(url, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  return fetch(url, { ...opts, headers });
}

// ── Auth modal ─────────────────────────────────────────────────────
function openAuthModal(tab = 'login') {
  document.getElementById('auth-modal').style.display = 'flex';
  switchAuthTab(tab);
}
function closeAuthModal() {
  document.getElementById('auth-modal').style.display = 'none';
  document.getElementById('login-error').style.display = 'none';
  document.getElementById('reg-error').style.display = 'none';
}
function switchAuthTab(tab) {
  document.getElementById('tab-login').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register').classList.toggle('active', tab === 'register');
  document.getElementById('auth-login').style.display = tab === 'login' ? 'block' : 'none';
  document.getElementById('auth-register').style.display = tab === 'register' ? 'block' : 'none';
}

async function doLogin() {
  const email = document.getElementById('login-email').value.trim();
  const pass  = document.getElementById('login-pass').value;
  const errBox = document.getElementById('login-error');
  errBox.style.display = 'none';

  if (!email || !pass) { showError(errBox, 'E-posta ve şifre zorunlu.'); return; }

  try {
    const fd = new FormData();
    fd.append('username', email);
    fd.append('password', pass);
    const r = await fetch('/api/v1/auth/token', { method: 'POST', body: fd });
    if (!r.ok) { showError(errBox, 'E-posta veya şifre hatalı.'); return; }
    const d = await r.json();
    authToken = d.access_token;
    localStorage.setItem('geohan_token', authToken);
    await fetchMe();
    closeAuthModal();
    onAuthChanged();
  } catch {
    showError(errBox, 'Sunucuya bağlanılamadı.');
  }
}

async function doRegister() {
  const email = document.getElementById('reg-email').value.trim();
  const pass  = document.getElementById('reg-pass').value;
  const errBox = document.getElementById('reg-error');
  errBox.style.display = 'none';

  if (!email || !pass) { showError(errBox, 'E-posta ve şifre zorunlu.'); return; }
  if (pass.length < 8)  { showError(errBox, 'Şifre en az 8 karakter olmalı.'); return; }

  try {
    const r = await apiFetch('/api/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password: pass }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      showError(errBox, err.detail || 'Kayıt başarısız.');
      return;
    }
    const d = await r.json();
    authToken = d.access_token;
    localStorage.setItem('geohan_token', authToken);
    await fetchMe();
    closeAuthModal();
    onAuthChanged();
  } catch {
    showError(errBox, 'Sunucuya bağlanılamadı.');
  }
}

async function fetchMe() {
  try {
    const r = await apiFetch('/api/v1/auth/me');
    if (!r.ok) { currentUser = null; localStorage.removeItem('geohan_user'); return; }
    currentUser = await r.json();
    localStorage.setItem('geohan_user', JSON.stringify(currentUser));
  } catch { currentUser = null; }
}

async function fetchBalance() {
  if (!authToken) return null;
  try {
    const r = await apiFetch('/api/v1/credits/balance');
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function logout() {
  authToken = ''; currentUser = null;
  localStorage.removeItem('geohan_token');
  localStorage.removeItem('geohan_user');
  onAuthChanged();
}

function toggleUserMenu() {
  const dd = document.getElementById('user-dropdown');
  dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
}

function showError(el, msg) {
  el.textContent = msg;
  el.style.display = 'block';
}

// Page-specific code overrides this
function onAuthChanged() {
  updateAuthUi();
}

function updateAuthUi() {
  const authBtn = document.getElementById('auth-btn');
  const userMenu = document.getElementById('user-menu');
  const balance = document.getElementById('balance-widget');
  if (!authBtn) return;

  if (isAuthed()) {
    authBtn.style.display = 'none';
    if (userMenu) {
      userMenu.style.display = 'block';
      const btn = document.getElementById('user-email-btn');
      if (btn && currentUser) btn.textContent = currentUser.email || '…';
    }
    if (balance) {
      balance.style.display = 'flex';
      fetchBalance().then(b => {
        if (b && document.getElementById('balance-val')) {
          document.getElementById('balance-val').textContent = b.credits ?? '—';
        }
      });
    }
  } else {
    authBtn.style.display = 'inline-flex';
    if (userMenu) userMenu.style.display = 'none';
    if (balance) balance.style.display = 'none';
  }
}

// Auto-init: refresh me + balance on load
document.addEventListener('DOMContentLoaded', () => {
  if (authToken) fetchMe().then(updateAuthUi);
  else updateAuthUi();

  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-menu');
    const dd = document.getElementById('user-dropdown');
    if (menu && dd && dd.style.display === 'block' && !menu.contains(e.target)) {
      dd.style.display = 'none';
    }
  });

  // Enter submits
  const loginPass = document.getElementById('login-pass');
  const regPass = document.getElementById('reg-pass');
  if (loginPass) loginPass.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
  if (regPass)   regPass.addEventListener('keydown',   e => { if (e.key === 'Enter') doRegister(); });
});
