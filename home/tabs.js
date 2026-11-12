// Jobs as tabs, the activity list, the red dot, the safety net's four dots, day and night.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages.
// ---- Jobs as tabs -------------------------------------------------------
let jobs = { tabs: [], current: null, agents: [] }, renaming = null;
const agentName = (id) => (jobs.agents.find((a) => a.id === (id || '')) || { name: 'Claude' }).name;
const escT = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const curTab = () => jobs.tabs.find((t) => t.id === jobs.current);
const atTime = (ts) => new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
function drawTabs() {
  drawAgent();
  if (renaming) return;   // never pull the name field away while it is being typed
  const nav = $('jobTabs');
  nav.hidden = !(jobs.tabs.length > 1 || phase === 'working');
  nav.innerHTML = '<div class="jobscroll" id="jobScroll">' + (jobs.tabs.length > 1 || phase === 'working' ? jobs.tabs.map((t) => {
    const mark = t.status === 'running' ? 'run' : t.status === 'queued' ? 'queued' : t.unseen ? 'unseen' : 'idle';
    const extra = t.status === 'queued' && t.at ? `<small>${atTime(t.at)}</small>` : t.status === 'queued' && t.place ? `<small>${t.place}</small>` : '';
    const who = [t.agent ? agentName(t.agent) : '', t.anon ? 'anonymous' : ''].filter(Boolean).join(' · ');
    const busy = t.status === 'running' || t.status === 'queued' ? ' busy' : '';
    return `<span class="jobtab${t.id === jobs.current ? ' on' : ''}${busy}" data-tab="${t.id}" title="${escT(t.note || [t.title || 'New', who].filter(Boolean).join(' · '))}">` +
      `<i class="${mark}"></i><b>${escT(t.title || 'New')}</b>${who ? `<small>${escT(who)}</small>` : ''}${extra}<button class="x" data-close="${t.id}" title="${t.anon ? 'Close and erase (Ctrl+W)' : busy ? 'Stop and close (Ctrl+W) · kept in Kiroku' : 'Close (Ctrl+W)'}">×</button></span>`;
  }).join('') : '') + '</div>' +
    '<button class="jobplus" id="jobPlus" title="Another job, side by side (Ctrl+T)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>';
  const on = nav.querySelector('.jobtab.on');
  if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  tabFades();
  // The open tab waiting in line: why, and what can be done about it.
  const t = curTab(), waiting = phase === 'working' && t && t.status === 'queued';
  $('working').classList.toggle('waiting', !!waiting);
  $('queueRow').hidden = !waiting;
  if (waiting) {
    $('queueNote').textContent = t.at ? 'Starts at ' + atTime(t.at) : t.note || (t.place > 1 ? `Number ${t.place} in line` : 'Starting…');
    $('qFirst').classList.toggle('on', t.priority === 2);
    $('qNow').hidden = !t.at;
  }
}
// Who does the job stays out of sight until the red line is clicked (earlier answers, or the line alone).
const askWho = (id) => (id || '').startsWith('agent:') ? agentName(id) : 'Claude';
function drawAgent() {
  const t = curTab(), many = jobs.agents.length > 1;
  if (phase === 'working') showWho = false;
  $('agentBtn').hidden = !many || !(showWho || phase === 'answer');
  if ($('agentBtn').hidden) $('agentMenu').hidden = true;
  $('agentBtn').textContent = agentName(t?.agent) + (t?.anon ? ' · anonymous' : '');
  if (dictation === 'off') ask.placeholder = 'Ask ' + askWho(t?.agent);
  if (!many) $('agentMenu').hidden = true;
  if (!$('agentMenu').hidden) $('agentMenu').innerHTML = jobs.agents.map((a) =>
    `<button data-agent="${escT(a.id)}" class="${a.id === (t?.agent || '') ? 'on' : ''}">${escT(a.name)}${a.about ? `<small>${escT(a.about)}</small>` : ''}</button>`).join('') +
    `<button data-anon class="anon${t?.anon ? ' on' : ''}">Anonymous${t?.anon ? ' · on' : ''}<small>not kept in Kiroku or any log; erased from this computer when the tab closes (Anthropic still processes it). Starts a fresh conversation.</small></button>`;
}
$('agentBtn').addEventListener('click', (e) => {
  e.preventDefault();
  $('agentMenu').hidden = !$('agentMenu').hidden; drawAgent(); place();
});
$('agentMenu').addEventListener('click', (e) => {
  if (e.target.closest('[data-anon]')) {
    $('agentMenu').hidden = true;
    tabDo('anon', jobs.current);
    const t = curTab(); if (t) t.anon = !t.anon;
    show('home'); drawTabs(); place(); return;
  }
  const b = e.target.closest('[data-agent]'); if (!b) return;
  $('agentMenu').hidden = true;
  tabDo('agent', jobs.current, { agent: b.dataset.agent });
  const t = curTab(); if (t) t.agent = b.dataset.agent;
  drawTabs(); place();
});
function startRename(id) {
  const b = document.querySelector(`.jobtab[data-tab="${id}"] b`), t = jobs.tabs.find((x) => x.id === id);
  if (!b || !t || renaming) return;
  renaming = id;
  const inp = document.createElement('input');
  inp.className = 'tabname'; inp.value = t.title || ''; inp.placeholder = 'Name'; inp.spellcheck = false;
  const fit = () => { inp.style.width = Math.max(40, Math.min(240, inp.value.length * 8 + 12)) + 'px'; };
  fit(); b.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = (save) => {
    if (done) return; done = true; renaming = null;
    const v = inp.value.trim();
    if (save && v !== (t.title || '')) { t.title = v || t.title; tabDo('rename', id, { title: v }); }
    drawTabs(); focusInput();
  };
  inp.addEventListener('input', fit);
  inp.addEventListener('keydown', (e) => {
    e.stopPropagation();   // Esc here must not stop Claude, Ctrl+W must not close the tab
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  inp.addEventListener('blur', () => finish(true));
  inp.addEventListener('click', (e) => e.stopPropagation());
}
window.setTabs = (j) => {
  const was = jobs.current; jobs = Object.assign({ agents: [] }, j);
  const t = curTab();
  if (was === jobs.current && t && phase === 'working' && t.status === 'idle' && !t.answer) { /* the answer arrives on its own */ }
  drawTabs();
};
// Each tab keeps its own view, its own typed text and its own prepared next message.
window.restoreTab = ({ id, status, question, activity, tokens, answer, draft, next }) => {
  if (id) jobs.current = id;
  clearTimeout(draftT);
  const busy = status === 'running' || status === 'queued';
  $('nextText').value = ''; $('nextText').hidden = true; $('next').classList.remove('on');
  $('agentMenu').hidden = true;
  if (busy) {
    show('working', question);
    (activity || []).forEach((a) => addActivity(a));
    if (tokens) setTokens(tokens);
  } else if (answer) { answerAt = answers.findIndex((a) => a.time === answer.time && a.question === answer.question); renderAnswer(answer, true); }
  else show('home');
  ask.value = draft || ''; grow();
  if (next && busy) { $('nextText').value = next; $('nextText').hidden = false; $('next').classList.add('on'); }
  else if (next && !ask.value) { ask.value = next; grow(); }
  drawTabs(); focusInput();
};
// Leaving a tab takes what was typed there along, so it is waiting when you come back.
const tabDo = (action, id, extra) => {
  const leaving = action === 'new' || action === 'open' || action === 'close';
  if (leaving) clearTimeout(draftT);
  native(Object.assign({ type: 'tab', action, id }, leaving ? { draft: ask.value, next: $('nextText').value } : {}, extra || {}));
};
$('jobTabs').addEventListener('click', (e) => {
  const x = e.target.closest('[data-close]'); if (x) { e.stopPropagation(); return tabDo('close', x.dataset.close); }
  if (e.target.closest('#jobPlus')) return tabDo('new');
  const t = e.target.closest('[data-tab]'); if (!t) return;
  if (t.dataset.tab !== jobs.current) tabDo('open', t.dataset.tab);
  else if (e.target.closest('b')) startRename(t.dataset.tab);
});
// Tabs live on one line: the wheel scrolls it sideways, the edges fade where more tabs hide.
function tabFades() {
  const n = $('jobScroll'); if (!n) return;
  n.classList.toggle('less', n.scrollLeft > 1);
  n.classList.toggle('more', n.scrollLeft + n.clientWidth < n.scrollWidth - 1);
}
$('jobTabs').addEventListener('scroll', tabFades, { passive: true, capture: true });
window.addEventListener('resize', tabFades);
$('jobTabs').addEventListener('wheel', (e) => {
  const n = $('jobScroll'); if (!n) return;
  if (n.scrollWidth <= n.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  e.preventDefault(); n.scrollLeft += e.deltaY;
}, { passive: false });
$('jobTabs').addEventListener('dblclick', (e) => {
  const t = e.target.closest('[data-tab]'); if (t && t.dataset.tab === jobs.current) startRename(t.dataset.tab);
});
$('qFirst').addEventListener('click', () => tabDo('first', jobs.current));
$('qNow').addEventListener('click', () => tabDo('now', jobs.current));
$('qLeave').addEventListener('click', () => { native({ type: 'cancel' }); show('home'); });
document.addEventListener('keydown', (e) => {
  if (!e.ctrlKey || page || panel) return;
  if (e.key === 't') { e.preventDefault(); tabDo('new'); }
  else if (e.key === 'w' && (jobs.tabs.length > 1 || phase === 'working')) { e.preventDefault(); tabDo('close', jobs.current); }
  else if (e.key === 'Tab' && jobs.tabs.length > 1) {
    e.preventDefault();
    const i = jobs.tabs.findIndex((t) => t.id === jobs.current), n = jobs.tabs.length;
    tabDo('open', jobs.tabs[(i + (e.shiftKey ? n - 1 : 1)) % n].id);
  } else if (/^[1-9]$/.test(e.key) && jobs.tabs[+e.key - 1]) { e.preventDefault(); tabDo('open', jobs.tabs[+e.key - 1].id); }
});

window.resetConversation = () => { setAsk(''); show('home'); };
window.focusInput = () => { if (!renaming && phase !== 'working' && $('sheet').hidden && !panel && !page) ask.focus(); };

ask.addEventListener('keydown', (e) => {
  const firstLine = !ask.value.slice(0, ask.selectionStart).includes('\n');
  const lastLine = !ask.value.slice(ask.selectionEnd).includes('\n');
  // Earlier questions come back with Up, but never at the cost of what is being written: the text
  // typed so far is kept and returns with Down past the newest question.
  const browsing = historyAt < history.length && ask.value === history[historyAt];
  if (e.key === 'ArrowUp' && history.length && firstLine && (browsing || !ask.value.trim() || ask.selectionStart === 0)) {
    if (!browsing) pendingDraft = ask.value;
    historyAt = Math.max(0, historyAt - 1); setAsk(history[historyAt]); e.preventDefault(); return;
  }
  if (e.key === 'ArrowDown' && browsing && lastLine) {
    historyAt = Math.min(history.length, historyAt + 1);
    setAsk(historyAt < history.length ? history[historyAt] : pendingDraft); e.preventDefault(); return;
  }
  // Escape closes what is open first (a panel, a page, the menu); only then does it clear the line.
  if (e.key === 'Escape') { if (panel || page || !$('sheet').hidden) return; if (voice !== 'off') native({ type: 'voice', on: false }); else if (dictation === 'rec') toggleDictation(); else if (!ask.value && phase === 'answer') closeAnswer(); else setAsk(''); return; }
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  if (!ask.value.trim() || dictation !== 'off') return;
  const q = ask.value.trim();
  history.push(q); historyAt = history.length;
  setAsk('');
  show('working', q);
  native({ type: 'ask', text: q });
});

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.code === 'Space') { e.preventDefault(); return talk(); }
  if (panel === 'calendar' && /^(ArrowLeft|ArrowRight|PageUp|PageDown)$/.test(e.key) && e.target.tagName !== 'INPUT') {
    return shiftCal(/Left|Up/.test(e.key) ? -1 : 1);
  }
  if (e.key !== 'Escape') return;
  if (panel === 'wifi' && wifi.open) { wifi.open = null; return renderWifi(); }
  if (page && !panel) return closePage();
  if (panel) return closePanel();
  if (!$('sheet').hidden) return closeMenu();
  if (!$('runs').hidden) return closeRuns();
  if (phase === 'working') { native({ type: 'cancel' }); show('home'); }
});

