const socket = io();
const roomUserId = Number(window.CHAT_USER_ID);
const messagesEl = document.getElementById('chat-messages');
const form = document.getElementById('chat-form');
const input = document.getElementById('chat-input');
function escapeHtml(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
function addMessage(m){const mine=Number(m.sender_id)===Number(window.CURRENT_USER_ID||0);const wrap=document.createElement('div');wrap.className='bubble-wrap '+(mine?'mine':'');wrap.innerHTML=`<div class="bubble">${escapeHtml(m.message)}</div><small>${escapeHtml(m.sender_name||'Support')} · ${m.created_at?new Date(m.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):''}</small>`;messagesEl.appendChild(wrap);messagesEl.scrollTop=messagesEl.scrollHeight;}
window.CURRENT_USER_ID=Number(document.body.dataset.userId||0);
socket.on('connect',()=>socket.emit('join_chat',{user_id:roomUserId}));
socket.on('new_message',m=>{if(Number(m.user_id)===roomUserId)addMessage(m);});
socket.on('withdrawal_approved',d=>{if(d.withdrawal_id)window.location.href=d.url;});
if(form)form.addEventListener('submit',e=>{e.preventDefault();const text=input.value.trim();if(!text)return;socket.emit('send_message',{user_id:roomUserId,message:text});input.value='';input.focus();});
messagesEl.scrollTop=messagesEl.scrollHeight;
