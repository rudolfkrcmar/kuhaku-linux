// Pages: Apps and the first start; Settings is in settings.js.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages, settings, pictures.
// ---- Pages: Apps and Settings -------------------------------------------
let page = null, appList = null, prefs = null, dragging = null, appQuery = '';
function openPage(name, section) {
  closePanel(); closeMenu();
  page = name;
  document.body.classList.add('paged');
  const el = $('page');
  $('pageClose').hidden = false;
  $('pageName').textContent = { apps: 'Apps', settings: 'Settings', welcome: 'First steps' }[name] || '';
  $('pageName').hidden = false;
  el.hidden = false; el.scrollTop = 0; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  if (!appList || name === 'apps') native({ type: 'panel', action: 'apps' });
  if (name === 'apps') { appQuery = ''; renderApps(); }
  if (name === 'welcome') { native({ type: 'panel', action: 'welcome' }); renderWelcome(); }
  if (name === 'settings') {
    native({ type: 'panel', action: 'prefs' });
    settingsOpened(section);
    renderSettings();
  }
}
$('pageClose').addEventListener('click', () => closePage());
// Open apps: a dot under the pinned ones, the rest follow after a hairline,
// in the order they were opened (the same order the three-finger swipe uses).
let windows = [];
const escWin = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
window.setWindows = (list) => {
  windows = list || [];
  renderRuns();
  const pinned = [...document.querySelectorAll('#quick [data-launch]')];
  pinned.forEach((b) => {
    const w = windows.find((x) => x.app === b.dataset.launch);
    b.classList.toggle('on', !!w); b.dataset.xid = w ? w.xid : '';
  });
  document.querySelectorAll('#quick .running, #quick .sep').forEach((n) => n.remove());
  const pins = pinned.map((b) => b.dataset.launch), seen = new Set();
  const others = windows.filter((w) => !pins.includes(w.app) && !(w.app && seen.has(w.app)) && (seen.add(w.app), true));
  if (!others.length) return;
  const q = $('quick');
  q.insertAdjacentHTML('beforeend', '<span class="sep"></span>' + others.map((w) =>
    `<button class="running on" data-xid="${escWin(w.xid)}" title="${escWin(w.name)}" aria-label="${escWin(w.name)}">` +
    (w.icon ? `<img src="${escWin(document.body.classList.contains('night') && w.iconNight ? w.iconNight : w.icon)}" alt="">` : `<span class="letter">${escWin((w.name || '?')[0].toUpperCase())}</span>`) +
    '</button>').join(''));
};
$('quick').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.xid) return native({ type: 'panel', action: 'focus', xid: b.dataset.xid });
  if (b.dataset.launch) native({ type: 'panel', action: 'launch', id: b.dataset.launch });
});
function closePage() {
  page = null;
  document.body.classList.remove('paged');
  $('page').hidden = true;
  $('pageClose').hidden = true;
  $('pageName').hidden = true;
  focusInput();
}