// Keep the cursor in the line whatever is clicked, except when selecting answer text.
document.addEventListener('mouseup', () => {
  if (!String(getSelection())) setTimeout(focusInput, 0);
});

// Four dots: the safety net.
function openMenu() { closeRuns(); $('sheetCap').textContent = ''; $('sheet').hidden = false; $('scrim').hidden = false; $('menu').classList.add('open'); }
function closeMenu() { $('sheet').hidden = true; $('scrim').hidden = true; $('menu').classList.remove('open'); focusInput(); }
$('menu').addEventListener('click', () => page ? closePage() : $('sheet').hidden ? openMenu() : closeMenu());
$('scrim').addEventListener('click', () => { closeMenu(); closeRuns(); });

// The red dot: what is open, and a way to close it.
const X_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
function renderRuns() {
  $('runDot').hidden = !windows.length;
  document.body.classList.toggle('running', !!windows.length);
  if (!windows.length) { if (!$('runs').hidden) closeRuns(); return; }
  $('runs').innerHTML = '<h4>Open</h4>' + windows.map((w) =>
    `<div class="row" data-xid="${escWin(w.xid)}">` +
    (w.icon ? `<img src="${escWin(document.body.classList.contains('night') && w.iconNight ? w.iconNight : w.icon)}" alt="">` : `<span class="letter">${escWin((w.name || '?')[0].toUpperCase())}</span>`) +
    `<span class="name" title="${escWin(w.title || w.name)}">${escWin(w.name)}</span>` +
    `<button class="x" data-close="${escWin(w.xid)}" title="Close" aria-label="Close ${escWin(w.name)}">${X_ICON}</button></div>`).join('') +
    (windows.length > 1 ? '<button class="all" data-closeall>Close all</button>' : '');
}
window.openRuns = openRuns;
function openRuns() { closeMenu(); renderRuns(); $('runs').hidden = false; $('scrim').hidden = false; $('runDot').classList.add('open'); }
function closeRuns() { $('runs').hidden = true; $('scrim').hidden = !$('sheet').hidden ? false : true; $('runDot').classList.remove('open'); }
// The dot opens the same cards as a swipe up; the list is the fallback.
$('runDot').addEventListener('click', () => $('runs').hidden ? native({ type: 'overview' }) : closeRuns());
$('runs').addEventListener('click', (e) => {
  const x = e.target.closest('[data-close]');
  if (x) return native({ type: 'panel', action: 'close', xid: x.dataset.close });
  if (e.target.closest('[data-closeall]')) { closeRuns(); return windows.forEach((w) => native({ type: 'panel', action: 'close', xid: w.xid })); }
  const row = e.target.closest('.row');
  if (row) { closeRuns(); native({ type: 'panel', action: 'focus', xid: row.dataset.xid }); }
});
$('sheet').addEventListener('mouseover', (e) => { const b = e.target.closest('button'); $('sheetCap').textContent = b ? b.title : ''; });
$('sheet').addEventListener('mouseleave', () => { $('sheetCap').textContent = ''; });
$('sheet').addEventListener('click', (e) => {
  const li = e.target.closest('button'); if (!li) return;
  closeMenu();
  const id = li.dataset.id;
  if (id === 'apps') return openPage('apps');
  if (id === 'settings' || id === 'sound' || id === 'display') return openPage('settings', id);
  if (id === 'network') return openPanel('wifi');
  native({ type: 'menu', id });
});

