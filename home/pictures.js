// Pictures for Claude: a calm picker over the home screen instead of the system's file dialog.
// Recent pictures from a few folders, newest first; Space looks closer, Enter adds, Esc closes.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages, pictures.
const picks = { open: false, folder: 'Screenshots', folders: [], items: [], at: 0, chosen: new Set(), peek: false };
const picEsc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function picWhen(t) {
  const d = new Date(t * 1000), now = new Date();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const ago = Math.round((day(now) - day(d)) / 86400000);
  if (ago === 0) return 'Today, ' + time;
  if (ago === 1) return 'Yesterday, ' + time;
  if (ago < 7) return d.toLocaleDateString([], { weekday: 'long' }) + ', ' + time;
  return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
}

function openPictures() {
  if (typeof closePanel === 'function' && panel) closePanel();
  if (typeof closeMenu === 'function') closeMenu();
  picks.open = true; picks.peek = false; picks.chosen.clear(); picks.at = 0;
  const el = $('picks');
  el.hidden = false; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  document.body.classList.add('picking');
  ask.blur();
  renderPictures(true);
  native({ type: 'picture', action: 'list', folder: picks.folder });
}
function closePictures() {
  picks.open = false; picks.peek = false;
  $('picks').hidden = true; $('peek').hidden = true;
  document.body.classList.remove('picking');
  focusInput();
}
function addPictures(paths) {
  if (!paths.length) return;
  native({ type: 'picture', action: 'add', paths });
  closePictures();
}
function chosenPaths() {
  if (picks.chosen.size) return picks.items.filter((p) => picks.chosen.has(p.path)).map((p) => p.path);
  const p = picks.items[picks.at];
  return p ? [p.path] : [];
}

window.setPictures = ({ folder, folders, items }) => {
  if (folder !== picks.folder) return;
  picks.folders = folders || []; picks.items = items || []; picks.at = 0;
  if (!picks.folders.includes(picks.folder) && picks.folders.length) { picks.folder = picks.folders[0]; native({ type: 'picture', action: 'list', folder: picks.folder }); }
  renderPictures(false);
};

