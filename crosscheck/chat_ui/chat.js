/**
 * crosscheck AI Dev Team -- Group Chat Client
 * WebSocket client for real-time team communication.
 */

const AGENT_COLORS = {
  coordinator: '#E94B3C',
  planner:     '#6A5ACD',
  architect:   '#4285F4',
  coder:       '#10A37F',
  debugger:    '#FF6D00',
  security:    '#D32F2F',
  analyst:     '#34A853',
  observer_1:  '#7B1FA2',
  observer_2:  '#FF8F00',
  system:      '#8b949e',
  human:       '#c9d1d9',
};

const AGENT_INITIALS = {
  coordinator: 'GK',
  planner:     'PL',
  architect:   'AR',
  coder:       'CO',
  debugger:    'DB',
  security:    'SC',
  analyst:     'AN',
  observer_1:  'O1',
  observer_2:  'O2',
  system:      'SY',
  human:       'U',
};

const PHASES = ['planning', 'discussion', 'coding', 'review', 'dissent', 'approval', 'testing', 'done'];

let ws = null;
let sessionActive = false;
let currentPhase = null;
let completedPhases = new Set();
let teamInfo = [];
let reconnectTimer = null;
let intentionalClose = false;

// -- Init --

document.addEventListener('DOMContentLoaded', () => {
  loadTeamInfo();
  setupInputHandlers();
});

async function loadTeamInfo() {
  try {
    const resp = await fetch('/api/team');
    teamInfo = await resp.json();
    renderTeamPanel(teamInfo);
  } catch (e) {
    console.warn('Could not load team info:', e);
  }
}

function renderTeamPanel(team) {
  const panel = document.getElementById('team-list');
  if (!panel) return;
  panel.innerHTML = '';
  team.forEach(member => {
    const div = document.createElement('div');
    div.className = 'team-member';
    div.setAttribute('data-role', member.role);
    div.onclick = () => insertMention(member.role);
    div.innerHTML = `
      <div class="dot" style="background:${member.color}"></div>
      <div class="info">
        <div class="role-name">${member.title}</div>
        <div class="model-name">${member.model.split('/').pop()}</div>
      </div>
    `;
    panel.appendChild(div);
  });
}

// -- WebSocket --

function connect() {
  // Clean up existing connection
  if (ws) {
    intentionalClose = true;
    try { ws.close(); } catch (_e) { /* ignore */ }
    ws = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  intentionalClose = false;
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  updateStatus('connecting', 'Connecting...');

  ws = new WebSocket(`${protocol}//${location.host}/ws/team`);

  ws.onopen = () => {
    updateStatus('connected', 'Connected');
    console.log('WebSocket connected');
  };

  ws.onclose = () => {
    ws = null;
    if (intentionalClose) {
      // We closed it ourselves, don't reconnect
      return;
    }
    if (sessionActive) {
      // Session was running, try to reconnect
      updateStatus('disconnected', 'Reconnecting...');
      reconnectTimer = setTimeout(connect, 3000);
    } else {
      updateStatus('disconnected', 'Disconnected');
    }
  };

  ws.onerror = (e) => {
    console.error('WebSocket error:', e);
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMessage(data);
  };
}

function disconnect() {
  intentionalClose = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    try { ws.close(); } catch (_e) { /* ignore */ }
    ws = null;
  }
  sessionActive = false;
  updateStatus('disconnected', 'Disconnected');
}

function handleMessage(data) {
  switch (data.type) {
    case 'agent_message':
      removeTypingIndicator(data.role);
      addMessage(data);
      break;
    case 'phase_change':
      setPhase(data.phase);
      break;
    case 'typing':
      showTypingIndicator(data.role, data.display_name);
      break;
    case 'change_proposal':
      showChangeProposal(data);
      break;
    case 'test_results':
      showTestResults(data);
      break;
    case 'done':
      sessionDone(data);
      break;
    case 'error':
      addSystemMessage('Error: ' + data.message);
      break;
    case 'pong':
      break;
  }
}

// -- Task Start --