// Day, night or auto: chosen by clicking the time.
let light = { theme: 'day', choice: 'auto', from: 20, until: 7 };
window.setTheme = (t) => {
  light = typeof t === 'string' ? { ...light, theme: t } : { ...light, ...t };
  document.body.classList.toggle('night', light.theme === 'night');
  if (panel === 'light') renderLight();
};
const LIGHT = [
  ['day', 'Day', '<circle cx="12" cy="12" r="6.5"/>', '<path class="hot" d="M12 2.5v3"/>'],
  ['night', 'Night', '<path d="M5.5 14.5a6.5 6.5 0 0 1 13 0"/>', '<path class="hot" d="M3.5 14.5h17"/>'],
  ['auto', 'Auto', '<circle cx="12" cy="12" r="6.5"/>', '<path class="hot" d="M12 5.5v13"/>'],
];
function renderLight() {
  const i = LIGHT.findIndex((x) => x[0] === light.choice);
  $('panel').innerHTML = `<div class="light">
    <div class="lsw" style="--i:${Math.max(0, i)}">${LIGHT.map(([k, label, base, hot]) =>
      `<button class="lopt${k === light.choice ? ' on' : ''}" data-light="${k}"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke-width="1.2">` +
      `<g stroke="currentColor">${base}</g>${hot}</svg><span>${label}</span></button>`).join('')}
      <i class="ltrack"></i><i class="lmark"></i></div></div>`;
}
$('panel').addEventListener('click', (e) => {
  const b = e.target.closest('[data-light]'); if (!b) return;
  light.choice = b.dataset.light;
  native({ type: 'menu', id: 'theme-' + light.choice });
  renderLight();
});

