// The line and the answer: the pulsing dots, dictation and talk, pictures, answers, drafts.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages.
const $ = (id) => document.getElementById(id);
// The pulsing dots breathe at 12 frames a second, not 60: every frame redraws the whole
// screen, and a CSS animation kept the processor and X busy the whole time Claude worked.
const pulseFrom = performance.now();
setInterval(() => {
  const dots = document.querySelectorAll('.dot, .rdot, .jobtab i.run'); if (!dots.length) return;
  const o = (0.575 + 0.425 * Math.cos((performance.now() - pulseFrom) / 1400 * 2 * Math.PI)).toFixed(2);
  dots.forEach((d) => { if (d.offsetParent !== null && d.style.opacity !== o) d.style.opacity = o; });
}, 83);
const ask = $('ask');
const native = (msg) => {
  try { window.webkit.messageHandlers.kuhaku.postMessage(JSON.stringify(msg)); }
  catch (e) { if (msg.type === 'ask') setTimeout(() => showAnswer({ question: msg.text, answer: 'Kūhaku is not connected to Claude here.' }), 1200); }
};

let pendingDraft = '', phase = 'home', hintTimer, history = [], historyAt = 0, showActivity = false, dictation = 'off', showWho = false;

function grow() { ask.style.height = 'auto'; ask.style.height = ask.scrollHeight + 'px'; }
function setAsk(v) { ask.value = v; grow(); if (typeof keepDraft === "function") keepDraft(); }
ask.addEventListener('input', grow);

// The pulsing line opens a quiet list of what Claude is doing, with a way to stop it.
$('dot').addEventListener('click', () => {
  showActivity = !showActivity;
  $('activity').hidden = !showActivity || phase !== 'working';
  $('tokens').hidden = $('activity').hidden || !$('tokens').textContent;
  $('stopRow').hidden = $('activity').hidden;
});
$('stopBtn').addEventListener('click', () => { native({ type: 'cancel' }); show('home'); });
const fmt = (n) => n < 1000 ? String(n) : n < 1e6 ? (n / 1e3).toFixed(n < 1e4 ? 1 : 0) + 'k' : (n / 1e6).toFixed(1) + 'M';
let tok = null, limits = null;
function drawTokens() {
  const parts = [];
  if (tok) parts.push(`${fmt(tok.context)} in context`, `${fmt(tok.written)} written`, `${tok.steps} ${tok.steps === 1 ? 'step' : 'steps'}`);
  if (limits?.session) parts.push(`session ${Math.round(limits.session.percent)}% used`);
  $('tokens').textContent = parts.join('  ·  ');
  $('tokens').hidden = !showActivity || phase !== 'working' || !parts.length;
}
window.setTokens = (t) => { if (t && t.tab && t.tab !== jobs.current) return; tok = t; drawTokens(); };
window.setLimits = (l) => { if (l) limits = l; drawTokens(); };
window.addActivity = ({ kind, text, tab }) => {
  if (phase !== 'working' || (tab && tab !== jobs.current)) return;
  const li = document.createElement('li');
  li.className = kind; li.textContent = text;
  $('activity').append(li);
  $('activity').scrollTop = $('activity').scrollHeight;
};

