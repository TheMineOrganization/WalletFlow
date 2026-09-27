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
const me = Number(document.body.dataset.userId || 0);
const adminName = window.CHAT_ADMIN_NAME || 'BitBuy';

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function addMessage(m) {
  if (!messagesEl || !m) return;
  if (m.id && messagesEl.querySelector(`[data-message-id="${CSS.escape(String(m.id))}"]`)) return;

  const mine = Number(m.sender_id) === me;
  const wrap = document.createElement('div');
  wrap.className = 'bubble-wrap ' + (mine ? 'mine' : '');
  if (m.id) wrap.dataset.messageId = String(m.id);

  const when = m.created_at
    ? new Date(m.created_at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
    : '';
  const sender = Number(m.sender_id) !== me && !window.IS_ADMIN ? adminName : (m.sender_name || (window.IS_ADMIN ? 'Customer' : adminName));

  wrap.innerHTML = `<div class="bubble">${escapeHtml(m.message)}</div><small>${escapeHtml(sender)} · ${escapeHtml(when)}</small>`;
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showChatNotification(m) {
  const el = document.getElementById('chat-notification');
  if (!el || !m) return;
  const sender = escapeHtml(m.sender_name || adminName);
  const preview = escapeHtml(String(m.message || '').slice(0, 120));
  el.innerHTML = `<strong>🔔 New message from ${sender}</strong><span>${preview}</span>`;
  el.hidden = false;
  clearTimeout(window.chatNotificationTimer);
  window.chatNotificationTimer = setTimeout(() => { el.hidden = true; }, 4500);
}

function joinCurrentChat() {
  if (!socket.connected || !roomUserId) return;
  socket.emit('join_chat', {user_id: roomUserId});
  socket.emit('join_notifications', {user_id: me});
}

async function markCurrentChatRead() {
  if (!roomUserId) return;
  try {
    await fetch('/api/chat/read', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify({user_id: roomUserId})
    });
    const badge = document.getElementById('chat-unread-dot');
    const inbox = document.getElementById('chat-inbox-indicator');
    if (!window.IS_ADMIN) {
      if (badge) badge.hidden = true;
      if (inbox) inbox.hidden = true;
    }
  } catch (e) {}
}

socket.on('connect', () => {
  joinCurrentChat();
  markCurrentChatRead();
});

function updateInboxCountFromServer() {
  fetch('/api/chat/unread', {headers: {'Accept': 'application/json'}})
    .then(r => r.json())
    .then(d => {
      if (!d.success) return;
      const dot = document.getElementById('chat-unread-dot');
      const inbox = document.getElementById('chat-inbox-indicator');
      const count = document.getElementById('chat-unread-count');
      const total = Number(d.total || 0);
      if (count) count.textContent = total > 99 ? '99+' : String(total);
      if (dot) dot.hidden = total === 0;
      if (inbox) inbox.hidden = total === 0;
      document.querySelectorAll('[data-chat-user-id]').forEach(el => {
        const uid = el.dataset.chatUserId;
        const n = Number((d.by_user || {})[uid] || 0);
        let badge = el.querySelector('.chat-user-unread');
        if (n > 0) {
          if (!badge) { badge = document.createElement('span'); badge.className = 'chat-user-unread'; el.appendChild(badge); }
          badge.textContent = n > 99 ? '99+' : String(n);
        } else if (badge) badge.remove();
      });
    }).catch(() => {});
}

socket.on('new_message', (m) => {
  const messageUserId = Number(m.user_id);
  const incoming = Number(m.sender_id) !== me;

  if (messageUserId === roomUserId) {
    addMessage(m);
    if (incoming) {
      showChatNotification(m);
      markCurrentChatRead();
    }
    return;
  }

  // Admins can receive messages from other customer conversations while
  // viewing a different chat. Refresh the shared unread inbox immediately.
  if (window.IS_ADMIN && incoming) {
    updateInboxCountFromServer();
    showChatNotification(m);
  }
});

if (form) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !socket.connected) return;
    socket.emit('send_message', {user_id: roomUserId, message: text});
    input.value = '';
    input.focus();
  });
}

if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
updateInboxCountFromServer();