function showChatScreen() {
  const taskScreen = document.getElementById('task-screen');
  const chatScreen = document.getElementById('chat-screen');
  if (taskScreen) taskScreen.style.display = 'none';
  if (chatScreen) {
    chatScreen.classList.remove('chat-screen-hidden');
    chatScreen.style.display = 'flex';
  }
}

function startTask() {
  const textarea = document.getElementById('task-textarea');
  const task = textarea ? textarea.value.trim() : '';
  if (!task) return;

  const codeTextarea = document.getElementById('code-textarea');
  const code = codeTextarea ? codeTextarea.value.trim() : '';

  // Show chat UI
  showChatScreen();

  // Reset state
  sessionActive = true;
  currentPhase = null;
  completedPhases.clear();
  const msgContainer = document.getElementById('chat-messages');
  if (msgContainer) msgContainer.innerHTML = '';

  // Connect WebSocket
  connect();

  // Wait for connection then send task
  const waitAndSend = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      clearInterval(waitAndSend);

      // Get API key from dashboard.js (localStorage) or env
      const key = (typeof apiKey !== 'undefined' && apiKey) ? apiKey : '';

      ws.send(JSON.stringify({
        type: 'start_task',
        task: task,
        code: code,
        team_config: (typeof teamConfig !== 'undefined') ? teamConfig : {},
        api_key: key,
      }));
      addSystemMessage('Task: ' + task);
    }
  }, 100);

  // Timeout after 10 seconds if can't connect
  setTimeout(() => {
    clearInterval(waitAndSend);
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addSystemMessage('Failed to connect to server. Check that the server is running.');
    }
  }, 10000);
}

// Start a new task from the chat input bar (when no session is active)
function startTaskFromChat(task) {
  showChatScreen();

  sessionActive = true;
  currentPhase = null;
  completedPhases.clear();

  connect();

  const waitAndSend = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      clearInterval(waitAndSend);
      const key = (typeof apiKey !== 'undefined' && apiKey) ? apiKey : '';
      ws.send(JSON.stringify({
        type: 'start_task',
        task: task,
        team_config: (typeof teamConfig !== 'undefined') ? teamConfig : {},
        api_key: key,
      }));
      addSystemMessage('Task: ' + task);
    }
  }, 100);

  setTimeout(() => {
    clearInterval(waitAndSend);
  }, 10000);
}

// -- Message Rendering --

function addMessage(data) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'message';

  const color = AGENT_COLORS[data.role] || '#8b949e';
  const initials = AGENT_INITIALS[data.role] || '??';
  const content = renderContent(data.content || '');

  div.innerHTML = `
    <div class="message-avatar" style="background:${color}">${initials}</div>
    <div class="message-body">
      <div class="message-header">
        <span class="message-name" style="color:${color}">${escapeHtml(data.display_name || data.role)}</span>
        <span class="message-model">${escapeHtml(data.model_id || '')}</span>
        <span class="message-time">${formatTime(data.timestamp)}</span>
      </div>
      <div class="message-content">${content}</div>
    </div>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;

  if (data.phase && data.phase !== currentPhase) {
    setPhase(data.phase);
  }
}

function addSystemMessage(text) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'system-message';
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function renderContent(text) {
  if (!text) return '';
  let html = escapeHtml(text);
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    return `<pre><code class="language-${lang}">${code.trim()}</code></pre>`;
  });
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\n/g, '<br>');
  html = html.replace(/<pre>([\s\S]*?)<\/pre>/g, (match) => {
    return match.replace(/<br>/g, '\n');
  });
  return html;
}

// -- Typing Indicator --

function showTypingIndicator(role, displayName) {
  removeTypingIndicator(role);
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'typing-indicator';
  div.id = `typing-${role}`;

  const color = AGENT_COLORS[role] || '#8b949e';
  div.innerHTML = `
    <span style="color:${color};font-weight:600">${escapeHtml(displayName)}</span>
    is thinking
    <div class="typing-dots">
      <span></span><span></span><span></span>
    </div>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTypingIndicator(role) {
  const el = document.getElementById(`typing-${role}`);
  if (el) el.remove();
}

// -- Phase Management --

