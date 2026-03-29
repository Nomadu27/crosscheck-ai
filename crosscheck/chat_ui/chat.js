/**
 * crosscheck AI Dev Team — Group Chat Client
 * WebSocket client for real-time team communication.
 */

const AGENT_COLORS = {
  planner:   '#6A5ACD',
  architect: '#4285F4',
  coder:     '#10A37F',
  debugger:  '#FF6D00',
  security:  '#E94B3C',
  analyst:   '#34A853',
  human:     '#c9d1d9',
};

const AGENT_INITIALS = {
  planner:   'PL',
  architect: 'AR',
  coder:     'CO',
  debugger:  'DB',
  security:  'SC',
  analyst:   'AN',
  human:     'U',
};

const PHASES = ['planning', 'discussion', 'coding', 'review', 'done'];

let ws = null;
let sessionActive = false;
let currentPhase = null;
let completedPhases = new Set();
let teamInfo = [];

// ─── Init ──────────────────────────────────────────────────────────────────

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

// ─── WebSocket ─────────────────────────────────────────────────────────────

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/ws/team`);

  ws.onopen = () => {
    updateStatus('connected', 'Connected');
    console.log('WebSocket connected');
  };

  ws.onclose = () => {
    updateStatus('disconnected', 'Disconnected');
    console.log('WebSocket disconnected');
    // Reconnect after 3 seconds
    setTimeout(connect, 3000);
  };

  ws.onerror = (e) => {
    console.error('WebSocket error:', e);
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMessage(data);
  };
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
    case 'done':
      sessionDone(data);
      break;
    case 'error':
      addSystemMessage(`Error: ${data.message}`);
      break;
    case 'pong':
      break;
  }
}

// ─── Task Start ────────────────────────────────────────────────────────────

function startTask() {
  const textarea = document.getElementById('task-textarea');
  const task = textarea.value.trim();
  if (!task) return;

  // Hide task input, show chat
  document.getElementById('task-screen').style.display = 'none';
  document.getElementById('chat-screen').style.display = 'flex';

  sessionActive = true;
  connect();

  // Wait for connection then send
  const waitAndSend = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      clearInterval(waitAndSend);
      ws.send(JSON.stringify({ type: 'start_task', task: task }));
      addSystemMessage(`Task: ${task}`);
    }
  }, 100);
}

// ─── Message Rendering ─────────────────────────────────────────────────────

function addMessage(data) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'message';

  const color = AGENT_COLORS[data.role] || '#8b949e';
  const initials = AGENT_INITIALS[data.role] || '??';

  // Process content: convert markdown code blocks
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

  // Update phase if agent message includes it
  if (data.phase && data.phase !== currentPhase) {
    setPhase(data.phase);
  }
}

function addSystemMessage(text) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'system-message';
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function renderContent(text) {
  if (!text) return '';

  // Escape HTML first
  let html = escapeHtml(text);

  // Convert fenced code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    return `<pre><code class="language-${lang}">${code.trim()}</code></pre>`;
  });

  // Convert inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Convert newlines to <br> (outside of pre blocks)
  html = html.replace(/\n/g, '<br>');

  // Fix <br> inside <pre> tags — remove them
  html = html.replace(/<pre>([\s\S]*?)<\/pre>/g, (match) => {
    return match.replace(/<br>/g, '\n');
  });

  return html;
}

// ─── Typing Indicator ──────────────────────────────────────────────────────

function showTypingIndicator(role, displayName) {
  removeTypingIndicator(role);
  const container = document.getElementById('chat-messages');
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

// ─── Phase Management ──────────────────────────────────────────────────────

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

// ─── User Input ────────────────────────────────────────────────────────────

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

    // @mention detection
    chatInput.addEventListener('input', () => {
      checkMentionTrigger(chatInput);
    });
  }
}

function sendMessage() {
  const input = document.getElementById('chat-input');
  const content = input.value.trim();
  if (!content || !ws || ws.readyState !== WebSocket.OPEN) return;

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

// ─── @mention Autocomplete ─────────────────────────────────────────────────

function checkMentionTrigger(input) {
  const val = input.value;
  const cursorPos = input.selectionStart;
  const textBefore = val.substring(0, cursorPos);
  const mentionMatch = textBefore.match(/@(\w*)$/);

  if (mentionMatch) {
    const query = mentionMatch[1].toLowerCase();
    showMentionDropdown(query);
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
    opt.onclick = () => {
      completeMention(role);
    };
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

// ─── Session Done ──────────────────────────────────────────────────────────

function sessionDone(data) {
  setPhase('done');
  addSystemMessage(`Session complete. ${data.message_count || 0} messages exchanged.`);
}

// ─── Utilities ─────────────────────────────────────────────────────────────

function updateStatus(cls, text) {
  const el = document.getElementById('conn-status');
  if (el) {
    el.className = `status ${cls}`;
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
  } catch {
    return '';
  }
}

function autoResize(textarea) {
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
  let text = '# crosscheck AI Dev Team — Transcript\n\n';
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
  a.download = `crosscheck-transcript-${new Date().toISOString().slice(0,10)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}
