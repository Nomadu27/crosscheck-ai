/**
 * crosscheck Dashboard — unified control center
 * Handles: tabs, setup, team config, model registry, review, history
 */

// ─── State ─────────────────────────────────────────────────────────────────

let apiKey = localStorage.getItem('crosscheck_api_key') || '';
let teamConfig = {};  // role -> model_id overrides
let models = [];      // full model registry
let teamDefaults = []; // default team from server

// ─── Tab Navigation ────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    // Deactivate all
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    // Activate clicked
    btn.classList.add('active');
    const tab = document.getElementById('tab-' + btn.dataset.tab);
    if (tab) tab.classList.add('active');
    // Load tab data
    if (btn.dataset.tab === 'history') loadHistory();
    if (btn.dataset.tab === 'team') loadTeamConfig();
  });
});

// ─── Init ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  // Restore API key
  if (apiKey) {
    const input = document.getElementById('api-key-input');
    if (input) input.value = apiKey;
  }

  // Restore team config from localStorage
  const savedConfig = localStorage.getItem('crosscheck_team_config');
  if (savedConfig) {
    try { teamConfig = JSON.parse(savedConfig); } catch (_e) { /* ignore */ }
  }

  // Load team + models
  loadTeamInfoDashboard();
  loadModels();
});

async function loadTeamInfoDashboard() {
  try {
    const resp = await fetch('/api/team');
    teamDefaults = await resp.json();
    // Also populate chat.js teamInfo for compatibility
    if (typeof teamInfo !== 'undefined') {
      teamInfo = teamDefaults;
      renderTeamPanel(teamDefaults);
    }
  } catch (e) {
    console.warn('Could not load team info:', e);
  }
}

// ─── Setup Tab ─────────────────────────────────────────────────────────────

function saveApiKey() {
  const input = document.getElementById('api-key-input');
  const status = document.getElementById('api-key-status');
  apiKey = input.value.trim();
  if (!apiKey) {
    status.textContent = 'API key is required';
    status.className = 'status-msg error';
    return;
  }
  localStorage.setItem('crosscheck_api_key', apiKey);
  status.textContent = 'API key saved to browser storage';
  status.className = 'status-msg success';
}

async function testApiKey() {
  const status = document.getElementById('api-key-status');
  const key = document.getElementById('api-key-input').value.trim();
  if (!key) {
    status.textContent = 'Enter an API key first';
    status.className = 'status-msg error';
    return;
  }
  status.textContent = 'Testing...';
  status.className = 'status-msg';

  try {
    const resp = await fetch('/api/test-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    const data = await resp.json();
    if (data.valid) {
      status.textContent = 'API key is valid! ' + (data.message || '');
      status.className = 'status-msg success';
      apiKey = key;
      localStorage.setItem('crosscheck_api_key', apiKey);
    } else {
      status.textContent = 'Invalid API key: ' + (data.message || 'unknown error');
      status.className = 'status-msg error';
    }
  } catch (e) {
    status.textContent = 'Test failed: ' + e.message;
    status.className = 'status-msg error';
  }
}

function copyCmd(el) {
  const code = el.querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    const hint = el.querySelector('.copy-hint');
    hint.textContent = 'copied!';
    setTimeout(() => { hint.textContent = 'click to copy'; }, 1500);
  });
}

// ─── Team Tab ──────────────────────────────────────────────────────────────

async function loadTeamConfig() {
  if (teamDefaults.length === 0) await loadTeamInfoDashboard();
  if (models.length === 0) await loadModels();
  renderTeamCards();
}

function renderTeamCards() {
  const grid = document.getElementById('team-config-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const descriptions = {
    planner: 'Breaks tasks into subtasks, delegates, resolves conflicts',
    architect: 'System design, patterns, tradeoffs, scalability',
    coder: 'Writes production-ready code — the only builder',
    debugger: 'Finds bugs, edge cases, logic errors',
    security: 'Scans for OWASP Top 10 vulnerabilities',
    analyst: 'Analyzes flow, performance, complexity',
  };

  teamDefaults.forEach(member => {
    const card = document.createElement('div');
    card.className = 'team-card';
    const currentModel = teamConfig[member.role] || member.model;

    // Build model options
    const options = models.map(m =>
      `<option value="${m.model_id}" ${m.model_id === currentModel ? 'selected' : ''}>${m.display_name} (${m.model_id.split('/').pop()})</option>`
    ).join('');

    card.innerHTML = `
      <div class="team-card-header">
        <div class="team-card-dot" style="background:${member.color}"></div>
        <span class="team-card-title">${member.title}</span>
        <span class="team-card-role">${member.role}</span>
      </div>
      <div class="team-card-desc">${descriptions[member.role] || ''}</div>
      <select onchange="updateTeamModel('${member.role}', this.value)">
        ${options}
      </select>
    `;
    grid.appendChild(card);
  });
}

function updateTeamModel(role, modelId) {
  teamConfig[role] = modelId;
  // Save to localStorage
  localStorage.setItem('crosscheck_team_config', JSON.stringify(teamConfig));
}

// ─── Model Registry ────────────────────────────────────────────────────────

