// Kūhaku's file picker: a calm grid instead of the system's file dialog. One core, two homes:
// the home screen's "Add a file" (home/pictures.js) and the window other apps open for a file
// (pick/pick.html: Tegami's attachments, Mado's uploads).
// Tabs of folders, newest first, and "Other" to walk the home folder. Click chooses, Space looks
// closer (also the corner mark on a picture), Enter adds, Esc steps back.
//
//   const p = makePicker({ root, peek, title, verb, multiple, send, onClose });
//   p.open(); p.set(listing); p.peekData(data); window.addEventListener('keydown', p.key, true)
// send(msg) gets {action:'list', folder, path}, {action:'add', paths} and {action:'peek', path}.
function makePicker({ root, peek, title = 'Add a picture', verb = 'Add', multiple = true, start = '', send, onClose }) {
  const s = { open: false, folder: start, path: '', crumbs: [], folders: [], items: [], at: 0, chosen: new Map(), peek: false, extra: null };
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const FOLDER = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M4 6h6l2 2h8v11H4z"/></svg>';
  const LOOK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 4h6v6M20 4l-6 6M10 20H4v-6M4 20l6-6"/></svg>';

  function when(t) {
    const d = new Date(t * 1000), now = new Date();
    const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const ago = Math.round((day(now) - day(d)) / 86400000);
    if (ago === 0) return 'Today, ' + time;
    if (ago === 1) return 'Yesterday, ' + time;
    if (ago < 7) return d.toLocaleDateString([], { weekday: 'long' }) + ', ' + time;
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
  }
  const list = () => send({ action: 'list', folder: s.folder, path: s.folder === 'Other' ? s.path : '' });
  function go(folder, path = '') {
    s.folder = folder; s.path = path; s.items = []; s.at = 0;
    render(true); list();
  }
  function open() {
    s.open = true; s.peek = false; s.chosen.clear(); s.at = 0;
    root.hidden = false; root.style.animation = 'none'; void root.offsetWidth; root.style.animation = '';
    render(true); list();
  }
  function close() {
    s.open = false; s.peek = false; root.hidden = true; peek.hidden = true;
    onClose?.();
  }
  function add(paths) {
    if (!paths.length) return;
    send({ action: 'add', paths });
    close();
  }
  function chosen() {
    if (s.chosen.size) return [...s.chosen.keys()];
    const p = s.items[s.at];
    return p && !p.dir ? [p.path] : [];
  }
  function set({ folder, path, crumbs, folders, items }) {
    if (folder !== s.folder && s.folder) return;          // an answer for a tab no longer shown
    if (folder === 'Other' && s.path && path !== s.path) return;
    s.folder = folder; s.path = path || ''; s.crumbs = crumbs || [];
    s.folders = folders || []; s.items = items || []; s.at = 0;
    if (!s.folders.includes(s.folder) && s.folders.length) return go(s.folders[0]);
    render(false);
  }
  function label() {
    const n = s.chosen.size;
    return n > 1 ? `${verb} ${n}` : verb;
  }
  function render(loading) {
    const tabs = (s.folders.length ? s.folders : [s.folder]).filter(Boolean).map((f) =>
      `<button class="pkf${f === s.folder ? ' on' : ''}" tabindex="-1" data-folder="${esc(f)}">${esc(f)}</button>`).join('');
    const crumbs = s.folder === 'Other' && s.crumbs.length
      ? '<nav class="pkcrumbs">' + s.crumbs.map((c, i) => i === s.crumbs.length - 1
        ? `<span>${esc(c.name)}</span>` : `<button tabindex="-1" data-path="${esc(c.path)}">${esc(c.name)}</button><i>/</i>`).join('') + '</nav>'
      : '';
    const named = s.folder === 'Other' || s.items.some((p) => !p.uri && !p.dir);
    const grid = loading ? '' : s.items.length
      ? s.items.map((p, i) => {
        const cls = `pk${p.dir ? ' dir' : ''}${p.uri ? '' : ' file'}${i === s.at ? ' at' : ''}${s.chosen.has(p.path) ? ' on' : ''}`;
        if (p.dir) return `<figure class="${cls}" data-i="${i}"><span class="pkimg">${FOLDER}</span><figcaption>${esc(p.name)}</figcaption></figure>`;
        const face = p.uri ? `<img loading="lazy" decoding="async" src="${esc(p.uri)}" alt="">` : `<em>${esc(p.kind)}</em>`;
        const cap = named ? `${esc(p.name)}<small>${esc(when(p.t))}${p.size ? ' · ' + esc(p.size) : ''}</small>` : esc(when(p.t));
        return `<figure class="${cls}" data-i="${i}"><span class="pkimg">${face}<i></i>` +
          `<b class="pklook" title="Look closer (Space)">${LOOK}</b></span><figcaption>${cap}</figcaption></figure>`;
      }).join('')
      : `<p class="pknone">${s.folder === 'Other' ? 'Nothing to choose here.' : 'Nothing here yet.'}</p>`;
    root.innerHTML =
      `<div class="pkhead"><h1>${esc(title)}</h1>` +
      `<button class="pkadd" tabindex="-1" data-act="add"${s.items.some((p) => !p.dir) || s.chosen.size ? '' : ' disabled'}><span class="line"></span>${esc(label())}</button></div>` +
      `<nav class="pkfolders">${tabs}</nav>${crumbs}` +
      `<div class="pkscroll"><div class="pkgrid">${grid}</div></div>` +
      `<div class="pkfoot"><span class="pkkeys">Click to choose · Space to look closer · Enter to ${esc(verb.toLowerCase())} · Esc to close</span></div>`;
  }
  function marks() {
    root.querySelectorAll('.pk').forEach((f) => {
      const i = +f.dataset.i, p = s.items[i];
      f.classList.toggle('at', i === s.at);
      f.classList.toggle('on', s.chosen.has(p.path));
    });
    const b = root.querySelector('.pkadd');
    if (b) { b.lastChild.textContent = label(); if (s.chosen.size) b.disabled = false; }
  }
  function moveTo(i) {
    if (!s.items.length) return;
    const step = Math.sign(i - s.at) || 1;
    i = Math.max(0, Math.min(s.items.length - 1, i));
    if (s.peek) {  // looking closer skips the folders
      while (s.items[i]?.dir && i + step >= 0 && i + step < s.items.length) i += step;
      if (s.items[i]?.dir) return;
    }
    s.at = i;
    marks();
    root.querySelector(`.pk[data-i="${s.at}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (s.peek) drawPeek();
  }
  function columns() {
    const figs = [...root.querySelectorAll('.pk')];
    if (!figs.length) return 1;
    const n = figs.findIndex((f) => f.offsetTop !== figs[0].offsetTop);
    return n < 0 ? figs.length : n;
  }
  function toggle(i) {
    const p = s.items[i]; if (!p || p.dir) return;
    if (s.chosen.has(p.path)) s.chosen.delete(p.path);
    else { if (!multiple) s.chosen.clear(); s.chosen.set(p.path, p); }
    marks();
  }
  function enter(i) { const p = s.items[i]; if (p?.dir) go('Other', p.path); }

  // Looking closer: a picture large; other files as their first page or first lines, when the app can tell.
  function drawPeek() {
    const p = s.items[s.at]; if (!p || p.dir) return closePeek();
    const files = s.items.filter((x) => !x.dir), at = files.indexOf(p);
    const x = s.extra && s.extra.path === p.path ? s.extra : null;
    const face = p.uri ? `<img src="${esc(p.uri)}" alt="">`
      : x?.image ? `<img src="${esc(x.image)}" alt="">`
        : x?.text != null ? `<pre class="pktext">${esc(x.text)}</pre>`
          : `<div class="pkcard"><em>${esc(p.kind)}</em></div>`;
    peek.innerHTML = face +
      `<div class="pkcap"><b>${esc(p.name)}</b><span>${esc(when(p.t))}${p.size ? ' · ' + esc(p.size) : ''}` +
      `${files.length > 1 ? ` · ${at + 1} of ${files.length}` : ''}</span>` +
      `<span class="pkhint">${s.chosen.has(p.path) ? 'Chosen · ' : ''}← → to move · X to choose · Enter to ${esc(verb.toLowerCase())} · Space to go back</span></div>`;
    peek.classList.toggle('on', s.chosen.has(p.path));
    if (!p.uri && !x) send({ action: 'peek', path: p.path });
  }
  function openPeek() {
    const p = s.items[s.at];
    if (!p) return;
    if (p.dir) return enter(s.at);
    s.peek = true;
    peek.hidden = false; peek.style.animation = 'none'; void peek.offsetWidth; peek.style.animation = '';
    drawPeek();
  }
  function closePeek() { s.peek = false; peek.hidden = true; }
  function peekData(data) { s.extra = data; if (s.peek) drawPeek(); }

  // Buttons never keep the focus: Space and Enter belong to the files, not to the last thing clicked.
  root.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  root.addEventListener('click', (e) => {
    if (e.target.closest('[data-act="add"]')) return add(chosen());
    const f = e.target.closest('[data-folder]');
    if (f) { if (f.dataset.folder !== s.folder) go(f.dataset.folder); return; }
    const c = e.target.closest('[data-path]');
    if (c) return go('Other', c.dataset.path);
    const fig = e.target.closest('.pk'); if (!fig) return;
    s.at = +fig.dataset.i;
    if (e.target.closest('.pklook')) { marks(); return openPeek(); }
    if (s.items[s.at].dir) return enter(s.at);
    toggle(s.at);
  });
  root.addEventListener('dblclick', (e) => {
    const fig = e.target.closest('.pk'); if (!fig || e.target.closest('.pklook')) return;
    const p = s.items[+fig.dataset.i];
    if (p.dir) return;
    if (!multiple) s.chosen.clear();
    s.chosen.set(p.path, p);
    add(chosen());
  });
  peek.addEventListener('click', (e) => { if (!e.target.closest('img, pre')) closePeek(); });
  peek.addEventListener('dblclick', (e) => { if (e.target.closest('img, pre')) add(s.chosen.size ? chosen() : [s.items[s.at].path]); });
  peek.addEventListener('wheel', (e) => {
    if (e.target.closest('pre')) return;
    e.preventDefault(); moveTo(s.at + (e.deltaY + e.deltaX > 0 ? 1 : -1));
  }, { passive: false });

  // While open, its keys come first: nothing reaches the page underneath or a button.
  function keyup(e) { if (s.open && (e.key === ' ' || e.key === 'Enter')) { e.preventDefault(); e.stopImmediatePropagation(); } }
  function key(e) {
    if (!s.open) return;
    e.stopImmediatePropagation();
    const k = e.key;
    if (k === 'Escape') { e.preventDefault(); return s.peek ? closePeek() : close(); }
    if (k === ' ') { e.preventDefault(); return s.peek ? closePeek() : openPeek(); }
    if (k === 'Enter') {
      e.preventDefault();
      const p = s.items[s.at];
      if (!s.peek && p?.dir && !s.chosen.size) return enter(s.at);
      if (s.peek && !s.chosen.size && p) return add([p.path]);
      return add(chosen());
    }
    if ((k === 'Backspace' || (e.altKey && k === 'ArrowUp')) && s.folder === 'Other' && s.crumbs.length > 1 && !s.peek) {
      e.preventDefault(); return go('Other', s.crumbs[s.crumbs.length - 2].path);
    }
    if (k === 'Tab' && s.folders.length > 1 && !s.peek) {
      e.preventDefault();
      const i = s.folders.indexOf(s.folder), n = s.folders.length;
      return go(s.folders[(i + (e.shiftKey ? n - 1 : 1)) % n]);
    }
    if (k === 'x' || k === 'X') { e.preventDefault(); toggle(s.at); if (s.peek) drawPeek(); return; }
    const cols = s.peek ? 1 : columns();
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -cols, ArrowDown: cols }[k];
    if (step) { e.preventDefault(); return moveTo(s.at + step); }
    if (k === 'Home') { e.preventDefault(); return moveTo(0); }
    if (k === 'End') { e.preventDefault(); return moveTo(s.items.length - 1); }
    if (!(e.ctrlKey || e.metaKey)) e.preventDefault();
  }
  return { open, close, set, peekData, key, keyup, state: s, get isOpen() { return s.open; } };
}
