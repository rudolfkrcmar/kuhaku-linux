// Pictures for Claude: a calm picker over the home screen instead of the system's file dialog.
// Recent pictures from a few folders, newest first, and "Other" to walk the home folder.
// Space (or the corner mark on a picture) looks closer, Enter adds, Esc steps back.
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages, pictures.
const picks = { open: false, folder: 'Screenshots', path: '', crumbs: [], folders: [], items: [], at: 0, chosen: new Map(), peek: false };
const picEsc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const FOLDER_MARK = '<svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 7.5a2 2 0 0 1 2-2h3.6l2 2h7.4a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>';
const LOOK_MARK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M10 20H4v-6"/><path d="M20 4l-6.5 6.5"/><path d="M4 20l6.5-6.5"/></svg>';

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
  listPictures();
}
function listPictures() { native({ type: 'picture', action: 'list', folder: picks.folder, path: picks.folder === 'Other' ? picks.path : '' }); }
function goFolder(folder, path = '') {
  picks.folder = folder; picks.path = path; picks.items = []; picks.at = 0;
  renderPictures(true);
  listPictures();
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
  if (picks.chosen.size) return [...picks.chosen.keys()];
  const p = picks.items[picks.at];
  return p && !p.dir ? [p.path] : [];
}

window.setPictures = ({ folder, path, crumbs, folders, items }) => {
  if (folder !== picks.folder || (folder === 'Other' && picks.path && path !== picks.path)) return;  // an older answer
  picks.path = path || ''; picks.crumbs = crumbs || [];
  picks.folders = folders || []; picks.items = items || []; picks.at = 0;
  if (!picks.folders.includes(picks.folder) && picks.folders.length) return goFolder(picks.folders[0]);
  renderPictures(false);
};

function renderPictures(loading) {
  const n = picks.chosen.size;
  const tabs = (picks.folders.length ? picks.folders : [picks.folder]).map((f) =>
    `<button class="pkf${f === picks.folder ? ' on' : ''}" tabindex="-1" data-folder="${picEsc(f)}">${picEsc(f)}</button>`).join('');
  const crumbs = picks.folder === 'Other' && picks.crumbs.length
    ? '<nav class="pkcrumbs">' + picks.crumbs.map((c, i) => i === picks.crumbs.length - 1
      ? `<span>${picEsc(c.name)}</span>` : `<button tabindex="-1" data-path="${picEsc(c.path)}">${picEsc(c.name)}</button><i>/</i>`).join('') + '</nav>'
    : '';
  const grid = loading ? '' : picks.items.length
    ? picks.items.map((p, i) => p.dir
      ? `<figure class="pk dir${i === picks.at ? ' at' : ''}" data-i="${i}"><span class="pkimg">${FOLDER_MARK}</span><figcaption>${picEsc(p.name)}</figcaption></figure>`
      : `<figure class="pk${i === picks.at ? ' at' : ''}${picks.chosen.has(p.path) ? ' on' : ''}" data-i="${i}">` +
        `<span class="pkimg"><img loading="lazy" decoding="async" src="${picEsc(p.uri)}" alt=""><i></i>` +
        `<b class="pklook" title="Look closer (Space)">${LOOK_MARK}</b></span>` +
        `<figcaption>${picEsc(picks.folder === 'Other' ? p.name : picWhen(p.t))}</figcaption></figure>`).join('')
    : `<p class="pknone">${picks.folder === 'Other' ? 'No pictures or folders here.' : 'No pictures here yet.'}</p>`;
  $('picks').innerHTML =
    '<div class="pkhead"><h1>Add a picture</h1>' +
    `<button class="pkadd" tabindex="-1" data-act="add"${picks.items.some((p) => !p.dir) || n ? '' : ' disabled'}><span class="line"></span>${n > 1 ? `Add ${n} pictures` : 'Add'}</button></div>` +
    `<nav class="pkfolders">${tabs}</nav>${crumbs}` +
    `<div class="pkscroll"><div class="pkgrid">${grid}</div></div>` +
    '<div class="pkfoot"><span class="pkkeys">Click to choose · Space to look closer · Enter to add · Esc to close</span></div>';
}
function drawMarks() {
  document.querySelectorAll('#picks .pk').forEach((f) => {
    const i = +f.dataset.i, p = picks.items[i];
    f.classList.toggle('at', i === picks.at);
    f.classList.toggle('on', picks.chosen.has(p.path));
  });
  const n = picks.chosen.size, b = document.querySelector('#picks .pkadd');
  if (b) { b.lastChild.textContent = n > 1 ? `Add ${n} pictures` : 'Add'; if (n) b.disabled = false; }
}
function moveTo(i) {
  if (!picks.items.length) return;
  const step = Math.sign(i - picks.at) || 1;
  i = Math.max(0, Math.min(picks.items.length - 1, i));
  if (picks.peek) {  // looking closer skips the folders
    while (picks.items[i]?.dir && i + step >= 0 && i + step < picks.items.length) i += step;
    if (picks.items[i]?.dir) return;
  }
  picks.at = i;
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
  const p = picks.items[i]; if (!p || p.dir) return;
  picks.chosen.has(p.path) ? picks.chosen.delete(p.path) : picks.chosen.set(p.path, p);
  drawMarks();
}
function enter(i) {
  const p = picks.items[i];
  if (p?.dir) goFolder('Other', p.path);
}