function renderPictures(loading) {
  const n = picks.chosen.size;
  const tabs = (picks.folders.length ? picks.folders : [picks.folder]).map((f) =>
    `<button class="pkf${f === picks.folder ? ' on' : ''}" data-folder="${picEsc(f)}">${picEsc(f)}</button>`).join('');
  const grid = loading ? '' : picks.items.length
    ? picks.items.map((p, i) =>
      `<figure class="pk${i === picks.at ? ' at' : ''}${picks.chosen.has(p.path) ? ' on' : ''}" data-i="${i}">` +
      `<span class="pkimg"><img loading="lazy" decoding="async" src="${picEsc(p.uri)}" alt=""><i></i></span>` +
      `<figcaption>${picEsc(picWhen(p.t))}</figcaption></figure>`).join('')
    : '<p class="pknone">No pictures here yet.</p>';
  $('picks').innerHTML =
    '<div class="pkhead"><h1>Add a picture</h1>' +
    '<button class="p-link" data-act="other">Other…</button></div>' +
    `<nav class="pkfolders">${tabs}</nav>` +
    `<div class="pkscroll"><div class="pkgrid">${grid}</div></div>` +
    '<div class="pkfoot"><span class="pkkeys">Click or X to choose · Space to look closer · Enter to add · Esc to close</span>' +
    `<button class="pkadd" data-act="add"${picks.items.length ? '' : ' disabled'}><span class="line"></span>${n > 1 ? `Add ${n} pictures` : 'Add'}</button></div>`;
}
function drawMarks() {
  document.querySelectorAll('#picks .pk').forEach((f) => {
    const i = +f.dataset.i, p = picks.items[i];
    f.classList.toggle('at', i === picks.at);
    f.classList.toggle('on', picks.chosen.has(p.path));
  });
  const n = picks.chosen.size, b = document.querySelector('#picks .pkadd');
  if (b) b.lastChild.textContent = n > 1 ? `Add ${n} pictures` : 'Add';
}
function moveTo(i) {
  if (!picks.items.length) return;
  picks.at = Math.max(0, Math.min(picks.items.length - 1, i));
  drawMarks();
  document.querySelector(`#picks .pk[data-i="${picks.at}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  if (picks.peek) drawPeek();
}
function columns() {
  const figs = [...document.querySelectorAll('#picks .pk')];
  if (!figs.length) return 1;
  const top = figs[0].offsetTop;
  const n = figs.findIndex((f) => f.offsetTop !== top);
  return n < 0 ? figs.length : n;
}
function toggleChosen(i) {
  const p = picks.items[i]; if (!p) return;
  picks.chosen.has(p.path) ? picks.chosen.delete(p.path) : picks.chosen.add(p.path);
  drawMarks();
}

// Looking closer: the picture large, its name and time quietly under it.
function drawPeek() {
  const p = picks.items[picks.at]; if (!p) return closePeek();
  const el = $('peek');
  el.innerHTML = `<img src="${picEsc(p.uri)}" alt="">` +
    `<div class="pkcap"><b>${picEsc(p.name)}</b><span>${picEsc(picWhen(p.t))}` +
    `${picks.items.length > 1 ? ` · ${picks.at + 1} of ${picks.items.length}` : ''}</span></div>`;
  el.classList.toggle('on', picks.chosen.has(p.path));
}
function openPeek() {
  if (!picks.items[picks.at]) return;
  picks.peek = true;
  const el = $('peek');
  el.hidden = false; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  drawPeek();
}
function closePeek() { picks.peek = false; $('peek').hidden = true; }

$('picks').addEventListener('click', (e) => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'add') return addPictures(chosenPaths());
  if (act === 'other') { closePictures(); return native({ type: 'picture', action: 'other' }); }
  const f = e.target.closest('[data-folder]');
  if (f && f.dataset.folder !== picks.folder) {
    picks.folder = f.dataset.folder; picks.chosen.clear(); picks.items = [];
    renderPictures(true);
    return native({ type: 'picture', action: 'list', folder: picks.folder });
  }
  const fig = e.target.closest('.pk');
  if (fig) { picks.at = +fig.dataset.i; toggleChosen(picks.at); }
});
$('picks').addEventListener('dblclick', (e) => {
  const fig = e.target.closest('.pk'); if (!fig) return;
  const p = picks.items[+fig.dataset.i];
  picks.chosen.add(p.path);
  addPictures(chosenPaths());
});
$('peek').addEventListener('click', (e) => { if (e.target.tagName !== 'IMG') closePeek(); });
$('peek').addEventListener('dblclick', () => addPictures(chosenPaths()));

// While the picker is open, its keys come first: nothing reaches the line or the tabs.
window.addEventListener('keydown', (e) => {
  if (!picks.open) return;
  e.stopImmediatePropagation();
  const k = e.key;
  if (k === 'Escape') { e.preventDefault(); return picks.peek ? closePeek() : closePictures(); }
  if (k === ' ') { e.preventDefault(); return picks.peek ? closePeek() : openPeek(); }
  if (k === 'Enter') {
    e.preventDefault();
    if (picks.peek && !picks.chosen.size) picks.chosen.add(picks.items[picks.at].path);
    return addPictures(chosenPaths());
  }
  if (k === 'Tab' && picks.folders.length > 1 && !picks.peek) {
    e.preventDefault();
    const i = picks.folders.indexOf(picks.folder), n = picks.folders.length;
    picks.folder = picks.folders[(i + (e.shiftKey ? n - 1 : 1)) % n]; picks.chosen.clear(); picks.items = [];
    renderPictures(true);
    return native({ type: 'picture', action: 'list', folder: picks.folder });
  }
  if (k === 'x' || k === 'X') { e.preventDefault(); toggleChosen(picks.at); if (picks.peek) drawPeek(); return; }
  const cols = picks.peek ? 1 : columns();
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[k];
  if (step) { e.preventDefault(); return moveTo(picks.at + step); }
  if (k === 'Home') { e.preventDefault(); return moveTo(0); }
  if (k === 'End') { e.preventDefault(); return moveTo(picks.items.length - 1); }
  e.preventDefault();
}, true);