// Dictation: recorded and transcribed offline by Kūhaku, text lands in the line.
function setMic(state) {
  dictation = state;
  $('mic').className = 'mic' + (state === 'rec' ? ' rec' : state === 'busy' ? ' busy' : '');
  ask.placeholder = state === 'rec' ? 'Listening…' : state === 'busy' ? 'Writing it down…' : 'Ask ' + askWho(curTab()?.agent);
}
function toggleDictation() {
  if (dictation === 'off') { setMic('rec'); native({ type: 'dictate', on: true }); }
  else if (dictation === 'rec') { setMic('busy'); native({ type: 'dictate', on: false }); }
}
// The mic, as chosen in Settings: talk with Claude in the Claude app (a voice conversation),
// or dictation: recorded and turned into text here, offline, then sent like typing.
let micMode = 'talk';
window.setMicMode = (m) => {
  if (m === micMode) return;
  if (dictation === 'rec') toggleDictation();
  micMode = m === 'dictate' ? 'dictate' : 'talk';
  const t = micMode === 'dictate' ? 'Dictate (Ctrl+Space)' : 'Talk with Claude (Ctrl+Space)';
  $('mic').title = t; $('mic').setAttribute('aria-label', t.replace(/ \(.*/, ''));
  if (micMode === 'dictate' && voice !== 'off') native({ type: 'voice', on: false });
  $('mic').querySelector('svg').innerHTML = micMode === 'dictate' ? '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>' : '<path d="M4 10.5v3"/><path d="M8 7.5v9"/><path d="M12 4.5v15"/><path d="M16 7.5v9"/><path d="M20 10.5v3"/>';
};
// Talk: a spoken conversation here. Claude listens until you pause, answers aloud, then listens again.
// The same button (or Ctrl+Space, or Escape) ends it; twenty quiet seconds end it too.
let voice = 'off';
window.voiceState = (st) => {
  const state = typeof st === 'string' ? st : st.state;
  voice = state === 'error' ? 'off' : state;
  $('mic').className = 'mic' + (voice === 'off' ? '' : ' voice ' + (voice === 'hearing' ? 'listening' : voice));
  const words = { listening: 'Listening…', hearing: 'Listening…', thinking: 'Thinking…', speaking: 'Speaking… (click to stop)' };
  if (state === 'error') { ask.placeholder = st.error || 'Voice failed.'; setTimeout(() => setMic(dictation), 3000); }
  else if (words[voice]) ask.placeholder = words[voice];
  else setMic(dictation);
};
window.voiceHeard = ({ text }) => {
  if (!text) return;
  history.push(text); historyAt = history.length;
  show('working', text);
  native({ type: 'ask', text, voice: true });
};
function talk() {
  if (micMode === 'dictate') return dictation === 'busy' ? null : toggleDictation();
  // Talk with Claude: the Claude app's own voice chat (a new chat, its mic ready).
  if (voice !== 'off') return native({ type: 'voice', on: false });   // a conversation here still on: end it
  native({ type: 'panel', action: 'talk' });
}
$('mic').addEventListener('click', talk);
// Pictures for Claude: picked files are copied into ~/Pictures/ForClaude and named in the line.
$('pic').addEventListener('click', (e) => { e.preventDefault(); native({ type: 'picture' }); });
window.picturesAdded = ({ files, error }) => {
  if (error) { ask.placeholder = error; setTimeout(() => setMic(dictation), 3000); return; }
  if (!files || !files.length) { focusInput(); return; }
  const note = (files.length > 1 ? 'Pictures: ' : 'Picture: ') + files.join(', ');
  setAsk((ask.value.trim() ? ask.value.replace(/\s*$/, '\n') : '') + note + '\n');
  focusInput();
  ask.setSelectionRange(ask.value.length, ask.value.length);
};
window.dictationDone = ({ text, error }) => {
  setMic('off');
  if (text) setAsk((ask.value.trim() ? ask.value.replace(/\s*$/, ' ') : '') + text);
  else if (error) { ask.placeholder = error; setTimeout(() => setMic('off'), 3000); }
  focusInput();
  ask.setSelectionRange(ask.value.length, ask.value.length);
};

function show(next, question) {
  const was = phase;
  phase = next;
  $('working').hidden = next !== 'working';
  $('next').hidden = next !== 'working';
  if (was === 'working' && next !== 'working') {
    // The prepared message moves into the line, ready to send.
    const draft = $('nextText').value.trim();
    $('nextText').value = ''; $('nextText').hidden = true; $('next').classList.remove('on');
    if (draft) setAsk(draft);
  }
  $('answer').hidden = next !== 'answer';
  $('inputRow').hidden = next === 'working';
  $('activity').hidden = next !== 'working' || !showActivity;
  $('tokens').hidden = next !== 'working' || !showActivity || !$('tokens').textContent;
  $('stopRow').hidden = next !== 'working' || !showActivity;
  drawTabs();
  if (next === 'working') { $('activity').textContent = ''; $('tokens').textContent = ''; tok = null; }
  if (question !== undefined) { $('askedWorking').textContent = question; $('askedAnswer').textContent = question; }
  clearTimeout(hintTimer);
  $('hint').classList.remove('show');
  if (next !== 'working') focusInput();
  place();
}

// The column's top stays at one fixed height and it grows downwards, so typing or switching tabs never makes it jump.
function place() {
  document.querySelector('.stage').style.setProperty('--top', Math.max(110, Math.round(innerHeight * 0.26)) + 'px');
}
addEventListener('resize', place);

// Claude is asked for plain text, but strip any markdown that slips through.
function plain(text) {
  return String(text || '')
    .replace(/```[a-z]*\n?([\s\S]*?)```/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1')
    .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, '$1$2')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '$1 ($2)')
    .replace(/^\s*[-*]\s+/gm, '·  ')
    .trim();
}

