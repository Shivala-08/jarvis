/**
 * Jarvis Co-Processor — PWA Client
 *
 * Handles:
 * - Push-to-talk voice recording via MediaRecorder API
 * - WebSocket connection for real-time streaming
 * - Text command input
 * - Message display and UI updates
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const WS_URL = `ws://${window.location.host}/ws/pwa`;
const SAMPLE_RATE = 16000;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let ws = null;
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let reconnectTimer = null;

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log('🔌 WebSocket connected');
    updateStatus(true);
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMessage(data);
  };

  ws.onclose = () => {
    console.log('🔌 WebSocket disconnected');
    updateStatus(false);
    // Reconnect after 3 seconds
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };
}

function updateStatus(connected) {
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  if (connected) {
    dot.style.background = 'var(--success)';
    text.textContent = 'Connected';
  } else {
    dot.style.background = 'var(--error)';
    text.textContent = 'Reconnecting...';
  }
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

function handleMessage(data) {
  // Unwrap broadcast messages: {type: "nudge", data: {...}} → treat as update
  const payload = data.data || data;

  switch (data.type) {
    case 'status':
      document.getElementById('recordStatus').textContent = data.text;
      break;

    case 'transcript':
      addMessage('user', data.text);
      break;

    case 'response':
      addMessage('assistant', data.text);
      break;

    case 'nudge':
      // Focus drift nudge from body-double agent
      showNudge(payload.text || data.text || 'Time to refocus', payload.drift_seconds || 0);
      break;

    case 'schedule_updated':
      addMessage('assistant', `📅 Schedule updated — ${payload.remaining_blocks || '?'} blocks remaining`);
      break;

    case 'braindump_completed':
      addMessage('assistant', `🧠 Brain dump captured ${payload.count || '?'} thoughts`);
      break;

    case 'response_audio':
      // Audio is received but played back via the TTS synthesis
      // The browser can play this if we decode it
      playAudioChunk(data.data, data.sample_rate);
      break;

    case 'error':
      addMessage('system', `⚠️ ${data.text}`);
      break;
  }
}

function addMessage(role, text) {
  const container = document.getElementById('messages');
  const msg = document.createElement('div');
  msg.className = `message ${role}`;

  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  msg.innerHTML = `${escapeHtml(text)}<div class="timestamp">${time}</div>`;

  container.appendChild(msg);
  container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Nudge notifications
// ---------------------------------------------------------------------------

let nudgeTimeout = null;

function showNudge(text, driftSeconds) {
  // Remove existing nudge
  const existing = document.getElementById('nudge-banner');
  if (existing) existing.remove();
  if (nudgeTimeout) clearTimeout(nudgeTimeout);

  // Create nudge banner
  const banner = document.createElement('div');
  banner.id = 'nudge-banner';
  banner.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    padding: 16px 20px;
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.95), rgba(139, 92, 246, 0.95));
    color: white;
    font-size: 15px;
    font-weight: 500;
    text-align: center;
    z-index: 1000;
    animation: slideDown 0.3s ease-out;
    cursor: pointer;
    box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
  `;

  const driftText = driftSeconds > 60
    ? ` (drifted ${Math.round(driftSeconds / 60)}m)`
    : driftSeconds > 0
      ? ` (drifted ${driftSeconds}s)`
      : '';

  banner.innerHTML = `🧠 ${escapeHtml(text)}${driftText}`;

  // Tap to dismiss
  banner.onclick = () => {
    banner.style.animation = 'slideUp 0.2s ease-in forwards';
    setTimeout(() => banner.remove(), 200);
  };

  document.body.appendChild(banner);

  // Also add as a system message
  addMessage('system', `🧠 ${text}`);

  // Try to send a push notification if permitted
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      new Notification('Jarvis', {
        body: text,
        icon: '/pwa/icons/icon-192.png',
        tag: 'nudge',
      });
    } catch (e) {
      // Push notifications not supported in this context
    }
  }

  // Auto-dismiss after 10 seconds
  nudgeTimeout = setTimeout(() => {
    if (banner.parentNode) {
      banner.style.animation = 'slideUp 0.2s ease-in forwards';
      setTimeout(() => banner.remove(), 200);
    }
  }, 10000);
}

// Add CSS animations for nudge banner
const nudgeStyle = document.createElement('style');
nudgeStyle.textContent = `
  @keyframes slideDown {
    from { transform: translateY(-100%); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
  }
  @keyframes slideUp {
    from { transform: translateY(0); opacity: 1; }
    to { transform: translateY(-100%); opacity: 0; }
  }
`;
document.head.appendChild(nudgeStyle);

// ---------------------------------------------------------------------------
// Audio playback
// ---------------------------------------------------------------------------

let audioQueue = [];
let isPlaying = false;

function playAudioChunk(base64Data, sampleRate) {
  audioQueue.push({ base64Data, sampleRate });
  if (!isPlaying) {
    playNextChunk();
  }
}

async function playNextChunk() {
  if (audioQueue.length === 0) {
    isPlaying = false;
    return;
  }

  isPlaying = true;
  const { base64Data, sampleRate } = audioQueue.shift();

  try {
    // Decode base64 PCM float32 to AudioBuffer
    const raw = atob(base64Data);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) {
      bytes[i] = raw.charCodeAt(i);
    }

    const float32 = new Float32Array(bytes.buffer);
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const buffer = audioCtx.createBuffer(1, float32.length, sampleRate);
    buffer.getChannelData(0).set(float32);

    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    source.onended = () => {
      audioCtx.close();
      playNextChunk();
    };
    source.start();
  } catch (e) {
    console.error('Audio playback error:', e);
    playNextChunk();
  }
}

// ---------------------------------------------------------------------------
// Voice recording (Push-to-talk)
// ---------------------------------------------------------------------------

async function startRecording() {
  if (isRecording) return;

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      }
    });

    // Use AudioWorklet or ScriptProcessor to capture raw PCM
    const audioContext = new AudioContext({ sampleRate: SAMPLE_RATE });
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    audioChunks = [];
    processor.onaudioprocess = (event) => {
      if (!isRecording) return;
      const data = event.inputBuffer.getChannelData(0);
      audioChunks.push(new Float32Array(data));
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    isRecording = true;
    document.getElementById('recordBtn').classList.add('recording');
    document.getElementById('recordStatus').textContent = 'Recording...';

    // Send start message
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'start', sample_rate: SAMPLE_RATE }));
    }

    // Store references for cleanup
    window._recordingStream = stream;
    window._recordingContext = audioContext;
    window._recordingProcessor = processor;
    window._recordingSource = source;

  } catch (e) {
    console.error('Recording error:', e);
    addMessage('system', '⚠️ Microphone access denied. Please allow microphone access.');
  }
}

function stopRecording() {
  if (!isRecording) return;

  isRecording = false;
  document.getElementById('recordBtn').classList.remove('recording');
  document.getElementById('recordStatus').textContent = 'Processing...';

  // Stop the stream
  if (window._recordingStream) {
    window._recordingStream.getTracks().forEach(t => t.stop());
  }
  if (window._recordingProcessor) {
    window._recordingProcessor.disconnect();
  }
  if (window._recordingSource) {
    window._recordingSource.disconnect();
  }
  if (window._recordingContext) {
    window._recordingContext.close();
  }

  // Merge chunks into a single Float32Array
  const totalLength = audioChunks.reduce((acc, chunk) => acc + chunk.length, 0);
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of audioChunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }

  // Convert to base64
  const bytes = new Uint8Array(merged.buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  const base64 = btoa(binary);

  // Send audio data
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'audio', data: base64 }));
    ws.send(JSON.stringify({ type: 'stop' }));
  }

  audioChunks = [];
}

// ---------------------------------------------------------------------------
// Text commands
// ---------------------------------------------------------------------------

function sendText() {
  const input = document.getElementById('textInput');
  const text = input.value.trim();
  if (!text) return;

  addMessage('user', text);

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'command', text: text }));
  } else {
    // Fallback to REST API
    fetch('/api/braindump', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text }),
    })
    .then(r => r.json())
    .then(data => {
      const thoughts = data.thoughts || [];
      addMessage('assistant', `Captured ${thoughts.length} thoughts.`);
    })
    .catch(e => {
      addMessage('system', `⚠️ Error: ${e.message}`);
    });
  }

  input.value = '';
}

function sendCommand(text) {
  addMessage('user', text);

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'command', text: text }));
  } else {
    // Fallback
    addMessage('system', '⚠️ Not connected. Please wait for reconnection.');
  }
}

// ---------------------------------------------------------------------------
// Push Notifications
// ---------------------------------------------------------------------------

let pushEnabled = false;

function requestPushPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().then(permission => {
      pushEnabled = permission === 'granted';
      if (pushEnabled) {
        addMessage('system', '🔔 Notifications enabled — you\'ll get gentle reminders.');
      }
    });
  } else if ('Notification' in window && Notification.permission === 'granted') {
    pushEnabled = true;
  }
}

function sendPushNotification(title, body, tag) {
  if (!pushEnabled || !('Notification' in window)) return;
  try {
    const notif = new Notification(title, {
      body: body,
      icon: '/pwa/icons/icon-192.png',
      tag: tag || 'jarvis_' + Date.now(),
      requireInteraction: false,
      silent: false,
    });
    notif.onclick = () => {
      window.focus();
      notif.close();
    };
  } catch (e) {
    console.log('Push notification failed:', e);
  }
}

async function pollNotifications() {
  try {
    const res = await fetch('/api/notifications?unread_only=true&limit=5');
    if (!res.ok) return;
    const data = await res.json();
    const notifs = data.notifications || [];
    
    for (const notif of notifs) {
      sendPushNotification(notif.title, notif.body, notif.category + '_' + notif.id);
    }
    
    // Mark as read after showing
    if (notifs.length > 0) {
      await fetch('/api/notifications/read-all', { method: 'POST' });
    }
  } catch (e) {
    // Notifications endpoint may not exist
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  connect();

  // Register service worker for offline support
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(e => {
      console.log('SW registration skipped:', e.message);
    });
  }

  // Request push notification permission on first user interaction
  const requestOnce = () => {
    requestPushPermission();
    document.removeEventListener('touchstart', requestOnce);
    document.removeEventListener('click', requestOnce);
  };
  document.addEventListener('touchstart', requestOnce, { once: true });
  document.addEventListener('click', requestOnce, { once: true });

  // Poll for notifications every 30 seconds
  setInterval(pollNotifications, 30000);
});


// ---------------------------------------------------------------------------
// Sovereignty status widget
// ---------------------------------------------------------------------------

let sovExpanded = false;

async function checkSovereignty() {
  try {
    const res = await fetch('/api/sovereignty/status');
    if (!res.ok) return;
    const d = await res.json();

    const dot = document.getElementById('sovDot');
    const label = document.getElementById('sovLabel');
    const tailscale = document.getElementById('sovTailscale');
    const google = document.getElementById('sovGoogle');

    if (d.verdict === 'clean') {
      dot.className = 'sov-dot clean';
      label.textContent = '✅ Sovereign — ' + d.total + ' connections, 0 violations';
    } else {
      dot.className = 'sov-dot violations';
      label.textContent = '🚨 ' + d.violations + ' violation(s) detected!';
    }

    // Show Tailscale/Google pills
    if (d.tailscale) tailscale.style.display = '';
    if (d.google_oauth) google.style.display = '';

    // Update expanded details
    document.getElementById('sovStatus').textContent = d.verdict === 'clean' ? '✅ Clean' : '🚨 Violations';
    document.getElementById('sovConns').textContent = d.total;
    document.getElementById('sovAllowed').textContent = d.allowed;
    document.getElementById('sovSystem').textContent = d.system;
    document.getElementById('sovViolations').textContent = d.violations;

  } catch (e) {
    console.log('Sovereignty check failed:', e.message);
  }
}

function toggleSovDetails() {
  sovExpanded = !sovExpanded;
  const el = document.getElementById('sovExpanded');
  if (sovExpanded) {
    el.classList.add('open');
    checkSovereignty(); // Refresh on open
  } else {
    el.classList.remove('open');
  }
}

// Check sovereignty on load + every 60s
document.addEventListener('DOMContentLoaded', () => {
  checkSovereignty();
  setInterval(checkSovereignty, 60000);
});