function setPhase(phase) {
  if (currentPhase) {
    completedPhases.add(currentPhase);
  }
  currentPhase = phase;
  updatePhaseUI();
}

function updatePhaseUI() {
  PHASES.forEach(phase => {
    const el = document.getElementById(`phase-${phase}`);
    if (!el) return;
    el.classList.remove('active', 'done');
    if (phase === currentPhase) {
      el.classList.add('active');
    } else if (completedPhases.has(phase)) {
      el.classList.add('done');
    }
  });
}

// -- User Input --

function setupInputHandlers() {
  // Task screen: Enter to start
  const taskTextarea = document.getElementById('task-textarea');
  if (taskTextarea) {
    taskTextarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        startTask();
      }
    });
  }

  // Chat input: Enter to send
  const chatInput = document.getElementById('chat-input');
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    chatInput.addEventListener('input', () => {
      checkMentionTrigger(chatInput);
    });
  }
}

function sendMessage() {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const content = input.value.trim();
  if (!content) return;

  // If no active session, start a new one using this message as the task
  if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) {
    input.value = '';
    autoResize(input);
    startTaskFromChat(content);
    return;
  }

  ws.send(JSON.stringify({
    type: 'user_message',
    content: content,
  }));

  // Show user message locally
  addMessage({
    role: 'human',
    display_name: 'You',
    model_id: '',
    content: content,
    timestamp: new Date().toISOString(),
    phase: currentPhase,
  });

  input.value = '';
  autoResize(input);
}

function insertMention(role) {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const current = input.value;
  input.value = `@${role} ${current}`;
  input.focus();
  hideMentionDropdown();
}

// -- @mention Autocomplete --

function checkMentionTrigger(input) {
  const val = input.value;
  const cursorPos = input.selectionStart;
  const textBefore = val.substring(0, cursorPos);
  const mentionMatch = textBefore.match(/@(\w*)$/);

  if (mentionMatch) {
    showMentionDropdown(mentionMatch[1].toLowerCase());
  } else {
    hideMentionDropdown();
  }
}

function showMentionDropdown(query) {
  const dropdown = document.getElementById('mention-dropdown');
  if (!dropdown) return;

  const roles = ['planner', 'architect', 'coder', 'debugger', 'security', 'analyst'];
  const filtered = roles.filter(r => r.startsWith(query));

  if (filtered.length === 0) {
    hideMentionDropdown();
    return;
  }

  dropdown.innerHTML = '';
  filtered.forEach(role => {
    const opt = document.createElement('div');
    opt.className = 'mention-option';
    opt.innerHTML = `
      <div class="dot" style="background:${AGENT_COLORS[role]}"></div>
      <span>@${role}</span>
    `;
    opt.onclick = () => { completeMention(role); };
    dropdown.appendChild(opt);
  });

  dropdown.classList.add('visible');
}

function hideMentionDropdown() {
  const dropdown = document.getElementById('mention-dropdown');
  if (dropdown) dropdown.classList.remove('visible');
}

function completeMention(role) {
  const input = document.getElementById('chat-input');
  if (!input) return;
  const val = input.value;
  const cursorPos = input.selectionStart;
  const textBefore = val.substring(0, cursorPos);
  const textAfter = val.substring(cursorPos);
  const newBefore = textBefore.replace(/@\w*$/, `@${role} `);
  input.value = newBefore + textAfter;
  input.selectionStart = input.selectionEnd = newBefore.length;
  input.focus();
  hideMentionDropdown();
}

// -- Session Done --

