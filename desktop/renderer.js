let API = 'http://127.0.0.1:5120';
let backendReady = false;
let setupComplete = false;

async function initApi() {
  if (window.sentinelEarn) {
    API = await window.sentinelEarn.getApiBase();
  }
}

function setStartupProgress(percent, detail) {
  const fill = document.getElementById('setup-progress-fill');
  const detailEl = document.getElementById('setup-progress-detail');
  if (fill) fill.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  if (detailEl && detail) detailEl.textContent = detail;
}

async function waitForBackendReady() {
  const overlay = document.getElementById('startup-overlay');
  const msg = document.getElementById('startup-message');
  const pyErr = document.getElementById('python-error');

  if (!window.sentinelEarn?.onBackendStatus) {
    backendReady = true;
    overlay?.classList.add('hidden');
    return true;
  }

  return new Promise((resolve) => {
    let done = false;
    const finish = (status) => {
      if (done) return;
      if (status?.state === 'error' && status.code === 'python_missing') {
        done = true;
        overlay?.classList.add('hidden');
        pyErr?.classList.remove('hidden');
        resolve(false);
        return;
      }
      if (status?.state === 'ready') {
        done = true;
        backendReady = true;
        if (status.api) API = status.api;
        if (msg) msg.textContent = 'Setting up Sentinel Earn engine…';
        setStartupProgress(5, 'Backend connected');
        resolve(true);
        return;
      }
      if (status?.state === 'starting' && msg) {
        msg.textContent = 'Starting Sentinel Earn engine…';
        setStartupProgress(2, 'Launching Python backend…');
      }
      if (status?.state === 'error') {
        done = true;
        if (msg) {
          msg.textContent = status.message || 'Backend failed to start';
          setStartupProgress(0, status.message || 'Backend failed to start');
        }
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

async function pollSetupStatus() {
  const overlay = document.getElementById('startup-overlay');
  const setupErr = document.getElementById('setup-error');
  const msg = document.getElementById('startup-message');

  for (let i = 0; i < 7200; i++) {
    try {
      const d = await fetch(`${API}/api/setup/status`).then((r) => r.json());
      const pct = Number(d.percent || 0);
      setStartupProgress(pct, d.message || 'Setting up…');

      if (d.phase === 'error' || d.error) {
        overlay?.classList.add('hidden');
        setupErr?.classList.remove('hidden');
        const errMsg = document.getElementById('setup-error-message');
        if (errMsg) errMsg.textContent = d.error || d.message || 'Setup failed.';
        const ollamaBtn = document.getElementById('setup-ollama-btn');
        if (ollamaBtn) {
          const show = d.action === 'manual' && d.url;
          ollamaBtn.classList.toggle('hidden', !show);
          if (show) {
            ollamaBtn.onclick = () => window.sentinelEarn?.openExternal(d.url);
          }
        }
        return false;
      }

      if (d.complete || d.setup_complete) {
        setupComplete = true;
        setStartupProgress(100, 'Ready');
        overlay?.classList.add('hidden');
        return true;
      }

      if (msg && d.message) msg.textContent = 'Setting up Sentinel Earn engine…';
    } catch {
      setStartupProgress(0, 'Waiting for backend…');
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  overlay?.classList.add('hidden');
  return false;
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

document.getElementById('setup-retry-btn')?.addEventListener('click', async () => {
  document.getElementById('setup-error')?.classList.add('hidden');
  document.getElementById('startup-overlay')?.classList.remove('hidden');
  setStartupProgress(0, 'Retrying setup…');
  try {
    await api('/api/setup/retry', { method: 'POST', body: '{}' });
  } catch (_) {}
  await pollSetupStatus();
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
  await pollSetupStatus();
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
