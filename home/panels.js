// Panels from the top bar: calendar, Claude usage, Wi-Fi, Bluetooth, battery, notifications.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages.
// ---- Panels: calendar, Wi-Fi, battery -----------------------------------
let panel = null, panelTimer;
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const triggers = { calendar: 'calBtn', light: 'timeBtn', wifi: 'wifiBtn', battery: 'batteryWrap' };

function openPanel(name) {
  if (panel === name) return closePanel();
  closeMenu();
  panel = name;
  document.body.classList.add('dim');
  $('pscrim').hidden = false;
  for (const [k, id] of Object.entries(triggers)) $(id).classList.toggle('open', k === name);
  const el = $('panel');
  el.hidden = false; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  clearInterval(panelTimer);
  if (name === 'calendar') { native({ type: 'panel', action: 'calendar' }); cal.y = today().getFullYear(); cal.m = today().getMonth(); cal.mode = 'month'; cal.sel = today(); renderCal(); askEvents(); }
  if (name === 'light') renderLight();
  if (name === 'notices') { notices = null; renderNotices(); native({ type: 'panel', action: 'notices' }); }
  if (name === 'wifi') { bt.note = ''; native({ type: 'panel', action: 'bt' }); wifi.open = null; renderWifi(); native({ type: 'panel', action: 'wifi' }); native({ type: 'panel', action: 'wifi', rescan: true }); }
  if (name === 'battery') {
    renderBattery(); native({ type: 'panel', action: 'battery' });
    panelTimer = setInterval(() => native({ type: 'panel', action: 'battery' }), 10000);
  }
}
function closePanel() {
  panel = null; clearInterval(panelTimer);
  document.body.classList.remove('dim');
  $('panel').hidden = true; $('pscrim').hidden = true;
  for (const id of Object.values(triggers)) $(id).classList.remove('open');
  focusInput();
}
for (const [k, id] of Object.entries(triggers)) $(id).addEventListener('click', () => openPanel(k));
$('pscrim').addEventListener('click', closePanel);

window.panelData = ({ action, data }) => {
  if (action === 'wifi' || action === 'wifi-radio') { wifi.data = data; wifi.toggling = false; }
  if (action === 'wifi-connect') {
    wifi.busy = null;
    if (data.ok) { wifi.open = null; wifi.note = {}; native({ type: 'panel', action: 'wifi' }); }
    else if (data.needPassword) { wifi.note[data.ssid] = wifi.open === data.ssid ? 'Wrong password, try again' : ''; wifi.open = data.ssid; }
    else wifi.note[data.ssid] = data.error;
  }
  if (action === 'wifi-disconnect') native({ type: 'panel', action: 'wifi' });
  if (action === 'battery' || action === 'power-profile') battery = data;
  if (action === 'apps') { appList = data || []; if (page === 'apps') renderApps(); if (page === 'settings') renderSettings(); }
  if (action === 'prefs' && data) { prefs = data; if (page === 'settings' && !dragging) renderSettings(); }
  if (action === 'calendar' && data) { usage = data.days || {}; if (data.limits) limits = data.limits; if (panel === 'calendar') renderCal(); }
  if (action === 'cal-events' && data) {
    calEvents = {}; (data.events || []).forEach((e) => (calEvents[e.day] = calEvents[e.day] || []).push(e));
    calNames = data.calendars || []; if (panel === 'calendar' && !calAdding) renderCal();
  }
  if (action === 'cal-add' && data) {
    if (data.ok) { calAdding = false; calNote = ''; calAsked = ''; askEvents(); }
    else { calNote = data.error; }
    if (panel === 'calendar') { renderCal(); if (calAdding) $('calUrl')?.focus(); }
  }
  if (action === 'cal-remove') { calAsked = ''; askEvents(); }
  if (action.startsWith('bt') && data) { bt.data = data; bt.busy = null; bt.scanning = false; bt.toggling = false; if (data.note !== undefined) bt.note = data.note; }
  if (panel === 'wifi' && action.startsWith('bt')) renderWifi();
  if (panel === 'wifi' && action.startsWith('wifi')) renderWifi();
  if (action === 'notices') { notices = data || []; if (panel === 'notices') renderNotices(); }
  if (panel === 'battery' && (action === 'battery' || action === 'power-profile')) renderBattery();
};

