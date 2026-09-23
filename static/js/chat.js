const socket = io({
  transports: ["websocket", "polling"],
  reconnection: true,
  reconnectionAttempts: Infinity,
  reconnectionDelay: 1000,
  reconnectionDelayMax: 5000,
});

const roomUserId = Number(window.CHAT_USER_ID);
const messagesEl = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function addMessage(m) {
  if (!messagesEl || !m) return;

  // Prevent duplicates after a reconnect or repeated server event.
  if (m.id && messagesEl.querySelector(`[data-message-id="${CSS.escape(String(m.id))}"]`)) return;

  const mine = Number(m.sender_id) === Number(window.CURRENT_USER_ID || 0);
  const wrap = document.createElement('div');
  wrap.className = 'bubble-wrap ' + (mine ? 'mine' : '');
  if (m.id) wrap.dataset.messageId = String(m.id);

  const when = m.created_at
    ? new Date(m.created_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
    : '';

  wrap.innerHTML = `<div class="bubble">${escapeHtml(m.message)}</div><small>${escapeHtml(m.sender_name || 'Support')} · ${escapeHtml(when)}</small>`;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}



function showChatNotification(m) {
  const el = document.getElementById('chat-notification');
  if (!el || !m) return;
  const sender = escapeHtml(m.sender_name || 'Support');
  const preview = escapeHtml(String(m.message || '').slice(0, 120));
  el.innerHTML = `<strong>🔔 New message from ${sender}</strong><span>${preview}</span>`;
  el.hidden = false;
  clearTimeout(window.chatNotificationTimer);
  window.chatNotificationTimer = setTimeout(() => {
    el.hidden = true;
  }, 4500);
}

window.CURRENT_USER_ID = Number(document.body.dataset.userId || 0);

function joinCurrentChat() {
  if (socket.connected && roomUserId) {
    socket.emit('join_chat', {user_id: roomUserId});
  }
}

socket.on('connect', joinCurrentChat);
socket.on('new_message', (m) => {
  if (Number(m.user_id) === roomUserId) {
    const incoming = Number(m.sender_id) !== Number(window.CURRENT_USER_ID || 0);
    addMessage(m);
    if (incoming) showChatNotification(m);
  }
});
socket.on('withdrawal_approved', (d) => {
  if (d && d.withdrawal_id) window.location.href = d.url;
});

if (form) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !socket.connected) return;

    socket.emit('send_message', {
      user_id: roomUserId,
      message: text,
    });

    input.value = '';
    input.focus();
  });
}

if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