async function loadModels() {
  try {
    const resp = await fetch('/api/models');
    models = await resp.json();
    renderModelTable(models);
    document.getElementById('model-count').textContent = models.length + ' models';
  } catch (e) {
    console.warn('Could not load models:', e);
  }

  // Filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      if (filter === 'all') {
        renderModelTable(models);
      } else if (filter === 'western' || filter === 'chinese') {
        renderModelTable(models.filter(m => m.origin === filter));
      } else {
        renderModelTable(models.filter(m => m.tiers.includes(filter)));
      }
    });
  });
}

function renderModelTable(data) {
  const body = document.getElementById('model-registry-body');
  if (!body) return;
  body.innerHTML = '';
  data.forEach(m => {
    const tiers = m.tiers.map(t =>
      `<span class="tier-badge ${t}">${t}</span>`
    ).join('');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${m.display_name}</strong><br><span style="font-size:0.7rem;color:var(--text-dim)">${m.model_id}</span></td>
      <td>${m.provider}</td>
      <td>${tiers}</td>
      <td>${m.context_k}K</td>
      <td>${m.origin}</td>
    `;
    body.appendChild(tr);
  });
}

// ─── Review Tab ────────────────────────────────────────────────────────────

async function startReview() {
  const code = document.getElementById('review-code').value.trim();
  if (!code) return alert('Paste some code first');

  const type = document.getElementById('review-type').value;
  const mode = document.getElementById('review-mode').value;
  const rounds = document.getElementById('review-rounds').value;
  const resultDiv = document.getElementById('review-result');

  resultDiv.style.display = 'block';
  resultDiv.innerHTML = '<p style="color:var(--text-secondary)">Starting review...</p>';

  try {
    const resp = await fetch('/api/review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code, type, mode,
        max_rounds: parseInt(rounds),
        api_key: apiKey,
      }),
    });
    const data = await resp.json();

    if (data.error) {
      resultDiv.innerHTML = `<p class="error">${escapeHtml(data.error)}</p>`;
      return;
    }

    const verdictClass = data.verdict === 'APPROVED' ? 'approved' : 'revise';
    resultDiv.innerHTML = `
      <h3 style="margin-bottom:12px">
        <span class="${verdictClass}">${data.verdict}</span>
        &mdash; Score: ${data.final_score?.toFixed(1) || '--'}/10
        &mdash; Cost: $${data.cost_usd?.toFixed(4) || '0'}
      </h3>
      <div style="font-size:0.82rem;line-height:1.6;white-space:pre-wrap">${escapeHtml(data.summary || JSON.stringify(data, null, 2))}</div>
    `;
  } catch (e) {
    resultDiv.innerHTML = `<p class="error">Review failed: ${escapeHtml(e.message)}</p>`;
  }
}

// ─── History Tab ───────────────────────────────────────────────────────────

async function loadHistory() {
  try {
    const [sessions, perf] = await Promise.all([
      fetch('/api/sessions?days=30').then(r => r.json()),
      fetch('/api/model-performance').then(r => r.json()),
    ]);

    // KPIs
    const total = sessions.length;
    const approved = sessions.filter(s => s.verdict === 'APPROVED').length;
    const avgScore = sessions.reduce((a, s) => a + (s.final_score || 0), 0) / Math.max(total, 1);
    const totalCost = sessions.reduce((a, s) => a + (s.cost_usd || 0), 0);

    setText('kpi-sessions', total);
    setText('kpi-approval', total ? (approved / total * 100).toFixed(0) + '%' : '--');
    setText('kpi-score', total ? avgScore.toFixed(1) + '/10' : '--');
    setText('kpi-cost', '$' + totalCost.toFixed(4));

    // Sessions table
    const sb = document.getElementById('sessions-body');
    if (sb) {
      sb.innerHTML = '';
      sessions.slice(0, 30).forEach(s => {
        const dt = new Date(s.ts * 1000).toLocaleString();
        const cls = s.verdict === 'APPROVED' ? 'approved' : 'revise';
        sb.innerHTML += `<tr>
          <td>${dt}</td>
          <td class="${cls}">${s.verdict}</td>
          <td>${s.final_score?.toFixed(1) || '--'}</td>
          <td>${s.total_rounds}</td>
          <td>$${s.cost_usd?.toFixed(4) || '0'}</td>
          <td>${s.duration_sec?.toFixed(1) || '--'}s</td>
          <td>${s.file_path || '--'}</td>
        </tr>`;
      });
    }

    // Model perf table
    const mp = document.getElementById('models-perf-body');
    if (mp) {
      mp.innerHTML = '';
      perf.forEach(m => {
        mp.innerHTML += `<tr>
          <td>${m.model_id}</td>
          <td>${m.runs}</td>
          <td>${m.avg_score?.toFixed(1) || '--'}</td>
          <td>${m.runs ? ((m.passes / m.runs) * 100).toFixed(0) + '%' : '--'}</td>
        </tr>`;
      });
    }
  } catch (e) {
    console.warn('History load failed:', e);
  }
}

// ─── Chat Tab integration ──────────────────────────────────────────────────
// Note: startTask() and startTaskFromChat() in chat.js read apiKey and
// teamConfig from this scope automatically (they are globals).

// ─── Utilities ─────────────────────────────────────────────────────────────

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// escapeHtml is in chat.js
