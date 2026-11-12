// Pages: Apps and Settings, then the start of everything.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages.
// ---- Pages: Apps and Settings -------------------------------------------
let page = null, appList = null, prefs = null, dragging = null, appQuery = '';
function openPage(name, section) {
  closePanel(); closeMenu();
  page = name;
  document.body.classList.add('paged');
  $('menu').classList.add('open');
  const el = $('page');
  $('pageClose').hidden = false; $('pageHome').hidden = false;
  el.hidden = false; el.scrollTop = 0; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  if (!appList || name === 'apps') native({ type: 'panel', action: 'apps' });
  if (name === 'apps') { appQuery = ''; renderApps(); }
  if (name === 'settings') {
    native({ type: 'panel', action: 'prefs' });
    renderSettings();
    if (section) requestAnimationFrame(() => { const t = document.getElementById('sec-' + section); if (t) el.scrollTop = t.offsetTop - 120; });
  }
}
$('pageClose').addEventListener('click', () => closePage());
$('pageHome').addEventListener('click', () => closePage());
// Open apps: a dot under the pinned ones, the rest follow after a hairline,
// in the order they were opened (the same order the three-finger swipe uses).
let windows = [];
const escWin = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
window.setWindows = (list) => {
  windows = list || [];
  renderRuns();
  const pinned = [...document.querySelectorAll('#quick [data-launch]')];
  [...pinned, $('filesMark')].forEach((b) => {
    const w = windows.find((x) => x.app === b.dataset.launch);
    b.classList.toggle('on', !!w); b.dataset.xid = w ? w.xid : '';
  });
  document.querySelectorAll('#quick .running, #quick .sep').forEach((n) => n.remove());
  const pins = [...pinned, $('filesMark')].map((b) => b.dataset.launch), seen = new Set();
  const others = windows.filter((w) => !pins.includes(w.app) && !(w.app && seen.has(w.app)) && (seen.add(w.app), true));
  if (!others.length) return;
  const q = $('quick');
  q.insertAdjacentHTML('beforeend', '<span class="sep"></span>' + others.map((w) =>
    `<button class="running on" data-xid="${escWin(w.xid)}" title="${escWin(w.name)}" aria-label="${escWin(w.name)}">` +
    (w.icon ? `<img src="${escWin(document.body.classList.contains('night') && w.iconNight ? w.iconNight : w.icon)}" alt="">` : `<span class="letter">${escWin((w.name || '?')[0].toUpperCase())}</span>`) +
    '</button>').join(''));
};
$('filesMark').addEventListener('click', () => {
  const b = $('filesMark');
  if (b.dataset.xid) native({ type: 'panel', action: 'focus', xid: b.dataset.xid });
  else native({ type: 'panel', action: 'launch', id: 'miru.desktop' });
});
$('quick').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.xid) return native({ type: 'panel', action: 'focus', xid: b.dataset.xid });
  if (b.dataset.launch) native({ type: 'panel', action: 'launch', id: b.dataset.launch });
});
function closePage() {
  page = null; onlySection = null;
  document.body.classList.remove('paged');
  $('menu').classList.remove('open');
  $('page').hidden = true;
  $('pageClose').hidden = true; $('pageHome').hidden = true;
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
function launch(id) { native({ type: 'panel', action: 'launch', id }); closePage(); }

const OPT = (key, options, current) => '<div class="opts grow">' + options.map(([v, label]) =>
  `<span class="opt${String(v) === String(current) ? ' sel' : ''}" data-pref="${key}" data-v="${esc(v)}">${label}</span>`).join('') + '</div>';
const SLIDE = (key, label, value) => value == null ? '' : `<div class="srow"><span class="k">${label}</span>
    <div class="slider" data-slide="${key}"><i style="width:${value}%"></i><b style="left:${value}%"></b></div>
    <span class="val" id="val-${key}">${value}%</span></div>`;
let acctAdding = false, onlySection = null, unlockForget = null;
function renderSettings() {
  const p = prefs;
  let h = '<div class="inner narrow"><h1>Settings</h1>';
  if (!p) { $('page').innerHTML = h + '<div class="p-wait" style="margin-top:24px"><span class="dot"></span>Reading settings…</div></div>'; return; }
  h += `<div class="p-label" id="sec-appearance"><span>Appearance</span></div><div class="p-rows">
    <div class="srow"><span class="k">Theme</span>${OPT('theme', [['day', 'Day'], ['night', 'Night'], ['auto', 'Follow the clock']], p.theme)}</div></div>`;
  h += `<div class="p-label" id="sec-sound"><span>Sound</span></div><div class="p-rows">
    ${SLIDE('volume', 'Volume', p.volume)}
    ${p.outputs.length > 1 ? `<div class="srow"><span class="k">Play through</span>${OPT('output', p.outputs.map((o) => [o.id, esc(o.name)]), (p.outputs.find((o) => o.active) || {}).id)}</div>` : ''}
    ${SLIDE('mic', 'Microphone', p.mic)}</div>`;
  h += `<div class="p-label" id="sec-display"><span>Display</span></div><div class="p-rows">
    ${SLIDE('brightness', 'Brightness', p.brightness)}
    ${screensHtml(p.displays)}</div>`;
  if (p.trackpad) h += `<div class="p-label" id="sec-trackpad"><span>Trackpad</span></div><div class="p-rows">
    <div class="srow"><span class="k">Tap to click</span>${OPT('tap', [[1, 'On'], [0, 'Off']], p.trackpad.tap)}</div>
    <div class="srow"><span class="k">Scrolling</span>${OPT('natural', [[1, 'Natural, like a Mac'], [0, 'Classic']], p.trackpad.natural)}</div>
    <div class="srow"><span class="k">Speed</span>${OPT('speed', [[-0.3, 'Slow'], [0, 'Medium'], [0.3, 'Fast'], [0.6, 'Fastest']], p.trackpad.speed)}</div></div>`;
  const ul = p.unlock;
  if (ul && (ul.reader || ul.face.camera)) {
    const NICE = (f) => f.replace('-finger', '').replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
    const fs = ul.fingers || [];
    h += `<div class="p-label" id="sec-unlock"><span>Unlock</span></div><div class="p-rows">`;
    if (!fs.length || (ul.face.camera && !ul.face.enrolled)) h += `<div class="srow"><span class="k">Set up</span><div class="grow"><div class="opts">
        <span class="opt" data-act2="unlock" data-what="guided">Guided setup</span></div>
        <div class="p-sub" style="margin-top:6px">Three fingers, then your face, one step at a time. About three minutes.</div></div></div>`;
    if (ul.reader) h += `<div class="srow"><span class="k">Fingers</span><div class="grow"><div class="opts">
        ${fs.map((f) => unlockForget === f ? `<span class="opt aidel" data-act2="unlock" data-what="forget-finger" data-v="${f}">Forget ${NICE(f).toLowerCase()}?</span>`
          : `<span class="opt sel" title="Click to forget" data-act2="unlock-ask" data-v="${f}">${NICE(f)}</span>`).join('')}
        ${fs.length < 10 ? `<span class="opt" data-act2="unlock" data-what="add-finger">${fs.length ? 'Add a finger' : 'Teach a finger'}</span>` : ''}
        ${fs.length > 1 ? (unlockForget === 'all' ? '<span class="opt aidel" data-act2="unlock" data-what="forget-fingers">Forget all, sure?</span>'
          : '<span class="opt" data-act2="unlock-ask">Forget all</span>') : ''}</div>
        <div class="p-sub" style="margin-top:6px">${fs.length >= 3 ? 'Opens the lock screen with any of these fingers, or your password.'
          : 'The reader is the power button. Three or more fingers make it much more reliable.'}</div></div></div>`;
    if (ul.face.camera) h += `<div class="srow"><span class="k">Face</span><div class="grow"><div class="opts">
        ${ul.face.enrolled ? '<span class="opt sel">Set up</span><span class="opt" data-act2="unlock" data-what="face-setup">Learn again</span><span class="opt" data-act2="unlock" data-what="face-forget">Forget face</span>'
          : '<span class="opt" data-act2="unlock" data-what="face-setup">Set up face</span>'}</div>
        <div class="p-sub" style="margin-top:6px">With the infrared camera, only while the lock screen is up. A convenience: your password and fingers are stronger.</div></div></div>`;
    h += '</div>';
  }
  const tm = p.time;
  if (tm) h += `<div class="p-label" id="sec-time"><span>Date and time</span></div><div class="p-rows">
    <div class="srow"><span class="k">Set the time</span>${OPT('time_auto', [[1, 'Automatically, from the internet'], [0, 'By hand']], tm.auto ? 1 : 0)}</div>
    <div class="srow"><span class="k">Now</span><span class="grow">${tm.auto ? (tm.synced ? 'In step with the internet' : 'Not yet checked with the internet') : ''}</span>
      ${tm.auto ? '' : `<input type="datetime-local" id="timeSet" value="${tm.now}" style="font:inherit;background:none;border:0;color:var(--fg);color-scheme:dark light"><button class="p-link" data-act2="time-set">Set</button>`}</div>
    <div class="srow"><span class="k">Time zone</span><input id="timeZone" list="zones" value="${esc(tm.zone)}" spellcheck="false" style="font:inherit;background:none;border:0;color:var(--fg);flex:1">
      <datalist id="zones">${['Europe/Prague','Europe/London','Europe/Berlin','Europe/Paris','America/New_York','America/Los_Angeles','Asia/Tokyo','UTC'].map((z) => `<option value="${z}">`).join('')}</datalist></div></div>`;
  h += `<div class="p-label" id="sec-power"><span>Power</span></div><div class="p-rows">
    ${p.profile ? `<div class="srow"><span class="k">Power mode</span>${OPT('profile', [['power-saver', 'Saver'], ['balanced', 'Balanced'], ['performance', 'Performance']], p.profile)}</div>` : ''}
    ${p.power ? `<div class="srow"><span class="k">Screen off</span>${OPT('screen_off', [[2, '2 min'], [5, '5 min'], [10, '10 min'], [0, 'Never']], p.power.screen_off)}</div>
    <div class="srow"><span class="k">Sleep</span>${OPT('sleep_after', [[15, '15 min'], [30, '30 min'], [60, '1 hour'], [0, 'Never']], p.power.sleep_after)}</div>
    <div class="srow"><span class="k">Closing the lid</span>${OPT('lid', [[1, 'Sleep'], [3, 'Lock'], [0, 'Screen off']], p.power.lid)}</div>` : ''}
    ${p.about.battery != null ? `<div class="srow"><span class="k">Battery</span><span class="grow"><button class="p-link" data-act2="battery">${p.about.battery}% · details</button></span></div>` : ''}
    <div class="srow"><span class="k">Network</span><span class="grow"><button class="p-link" data-act2="wifi">Wi‑Fi networks</button></span></div></div>`;
  // AI: sets (Personal, Work, …) of a Claude, an OpenAI and a Gemini account; then what uses which.
  const sets = p.ai_sets || [], signing = p.ai_signing || [], accts = p.accounts || [],
    ready = accts.filter((a) => a.signedIn), home = accts.find((a) => a.active) || {},
    KIND = [['claude', 'Claude'], ['codex', 'OpenAI'], ['gemini', 'Gemini']];
  const slot = (set, k) => { const a = set.slots[k];
    if (a && a.signedIn) return '<span class="aiok">Signed in</span>';
    if (a && signing.includes(a.id)) return '<span class="aiwait">Signing in…</span>';
    return `<span class="opt" data-act2="ai-signin" data-set="${esc(set.name)}" data-v="${k}">Sign in</span>`; };
  h += `<div class="p-label" id="sec-claude"><span>AI</span></div><div class="p-rows">
    <div class="aigrid"><span></span>${KIND.map(([, l]) => `<span class="aihead">${l}</span>`).join('')}<span></span>
      ${sets.map((set) => `<span class="k">${esc(set.name)}</span>${KIND.map(([k]) => `<span>${slot(set, k)}</span>`).join('')}
        <span>${set.removable ? `<span class="opt aidel" data-act2="ai-set-remove" data-v="${esc(set.name)}">Remove</span>` : ''}</span>`).join('')}
    </div>
    ${(() => { const L = p.ai_local || {};
      // A model on this computer (Ollama), for people who want to try Kūhaku without a cloud AI.
      const body = L.running && L.models.length ? OPT('local_model', L.models.map((m) => [m, esc(m)]), L.model)
        : `<span class="grow">${L.running ? 'Ollama runs, without a model yet · <a class="p-link" href="https://ollama.com/library">Pick a model</a>'
          : L.installed ? 'Ollama is here but not running'
          : 'A model on this computer, through Ollama · <a class="p-link" href="https://ollama.com/download">Get Ollama</a>'}</span>`;
      return `<div class="srow"><span class="k">Local</span>${body}</div>`; })()}
    <div class="srow"><span class="k"></span><div class="grow">${acctAdding
      ? '<label class="acctadd"><span class="line"></span><input id="acctNew" placeholder="Name of the set, like Client" spellcheck="false"></label>'
      : '<div class="opts"><span class="opt" data-act2="ai-set-add">Add a set</span></div>'}</div></div>
    <div class="srow"><span class="k">Home uses</span>${OPT('home_set', sets.map((x) => [x.name, esc(x.name) + ' Claude']), home.set)}</div>
    ${[['mail', 'Mail uses'], ['dev', 'Dev tools use']].map(([k, label]) =>
      `<div class="srow"><span class="k">${label}</span>${OPT('account_' + k, [['', 'Same as home']].concat(ready.map((a) => [a.id, esc(a.label)])), (p.account_for || {})[k] || '')}</div>`).join('')}
    <div class="srow"><span class="k">Model</span>${OPT('model', [['default', 'Default'], ['opus', 'Opus'], ['sonnet', 'Sonnet'], ['haiku', 'Haiku']], p.model)}</div>
    <div class="srow"><span class="k">May do</span>${OPT('permission_mode', [['bypassPermissions', 'Anything, without asking'], ['acceptEdits', 'Careful: edit files, no commands']], p.permission_mode === 'default' ? 'acceptEdits' : p.permission_mode)}</div>
    <div class="srow"><span class="k">Microphone</span>${OPT('mic_button', [['talk', 'Talk with Claude'], ['dictate', 'Dictate, offline']], p.mic_button)}</div>
    <div class="srow"><span class="k">Dictation</span>${OPT('dictation_model', [['auto', 'Best for this computer'], ['base', 'Fast'], ['small', 'Accurate, slower']], p.dictation_model)}</div></div>`;
  const u = p.updates || {};
  h += `<div class="p-label" id="sec-updates"><span>Updates</span></div><div class="p-rows">
    <div class="srow"><span class="k">Updates</span><span class="grow">${u.auto ? 'Security fixes install by themselves' : 'Not automatic'}${u.checked ? ' · last ' + when(u.checked) : ''}</span>
      <button class="p-link" data-pref-btn="update-now">${u.running ? 'Updating…' : 'Update now'}</button></div></div>`;
  const a = p.about;
  h += `<div class="p-label" id="sec-about"><span>This computer</span></div><div class="p-rows">` +
    [['Computer', a.computer], ['System', a.system + ' · Kūhaku'], ['Processor', a.processor], ['Memory', a.memory && a.memory + ' GB'],
     ['Storage', a.disk && `${a.disk[0]} GB free of ${a.disk[1]} GB`], ['Kernel', a.kernel]]
      .filter(([, v]) => v).map(([k, v]) => `<div class="srow"><span class="k">${k}</span><span class="grow">${esc(v)}</span></div>`).join('') + '</div>';
  const tools = (appList || []).filter((x) => x.system);
  if (tools.length) h += `<div class="p-label"><span>System tools</span></div><div class="tools">` +
    tools.map((t) => `<span data-act2="tool" data-v="${esc(t.id)}">${esc(t.name)}</span>`).join('') + '</div>';
  $('page').innerHTML = h + '</div>';
  // Opened for one thing (power, from the battery): only that part shows, and the rest is one click away.
  if (onlySection) {
    const keep = document.getElementById('sec-' + onlySection);
    if (keep) {
      const inner = $('page').querySelector('.inner');
      [...inner.children].forEach((n) => { if (n !== keep && n !== keep.nextElementSibling && n.tagName !== 'H1') n.remove(); });
      inner.querySelector('h1').textContent = keep.textContent.trim().replace(/^./, (c) => c.toUpperCase());
      inner.insertAdjacentHTML('beforeend', '<div class="p-foot"><button class="p-link" data-act2="all-settings">All settings</button></div>');
    }
  }
}

function screensHtml(d) {
  if (!d) return '';
  const outs = d.outputs || [];
  const nice = (o) => o.builtin ? 'Built-in display' : o.name.replace(/-\d+$/, '').replace('HDMI', 'HDMI display').replace(/^DP$/, 'DisplayPort display');
  let h = outs.map((o) => `<div class="srow"><span class="k">${outs.length > 1 ? esc(nice(o)) : 'Resolution'}</span>` +
    '<div class="opts grow">' + o.modes.map((m) =>
      `<span class="opt${m === o.current ? ' sel' : ''}" data-disp='${JSON.stringify({ output: o.name, mode: m })}'>${m.replace('x', ' × ')}</span>`).join('') + '</div></div>').join('');
  if (outs.length > 1) h += `<div class="srow"><span class="k">Arrangement</span><div class="opts grow">` +
    [['left', 'Left of laptop'], ['right', 'Right of laptop'], ['mirror', 'Mirror'], ['outer', 'Only external'], ['inner', 'Only laptop']]
      .map(([v, l]) => `<span class="opt" data-disp='${JSON.stringify({ arrange: v })}'>${l}</span>`).join('') + '</div></div>';
  h += `<div class="srow"><span class="k">Text size</span><div class="opts grow">` +
    [[84, 'Smaller'], [96, 'Default'], [120, 'Larger'], [144, 'Largest']]
      .map(([v, l]) => `<span class="opt${v === d.dpi ? ' sel' : ''}" data-disp='${JSON.stringify({ dpi: v })}'>${l}</span>`).join('') + '</div></div>';
  return h;
}

$('page').addEventListener('click', (e) => {
  const disp = e.target.closest('[data-disp]');
  if (disp) {
    disp.parentElement.querySelectorAll('.opt').forEach((x) => x.classList.toggle('sel', x === disp));
    return native({ type: 'panel', action: 'pref', key: 'display', value: JSON.parse(disp.dataset.disp) });
  }
  const app = e.target.closest('[data-app]'); if (app) return launch(app.dataset.app);
  const o = e.target.closest('[data-pref]');
  if (o) {
    // A set whose Claude is not signed in yet is not chosen by a click; it opens its sign-in.
    if (!(o.dataset.pref === 'home_set' && !(((prefs.ai_sets || []).find((x) => x.name === o.dataset.v) || {}).slots || {}).claude?.signedIn))
      o.parentElement.querySelectorAll('.opt').forEach((x) => x.classList.toggle('sel', x === o));
    return native({ type: 'panel', action: 'pref', key: o.dataset.pref, value: o.dataset.v });
  }
  const ub = e.target.closest('[data-pref-btn]');
  if (ub) { ub.textContent = 'Updating…'; return native({ type: 'panel', action: 'pref', key: ub.dataset.prefBtn, value: 1 }); }
  const t = e.target.closest('[data-act2]'); if (!t) return;
  if (t.dataset.act2 === 'tool') launch(t.dataset.v);
  if (t.dataset.act2 === 'time-set') native({ type: 'panel', action: 'pref', key: 'time_set', value: $('timeSet').value });
  if (t.dataset.act2 === 'all-settings') { onlySection = null; renderSettings(); $('page').scrollTop = 0; }
  if (t.dataset.act2 === 'ai-signin') native({ type: 'panel', action: 'ai-signin', set: t.dataset.set, kind: t.dataset.v });
  if (t.dataset.act2 === 'ai-set-remove') native({ type: 'panel', action: 'ai-set-remove', name: t.dataset.v });
  if (t.dataset.act2 === 'ai-set-add') { acctAdding = true; renderSettings(); const i = $('acctNew'); i.focus();
    i.addEventListener('keydown', (e) => { if (e.key === 'Escape') { acctAdding = false; renderSettings(); }
      if (e.key === 'Enter' && i.value.trim()) { acctAdding = false; native({ type: 'panel', action: 'ai-set-add', name: i.value.trim() }); } }); }
  if (t.dataset.act2 === 'battery') { closePage(); openPanel('battery'); }
  if (t.dataset.act2 === 'unlock-ask') { unlockForget = t.dataset.v || 'all'; renderSettings(); setTimeout(() => { unlockForget = null; if (page === 'settings') renderSettings(); }, 5000); }
  if (t.dataset.act2 === 'unlock') { unlockForget = null;
    native({ type: 'panel', action: 'unlock', what: t.dataset.what, finger: t.dataset.v || '' }); }
  if (t.dataset.act2 === 'wifi') { closePage(); openPanel('wifi'); }
});
$('page').addEventListener('change', (e) => {
  if (e.target.id === 'timeZone' && e.target.value.trim()) native({ type: 'panel', action: 'pref', key: 'time_zone', value: e.target.value.trim() });
});
let slideTimer = 0;
function slideTo(el, x) {
  const r = el.getBoundingClientRect(), key = el.dataset.slide;
  const v = Math.round(Math.max(key === 'brightness' ? 5 : 0, Math.min(100, (x - r.left) / r.width * 100)));
  el.querySelector('i').style.width = v + '%'; el.querySelector('b').style.left = v + '%';
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

window.setRecording = (on) => { $('recBtn').hidden = !on; };
$('recBtn').addEventListener('click', () => native({ type: 'stopRecording' }));

const params = new URLSearchParams(location.search);
if (params.get('theme')) setTheme(params.get('theme'));
focusInput();
place();