function renderApps() {
  const q = appQuery.trim().toLowerCase();
  const list = (appList || []).filter((a) => !a.system && (!q || (a.name + ' ' + a.about).toLowerCase().includes(q)));
  const first = q && list.length ? list[0].id : null;
  let h = `<div class="inner"><label class="find"><span class="line"></span>
           <input id="appFind" placeholder="Find an app" spellcheck="false" autocomplete="off" value="${esc(appQuery)}"></label>`;
  if (!appList) h += '<div class="p-wait"><span class="dot"></span>Looking for apps…</div>';
  else if (!list.length) h += '<p class="empty">Nothing by that name. Press Enter to ask Claude to find or install it.</p>';
  else h += '<div class="applist">' + list.map((a) =>
    `<span class="app${a.id === first ? ' first' : ''}" data-app="${esc(a.id)}">${esc(a.name)}${q && a.about ? `<small>${esc(a.about)}</small>` : ''}</span>`).join('') + '</div>';
  $('page').innerHTML = h + '</div>';
  const f = $('appFind');
  f.focus(); f.setSelectionRange(f.value.length, f.value.length);
  f.addEventListener('input', () => { appQuery = f.value; renderApps(); });
  f.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const hit = $('page').querySelector('.app.first');
    if (hit) return launch(hit.dataset.app);
    if (appQuery.trim()) { const q = 'Open or install the app: ' + appQuery.trim(); closePage(); setAsk(''); show('working', q); native({ type: 'ask', text: q }); }
  });
}
// ---- The first start: a few choices, once (for those who added Kūhaku without its installer) ----
let welcome = null, wTimer = 0;
window.setWelcome = (d) => { welcome = d; if (page === 'welcome') renderWelcome(); };
const WELCOME_AI = [
  ['claude', 'Claude', 'Claude Code, by Anthropic.'],
  ['codex', 'OpenAI', 'Codex, by OpenAI.'],
  ['gemini', 'Gemini', 'Gemini CLI, by Google.'],
  ['grok', 'Grok', 'Grok Build, by xAI.'],
  ['copilot', 'Copilot', 'Copilot CLI, by GitHub. One subscription, several models.'],
];
function renderWelcome() {
  clearTimeout(wTimer);
  if (page !== 'welcome') return;
  wTimer = setTimeout(() => native({ type: 'panel', action: 'welcome' }), 4000);  // sign-ins finish elsewhere
  const w = welcome;
  let h = '<div class="inner narrow"><h1>Welcome to Kūhaku</h1>' +
    '<p class="p-sub" style="font-size:15px">A few choices, once. Any of them can be changed later in Settings, or just by asking.</p>';
  if (!w) { $('page').innerHTML = h + '<div class="p-wait" style="margin-top:24px"><span class="dot"></span>Looking around…</div></div>'; return; }
  const act = (k, st) => ({
    ready: '<span class="p-sub" style="margin:0">Signed in</span>',
    working: '<span class="p-sub" style="margin:0">Signing in…</span>',
    waiting: '<span class="p-sub" style="margin:0">Arriving…</span>',
    signin: `<button class="p-link" data-wel="ai" data-v="${k}">Sign in</button>`,
    get: `<button class="p-link" data-wel="ai" data-v="${k}">Get and sign in</button>`,
  }[st] || '');
  h += `<div class="p-label"><span>Your AI</span></div><div class="p-rows">` +
    WELCOME_AI.map(([k, name, about]) => `<div class="srow"><span class="k">${name}</span><span class="grow">${about}</span>${act(k, (w.ai || {})[k])}</div>`).join('');
  const L = w.local || {};
  h += `<div class="srow"><span class="k">This computer</span><span class="grow">A model that runs here, without the cloud, through Ollama.${L.models && L.models.length ? '' : '<div class="p-sub">Slower, and needs a strong computer.</div>'}</span>
    ${L.running && L.models.length ? '<span class="p-sub" style="margin:0">Ready</span>' : '<a class="p-link" href="https://ollama.com/download">Get Ollama</a>'}</div>
    <div class="srow"><span class="k"></span><span class="grow p-sub" style="margin:0">Sign in to the ones you use; one is enough. Each is fetched from its maker and signs in with your own account.</span></div>`;
  const others = (w.ready || []).filter((a) => a.kind !== 'claude');
  if (others.length) h += `<div class="srow"><span class="k">Home uses</span><div class="grow">${OPT('account_home', [['', 'Claude']].concat(others.map((a) => [a.id, esc(a.label)])), w.home || '')}
    <div class="p-sub">The AI the home screen asks. Mail and Tsukuru can use another, in Settings, under AI.</div></div></div>`;
  h += '</div>';
  const mode = w.permission_mode === 'bypassPermissions' ? 'bypassPermissions' : 'acceptEdits';  // as Settings reads it
  h += `<div class="p-label"><span>How freely it works</span></div><div class="p-rows">
    <div class="srow"><span class="k">May do</span><div class="grow">${OPT('permission_mode', [['bypassPermissions', 'Anything, without asking'], ['acceptEdits', 'Careful']], mode)}
      <div class="p-sub">${mode === 'acceptEdits' ? 'The AI reads and edits files, but runs no commands, so it cannot change settings or install anything.'
        : 'Recommended. The AI edits, runs and installs on its own, and asks before deleting or anything that cannot be undone.'}</div></div></div></div>`;
  const c = w.codecs || {};
  h += `<div class="p-label"><span>Video formats</span></div><div class="p-rows">
    <div class="srow"><span class="k">Formats</span><div class="grow">${c.added ? 'All, including H.264 and AAC.' : 'Free formats play now: WebM, VP9, Opus, FLAC, MP3.'}
      ${c.added ? '' : '<div class="p-sub">H.264, AAC and some others are covered by patents in some countries, so Kūhaku does not include them. Whether you may use them depends on where you live; the choice is yours. They come from Debian.</div>'}</div>
      ${c.added ? '' : `<button class="p-link" data-wel="codecs">${c.running ? 'Adding…' : 'Add them'}</button>`}</div></div>`;
  h += `<div class="p-label"><span>Everything else</span></div><div class="p-rows">
    <div class="srow"><span class="k">Programs</span><span class="grow">Just ask on the home screen, like “install LibreOffice” or “I need something to edit photos”.</span></div></div>`;
  h += `<div class="p-rows" style="margin-top:34px"><div class="srow"><span class="k"></span><span class="grow"></span><button class="p-link" data-wel="done" style="font-size:15px;color:var(--fg)">Done</button></div></div></div>`;
  const top = $('page').scrollTop;
  $('page').innerHTML = h; $('page').scrollTop = top;
}
$('page').addEventListener('click', (e) => {
  if (page !== 'welcome') return;
  if (e.target.closest('[data-pref]')) return setTimeout(() => native({ type: 'panel', action: 'welcome' }), 600);
  const b = e.target.closest('[data-wel]'); if (!b) return;
  e.stopPropagation();
  const act = b.dataset.wel, pref = (key, value) => native({ type: 'panel', action: 'pref', key, value, quiet: true });
  if (act === 'ai') { pref('welcome-ai', b.dataset.v); b.textContent = 'Signing in…'; }
  if (act === 'codecs') { pref('add-codecs', 1); b.textContent = 'Adding…'; }
  if (act === 'done') { pref('welcome-done', 1); return closePage(); }
  setTimeout(() => native({ type: 'panel', action: 'welcome' }), 600);
}, true);
function launch(id) { native({ type: 'panel', action: 'launch', id }); closePage(); }