// Looking closer: the picture large, its name and time quietly under it.
function drawPeek() {
  const p = picks.items[picks.at]; if (!p || p.dir) return closePeek();
  const pics = picks.items.filter((x) => !x.dir), at = pics.indexOf(p);
  const el = $('peek');
  el.innerHTML = `<img src="${picEsc(p.uri)}" alt="">` +
    `<div class="pkcap"><b>${picEsc(p.name)}</b><span>${picEsc(picWhen(p.t))}` +
    `${pics.length > 1 ? ` · ${at + 1} of ${pics.length}` : ''}</span>` +
    `<span class="pkhint">${picks.chosen.has(p.path) ? 'Chosen · ' : ''}← → to move · X to choose · Enter to add · Space to go back</span></div>`;
  el.classList.toggle('on', picks.chosen.has(p.path));
}
function openPeek() {
  const p = picks.items[picks.at];
  if (!p) return;
  if (p.dir) return enter(picks.at);
  picks.peek = true;
  const el = $('peek');
  el.hidden = false; el.style.animation = 'none'; void el.offsetWidth; el.style.animation = '';
  drawPeek();
}
function closePeek() { picks.peek = false; $('peek').hidden = true; }

// Buttons never keep the focus: Space and Enter belong to the pictures, not to the last thing clicked.
$('picks').addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
$('picks').addEventListener('click', (e) => {
  if (e.target.closest('[data-act="add"]')) return addPictures(chosenPaths());
  const f = e.target.closest('[data-folder]');
  if (f) { if (f.dataset.folder !== picks.folder) goFolder(f.dataset.folder); return; }
  const c = e.target.closest('[data-path]');
  if (c) return goFolder('Other', c.dataset.path);
  const fig = e.target.closest('.pk'); if (!fig) return;
  picks.at = +fig.dataset.i;
  if (e.target.closest('.pklook')) { drawMarks(); return openPeek(); }
  if (picks.items[picks.at].dir) return enter(picks.at);
  toggleChosen(picks.at);
});
$('picks').addEventListener('dblclick', (e) => {
  const fig = e.target.closest('.pk'); if (!fig || e.target.closest('.pklook')) return;
  const p = picks.items[+fig.dataset.i];
  if (p.dir) return;
  picks.chosen.set(p.path, p);
  addPictures(chosenPaths());
});
$('peek').addEventListener('click', (e) => { if (e.target.tagName !== 'IMG') closePeek(); });
$('peek').addEventListener('dblclick', (e) => { if (e.target.tagName === 'IMG') addPictures(chosenPaths().length ? chosenPaths() : [picks.items[picks.at].path]); });
$('peek').addEventListener('wheel', (e) => { e.preventDefault(); moveTo(picks.at + (e.deltaY + e.deltaX > 0 ? 1 : -1)); }, { passive: false });

// While the picker is open, its keys come first: nothing reaches the line, the tabs or a button.
const pickKeys = new Set([' ', 'Enter']);
window.addEventListener('keyup', (e) => { if (picks.open && pickKeys.has(e.key)) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
window.addEventListener('keydown', (e) => {
  if (!picks.open) return;
  e.stopImmediatePropagation();
  const k = e.key;
  if (k === 'Escape') { e.preventDefault(); return picks.peek ? closePeek() : closePictures(); }
  if (k === ' ') { e.preventDefault(); return picks.peek ? closePeek() : openPeek(); }
  if (k === 'Enter') {
    e.preventDefault();
    const p = picks.items[picks.at];
    if (!picks.peek && p?.dir && !picks.chosen.size) return enter(picks.at);
    if (picks.peek && !picks.chosen.size && p) return addPictures([p.path]);
    return addPictures(chosenPaths());
  }
  if ((k === 'Backspace' || (e.altKey && k === 'ArrowUp')) && picks.folder === 'Other' && picks.crumbs.length > 1 && !picks.peek) {
    e.preventDefault(); return goFolder('Other', picks.crumbs[picks.crumbs.length - 2].path);
  }
  if (k === 'Tab' && picks.folders.length > 1 && !picks.peek) {
    e.preventDefault();
    const i = picks.folders.indexOf(picks.folder), n = picks.folders.length;
    return goFolder(picks.folders[(i + (e.shiftKey ? n - 1 : 1)) % n]);
  }
  if (k === 'x' || k === 'X') { e.preventDefault(); toggleChosen(picks.at); if (picks.peek) drawPeek(); return; }
  const cols = picks.peek ? 1 : columns();
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[k];
  if (step) { e.preventDefault(); return moveTo(picks.at + step); }
  if (k === 'Home') { e.preventDefault(); return moveTo(0); }
  if (k === 'End') { e.preventDefault(); return moveTo(picks.items.length - 1); }
  e.preventDefault();
}, true);