$('panel').addEventListener('keydown', (e) => {
  if (e.target.id !== 'calUrl') return;
  e.stopPropagation();
  if (e.key === 'Enter' && e.target.value.trim()) { calNote = 'Connecting…'; native({ type: 'panel', action: 'cal-add', url: e.target.value.trim() }); }
  if (e.key === 'Escape') { calAdding = false; calNote = ''; renderCal(); }
});
$('panel').addEventListener('click', (e) => {
  const t = e.target.closest('[data-act]'); if (!t) return;
  const a = t.dataset.act, v = t.dataset.v;
  if (a === 'notices-clear') { native({ type: 'panel', action: 'notices-clear' }); return; }
  if (a === 'cal-prev') shiftCal(-1);
  if (a === 'cal-next') shiftCal(1);
  if (a === 'cal-connect') { calAdding = true; calNote = ''; renderCal(); $('calUrl').focus(); return; }
  if (a === 'cal-remove') { native({ type: 'panel', action: 'cal-remove', index: +v }); return; }
  if (a === 'cal-today') { cal.y = today().getFullYear(); cal.m = today().getMonth(); cal.sel = today(); cal.mode = 'month'; renderCal(); }
  if (a === 'cal-mode') { cal.mode = cal.mode === 'month' ? 'year' : 'month'; renderCal(); }
  if (a === 'cal-month') { cal.m = +v; cal.mode = 'month'; renderCal('l'); }
  if (a === 'cal-day') { const [y, m, d] = v.split('-').map(Number); cal.sel = new Date(y, m, d);
                          if (m !== cal.m) { const dir = (y * 12 + m) > (cal.y * 12 + cal.m) ? 'l' : 'r'; cal.y = y; cal.m = m; renderCal(dir); } else renderCal(); }
  if (a === 'net') {
    const n = wifi.data.networks[+v]; if (n.active || wifi.busy) return;
    if (n.secure && !n.known) { wifi.open = wifi.open === n.ssid ? null : n.ssid; renderWifi(); }
    else connect(n.ssid);
  }
  if (a === 'wifi-off') { wifi.toggling = true; native({ type: 'panel', action: 'wifi-radio', on: !wifi.data?.enabled }); renderWifi(); }
  if (a === 'wifi-rescan') { wifi.scanning = true; renderWifi(); native({ type: 'panel', action: 'wifi', rescan: true }); }
  if (a === 'wifi-disconnect') native({ type: 'panel', action: 'wifi-disconnect', ssid: v });
  if (a === 'wifi-pass') { wifi.open = wifi.open === v ? null : v; wifi.note[v] = ''; renderWifi(); }
  if (a === 'wifi-forget') { if (wifi.open === v) wifi.open = null; native({ type: 'panel', action: 'wifi-forget', ssid: v }); }
  if (a === 'profile') { battery.profile = v; renderBattery(); native({ type: 'panel', action: 'power-profile', profile: v }); }
  if (a === 'settings' && v === 'power') { onlySection = 'power'; return openPage('settings', 'power'); }
  if (a === 'settings') { closePanel(); native({ type: 'panel', action: 'settings', which: v }); }
});
$('panel').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.matches('.pw input') && e.target.value) connect(wifi.open, e.target.value);
});
$('panel').addEventListener('wheel', (e) => {
  if (panel !== 'calendar' || Math.abs(e.deltaY) < 4 || wheelLock) return;
  wheelLock = true; setTimeout(() => wheelLock = false, 320);
  shiftCal(e.deltaY > 0 ? 1 : -1);
}, { passive: true });
let wheelLock = false;