function linkify(el, text) {
  el.textContent = '';
  const re = /https?:\/\/[^\s)]+/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    el.append(text.slice(last, m.index));
    const a = document.createElement('a');
    a.href = m[0]; a.textContent = m[0];
    el.append(a);
    last = m.index + m[0].length;
  }
  el.append(text.slice(last));
}

// Every answer is kept; the arrows above an answer step through them.
let answers = [], answerAt = -1;
function when(t) {
  if (!t) return '';
  const d = new Date(t * 1000), now = new Date();
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return time;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) + ', ' + time;
}
function showAt(i, anim = true) {
  if (i < 0 || i >= answers.length) return;
  answerAt = i;
  const e = answers[i];
  renderAnswer(e, anim);
}
function drawNav() {
  const n = answers.length, i = answerAt;
  $('answerNav').style.visibility = '';
  $('prevAnswer').disabled = i <= 0;
  $('nextAnswer').disabled = i < 0 || i >= n - 1;
  $('answerPos').textContent = n > 1 && i >= 0 ? `${i + 1} of ${n}` : '';
  $('answerWhen').textContent = i >= 0 ? when(answers[i].time) : '';
}
window.setAnswers = ({ answers: list, show: visible }) => {
  answers = list || [];
  if (visible && answers.length && phase === 'home' && !ask.value) showAt(answers.length - 1, false);
};
window.showAnswer = (entry) => {
  if (entry.tab && entry.tab !== jobs.current) { if (!entry.error) answers.push(entry); return; }  // its tab shows a red dot
  if (!entry.error) { answers.push(entry); answerAt = answers.length - 1; }
  else answerAt = -1;
  renderAnswer(entry, true);
};
function closeAnswer() { native({ type: 'dismiss' }); show('home'); }
$('prevAnswer').addEventListener('click', () => showAt(answerAt - 1));
$('nextAnswer').addEventListener('click', () => showAt(answerAt + 1));
$('closeAnswer').addEventListener('click', closeAnswer);
// Clicking the line beside the prompt shows earlier messages; again hides them.
document.querySelector('#inputRow .line').addEventListener('click', (e) => {
  e.preventDefault();
  if (phase === 'answer') closeAnswer(); else if (answers.length) showAt(answers.length - 1);
  else { showWho = !showWho; if (!showWho) $('agentMenu').hidden = true; drawAgent(); place(); }
});
// From home, the left arrow on an empty line brings the last answer back.
document.addEventListener('keydown', (e) => {
  if (!e.altKey || page || panel) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); phase === 'answer' ? showAt(answerAt - 1) : showAt(answers.length - 1); }
  if (e.key === 'ArrowRight' && phase === 'answer') { e.preventDefault(); showAt(answerAt + 1); }
});

function renderAnswer({ question, answer, error }, anim) {
  const text = plain(answer);
  const el = $('answerText');
  el.className = 'a' + (text.length > 600 ? ' long' : text.length > 200 ? ' medium' : '') + (error ? ' error' : '');
  linkify(el, text);
  if (error && /went wrong|unexpectedly/.test(text)) {   // one click to what happened, instead of a shortcut
    const a = document.createElement('a');
    a.href = 'kuhaku://details'; a.textContent = 'See what happened';
    el.append(' ', a);
  }
  el.scrollTop = 0;
  if (anim) { $('answer').style.animation = 'none'; void $('answer').offsetWidth; $('answer').style.animation = ''; }
  show('answer', question);
  drawNav();
}

$('nextPlus').addEventListener('click', () => {
  const t = $('nextText'); t.hidden = false; $('next').classList.add('on'); t.focus();
});
$('nextText').addEventListener('input', () => { const t = $('nextText'); t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; });
$('nextText').addEventListener('keydown', (e) => {
  e.stopPropagation();  // Esc here must not stop Claude
  if (e.key === 'Escape') { $('nextText').value = ''; $('nextText').hidden = true; $('next').classList.remove('on'); $('nextText').blur(); }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('nextText').blur(); }
});

// Drafts survive restarts: whatever is in the line (or prepared as next) is kept by Kūhaku.
var draftT;
function keepDraft() {
  clearTimeout(draftT);
  const d = { type: 'draft', tab: jobs.current, text: ask.value, next: $('nextText').value };   // for the tab it was typed in
  draftT = setTimeout(() => native(d), 400);
}
ask.addEventListener('input', keepDraft);
$('nextText').addEventListener('input', keepDraft);
window.setDraft = ({ text, next }) => {
  if (text && !ask.value) setAsk(text);
  if (next) {
    if (phase === 'working') { $('nextText').value = next; $('nextText').hidden = false; $('next').classList.add('on'); }
    else if (!ask.value) setAsk(next);
  }
};