// A choice: each word followed by its square, exactly one filled with ink. Five or more options are a
// list instead: the value and a small chevron, opening the same choice set upright.
const DOWN = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 9l7 7 7-7"/></svg>';
const CHOICE = (items, asList) => {
  const opts = (up) => `<div class="opts choice${up ? ' up' : ' grow'}">` +
    items.map((x) => `<span class="opt${x.sel ? ' sel' : ''}" ${x.attrs}>${x.label}</span>`).join('') + '</div>';
  if (items.length <= 4 && !asList) return opts(false);
  const cur = items.find((x) => x.sel);
  return `<div class="list grow"><button class="lsb" data-list><span class="lv">${cur ? cur.label : 'Choose'}</span>${DOWN}</button>${opts(true)}</div>`;
};
const OPT = (key, options, current) => CHOICE(options.map(([v, label]) =>
  ({ label, sel: String(v) === String(current), attrs: `data-pref="${key}" data-v="${esc(v)}"` })));
// A choice was made: one square filled; a list shows the new value and closes.
function chose(o) {
  o.parentElement.querySelectorAll('.opt').forEach((x) => x.classList.toggle('sel', x === o));
  const l = o.closest('.list');
  if (l) { l.querySelector('.lv').innerHTML = o.innerHTML; l.classList.remove('open'); }
}
$('page').addEventListener('click', (e) => {
  const list = e.target.closest('[data-list]');
  if (list) { const l = list.parentElement, was = l.classList.contains('open');
    $('page').querySelectorAll('.list.open').forEach((x) => x.classList.remove('open'));
    l.classList.toggle('open', !was); return; }
  const disp = e.target.closest('[data-disp]');
  if (disp) {
    if (disp.parentElement.classList.contains('choice')) chose(disp);
    return native({ type: 'panel', action: 'pref', key: 'display', value: JSON.parse(disp.dataset.disp) });
  }
  const app = e.target.closest('[data-app]'); if (app) return launch(app.dataset.app);
  const o = e.target.closest('[data-pref]');
  if (o) {
    // A set whose Claude is not signed in yet is not chosen by a click; it opens its sign-in.
    if (!(o.dataset.pref === 'home_set' && !(((prefs.ai_sets || []).find((x) => x.name === o.dataset.v) || {}).slots || {}).claude?.signedIn))
      chose(o);
    return native({ type: 'panel', action: 'pref', key: o.dataset.pref, value: o.dataset.v });
  }
  const ub = e.target.closest('[data-pref-btn]');
  if (ub) { ub.textContent = ub.dataset.prefBtn === 'add-codecs' ? 'Adding…' : 'Updating…'; return native({ type: 'panel', action: 'pref', key: ub.dataset.prefBtn, value: 1 }); }
  const t = e.target.closest('[data-act2]'); if (!t) return;
  if (t.dataset.act2 === 'tool') launch(t.dataset.v);
  if (t.dataset.act2 === 'welcome') openPage('welcome');
  if (t.dataset.act2 === 'time-set') native({ type: 'panel', action: 'pref', key: 'time_set', value: $('timeSet').value });
  if (t.dataset.act2 === 'ai-signin') native({ type: 'panel', action: 'ai-signin', set: t.dataset.set, kind: t.dataset.v });
  if (t.dataset.act2 === 'ai-set-remove') native({ type: 'panel', action: 'ai-set-remove', name: t.dataset.v });
  if (t.dataset.act2 === 'unlock') {
    native({ type: 'panel', action: 'unlock', what: t.dataset.what, finger: t.dataset.v || '' }); }
});
$('page').addEventListener('change', (e) => {
  if (e.target.id === 'timeZone' && e.target.value.trim()) native({ type: 'panel', action: 'pref', key: 'time_zone', value: e.target.value.trim() });
});
let slideTimer = 0;
function slideTo(el, x) {
  const r = el.getBoundingClientRect(), key = el.dataset.slide;
  const v = Math.round(Math.max(key === 'brightness' ? 5 : 0, Math.min(100, (x - r.left) / r.width * 100)));
  el.querySelector('i').style.width = v + '%';
  $('val-' + key).textContent = v + '%';
  if (prefs) prefs[key] = v;
  const now = Date.now();
  if (now - slideTimer > 90) { slideTimer = now; native({ type: 'panel', action: 'pref', key, value: v, quiet: true }); }
  return v;
}
$('page').addEventListener('pointerdown', (e) => {
  const el = e.target.closest('.slider'); if (!el) return;
  dragging = el; el.classList.add('drag'); el.setPointerCapture(e.pointerId); slideTo(el, e.clientX);
});
$('page').addEventListener('pointermove', (e) => { if (dragging) slideTo(dragging, e.clientX); });
$('page').addEventListener('pointerup', (e) => {
  if (!dragging) return;
  const el = dragging, v = slideTo(el, e.clientX);
  dragging = null; el.classList.remove('drag');
  native({ type: 'panel', action: 'pref', key: el.dataset.slide, value: v, quiet: true });
});

// While recording: "Stop recording" (a click stops and saves it), then the microphone, which a click switches.
window.setRecording = (s) => {
  const on = !!(s && (s === true || s.on)), mic = s && s.mic;
  $('recBtn').hidden = !on;
  $('recMicBtn').hidden = !on;
  $('recMicBtn').classList.toggle('off', mic !== true);
  $('recMicBtn').disabled = mic == null;
  $('recMicBtn').title = mic == null ? 'No microphone' : mic ? 'Microphone on: click to mute it in this recording' : 'Microphone off: click to turn it on';
};
$('recBtn').addEventListener('click', () => native({ type: 'stopRecording' }));
$('recMicBtn').addEventListener('click', () => native({ type: 'recMic' }));

const params = new URLSearchParams(location.search);
if (params.get('theme')) setTheme(params.get('theme'));
focusInput();
place();
