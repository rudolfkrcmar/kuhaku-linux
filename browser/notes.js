// Mado's comments: in Developer mode, point at anything on a page and leave a note on it
// for Claude. This runs in its own script world, apart from the page's scripts: the page can
// neither see these notes nor write one. Its layer lives in a closed shadow root, styled with
// a constructed sheet so a site's Content-Security-Policy cannot strip it.
(() => {
  if (window.madoNotes) return;
  let bridge = null;
  const queue = [];
  const post = (m) => { const s = JSON.stringify(m); bridge ? bridge.post(s) : queue.push(s); };
  const connect = () => new QWebChannel(qt.webChannelTransport, (ch) => {
    bridge = ch.objects.notes;
    queue.splice(0).forEach((s) => bridge.post(s));
  });
  try { connect(); } catch (e) { /* no channel: Mado outside developer use */ }

  let state = { on: false, picking: false, night: false, notes: [] };
  let host = null, root = null, layer = null, hilite = null, box = null, hover = null, frame = 0;

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .layer { position: fixed; inset: 0; pointer-events: none; z-index: 2147483647;
             font-family: 'Inter Variable', 'Inter', system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
    .hi { position: fixed; border: 1.5px solid #e0301e; background: rgba(224,48,30,.06); border-radius: 3px;
          pointer-events: none; transition: all .08s ease-out; }
    .hi i { position: absolute; left: -1.5px; bottom: 100%; margin-bottom: 4px; font-weight: 300; font-size: 11px; line-height: 1;
            font-style: normal; color: #fff; background: #e0301e; padding: 3px 6px; border-radius: 4px; white-space: nowrap; }
    .pin { position: fixed; width: 22px; height: 22px; margin: -11px 0 0 -11px; border-radius: 50%;
           background: #e0301e; color: #fff; font-weight: 500; font-size: 11px; line-height: 22px; text-align: center; cursor: pointer;
           pointer-events: auto; box-shadow: 0 0 0 2px #fff, 0 2px 8px rgba(0,0,0,.25); user-select: none; }
    .pin:hover { transform: scale(1.12); }
    .box { position: fixed; width: 300px; pointer-events: auto; border-radius: 12px; padding: 12px;
           background: #fff; color: #111; box-shadow: 0 0 0 1px rgba(17,17,17,.08), 0 10px 34px rgba(0,0,0,.18);
           font-weight: 300; font-size: 14px; line-height: 1.45; }
    .box.night { background: #1b1b1b; color: #ececec; box-shadow: 0 0 0 1px rgba(236,236,236,.1), 0 10px 34px rgba(0,0,0,.5); }
    .box .what { font-size: 11.5px; opacity: .55; margin: 0 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .box textarea { width: 100%; min-height: 64px; resize: vertical; border: 0; outline: 0; padding: 0;
                    background: transparent; color: inherit; font: inherit; caret-color: #e0301e; }
    .box .said { white-space: pre-wrap; margin: 0 0 4px; }
    .box .row { display: flex; gap: 6px; justify-content: flex-end; margin-top: 10px; }
    .box button { border: 0; background: none; color: inherit; opacity: .6; font: inherit; font-size: 13px;
                  padding: 5px 10px; border-radius: 8px; cursor: pointer; }
    .box button:hover { opacity: 1; background: rgba(127,127,127,.12); }
    .box button.go { opacity: 1; color: #e0301e; }
    .box.night button.go { color: #f0705a; }
    .box .hint { flex: 1; font-size: 11px; opacity: .4; align-self: center; }
  `;

  function build() {
    if (host && host.isConnected) return;
    host = document.createElement('mado-notes');
    root = host.attachShadow({ mode: 'closed' });
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(CSS);
    root.adoptedStyleSheets = [sheet];
    layer = document.createElement('div');
    layer.className = 'layer';
    root.appendChild(layer);
    (document.documentElement || document.body).appendChild(host);
  }

  // A selector that finds the element again: its id when that is unique, else a short path.
  function selector(el) {
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      if (el.id && document.querySelectorAll('#' + CSS_escape(el.id)).length === 1) {
        parts.unshift('#' + CSS_escape(el.id));
        break;
      }
      let part = el.localName;
      const p = el.parentElement;
      if (p) {
        const same = [...p.children].filter((c) => c.localName === el.localName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
      }
      parts.unshift(part);
      el = p;
    }
    return parts.join(' > ');
  }
  const CSS_escape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, '\\$&'));

  function label(el) {
    let t = el.localName;
    if (el.id) t += '#' + el.id;
    const c = [...el.classList].slice(0, 2).join('.');
    if (c) t += '.' + c;
    return t;
  }

  function find(n) {
    try { return document.querySelector(n.element.selector); } catch (e) { return null; }
  }

  function mine(e) { return e.composedPath().includes(host); }

  function drawHilite(el) {
    if (!el) { if (hilite) hilite.remove(); hilite = null; return; }
    if (!hilite) { hilite = document.createElement('div'); hilite.className = 'hi'; hilite.appendChild(document.createElement('i')); layer.appendChild(hilite); }
    const r = el.getBoundingClientRect();
    Object.assign(hilite.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
    hilite.firstChild.textContent = label(el);
    hilite.firstChild.style.display = r.top < 22 ? 'none' : '';
  }

  function drawPins() {
    if (!layer) return;
    layer.querySelectorAll('.pin').forEach((p) => p.remove());
    if (!state.on) return;
    state.notes.forEach((n, i) => {
      const el = find(n);
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (!r.width && !r.height) return;
      const at = n.element.at || [1, 0];
      const pin = document.createElement('div');
      pin.className = 'pin';
      pin.textContent = i + 1;
      pin.title = n.text;
      pin.style.left = Math.min(innerWidth - 14, Math.max(14, r.left + r.width * at[0])) + 'px';
      pin.style.top = Math.min(innerHeight - 14, Math.max(14, r.top + r.height * at[1])) + 'px';
      pin.addEventListener('click', (e) => { e.stopPropagation(); openNote(n, pin); });
      layer.appendChild(pin);
    });
  }

  function place(b, x, y) {
    layer.appendChild(b);
    const w = 300, h = b.offsetHeight || 140;
    b.style.left = Math.max(8, Math.min(innerWidth - w - 8, x + 12)) + 'px';
    b.style.top = Math.max(8, Math.min(innerHeight - h - 8, y + 12)) + 'px';
  }

  function closeBox() { if (box) box.remove(); box = null; }

  function make(html) {
    closeBox();
    box = document.createElement('div');
    box.className = 'box' + (state.night ? ' night' : '');
    box.innerHTML = html;
    // Keys typed here stay here: the page never hears them.
    ['keydown', 'keyup', 'keypress', 'input', 'mousedown', 'mouseup', 'click', 'pointerdown', 'pointerup', 'wheel']
      .forEach((t) => box.addEventListener(t, (e) => e.stopPropagation()));
    return box;
  }

  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // A new note on the element just clicked.
  function compose(el, e) {
    const r = el.getBoundingClientRect();
    const at = [r.width ? (e.clientX - r.left) / r.width : 0, r.height ? (e.clientY - r.top) / r.height : 0];
    const b = make(`<p class="what">${esc(label(el))}</p><textarea placeholder="What should change here?"></textarea>
      <div class="row"><span class="hint">Enter to keep · Esc</span><button class="no">Cancel</button><button class="go">Comment</button></div>`);
    const ta = b.querySelector('textarea');
    const save = () => {
      const text = ta.value.trim();
      closeBox();
      if (!text) { drawHilite(null); return; }
      drawHilite(el);  // left in place for the picture Mado takes of it
      const rr = el.getBoundingClientRect();
      post({
        type: 'save',
        text,
        title: document.title,
        url: location.href,
        element: {
          selector: selector(el),
          tag: label(el),
          text: (el.innerText || el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 200),
          html: el.outerHTML.slice(0, 400),
          at: at.map((v) => Math.round(v * 1000) / 1000),
        },
        rect: [rr.left, rr.top, rr.width, rr.height],
        viewport: [innerWidth, innerHeight],
        scroll: [Math.round(scrollX), Math.round(scrollY)],
      });
    };
    ta.addEventListener('keydown', (k) => {
      if (k.key === 'Enter' && !k.shiftKey) { k.preventDefault(); save(); }
      if (k.key === 'Escape') { k.preventDefault(); closeBox(); drawHilite(null); }
    });
    b.querySelector('.go').addEventListener('click', save);
    b.querySelector('.no').addEventListener('click', () => { closeBox(); drawHilite(null); });
    place(b, e.clientX, e.clientY);
    ta.focus();
  }

  // An existing note: read it, change it, mark it done or remove it.
  function openNote(n, pin) {
    const b = make(`<p class="what">${esc(n.element.tag)}</p><textarea>${esc(n.text)}</textarea>
      <div class="row"><button class="del">Delete</button><button class="done">Done</button><button class="go">Keep</button></div>`);
    const ta = b.querySelector('textarea');
    const keep = () => { const t = ta.value.trim(); closeBox(); if (t && t !== n.text) post({ type: 'edit', id: n.id, text: t }); };
    ta.addEventListener('keydown', (k) => {
      if (k.key === 'Enter' && !k.shiftKey) { k.preventDefault(); keep(); }
      if (k.key === 'Escape') { k.preventDefault(); closeBox(); }
    });
    b.querySelector('.go').addEventListener('click', keep);
    b.querySelector('.done').addEventListener('click', () => { closeBox(); post({ type: 'done', id: n.id }); });
    b.querySelector('.del').addEventListener('click', () => { closeBox(); post({ type: 'delete', id: n.id }); });
    const r = pin.getBoundingClientRect();
    place(b, r.left, r.top);
  }

  // Picking: the element under the pointer is outlined; a click opens a note on it.
  function onMove(e) {
    if (!state.picking || box || mine(e)) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (el && el !== host) { hover = el; drawHilite(el); }
  }
  function onClick(e) {
    if (!state.picking || mine(e)) return;
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    if (box) { closeBox(); drawHilite(null); return; }
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (el && el !== host) compose(el, e);
  }
  function swallow(e) {
    if (state.picking && !mine(e)) { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); }
  }
  function onKey(e) {
    if (e.key === 'Escape' && state.picking && !box) { e.preventDefault(); post({ type: 'pick', on: false }); }
  }
  addEventListener('mousemove', onMove, true);
  addEventListener('click', onClick, true);
  ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'auxclick', 'dblclick', 'submit'].forEach((t) => addEventListener(t, swallow, true));
  addEventListener('keydown', onKey, true);
  const redraw = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; drawPins(); if (state.picking && hover && !box) drawHilite(hover); });
  };
  addEventListener('scroll', redraw, true);
  addEventListener('resize', redraw);

  window.madoNotes = {
    set(s) {
      state = Object.assign(state, s);
      if (!state.on && !host) return;
      build();
      if (!state.picking) { drawHilite(null); hover = null; }
      if (!state.on) closeBox();
      document.documentElement.style.cursor = state.picking ? 'crosshair' : '';
      drawPins();
    },
    saved() { drawHilite(null); },
    reveal(id) {
      const n = state.notes.find((x) => x.id === id);
      const el = n && find(n);
      if (!el) return;
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => { const pins = layer.querySelectorAll('.pin'); const p = pins[state.notes.indexOf(n)]; if (p) openNote(n, p); }, 450);
    },
  };
  post({ type: 'ready' });
})();