// Calendar
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const cal = { y: 0, m: 0, mode: 'month', sel: null };
const today = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
const same = (a, b) => a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
function isoWeek(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 864e5 + 1) / 7);
}
const dayOfYear = (d) => Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 864e5);
const yearLen = (y) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 366 : 365;
function relative(d) {
  const n = Math.round((d - today()) / 864e5), a = Math.abs(n);
  if (n === 0) return 'Today';
  if (n === 1) return 'Tomorrow';
  if (n === -1) return 'Yesterday';
  const unit = a < 14 ? [a, 'day'] : a < 60 ? [Math.round(a / 7), 'week'] : a < 700 ? [Math.round(a / 30.44), 'month'] : [Math.round(a / 365.25), 'year'];
  const txt = unit[0] + ' ' + unit[1] + (unit[0] === 1 ? '' : 's');
  return n > 0 ? 'In ' + txt : txt.charAt(0).toUpperCase() + txt.slice(1) + ' ago';
}
function shiftCal(dir) {
  if (cal.mode === 'year') cal.y += dir;
  else { cal.m += dir; if (cal.m < 0) { cal.m = 11; cal.y--; } if (cal.m > 11) { cal.m = 0; cal.y++; } }
  renderCal(dir > 0 ? 'l' : 'r');
}
function monthCells(y, m) {
  const off = (new Date(y, m, 1).getDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, i) => new Date(y, m, 1 - off + i));
}
const chev = (d) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
// Google Calendar (any iCal feed): events in the month, the chosen day's list.
let calEvents = {}, calNames = [], calAdding = false, calNote = '', calAsked = '';
function askEvents() {
  const k = cal.y + '-' + cal.m; if (calAsked === k) return; calAsked = k;
  native({ type: 'panel', action: 'cal-events', year: cal.y, month: cal.m });
}
const hm = (iso) => iso.slice(11, 16);
function eventsHtml(day) {
  const list = calEvents[key(day)] || [];
  let h = '';
  if (list.length) h += '<ul class="evs">' + list.map((e) =>
    `<li><span class="t">${e.allDay ? 'All day' : hm(e.start)}</span><span class="e"><b>${esc(e.title)}</b>${e.where ? `<small>${esc(e.where)}</small>` : ''}</span></li>`).join('') + '</ul>';
  else if (calNames.length) h += '<p class="p-sub evnone">Nothing planned.</p>';
  return h;
}
function calendarsHtml() {
  let h = `<div class="p-label"><span>Calendars</span>${calAdding ? '' : '<button class="p-link" data-act="cal-connect">Connect Google Calendar</button>'}</div>`;
  h += calNames.map((c, i) => `<div class="p-row"><span class="k">${esc(c.name)}</span><span class="v"><button class="p-link" data-act="cal-remove" data-v="${i}">Remove</button></span></div>`).join('');
  if (calAdding) h += `<div class="calform"><p class="p-sub">In Google Calendar on the web: Settings, choose the calendar, then copy <b>Secret address in iCal format</b> and paste it here.</p>
    <div class="pw"><span class="line"></span><input id="calUrl" placeholder="https://calendar.google.com/…/basic.ics" spellcheck="false"></div>
    ${calNote ? `<p class="p-sub" style="color:var(--accent)">${esc(calNote)}</p>` : ''}</div>`;
  return h;
}
function renderCal(anim) {
  if (cal.mode === 'month') askEvents();
  const t = today(), home = cal.y === t.getFullYear() && (cal.mode === 'year' || cal.m === t.getMonth());
  const head = `<div class="p-head">
      <button class="p-title cal-title" data-act="cal-mode" title="${cal.mode === 'month' ? 'Show the whole year' : 'Back to the month'}">${cal.mode === 'month' ? MONTHS[cal.m] + `<span class="y">${cal.y}</span>` : cal.y}</button>
      <span class="cal-nav">${home ? '' : '<button class="p-link" data-act="cal-today">Today</button>'}
        <button data-act="cal-prev" title="Previous">${chev('M15 5l-7 7 7 7')}</button><button data-act="cal-next" title="Next">${chev('M9 5l7 7-7 7')}</button></span>
    </div>`;
  let body;
  if (cal.mode === 'month') {
    const cells = monthCells(cal.y, cal.m);
    let g = '<span></span>' + ['M','T','W','T','F','S','S'].map((d) => `<span class="wd">${d}</span>`).join('');
    cells.forEach((d, i) => {
      if (i % 7 === 0) g += `<span class="wk">${isoWeek(d)}</span>`;
      const u = usage[key(d)], tot = u ? u.written + u.fresh + u.cached : 0;
      const mark = tot ? `<i style="opacity:${Math.min(1, .3 + Math.log10(tot) / 9).toFixed(2)}"></i>` : '';
      const cls = ['day', d.getMonth() !== cal.m && 'out', i % 7 > 4 && 'we', same(d, t) && 'today', same(d, cal.sel) && 'sel', calEvents[key(d)] && 'ev'].filter(Boolean).join(' ');
      g += `<span class="${cls}" data-act="cal-day" data-v="${d.getFullYear()}-${d.getMonth()}-${d.getDate()}"><b>${d.getDate()}</b>${mark}</span>`;
    });
    const s = cal.sel || t;
    body = `<div class="grid">${g}</div>
      <div class="cal-day"><div class="d">${DAYS[s.getDay()]} ${s.getDate()} ${MONTHS[s.getMonth()]}${s.getFullYear() !== t.getFullYear() ? ' ' + s.getFullYear() : ''}</div>
      <p class="p-sub">${relative(s)} · Week ${isoWeek(s)} · Day ${dayOfYear(s)} of ${yearLen(s.getFullYear())}</p></div>` + eventsHtml(s) + usageHtml(s) + calendarsHtml();
  } else {
    body = '<div class="years">' + MONTHS.map((name, m) => {
      const cells = monthCells(cal.y, m).slice(0, (new Date(cal.y, m, 1).getDay() + 6) % 7 + new Date(cal.y, m + 1, 0).getDate());
      const now = cal.y === t.getFullYear() && m === t.getMonth();
      return `<div class="mini${now ? ' now' : ''}" data-act="cal-month" data-v="${m}"><div class="mn">${name}</div><div class="mg">` +
        cells.map((d) => d.getMonth() !== m ? '<i></i>' : `<i${same(d, t) ? ' class="t"' : ''}>${d.getDate()}</i>`).join('') + '</div></div>';
    }).join('') + '</div>';
  }
  $('panel').innerHTML = head + `<div class="cal-body ${anim || ''}">${body}</div>`;
}

