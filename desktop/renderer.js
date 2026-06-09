let API = 'http://127.0.0.1:5120';
let backendReady = false;
let setupComplete = false;

async function initApi() {
  if (window.sentinelEarn) {
    API = await window.sentinelEarn.getApiBase();
  }
}

const setupEls = () => ({
  screen: document.getElementById('setup-screen'),
  backendMsg: document.getElementById('setup-backend-msg'),
  detecting: document.getElementById('setup-detecting'),
  hardware: document.getElementById('setup-hardware-panel'),
  progress: document.getElementById('setup-progress-panel'),
  ready: document.getElementById('setup-ready-panel'),
  error: document.getElementById('setup-error'),
});

function formatEta(seconds) {
  if (seconds == null || seconds < 0) return '';
  if (seconds < 60) return `${seconds}s remaining`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s remaining`;
}

function showSetupPanel(panel) {
  const e = setupEls();
  [e.detecting, e.hardware, e.progress, e.ready].forEach((el) => el?.classList.add('hidden'));
  panel?.classList.remove('hidden');
}

function renderHardwareSummary(d) {
  const summary = d.hardware_summary || {};
  const hw = d.hardware || {};
  document.getElementById('hw-cpu').textContent = summary.cpu || hw.cpu_name || '—';
  document.getElementById('hw-ram').textContent = summary.ram || `${hw.ram_gb || '?'} GB`;
  document.getElementById('hw-gpu').textContent = summary.gpu || hw.gpu_name || 'CPU only';
  const npuRow = document.getElementById('hw-npu-row');
  const npuText = summary.npu || hw.npu_device;
  if (npuRow) {
    const show = Boolean(hw.npu_present && npuText);
    npuRow.classList.toggle('hidden', !show);
    if (show) document.getElementById('hw-npu').textContent = npuText;
  }
  const recText = d.recommendation_text || d.recommendation?.explanation || '';
  const model = d.model || d.recommended_model || d.hardware?.recommended_model || '';
  document.getElementById('setup-rec-text').textContent = recText;
  document.getElementById('setup-model-name').textContent = model;
}

function renderDownloadProgress(d) {
  const pct = Number(d.percent || 0);
  const fill = document.getElementById('setup-progress-fill');
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;

  const model = d.model || '';
  document.getElementById('setup-progress-model').textContent = model;

  const dl = d.downloaded_mb;
  const total = d.total_mb;
  const stats = document.getElementById('setup-progress-stats');
  if (stats) {
    let line = `${pct}%`;
    if (dl != null && total != null) line += ` · ${dl} MB / ${total} MB`;
    else if (dl != null) line += ` · ${dl} MB downloaded`;
    const eta = formatEta(d.eta_seconds);
    if (eta) line += ` · ${eta}`;
    stats.textContent = line;
  }

  const detail = document.getElementById('setup-progress-detail');
  if (detail) {
    if (d.phase === 'installing_ollama') detail.textContent = d.message || 'Installing Ollama runtime…';
    else if (d.phase === 'pulling_model') detail.textContent = d.message || `Downloading ${model}…`;
    else detail.textContent = d.message || '';
  }
}

function applySetupState(d) {
  const e = setupEls();
  if (e.backendMsg) e.backendMsg.classList.add('hidden');

  if (d.phase === 'error' || d.error) {
    e.screen?.classList.add('hidden');
    e.error?.classList.remove('hidden');
    document.getElementById('setup-error-message').textContent = d.error || d.message || 'Setup failed.';
    return 'error';
  }

  if (d.complete || d.setup_complete || d.phase === 'ready') {
    setupComplete = true;
    showSetupPanel(e.ready);
    return 'ready';
  }

  if (d.phase === 'detecting' || d.phase === 'idle') {
    showSetupPanel(e.detecting);
    return 'detecting';
  }

  if (d.phase === 'awaiting_download' || d.awaiting_user) {
    showSetupPanel(e.hardware);
    renderHardwareSummary(d);
    return 'awaiting';
  }

  if (['installing_ollama', 'starting_ollama', 'pulling_model'].includes(d.phase)) {
    showSetupPanel(e.progress);
    renderDownloadProgress(d);
    return 'downloading';
  }

  return 'unknown';
}

async function waitForBackendReady() {
  const e = setupEls();
  const pyErr = document.getElementById('python-error');

  if (!window.sentinelEarn?.onBackendStatus) {
    backendReady = true;
    return true;
  }

  return new Promise((resolve) => {
    let done = false;
    const finish = (status) => {
      if (done) return;
      if (status?.state === 'error' && status.code === 'python_missing') {
        done = true;
        e.screen?.classList.add('hidden');
        pyErr?.classList.remove('hidden');
        resolve(false);
        return;
      }
      if (status?.state === 'ready') {
        done = true;
        backendReady = true;
        if (status.api) API = status.api;
        if (e.backendMsg) {
          e.backendMsg.textContent = 'Engine connected';
          e.backendMsg.classList.remove('hidden');
        }
        resolve(true);
        return;
      }
      if (status?.state === 'starting' && e.backendMsg) {
        e.backendMsg.textContent = 'Starting Sentinel Earn engine…';
      }
      if (status?.state === 'error') {
        done = true;
        if (e.backendMsg) e.backendMsg.textContent = status.message || 'Backend failed to start';
        resolve(false);
      }
    };

    window.sentinelEarn.getBackendStatus().then(finish);
    window.sentinelEarn.onBackendStatus((status) => {
      finish(status);
      if (status?.state === 'ready' && status.api) API = status.api;
    });
  });
}

async function pollSetupStatus(untilComplete = true) {
  const e = setupEls();
  e.error?.classList.add('hidden');
  e.screen?.classList.remove('hidden');

  for (let i = 0; i < 7200; i++) {
    try {
      const d = await fetch(`${API}/api/setup/status`).then((r) => r.json());
      const state = applySetupState(d);

      if (state === 'error') return false;

      if (state === 'ready') {
        await new Promise((r) => setTimeout(r, 1200));
        e.screen?.classList.add('hidden');
        return true;
      }

      if (!untilComplete && state === 'awaiting') return true;
    } catch {
      showSetupPanel(e.detecting);
    }
    await new Promise((r) => setTimeout(r, 800));
  }
  e.screen?.classList.add('hidden');
  return false;
}

async function runSetupFlow() {
  await pollSetupStatus(false);
}

async function startDownload() {
  const btn = document.getElementById('setup-download-btn');
  if (btn) btn.disabled = true;
  try {
    await api('/api/setup/download', { method: 'POST', body: '{}' });
  } catch (_) {}
  await pollSetupStatus(true);
  loadHealthStatus();
  loadBounties();
  if (btn) btn.disabled = false;
}

async function api(path, opts = {}) {
  if (!backendReady) throw new Error('Backend starting…');
  const res = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

// Tabs
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`panel-${btn.dataset.tab}`).classList.add('active');
    refreshCurrentPanel(btn.dataset.tab);
  });
});

function refreshCurrentPanel(tab) {
  if (tab === 'bounties') loadBounties();
  if (tab === 'patches') loadPatches();
  if (tab === 'history') loadHistory();
  if (tab === 'earnings') loadEarnings();
  if (tab === 'settings') loadSettings();
}

async function loadHealthStatus() {
  const backendEl = document.getElementById('backend-status');
  const ollamaEl = document.getElementById('ollama-status');

  if (!backendReady) {
    if (backendEl) {
      backendEl.textContent = 'Backend: starting…';
      backendEl.className = 'status-pill';
    }
    if (ollamaEl) {
      ollamaEl.textContent = 'Ollama: waiting…';
      ollamaEl.className = 'status-pill';
    }
    return;
  }

  if (backendEl) {
    backendEl.textContent = 'Backend: online';
    backendEl.className = 'status-pill online';
  }

  try {
    const d = await api('/api/health');
    const ollama = d.ollama || {};
    if (ollamaEl) {
      if (ollama.online) {
        ollamaEl.textContent = `Ollama: online (${(ollama.models || []).length} models)`;
        ollamaEl.className = 'status-pill online';
      } else if (ollama.installed) {
        ollamaEl.textContent = 'Ollama: offline';
        ollamaEl.className = 'status-pill offline';
      } else {
        ollamaEl.textContent = 'Ollama: not installed';
        ollamaEl.className = 'status-pill offline';
      }
    }
  } catch {
    if (backendEl) {
      backendEl.textContent = 'Backend: offline';
      backendEl.className = 'status-pill offline';
    }
    if (ollamaEl) {
      ollamaEl.textContent = 'Ollama: unknown';
      ollamaEl.className = 'status-pill offline';
    }
  }
}

async function hasGithubToken() {
  try {
    const s = await api('/api/settings');
    return Boolean(s.github_token_set);
  } catch {
    return false;
  }
}

async function loadBounties() {
  const prompt = document.getElementById('github-token-prompt');
  if (!backendReady) return;

  const tokenOk = await hasGithubToken();
  if (prompt) prompt.classList.toggle('hidden', tokenOk);

  if (!tokenOk) {
    document.getElementById('github-list').innerHTML =
      '<div class="empty">Configure GitHub in Settings to scan for bounty issues.</div>';
    return;
  }

  try {
    const d = await api('/api/dashboard');
    renderGithub(d.opportunities || []);
  } catch (e) {
    const msg = String(e.message || '');
    if (msg.includes('fetch') || msg.includes('Failed to fetch')) {
      document.getElementById('github-list').innerHTML =
        '<div class="empty">Cannot reach backend — retrying…</div>';
    } else {
      document.getElementById('github-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  }
}

function renderGithub(opps) {
  const el = document.getElementById('github-list');
  if (!opps.length) {
    el.innerHTML = '<div class="empty">No GitHub bounties yet — run a scan.</div>';
    return;
  }
  el.innerHTML = opps.map((o) => `
    <div class="card">
      <h3>${esc(o.title)}</h3>
      <p>${esc(o.issue_url)}</p>
      <div class="meta">
        <span class="badge">${esc(o.status)}</span>
        <span class="badge">${esc(o.source)}</span>
      </div>
      <div class="card-actions">
        <button class="btn small primary" onclick="generatePatch(${o.id})">Generate Patch</button>
        ${o.status === 'ready_to_submit' ? `<button class="btn small" onclick="submitPatch(${o.id})">Submit PR</button>` : ''}
      </div>
    </div>
  `).join('');
}

function renderH1(programs) {
  const el = document.getElementById('h1-list');
  if (!programs.length) {
    el.innerHTML = '<div class="empty">No programs loaded.</div>';
    return;
  }
  el.innerHTML = programs.slice(0, 40).map((p) => `
    <div class="card">
      <h3>${esc(p.title || p.name)}</h3>
      <p>${esc(p.reward || p.bounty_range_display || 'Varies')} · ${esc(p.max_severity || '')}</p>
      <div class="meta"><span class="badge">${esc(p.handle || '')}</span></div>
      <p><a href="${esc(p.url)}" style="color:var(--teal)" target="_blank">View program</a></p>
    </div>
  `).join('');
}

async function loadPatches() {
  const d = await api('/api/patches');
  const el = document.getElementById('patch-list');
  const patches = d.patches || [];
  if (!patches.length) {
    el.innerHTML = '<div class="empty">Patch queue is empty.</div>';
    return;
  }
  el.innerHTML = patches.map((p) => `
    <div class="card">
      <h3>${esc(p.title)}</h3>
      <p>${esc(p.issue_url)}</p>
      <div class="meta">
        <span class="badge">${esc(p.status)}</span>
        <span class="badge">${esc(p.opp_status)}</span>
      </div>
      <div class="card-actions">
        ${p.opp_status === 'ready_to_submit' ? `<button class="btn small primary" onclick="submitPatch(${p.opportunity_id})">Submit PR</button>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadHistory() {
  const d = await api('/api/submissions');
  const el = document.getElementById('history-list');
  const rows = d.submissions || [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty">No submissions yet.</div>';
    return;
  }
  el.innerHTML = rows.map((s) => `
    <div class="card">
      <h3>${esc(s.title || 'Submission #' + s.id)}</h3>
      <p>${esc(s.pr_url || 'No PR URL')}</p>
      <div class="meta">
        <span class="badge">${esc(s.status)}</span>
        ${s.earnings ? `<span class="badge">$${s.earnings}</span>` : ''}
      </div>
    </div>
  `).join('');
}

async function loadEarnings() {
  const d = await api('/api/earnings');
  document.getElementById('earnings-stats').innerHTML = `
    <div class="stat"><div class="value">$${Number(d.confirmed_earnings || 0).toFixed(2)}</div><div class="label">Confirmed earnings</div></div>
    <div class="stat"><div class="value">${d.pending_count || 0}</div><div class="label">Pending submissions</div></div>
    <div class="stat"><div class="value">${d.merged_count || 0}</div><div class="label">Merged PRs</div></div>
    <div class="stat"><div class="value">${d.merge_rate || 0}%</div><div class="label">Merge rate</div></div>
  `;
}

async function loadSettings() {
  const s = await api('/api/settings');
  const form = document.getElementById('settings-form');
  ['ollama_host', 'ollama_model', 'github_username', 'hackerone_username'].forEach((k) => {
    if (form[k]) form[k].value = s[k] || '';
  });
  if (form.auto_generate_patches) form.auto_generate_patches.checked = !!s.auto_generate_patches;
}

document.getElementById('scan-github').onclick = async () => {
  if (!(await hasGithubToken())) {
    toast('Add your GitHub token in Settings first');
    document.querySelector('.tab[data-tab="settings"]')?.click();
    return;
  }
  toast('Scanning GitHub…');
  const d = await api('/api/bounties/scan/github', { method: 'POST', body: '{}' });
  toast(`Found ${d.found} issues, queued ${d.queued}`);
  loadBounties();
};

document.getElementById('scan-hackerone').onclick = async () => {
  toast('Scanning HackerOne programs…');
  const d = await api('/api/bounties/scan/hackerone', { method: 'POST', body: JSON.stringify({ force_refresh: true }) });
  renderH1(d.programs || []);
  toast(`Loaded ${(d.programs || []).length} programs`);
};

document.getElementById('run-cycle').onclick = async () => {
  toast('Running pipeline cycle…');
  await api('/api/pipeline/cycle', { method: 'POST', body: '{}' });
  toast('Cycle complete');
  loadBounties();
};

document.getElementById('refresh-patches').onclick = () => loadPatches();

document.getElementById('settings-form').onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  body.auto_generate_patches = !!fd.get('auto_generate_patches');
  await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
  toast('Settings saved');
  loadHealthStatus();
  loadBounties();
};

document.getElementById('goto-settings-token')?.addEventListener('click', () => {
  document.querySelector('.tab[data-tab="settings"]')?.click();
});

document.getElementById('setup-download-btn')?.addEventListener('click', () => startDownload());

document.getElementById('setup-retry-btn')?.addEventListener('click', async () => {
  setupEls().error?.classList.add('hidden');
  setupEls().screen?.classList.remove('hidden');
  showSetupPanel(setupEls().detecting);
  try {
    await api('/api/setup/retry', { method: 'POST', body: '{}' });
  } catch (_) {}
  await pollSetupStatus(true);
  loadHealthStatus();
  loadBounties();
});

window.generatePatch = async (id) => {
  toast('Generating patch via Ollama — this may take several minutes…');
  try {
    await api(`/api/patches/${id}/generate`, { method: 'POST', body: '{}' });
    toast('Patch generation finished — check Patch Queue');
    loadPatches();
  } catch (e) {
    toast('Patch failed: ' + e.message);
  }
};

window.submitPatch = async (id) => {
  toast('Submitting PR…');
  const d = await api(`/api/patches/${id}/submit`, { method: 'POST', body: '{}' });
  toast(d.success ? 'PR submitted!' : (d.error || 'Submit failed'));
  loadHistory();
};

(async () => {
  document.getElementById('python-download-btn')?.addEventListener('click', () => {
    window.sentinelEarn?.openPythonDownload();
  });
  const ready = await waitForBackendReady();
  if (ready) await initApi();
  if (!ready) return;
  await runSetupFlow();
  initUpdateBanner();
  loadHealthStatus();
  loadBounties();
  setInterval(loadHealthStatus, 30000);
})();

function initUpdateBanner() {
  const banner = document.getElementById('update-banner');
  const textEl = document.getElementById('update-banner-text');
  const restartBtn = document.getElementById('update-restart-btn');
  const laterBtn = document.getElementById('update-later-btn');
  if (!banner || !window.sentinelEarn?.onUpdate) return;

  let dismissed = false;

  function render(status) {
    if (dismissed || !status || status.state !== 'ready') {
      banner.classList.add('hidden');
      return;
    }
    banner.classList.remove('hidden');
    textEl.textContent = status.version
      ? `Update available — restart to install (v${status.version})`
      : 'Update available — restart to install';
  }

  window.sentinelEarn.getUpdateStatus().then(render).catch(() => undefined);
  window.sentinelEarn.onUpdate(render);
  restartBtn?.addEventListener('click', () => window.sentinelEarn.restartToUpdate());
  laterBtn?.addEventListener('click', () => {
    dismissed = true;
    banner.classList.add('hidden');
  });
}
