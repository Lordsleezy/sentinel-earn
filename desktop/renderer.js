let API = 'http://127.0.0.1:5120';

async function initApi() {
  if (window.sentinelEarn) {
    API = await window.sentinelEarn.getApiBase();
  }
}

async function api(path, opts = {}) {
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

async function loadOllamaStatus() {
  const el = document.getElementById('ollama-status');
  try {
    const d = await api('/api/ollama/status');
    el.textContent = d.online ? `Ollama: online (${d.models?.length || 0} models)` : 'Ollama: offline';
    el.className = 'status-pill ' + (d.online ? 'online' : 'offline');
  } catch {
    el.textContent = 'Backend: offline';
    el.className = 'status-pill offline';
  }
}

async function loadBounties() {
  try {
    const d = await api('/api/dashboard');
    renderGithub(d.opportunities || []);
  } catch (e) {
    document.getElementById('github-list').innerHTML = `<div class="empty">${esc(e.message)}</div>`;
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
  loadOllamaStatus();
};

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
  await initApi();
  loadOllamaStatus();
  loadBounties();
  setInterval(loadOllamaStatus, 30000);
})();