function sessionDone(data) {
  setPhase('done');
  sessionActive = false;
  addSystemMessage('Session complete. ' + (data.message_count || 0) + ' messages exchanged. Type a new task to start again.');
  // Don't reconnect after session ends
  intentionalClose = true;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

// -- Utilities --

function updateStatus(cls, text) {
  const el = document.getElementById('conn-status');
  if (el) {
    el.className = 'status ' + cls;
    el.textContent = text;
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch (_e) {
    return '';
  }
}

function autoResize(textarea) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}

function toggleTheme() {
  const body = document.body;
  const current = body.getAttribute('data-theme');
  body.setAttribute('data-theme', current === 'light' ? 'dark' : 'light');
}

function exportTranscript() {
  const messages = document.querySelectorAll('.message');
  let text = '# crosscheck AI Dev Team -- Transcript\n\n';
  messages.forEach(msg => {
    const name = msg.querySelector('.message-name')?.textContent || 'Unknown';
    const content = msg.querySelector('.message-content')?.textContent || '';
    const time = msg.querySelector('.message-time')?.textContent || '';
    text += `**${name}** (${time})\n${content}\n\n---\n\n`;
  });

  const blob = new Blob([text], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'crosscheck-transcript-' + new Date().toISOString().slice(0,10) + '.md';
  a.click();
  URL.revokeObjectURL(url);
}

// -- Change Proposal --

function showChangeProposal(data) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'proposal-card';
  div.id = 'proposal-' + data.proposal_id;

  let changesHtml = '';
  (data.changes || []).forEach((change, i) => {
    const diffText = (data.diffs && data.diffs[i]) ? escapeHtml(data.diffs[i]) : '';
    changesHtml += `
      <div class="proposal-file">
        <div class="proposal-file-header">
          <span class="proposal-action proposal-action-${change.action}">${change.action.toUpperCase()}</span>
          <span class="proposal-filename">${escapeHtml(change.filename)}</span>
        </div>
        ${diffText ? '<pre class="proposal-diff">' + diffText + '</pre>' : ''}
      </div>
    `;
  });

  const summaryHtml = data.coordinator_summary
    ? '<div class="proposal-summary"><strong>Coordinator:</strong> ' + renderContent(data.coordinator_summary) + '</div>'
    : '';

  let findingsHtml = '';
  if (data.observer_findings && data.observer_findings.length > 0) {
    findingsHtml = '<div class="proposal-findings"><strong>Observer Findings:</strong><ul>';
    data.observer_findings.forEach(f => {
      findingsHtml += '<li>' + renderContent(f.substring(0, 500)) + '</li>';
    });
    findingsHtml += '</ul></div>';
  }

  div.innerHTML = `
    <div class="proposal-header">
      <span class="proposal-icon">&#128221;</span>
      <span class="proposal-title">Change Proposal #${escapeHtml(data.proposal_id)}</span>
    </div>
    ${summaryHtml}
    ${findingsHtml}
    <div class="proposal-changes">${changesHtml}</div>
    <div class="proposal-actions">
      <button class="btn-accept" onclick="sendProposalDecision('${data.proposal_id}', 'accept')">Accept</button>
      <button class="btn-reject" onclick="sendProposalDecision('${data.proposal_id}', 'reject')">Reject</button>
    </div>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  setPhase('approval');
}

function sendProposalDecision(proposalId, decision, edits) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const payload = {
    type: 'proposal_decision',
    proposal_id: proposalId,
    decision: decision,
  };
  if (edits) payload.edits = edits;
  ws.send(JSON.stringify(payload));

  const card = document.getElementById('proposal-' + proposalId);
  if (card) {
    const actions = card.querySelector('.proposal-actions');
    if (actions) {
      const label = decision === 'accept' ? 'Accepted' : 'Rejected';
      const cls = decision === 'accept' ? 'decision-accepted' : 'decision-rejected';
      actions.innerHTML = '<span class="proposal-decision ' + cls + '">' + label + '</span>';
    }
  }

  addSystemMessage('Proposal ' + proposalId + ': ' + decision.toUpperCase());
}

// -- Test Results --

function showTestResults(data) {
  const container = document.getElementById('chat-messages');
  if (!container) return;
  const div = document.createElement('div');
  div.className = 'test-results ' + (data.passed ? 'test-passed' : 'test-failed');

  const icon = data.passed ? '&#9989;' : '&#10060;';
  const status = data.passed ? 'ALL TESTS PASSED' : 'TESTS FAILED';

  div.innerHTML = `
    <div class="test-header">
      <span class="test-icon">${icon}</span>
      <span class="test-status">${status}</span>
    </div>
    <pre class="test-output">${escapeHtml(data.content || '')}</pre>
  `;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}
