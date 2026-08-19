/**
 * ADHD Co-Processor — Desktop Shell
 *
 * Manages:
 * - View switching (sidebar navigation)
 * - WebSocket connection to FastAPI backend
 * - Push-to-talk voice recording via MediaRecorder
 * - API calls for each agent view
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const API_BASE = `http://${window.location.hostname || 'localhost'}:8080`;
const WS_URL   = `ws://${window.location.hostname || 'localhost'}:8080/ws/pwa`;
const SAMPLE_RATE = 16000;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let currentView = 'braindump';
let ws = null;
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];
let reconnectTimer = null;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const statusDot   = document.getElementById('status-dot');
const statusText  = document.getElementById('status-text');
const header      = document.getElementById('content-header');
const log         = document.getElementById('transcript-log');
const textInput   = document.getElementById('text-input');
const sendBtn     = document.getElementById('send-btn');
const voiceBtn    = document.getElementById('voice-btn');
const sidebarItems = document.querySelectorAll('.sidebar-item');

// ---------------------------------------------------------------------------
// View definitions
// ---------------------------------------------------------------------------

const VIEWS = {
  braindump: {
    title: 'Brain Dump',
    subtitle: 'Dump your thoughts — the agent structures them into tasks.',
    icon: '🧠',
  },
  schedule: {
    title: 'Schedule',
    subtitle: 'Your adaptive schedule with transition buffers.',
    icon: '📅',
  },
  study: {
    title: 'Study Plan',
    subtitle: 'Decompose any topic into sub-15-minute learning units.',
    icon: '📚',
  },
  code: {
    title: 'Code Assistant',
    subtitle: 'Fix bugs, add features, explain code — all local.',
    icon: '💻',
  },
  web: {
    title: 'Web Task',
    subtitle: 'Search, scrape, or complete web tasks in plain English.',
    icon: '🌐',
  },
  skills: {
    title: 'Skills',
    subtitle: 'Available agent skills and their usage statistics.',
    icon: '🛠️',
  },
  memories: {
    title: 'Memories',
    subtitle: 'All stored memories from brain dumps and interactions.',
    icon: '💾',
  },
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Performance metrics, latency, and recommendations.',
    icon: '📊',
  },
  sovereignty: {
    title: 'Data Sovereignty',
    subtitle: 'Network trace — what the app is allowed to talk to.',
    icon: '🛡️',
  },
};

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setStatus('online', 'Connected');
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleServerMessage(msg);
    } catch (e) {
      console.error('WS parse error:', e);
    }
  };

  ws.onclose = () => {
    setStatus('offline', 'Disconnected');
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    setStatus('offline', 'Error');
  };
}

function handleServerMessage(msg) {
  switch (msg.type) {
    case 'transcript':
      appendLog(msg.text, 'user');
      break;
    case 'response':
    case 'response_text':
      appendLog(msg.text, 'system');
      break;
    case 'status':
      if (msg.text === 'Recording...') {
        voiceBtn.classList.add('recording');
      } else {
        voiceBtn.classList.remove('recording');
      }
      break;
    case 'error':
      appendLog(`⚠️ ${msg.text}`, 'system');
      break;
    case 'nudge':
      appendLog(`🔔 ${msg.text}`, 'system');
      break;
  }
}

function setStatus(state, text) {
  statusDot.className = 'status-dot ' + (state === 'online' ? '' : state);
  statusText.textContent = text;
}

// ---------------------------------------------------------------------------
// Sidebar navigation
// ---------------------------------------------------------------------------

sidebarItems.forEach(item => {
  item.addEventListener('click', () => {
    const view = item.dataset.view;
    if (view === currentView) return;

    sidebarItems.forEach(i => i.classList.remove('active'));
    item.classList.add('active');
    currentView = view;
    renderView(view);
  });
});

function renderView(view) {
  const def = VIEWS[view];
  header.innerHTML = `<h1>${def.icon} ${def.title}</h1><p>${def.subtitle}</p>`;
  log.innerHTML = '';
  loadViewData(view);
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${API_BASE}${path}`, opts);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// View data loaders
// ---------------------------------------------------------------------------

async function loadViewData(view) {
  try {
    switch (view) {
      case 'schedule':   await loadSchedule();   break;
      case 'skills':     await loadSkills();     break;
      case 'memories':   await loadMemories();   break;
      case 'dashboard':  await loadDashboard();  break;
      case 'sovereignty': await loadSovereignty(); break;
      case 'study':      loadStudyPlaceholder(); break;
      case 'code':       loadCodePlaceholder();  break;
      case 'web':        loadWebPlaceholder();   break;
      case 'braindump':  loadBraindumpPlaceholder(); break;
    }
  } catch (e) {
    log.innerHTML = `<div class="empty-state"><p>⚠️ ${e.message}</p></div>`;
  }
}

// --- Schedule ---
async function loadSchedule() {
  const data = await api('GET', '/api/schedule');
  const blocks = data.schedule || [];
  if (!blocks.length) {
    log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📅</div><h2>No tasks yet</h2><p>Do a brain dump first to populate your schedule.</p></div>`;
    return;
  }
  let html = `<div class="schedule-grid">`;
  for (const b of blocks) {
    const type = b.type || 'task';
    const cls = type === 'buffer' ? 'buffer' : type === 'calendar_block' ? 'calendar-block' : '';
    const start = b.start ? new Date(b.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const end = b.end ? new Date(b.end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const label = b.label || (type === 'buffer' ? '☕ Transition break' : type);
    const mins = b.scaled_minutes || b.estimated_minutes || '';
    html += `<div class="schedule-block ${cls}">
      <span class="schedule-time">${start}–${end}</span>
      <span class="schedule-label">${label}</span>
      <span class="schedule-duration">${mins} min</span>
    </div>`;
  }
  html += `</div>`;
  log.innerHTML = html;
}

// --- Skills ---
async function loadSkills() {
  const data = await api('GET', '/api/skills');
  const skills = data.skills || [];
  if (!skills.length) {
    log.innerHTML = `<div class="empty-state"><p>No skills registered.</p></div>`;
    return;
  }
  let html = `<div class="skills-grid">`;
  for (const s of skills) {
    html += `<div class="skill-card" onclick="invokeSkill('${s.name}')">
      <div class="skill-name">${s.name}</div>
      <div class="skill-desc">${s.description || ''}</div>
    </div>`;
  }
  html += `</div>`;
  log.innerHTML = html;
}

async function invokeSkill(name) {
  appendLog(`Invoking skill: ${name}…`, 'system');
  try {
    const data = await api('POST', `/api/skills/${name}/invoke`, {});
    appendLog(`✅ ${name}: ${JSON.stringify(data.result || data).substring(0, 200)}`, 'system');
  } catch (e) {
    appendLog(`⚠️ Skill failed: ${e.message}`, 'system');
  }
}

// --- Memories ---
async function loadMemories() {
  const data = await api('GET', '/api/memories');
  const memories = data.memories || [];
  if (!memories.length) {
    log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">💾</div><h2>No memories yet</h2><p>Your brain dumps will appear here.</p></div>`;
    return;
  }
  let html = `<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">${memories.length} memories stored</div>`;
  for (const m of memories.slice(0, 30)) {
    const text = m.memory || m.text || JSON.stringify(m).substring(0, 120);
    const score = m.score != null ? ` (${(m.score * 100).toFixed(0)}%)` : '';
    html += `<div class="card"><div class="card-text">${text}${score}</div></div>`;
  }
  log.innerHTML = html;
}

// --- Dashboard ---
async function loadDashboard() {
  const data = await api('GET', '/api/dashboard');
  const latency = data.latency || {};
  const energy = data.energy || {};
  let html = `<div class="card"><div class="card-header"><span class="card-title">Latency</span></div>`;
  html += `<div class="card-text">`;
  for (const [k, v] of Object.entries(latency)) {
    if (v && v.avg_ms != null) html += `<div>${k}: <strong>${v.avg_ms.toFixed(0)}ms</strong> avg (${v.count} calls)</div>`;
  }
  html += `</div></div>`;
  html += `<div class="card"><div class="card-header"><span class="card-title">Energy</span></div>`;
  html += `<div class="card-text">`;
  for (const [k, v] of Object.entries(energy)) {
    if (v && v.total_flops != null) html += `<div>${k}: <strong>${v.total_flops.toExponential(1)}</strong> FLOPs</div>`;
  }
  html += `</div></div>`;
  log.innerHTML = html;
}

// --- Placeholders ---
function loadStudyPlaceholder() {
  log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📚</div><h2>Study Plan</h2><p>Type a topic below to decompose it into sub-15-minute learning units.</p></div>`;
}
function loadCodePlaceholder() {
  log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">💻</div><h2>Code Assistant</h2><p>Describe a coding task below. The agent will fix bugs, add features, or explain code — all running locally.</p></div>`;
}
function loadWebPlaceholder() {
  log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🌐</div><h2>Web Task</h2><p>Ask me to search, scrape, or complete a web task in plain English.</p></div>`;
}
function loadBraindumpPlaceholder() {
  log.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🧠</div><h2>Ready for your thoughts</h2><p>Type or speak a brain dump. The agent will extract tasks, ideas, and reminders — then store them in memory.</p></div>`;
}

// ---------------------------------------------------------------------------
// Chat / command handling
// ---------------------------------------------------------------------------

function appendLog(text, type) {
  // Remove empty state if present
  const empty = log.querySelector('.empty-state');
  if (empty) empty.remove();

  const entry = document.createElement('div');
  entry.className = `log-entry log-${type}`;
  entry.textContent = text;
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function handleInput(text) {
  if (!text.trim()) return;
  textInput.value = '';

  appendLog(text, 'user');

  // Route based on current view
  switch (currentView) {
    case 'braindump': sendBraindump(text); break;
    case 'schedule':  sendScheduleCommand(text); break;
    case 'study':     sendStudy(text); break;
    case 'code':      sendCode(text); break;
    case 'web':       sendWebTask(text); break;
    default:          sendBraindump(text);
  }
}

async function sendBraindump(text) {
  try {
    const data = await api('POST', '/api/braindump', { text });
    const thoughts = data.thoughts || [];
    appendLog(`✅ Captured ${thoughts.length} thoughts. Mood: ${data.mood_hint || '?'}. First step: ${data.suggested_first_step || 'none'}`, 'system');
  } catch (e) {
    appendLog(`⚠️ ${e.message}`, 'system');
  }
}

async function sendStudy(text) {
  appendLog('Decomposing topic…', 'system');
  try {
    const data = await api('POST', '/api/study', { topic: text });
    const units = data.units || [];
    let msg = `📚 ${units.length} units, ~${data.total_estimated_minutes || '?'} min total\n`;
    for (const u of units.slice(0, 5)) {
      msg += `  [${u.id}] ${u.title} (${u.estimated_minutes} min)\n`;
    }
    if (units.length > 5) msg += `  … and ${units.length - 5} more`;
    appendLog(msg, 'system');
  } catch (e) {
    appendLog(`⚠️ ${e.message}`, 'system');
  }
}

async function sendCode(text) {
  appendLog('Thinking…', 'system');
  try {
    const data = await api('POST', '/api/code', { instruction: text, action: 'auto' });
    appendLog(`💻 ${data.summary || data.explanation || 'Done.'}`, 'system');
    if (data.files_changed && data.files_changed.length) {
      for (const fc of data.files_changed) {
        appendLog(`  📄 ${fc.path}: ${fc.changes}`, 'system');
      }
    }
  } catch (e) {
    appendLog(`⚠️ ${e.message}`, 'system');
  }
}

async function sendWebTask(text) {
  appendLog('Searching…', 'system');
  try {
    const data = await api('POST', '/api/web-task', { task: text, action: 'auto' });
    appendLog(`🌐 ${data.synthesis || 'Task completed.'}`, 'system');
  } catch (e) {
    appendLog(`⚠️ ${e.message}`, 'system');
  }
}

async function sendScheduleCommand(text) {
  appendLog('Rebalancing…', 'system');
  try {
    const data = await api('POST', '/api/rebalance', { missed_block_id: null });
    appendLog(`📅 ${data.suggestion || 'Schedule updated.'}`, 'system');
    await loadSchedule(); // refresh
  } catch (e) {
    appendLog(`⚠️ ${e.message}`, 'system');
  }
}

// ---------------------------------------------------------------------------
// Voice (push-to-talk)
// ---------------------------------------------------------------------------

async function toggleRecording() {
  if (isRecording) {
    stopRecording();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      voiceBtn.classList.remove('recording');
      isRecording = false;

      // Send audio via WebSocket
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'start', sample_rate: SAMPLE_RATE }));

        // Convert to base64 PCM — for now send as-is via the audio endpoint
        const blob = new Blob(audioChunks, { type: 'audio/webm' });
        const reader = new FileReader();
        reader.onload = () => {
          const b64 = reader.result.split(',')[1];
          // Send via REST (simpler for webm blobs)
          fetch(`${API_BASE}/api/pwa/audio`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_base64: b64, sample_rate: SAMPLE_RATE }),
          })
          .then(r => r.json())
          .then(data => {
            if (data.transcript) appendLog(data.transcript, 'user');
            if (data.response) appendLog(data.response, 'system');
          })
          .catch(e => appendLog(`⚠️ Audio upload failed: ${e.message}`, 'system'));
        };
        reader.readAsDataURL(blob);
      } else {
        appendLog('⚠️ Not connected to server.', 'system');
      }
    };

    mediaRecorder.start();
    isRecording = true;
    voiceBtn.classList.add('recording');
    ws?.send(JSON.stringify({ type: 'status', text: 'Recording...' }));
  } catch (e) {
    appendLog(`⚠️ Microphone access denied: ${e.message}`, 'system');
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
  isRecording = false;
  voiceBtn.classList.remove('recording');
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

sendBtn.addEventListener('click', () => handleInput(textInput.value));
textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleInput(textInput.value);
  }
});
voiceBtn.addEventListener('click', toggleRecording);


// --- Sovereignty ---
async function loadSovereignty() {
  try {
    const data = await api('GET', '/api/sovereignty/status');
    const v = data.verdict;
    const clean = v === 'clean';

    let html = `<div class="card">
      <div class="card-header">
        <span class="card-title">🛡️ Network Sovereignty</span>
        <span class="card-badge ${clean ? 'badge-active' : 'badge-now'}">${clean ? '✅ CLEAN' : '🚨 VIOLATIONS'}</span>
      </div>
      <div class="card-text">
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px">
          <div><div style="font-size:11px;color:var(--text-muted)">Total Connections</div><div style="font-size:20px;font-weight:600">${data.total}</div></div>
          <div><div style="font-size:11px;color:var(--text-muted)">Violations</div><div style="font-size:20px;font-weight:600;color:${data.violations > 0 ? 'var(--red)' : 'var(--green)'}">${data.violations}</div></div>
          <div><div style="font-size:11px;color:var(--text-muted)">Allowed</div><div style="font-size:20px;font-weight:600">${data.allowed}</div></div>
          <div><div style="font-size:11px;color:var(--text-muted)">System</div><div style="font-size:20px;font-weight:600">${data.system}</div></div>
        </div>
      </div>
    </div>`;

    html += `<div class="card">
      <div class="card-header"><span class="card-title">Allowlist</span></div>
      <div class="card-text">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
          <span class="card-badge ${data.tailscale ? 'badge-active' : 'badge-someday'}">🔒 Tailscale ${data.tailscale ? '✅' : 'not seen'}</span>
          <span class="card-badge ${data.google_oauth ? 'badge-active' : 'badge-someday'}">📅 Google OAuth ${data.google_oauth ? '✅' : 'not seen'}</span>
        </div>
        <div style="margin-top:12px;font-size:12px;color:var(--text-muted)">
          <div>✅ Tailscale peers (100.x.x.x) — private tunnel</div>
          <div>✅ Google OAuth (142.250.x.x:443) — Calendar sync</div>
          <div>✅ Localhost — Ollama, Qdrant</div>
          <div>🚨 Any other outbound from app process = violation</div>
        </div>
      </div>
    </div>`;

    if (data.violation_details && data.violation_details.length) {
      html += `<div class="card"><div class="card-header"><span class="card-title" style="color:var(--red)">🚨 Violations</span></div>`;
      for (const v of data.violation_details) {
        html += `<div class="card-text" style="margin-top:4px">• ${v.process} → ${v.remote_ip}:${v.remote_port}<br><span style="color:var(--text-muted)">${v.reason}</span></div>`;
      }
      html += `</div>`;
    }

    html += `<div class="card"><div class="card-text" style="font-size:12px;color:var(--text-muted)">
      💡 Run <code>uv run python main.py --sovereignty</code> for a full 30-second continuous trace.
    </div></div>`;

    log.innerHTML = html;
  } catch (e) {
    log.innerHTML = `<div class="empty-state"><p>⚠️ ${e.message}</p></div>`;
  }
}

async function checkSovereigntySidebar() {
  try {
    const res = await fetch(API_BASE + '/api/sovereignty/status');
    if (!res.ok) return;
    const d = await res.json();
    const dot = document.getElementById('sov-sidebar-dot');
    const label = document.getElementById('sov-sidebar-label');
    if (!dot || !label) return;

    if (d.verdict === 'clean') {
      dot.className = 'status-dot';
      label.textContent = `✅ ${d.total} conn`;
    } else {
      dot.className = 'status-dot offline';
      label.textContent = `🚨 ${d.violations} vio`;
    }
  } catch (e) {}
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

connect();
checkSovereigntySidebar();
setInterval(checkSovereigntySidebar, 60000);

// --- Notifications ---
let desktopNotificationsEnabled = false;

async function pollDesktopNotifications() {
  try {
    const res = await fetch(API_BASE + '/api/notifications?unread_only=true&limit=5');
    if (!res.ok) return;
    const data = await res.json();
    const notifs = data.notifications || [];
    
    for (const notif of notifs) {
      appendLog(`🔔 ${notif.title}: ${notif.body}`, 'system');
      
      // Try system notification
      if (desktopNotificationsEnabled && 'Notification' in window) {
        try {
          new Notification(notif.title, {
            body: notif.body,
            tag: notif.category + '_' + notif.id,
          });
        } catch (e) {}
      }
    }
    
    if (notifs.length > 0) {
      await fetch(API_BASE + '/api/notifications/read-all', { method: 'POST' });
    }
  } catch (e) {}
}

// Request notification permission on first click
document.addEventListener('click', function reqNotif() {
  if (!desktopNotificationsEnabled && 'Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().then(p => {
      desktopNotificationsEnabled = p === 'granted';
    });
  }
  document.removeEventListener('click', reqNotif);
}, { once: true });

setInterval(pollDesktopNotifications, 30000);