// Claude usage in the calendar: tokens on the chosen day, plan limits now.
let usage = {};
const key = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
function resets(iso) {
  if (!iso) return '';
  const t = new Date(iso), mins = Math.round((t - new Date()) / 6e4);
  if (mins < 1) return 'resets now';
  if (mins < 60 * 20) return `resets in ${mins >= 60 ? Math.floor(mins / 60) + ' h ' : ''}${mins % 60} min`;
  return 'resets ' + DAYS[t.getDay()].slice(0, 3) + ' ' + t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}
function usageHtml(day) {
  const u = usage[key(day)];
  let h = '';
  if (u) {
    const total = u.written + u.fresh + u.cached;
    h += `<div class="p-label"><span>Claude that day</span><span style="text-transform:none;letter-spacing:.01em">${fmt(total)} tokens</span></div><div class="p-rows">` +
      [['Written by Claude', fmt(u.written)], ['Read, new', fmt(u.fresh)], ['Read again from cache', fmt(u.cached)], ['Replies', u.replies]]
        .map(([k, v]) => `<div class="p-row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('') + '</div>';
  }
  const L = limits || {};
  const items = [['session', 'This session'], ['week', 'This week'], ['opus', 'This week, Opus'], ['sonnet', 'This week, Sonnet']].filter(([k]) => L[k]);
  if (items.length) {
    h += `<div class="p-label"><span>Plan limits</span></div>` + items.map(([k, name]) =>
      `<div class="limit"><div class="top"><span>${name} · ${Math.round(L[k].percent)}%</span><span>${resets(L[k].resets)}</span></div>
       <div class="meter"><i style="width:${Math.min(100, L[k].percent)}%"></i></div></div>`).join('');
  }
  return h;
}

// Wi-Fi
const wifi = { data: null, open: null, busy: null, note: {}, toggling: false, scanning: false };
const bars = (sig) => `<svg width="16" height="12" viewBox="0 0 16 12">${[3, 6, 9, 12].map((h, i) =>
    `<rect x="${i * 4 + 1}" y="${12 - h}" width="2" height="${h}" rx="1" fill="currentColor" opacity="${sig > [0, 30, 55, 75][i] ? 1 : .25}"/>`).join('')}</svg>`;
const lock = '<svg width="10" height="12" viewBox="0 0 10 12" fill="none" stroke="currentColor" stroke-width="1.1"><rect x="1" y="5" width="8" height="6" rx="1.5"/><path d="M3 5V3.5a2 2 0 0 1 4 0V5"/></svg>';
const quality = (s) => s >= 75 ? 'Excellent signal' : s >= 55 ? 'Good signal' : s >= 35 ? 'Fair signal' : 'Weak signal';
function connect(ssid, password) {
  wifi.busy = ssid; wifi.note[ssid] = ''; renderWifi();
  native({ type: 'panel', action: 'wifi-connect', ssid, password });
}
// Bluetooth: the same quiet list as Wi-Fi.
const bt = { data: null, busy: null, note: '', scanning: false, toggling: false };
function btSection() {
  const d = bt.data, on = d && d.present ? d.on : false;
  let h = `<div class="p-head" style="margin-top:38px"><h2 class="p-title">Bluetooth</h2>${d && d.present ? `<button class="switch${on !== bt.toggling ? ' on' : ''}" data-act="bt-power" title="Turn Bluetooth ${on ? 'off' : 'on'}"></button>` : ''}</div>`;
  if (!d) h += '<div class="p-wait" style="margin-top:26px"><span class="dot"></span>Looking…</div>';
  else if (!d.present) h += '<p class="p-sub" style="margin-top:14px">No Bluetooth on this computer yet. On this MacBook it starts working after the next restart.</p>';
  else if (!d.on) h += '<p class="p-sub" style="margin-top:14px">Bluetooth is off.</p>';
  else {
    const mine = d.devices.filter((x) => x.paired), near = d.devices.filter((x) => !x.paired);
    const row = (x) => `<li class="net" data-act="bt-dev" data-v="${esc(x.mac)}"><div class="top"><span class="n">${esc(x.name)}</span>` +
      (x.connected ? '<span class="cdot"></span>' : '') + `</div>` +
      (bt.busy === x.mac ? '<div class="p-wait" style="padding:6px 0 0;font-size:12px"><span class="dot"></span>Connecting…</div>'
        : bt.note && bt.noteMac === x.mac ? `<div class="note err">${esc(bt.note)}</div>`
        : `<div class="note">${x.connected ? 'Connected · click to disconnect' : x.paired ? 'Paired · click to connect' : 'Click to pair'}</div>`) + '</li>';
    h += `<div class="p-label"><span>My devices</span></div><ul class="nets">${mine.length ? mine.map(row).join('') : '<li class="p-sub" style="list-style:none">None yet.</li>'}</ul>`;
    h += `<div class="p-label"><span>Nearby</span>${bt.scanning ? '<span class="p-sub">Looking…</span>' : '<button class="p-link" data-act="bt-scan">Look for devices</button>'}</div>` +
         `<ul class="nets">${near.map(row).join('')}</ul>`;
  }
  return h;
}
const renderBt = () => renderWifi();
$('panel').addEventListener('click', (e) => {
  const a = e.target.closest('[data-act]'); if (!a || panel !== 'wifi' || !a.dataset.act.startsWith('bt')) return;
  const k = a.dataset.act;
  if (k === 'bt-power') { bt.toggling = true; renderBt(); native({ type: 'panel', action: 'bt-power', on: !bt.data?.on }); }
  if (k === 'bt-scan') { bt.scanning = true; renderBt(); native({ type: 'panel', action: 'bt', scan: true }); }
  if (k === 'bt-dev') {
    const x = bt.data.devices.find((d) => d.mac === a.dataset.v); if (!x) return;
    if (x.connected) native({ type: 'panel', action: 'bt-disconnect', mac: x.mac });
    else { bt.busy = x.mac; bt.noteMac = x.mac; bt.note = ''; renderBt(); native({ type: 'panel', action: 'bt-connect', mac: x.mac }); }
  }
});

function renderWifi() {
  const d = wifi.data, on = d ? d.enabled : true;
  let h = `<div class="p-head"><h2 class="p-title">Wi‑Fi</h2><button class="switch${on !== wifi.toggling ? ' on' : ''}" data-act="wifi-off" title="Turn Wi‑Fi ${on ? 'off' : 'on'}"></button></div>`;
  if (!d) h += '<div class="p-wait" style="margin-top:26px"><span class="dot"></span>Looking for networks…</div>';
  else if (!d.enabled) h += '<p class="p-sub" style="margin-top:14px">Wi‑Fi is off.</p>';
  else {
    const nets = d.networks, cur = nets.find((n) => n.active);
    h += cur ? `<div class="current"><span class="cdot"></span><div><div class="name">${esc(cur.ssid)}</div>
                <p class="p-sub">Connected · ${cur.band} · ${quality(cur.signal)}</p></div>
                <button class="p-link" data-act="wifi-disconnect" data-v="${esc(cur.ssid)}">Disconnect</button></div>
                <div class="netacts"><button class="p-link" data-act="wifi-pass" data-v="${esc(cur.ssid)}">Change password</button><button class="p-link" data-act="wifi-forget" data-v="${esc(cur.ssid)}">Forget</button></div>
                ${wifi.open === cur.ssid ? `<div class="pw" style="margin-left:22px"><span class="line"></span><input type="password" placeholder="New password" spellcheck="false"></div>${wifi.note[cur.ssid] ? `<div class="note err" style="margin-left:22px">${esc(wifi.note[cur.ssid])}</div>` : ''}` : ''}`
             : '<p class="p-sub" style="margin-top:14px">Not connected.</p>';
    h += `<div class="p-label"><span>Networks</span>${wifi.scanning && !d.networks.length ? '' : '<button class="p-link" data-act="wifi-rescan">Refresh</button>'}</div><ul class="nets">`;
    nets.forEach((n, i) => {
      if (n.active) return;
      const busy = wifi.busy === n.ssid, note = wifi.note[n.ssid];
      h += `<li class="net" data-act="net" data-v="${i}"><div class="top"><span class="n">${esc(n.ssid)}</span>${n.secure ? lock : ''}${bars(n.signal)}</div>`;
      if (busy) h += '<div class="p-wait" style="padding:6px 0 0;font-size:12px"><span class="dot"></span>Connecting…</div>';
      else if (note) h += `<div class="note err">${esc(note)}</div>`;
      else if (n.known) h += `<div class="note">Saved · <button class="p-link inline" data-act="wifi-forget" data-v="${esc(n.ssid)}">Forget</button></div>`;
      if (wifi.open === n.ssid && !busy) h += `<div class="pw" onclick="event.stopPropagation()"><span class="line"></span><input type="password" placeholder="Password" spellcheck="false"></div>`;
      h += '</li>';
    });
    h += '</ul>';
  }
  h += btSection();
  const focused = document.activeElement?.matches?.('.pw input') ? document.activeElement.value : null;
  $('panel').innerHTML = h;
  wifi.scanning = false;
  const pw = $('panel').querySelector('.pw input');
  if (pw) { if (focused) pw.value = focused; pw.focus(); }
}

// Battery
let battery = null;
const dur = (m) => m == null ? '' : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ' ' + (m % 60) + ' min' : ''}`;
const PROFILES = { 'power-saver': 'Saver', balanced: 'Balanced', performance: 'Performance' };
function chart(hist, now) {
  if (!hist || hist.length < 3) return '';
  const W = 376, H = 96, t0 = hist[0][0], span = Math.max(now - t0, 600);
  const pts = hist.map(([t, v]) => [((t - t0) / span) * W, H - (v / 100) * (H - 6) - 3]);
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const last = pts[pts.length - 1];
  const ago = Math.round(span / 3600);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <defs><linearGradient id="bf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".22"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      <line x1="0" x2="${W}" y1="3" y2="3" stroke="var(--faint)" stroke-dasharray="2 4"/><line x1="0" x2="${W}" y1="${H - 3}" y2="${H - 3}" stroke="var(--faint)"/>
      <path d="${line} L${last[0].toFixed(1)} ${H} L0 ${H} Z" fill="url(#bf)"/>
      <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    </svg><div class="axis"><span>${ago >= 1 ? ago + ' h ago' : Math.round(span / 60) + ' min ago'}</span><span>Now</span></div>`;
}
// Notifications (F9): what came in, newest first, kept by the system's notice daemon.
let notices = null;
function renderNotices() {
  let h = '<div class="p-head"><h2 class="p-title">Notifications</h2></div>';
  if (!notices) h += '<div class="p-wait" style="margin-top:26px"><span class="dot"></span>Looking…</div>';
  else if (!notices.length) h += '<p class="p-sub" style="margin-top:12px">Nothing new. All quiet.</p>';
  else h += '<div class="p-rows" style="margin-top:22px">' + notices.map((n) =>
    `<div class="notice"><div class="nh"><span>${n.new ? '<i class="ndot"></i>' : ''}${esc(n.app || 'Notice')}</span><span>${when(n.t)}</span></div>` +
    (n.title ? `<div class="nt">${esc(n.title)}</div>` : '') + (n.body ? `<div class="nb">${esc(n.body)}</div>` : '') + '</div>').join('') + '</div>' +
    '<div class="p-foot"><button class="p-link" data-act="notices-clear">Clear all</button></div>';
  $('panel').innerHTML = h;
}
function renderBattery() {
  const b = battery;
  if (!b) { $('panel').innerHTML = '<div class="p-head"><h2 class="p-title">Battery</h2></div><div class="p-wait" style="margin-top:26px"><span class="dot"></span>Reading the battery…</div>'; return; }
  const lvl = Math.round(b.level ?? 0);
  const state = b.state === 'charging' ? 'Charging' + (b.toFull ? ` · full in ${dur(b.toFull)}` : '')
    : b.state === 'fully-charged' ? 'Fully charged' : b.state === 'pending-charge' ? 'Plugged in, not charging'
    : 'On battery' + (b.toEmpty ? ` · ${dur(b.toEmpty)} left` : '');
  const rows = [
    b.health != null && ['Health', `${b.health}%<small>of original capacity</small>`],
    b.cycles && ['Charge cycles', b.cycles],
    b.rate != null && [b.state === 'charging' ? 'Charging at' : 'Using', `${b.rate.toFixed(1)} W`],
    b.energy != null && ['Energy', `${b.energy.toFixed(1)}<small>of ${b.full?.toFixed(1)} Wh</small>`],
    b.design && ['Designed for', `${b.design.toFixed(1)} Wh`],
    b.temperature && ['Temperature', `${b.temperature.toFixed(1)} °C`],
    b.name && ['Battery', esc(b.name) + (b.technology ? `<small>${esc(b.technology)}</small>` : '')],
  ].filter(Boolean);
  let h = `<div class="big">${lvl}<span>%</span></div><p class="p-sub" style="margin-top:12px">${state}</p>
    <div class="meter"><i style="width:${lvl}%"></i></div>`;
  const c = chart(b.history, b.now);
  if (c) h += `<div class="p-label"><span>Last hours</span></div>${c}`;
  if (b.profiles?.length) h += `<div class="p-label"><span>Power mode</span></div><div class="opts">` +
    b.profiles.slice().sort((x, y) => Object.keys(PROFILES).indexOf(x) - Object.keys(PROFILES).indexOf(y))
      .map((p) => `<span class="opt${p === b.profile ? ' sel' : ''}" data-act="profile" data-v="${p}">${PROFILES[p] || p}</span>`).join('') + '</div>';
  h += `<div class="p-label"><span>Details</span></div><div class="p-rows">` +
    rows.map(([k, v]) => `<div class="p-row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('') + '</div>';
  h += '<div class="p-foot"><button class="p-link" data-act="settings" data-v="power">Power settings</button></div>';
  $('panel').innerHTML = h;
}