window.setAccount = ({ name, many }) => {
  const b = $('acctBtn'), changed = $('acctName').textContent && $('acctName').textContent !== name;
  b.hidden = !many; $('acctName').textContent = name;
  if (changed) { b.classList.remove('flip'); void b.offsetWidth; b.classList.add('flip'); }
};
$('acctBtn').addEventListener('click', () => native({ type: 'panel', action: 'account-cycle' }));
window.setTablet = () => {};
window.setStatus = ({ battery, wifi, bt: btIcon, signal }) => {
  const bars = wifi === 'on' ? (signal >= 67 ? 3 : signal >= 34 ? 2 : 1) : 0;
  ['l1', 'l2', 'l3'].forEach((c, i) => $('wifi').querySelector('.' + c).classList.toggle('dim', i >= bars));
  // Bluetooth: dim when off or missing, full when on; a connected device keeps it full too.
  $('wifi').classList.toggle('bt', btIcon === 'connected');
  $('wifi').classList.toggle('off', wifi === 'off' && btIcon !== 'connected');
  if (!battery) { $('batteryWrap').hidden = true; return; }
  $('batteryWrap').hidden = false;
  $('batteryFill').setAttribute('width', Math.max(1.5, 18 * battery.level / 100).toFixed(1));
  $('batteryFill').style.fill = battery.level <= 15 && !battery.charging ? 'var(--accent)' : '';
  $('batteryBolt').hidden = !battery.charging;
  $('batteryWrap').title = battery.level + '%' + (battery.charging ? ', charging' : '');
};

function tick() {
  const now = new Date();
  $('clock').textContent = now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  $('date').textContent = now.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).replace(',', '');
}
tick(); setInterval(tick, 5000);
