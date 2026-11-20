// Settings: the places on the left, under "Find a setting"; one place at a time on the right.
// A place is a title, one grey line saying how things stand, then groups of rows. A row is one setting:
// its name (a grey hint under it if it needs one) and its control, every control on one right edge
// (the codex, Controls 9.4). Classic script, loaded after pages.js: it uses CHOICE, OPT and chose.
let settingsCat = 'look', settingsQuery = '', acctAdding = false, unlockForget = null, settingsAsk = null, settingsTimer = 0;

// The places, in three groups. What is used most comes first in each.
const PLACES = [
  ['You', [['look', 'Look'], ['notices', 'Notices'], ['ai', 'AI'], ['privacy', 'Privacy']]],
  ['Computer', [['sound', 'Sound'], ['display', 'Display'], ['network', 'Network'], ['battery', 'Battery'], ['input', 'Keyboard and trackpad']]],
  ['System', [['time', 'Language and time'], ['storage', 'Storage'], ['updates', 'Updates'], ['about', 'About']]],
];
// Other names a place is asked for by (the menu, the battery card, keyboard shortcuts, older pages).
const PLACE_ALIAS = { general: 'look', appearance: 'look', power: 'battery', claude: 'ai', unlock: 'privacy', lock: 'privacy', notifications: 'notices', language: 'time', keys: 'input',
  trackpad: 'input', tools: 'about', wifi: 'network' };
const catOf = (s) => PLACE_ALIAS[s] || (PLACES.some(([, l]) => l.some(([id]) => id === s)) ? s : settingsCat);

// Opened from the menu, a card or a key: go to that place, with an empty search.
function settingsOpened(section) {
  if (section && section !== 'settings') settingsCat = catOf(section);
  settingsQuery = ''; acctAdding = false; unlockForget = null; settingsAsk = null;
  settingsFetch();
}
// Battery and network come from the cards' own readers; the battery is read again every 10 seconds.
function settingsFetch() {
  clearInterval(settingsTimer);
  if (settingsCat === 'battery') {
    native({ type: 'panel', action: 'battery' });
    settingsTimer = setInterval(() => {
      if (page === 'settings' && settingsCat === 'battery') native({ type: 'panel', action: 'battery' });
      else clearInterval(settingsTimer);
    }, 10000);
  }
  if (settingsCat === 'network') native({ type: 'panel', action: 'wifi' });
}

// The parts a row is made of.
const WORD = (label, attrs, ink) => `<button class="st-w${ink ? ' ink' : ''}" ${attrs}>${label}</button>`;
const SWITCH = (on, attrs, name) => `<button class="switch${on ? ' on' : ''}" role="switch" aria-checked="${!!on}" aria-label="${name}" ${attrs}></button>`;
const VAL = (v) => `<span class="st-v">${v}</span>`;
const METER = (v, tick, low) => `<div class="meter st-meter${low ? ' low' : ''}"><i style="width:${Math.max(0, Math.min(100, v))}%"></i>${tick ? `<b style="left:${tick}%"></b>` : ''}</div>`;
// A level: the name and the exact number share the row; the bar runs the full width beneath.
const LEVEL = (key, n, value, k) => value == null ? null : { n, k, c: `<span class="val" id="val-${key}">${value}%</span>`,
  below: `<div class="slider" data-slide="${key}"><i style="width:${value}%"></i></div>`, cls: 'level' };
// A choice whose words would not fit beside the name becomes a list.
const PICK = (key, options, current) => CHOICE(options.map(([v, label]) =>
  ({ label, sel: String(v) === String(current), attrs: `data-pref="${key}" data-v="${esc(v)}"` })), options.reduce((n, [, l]) => n + stripTags(l).length, 0) > 34);
const FIELD = (attrs, value, extra = '') => `<input class="st-in" ${attrs} value="${esc(value)}" spellcheck="false" autocomplete="off"${extra}>`;

function settingsPlaces(p) {
  const out = {}, a = p.about || {}, d = p.displays;
  const textSize = d && { n: 'Text size', h: 'How large words are drawn everywhere.', k: 'font scale zoom dpi bigger smaller',
    c: CHOICE([[84, 'Smaller'], [96, 'Default'], [120, 'Larger'], [144, 'Largest']]
      .map(([v, l]) => ({ label: l, sel: v === d.dpi, attrs: `data-disp='${JSON.stringify({ dpi: v })}'` }))) };
  const THEMES = { day: 'Paper by day, all the time.', night: 'Dark, all the time.', auto: 'Day by daylight, night after sunset.' };
  out.look = { title: 'Look', sub: THEMES[p.theme] || THEMES.auto, parts: [{ rows: [
    { n: 'Theme', k: 'dark light night day mode appearance colour', c: OPT('theme', [['day', 'Day'], ['night', 'Night'], ['auto', 'Follow the clock']], p.theme) },
    textSize] }] };

  out.ai = aiPlace(p);

  out.notices = noticesPlace(p);
  out.privacy = privacyPlace(p);

  const outCur = (p.outputs || []).find((o) => o.active) || {};
  out.sound = { title: 'Sound', sub: outCur.name ? 'Playing through ' + esc(outCur.name) + '.' : 'Volume and microphone.', parts: [
    { cap: 'Output', rows: [LEVEL('volume', 'Volume', p.volume, 'loud quiet audio speaker'),
      (p.outputs || []).length > 1 && { n: 'Play through', k: 'speakers headphones output device', c: PICK('output', p.outputs.map((o) => [o.id, esc(o.name)]), outCur.id) }] },
    { cap: 'Input', rows: [LEVEL('mic', 'Microphone', p.mic, 'mic input recording')] }] };

  const outs = (d && d.outputs) || [];
  const nice = (o) => o.builtin ? 'Built-in display' : o.name.replace(/-\d+$/, '').replace('HDMI', 'HDMI display').replace(/^DP$/, 'DisplayPort display');
  out.display = { title: 'Display', sub: outs.length > 1 ? `${outs.length} screens.` : outs[0] ? `${outs[0].current.replace('x', ' × ')}, ${outs[0].builtin ? 'built in' : 'one screen'}.` : 'The screen.', parts: [
    { rows: [LEVEL('brightness', 'Brightness', p.brightness, 'screen light dim bright')] },
    { cap: outs.length > 1 ? 'Screens' : 'Resolution', rows: outs.map((o) => ({ n: outs.length > 1 ? esc(nice(o)) : 'Resolution', k: 'resolution screen monitor size pixels',
      c: CHOICE(o.modes.map((m) => ({ label: m.replace('x', ' × '), sel: m === o.current, attrs: `data-disp='${JSON.stringify({ output: o.name, mode: m })}'` }))) }))
      .concat(outs.length > 1 ? [{ n: 'Arrangement', h: 'Where the other screen stands.', k: 'external monitor mirror extend',
        c: '<span class="st-words">' + [['left', 'Left of laptop'], ['right', 'Right of laptop'], ['mirror', 'Mirror'], ['outer', 'Only external'], ['inner', 'Only laptop']]
          .map(([v, l]) => WORD(l, `data-disp='${JSON.stringify({ arrange: v })}'`)).join('') + '</span>' }] : []) }] };

  out.network = networkPlace();
  out.battery = batteryPlace(p);

  const tp = p.trackpad;
  out.input = { title: 'Keyboard and trackpad', sub: 'Screenshots and recordings are a key away.', parts: [
    tp && { cap: 'Trackpad', rows: [
      { n: 'Tap to click', h: 'A light tap is a click.', k: 'touchpad mouse', c: SWITCH(+tp.tap === 1, `data-switch="tap" data-v="${+tp.tap === 1 ? 0 : 1}"`, 'Tap to click') },
      { n: 'Scrolling', h: +tp.natural === 1 ? 'The page follows your fingers, like a Mac.' : 'The page moves against your fingers.', k: 'touchpad scroll direction natural reverse',
        c: OPT('natural', [[1, 'Natural'], [0, 'Classic']], tp.natural) },
      { n: 'Speed', k: 'touchpad pointer mouse cursor', c: OPT('speed', [[-0.3, 'Slow'], [0, 'Medium'], [0.3, 'Fast'], [0.6, 'Fastest']], tp.speed) }] },
    { cap: 'Screenshots and recording', rows: [['Print', 'Pick a part of the screen'], ['Shift + Print', 'The whole screen'], ['Ctrl + Print', 'Copy a part, without saving'],
      ['Ctrl + Shift + 5', 'Record the screen, with your microphone', 'Again to stop.'], ['Ctrl + Shift + 6', 'Record a part of the screen']]
      .map(([key, n, h]) => ({ n, h, k: 'screenshot capture video shortcut keys', c: `<span class="st-keys">${key}</span>` })) }] };

  const tm = p.time, ly = p.layouts || { on: [], all: {} };
  const lyName = (x) => esc(ly.all[x] || x);
  const typing = { cap: 'Typing', rows: [
    { n: 'Kūhaku speaks English', h: 'The AI answers in the language you write in.', k: 'language english translate locale', c: '' },
    ...ly.on.map((x, i) => ({ n: lyName(x), k: 'keyboard layout language typing input',
      h: i === 0 ? (ly.on.length > 1 ? 'Used first. Alt + Shift moves to the next one.' : 'The keyboard layout.') : '',
      c: (i ? WORD('Use first', `data-pref="layout_first" data-v="${x}"`) : '') + (ly.on.length > 1 ? WORD('Remove', `data-pref="layout_remove" data-v="${x}"`) : '') })),
    ly.on.length < 4 && { n: 'Add a layout', h: 'Another language to type in.', k: 'keyboard layout language typing input',
      c: CHOICE(Object.entries(ly.all).filter(([x]) => !ly.on.includes(x)).map(([x, l]) => ({ label: esc(l), sel: false, attrs: `data-pref="layout_add" data-v="${x}"` })), true)
        .replace('<span class="lv">Choose</span>', '<span class="lv">Choose a language</span>') }] };
  const now = new Date();
  out.time = { title: 'Language and time', sub: tm ? `${now.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}, ${esc(tm.zone || '')}.` : 'Typing and the clock.', parts: [typing,
    tm && { cap: 'Time', rows: [
      { n: 'Set the time automatically', h: tm.auto ? (tm.synced ? 'In step with the internet.' : 'Not yet checked with the internet.') : 'Set by hand.', k: 'clock sync internet ntp date',
        c: SWITCH(tm.auto, `data-switch="time_auto" data-v="${tm.auto ? 0 : 1}"`, 'Set the time automatically') },
      !tm.auto && { n: 'Date and time', k: 'clock', c: FIELD('type="datetime-local" id="timeSet"', tm.now) + WORD('Set', 'data-act2="time-set"', true) },
      { n: 'Time zone', h: 'Type a city, like Europe/Prague.', k: 'timezone clock city region date',
        c: FIELD('id="timeZone" list="zones"', tm.zone) + `<datalist id="zones">${(tm.zones || []).map((z) => `<option value="${z}">`).join('')}</datalist>` }] }] };

  if (a.disk) {
    const [free, total] = a.disk, used = Math.round(100 * (total - free) / Math.max(total, 1));
    out.storage = { title: 'Storage', sub: `${free} GB free of ${total} GB.`, parts: [{ rows: [
      { n: 'Used', h: `${total - free} GB of ${total} GB`, k: 'disk space free full', c: VAL(used + '%'), below: METER(used, 0, used >= 95) }] }] };
  }

  const u = p.updates || {}, cod = p.codecs || {};
  out.updates = { title: 'Updates', sub: u.auto ? 'Security fixes install by themselves.' : 'Updates are not automatic.', parts: [{ rows: [
    { n: 'Everything up to date', h: u.checked ? 'Last checked ' + when(u.checked) + '.' : 'Not checked yet.', k: 'upgrade security apt software',
      c: WORD(u.running ? 'Updating…' : 'Update now', 'data-pref-btn="update-now"', true) },
    { n: 'Video formats', h: cod.added ? 'All, including H.264 and AAC.' : 'Free formats only. H.264, AAC and others are covered by patents in some countries; adding them is your choice.',
      k: 'codecs h264 aac mp4 video audio patents', c: cod.added ? '' : WORD(cod.running ? 'Adding…' : 'Add', 'data-pref-btn="add-codecs"') },
    { n: 'First steps', h: 'Your AI, how freely it works and video formats, on one page.', k: 'welcome setup start', c: WORD('Open', 'data-act2="welcome"') }] }] };

  const tools = (appList || []).filter((x) => x.system);
  out.about = { title: 'About', sub: 'Kūhaku on ' + esc(a.system || 'Linux') + (a.computer ? ', on a ' + esc(a.computer) : '') + '.', parts: [
    { rows: [['Computer', a.computer], ['System', a.system && a.system + ' · Kūhaku'], ['Processor', a.processor], ['Memory', a.memory && a.memory + ' GB'],
      ['Storage', a.disk && `${a.disk[0]} GB free of ${a.disk[1]} GB`], ['Kernel', a.kernel]]
      .filter(([, v]) => v).map(([n, v]) => ({ n, k: 'about version hardware', c: VAL(esc(v)) })) },
    tools.length && { cap: 'System tools', k: 'tools apps', raw: '<div class="tools">' + tools.map((t) => WORD(esc(t.name), `data-act2="tool" data-v="${esc(t.id)}"`)).join('') + '</div>' }] };
  return out;
}

// Notices: quiet for a while, apps that never show a card, cards without their words.
function noticesPlace(p) {
  const n = p.notices || { quiet_until: 0, previews: 1, show_s: 6, muted: [], apps: [] };
  const q = n.quiet_until || 0, quiet = q === -1 || q > (n.now || Date.now() / 1000);
  const until = q > 0 ? new Date(q * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
  const qv = !quiet ? 'off' : q === -1 ? 'always' : q - (n.now || 0) <= 3600 ? 'hour' : 'morning';
  const apps = [...new Set([...(n.apps || []), ...(n.muted || [])])];
  return { title: 'Notices', sub: !quiet ? 'Cards come in at the top right, and leave by themselves.' : q === -1 ? 'Quiet. Only urgent notices show.' : `Quiet until ${until}. Only urgent notices show.`,
    parts: [
      { rows: [
        { n: 'Quiet', h: 'Notices wait in the list (F9). Urgent ones, like a low battery, still show.', k: 'do not disturb dnd silence focus mute',
          c: OPT('notices_quiet', [['off', 'Off'], ['hour', 'An hour'], ['morning', 'Until 7:00'], ['always', 'Always']], qv) },
        { n: 'Show what they say', h: n.previews ? 'A card shows its title and a line or two.' : 'A card shows only the app. The list keeps the words.', k: 'preview content private privacy',
          c: SWITCH(!!n.previews, `data-switch="notices_previews" data-v="${n.previews ? 0 : 1}"`, 'Show what they say') },
        { n: 'A card stays for', k: 'duration time seconds', c: OPT('notices_show_s', [[4, '4 s'], [6, '6 s'], [10, '10 s']], n.show_s) },
        { n: 'The list', h: 'What came in, newest first. F9 opens it.', k: 'history clear', c: WORD('Open', 'data-act2="notices-open"') + WORD('Clear', 'data-act2="notices-clear"') }] },
      { cap: 'Apps', rows: apps.length ? apps.map((x) => ({ n: esc(x), h: (n.muted || []).includes(x) ? 'Kept in the list, never a card.' : '', k: 'app mute notifications',
        c: SWITCH(!(n.muted || []).includes(x), `data-switch="notices_mute" data-v="${esc(x)}|${(n.muted || []).includes(x) ? 0 : 1}"`, 'Cards from ' + esc(x)) }))
        : [{ n: 'No app has sent a notice yet', h: 'Each will be listed here, to be quieted one by one.', c: '' }] }] };
}

// Privacy: the lock, the phone's camera, what the AI may do, and what is kept.
function privacyPlace(p) {
  const pv = p.privacy || { eye: 1, answers: 0, picture: false }, pw = p.power || {}, ul = p.unlock;
  const mode = p.permission_mode === 'bypassPermissions' ? 'Anything, and asks before what cannot be undone.' : 'Careful: it reads and edits files, but runs no commands.';
  const ask = (key, label, word, hint, sure) => settingsAsk === key
    ? { n: label, h: sure, c: WORD(word, `data-act2="do" data-v="${key}"`, true) }
    : { n: label, h: hint, c: WORD(word, `data-act2="ask" data-v="${key}"`) };
  const parts = [
    { cap: 'Lock', rows: [
      pw.screen_off != null && { n: 'Lock when the screen goes off', h: pw.screen_off ? `After ${pw.screen_off} minutes without a touch. Closing the lid follows its own choice, in Battery.` : 'The screen never goes off by itself; choose when, in Battery.',
        k: 'lock screen password security idle', c: SWITCH(!!pw.lock_off, `data-switch="lock_off" data-v="${pw.lock_off ? 0 : 1}"`, 'Lock when the screen goes off') },
      { n: 'Lock now', h: 'Ctrl + Alt + L, from anywhere.', k: 'lock screen password', c: WORD('Lock', 'data-act2="do" data-v="lock-now"') }] },
  ];
  if (ul && ul.reader) {
    const NICE = (f) => f.replace('-finger', '').replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
    const fs = ul.fingers || [];
    const rows = fs.map((f) => ({ n: NICE(f), k: 'fingerprint finger unlock',
      h: unlockForget === f ? 'Press again to forget this finger.' : '',
      c: unlockForget === f ? WORD('Forget', `data-act2="unlock" data-what="forget-finger" data-v="${f}"`, true)
        : WORD('Forget', `data-act2="unlock-ask" data-v="${f}"`) }));
    if (!fs.length) rows.push({ n: 'Set up', h: 'Three fingers, one at a time. About two minutes.', k: 'fingerprint unlock',
      c: WORD('Start', 'data-act2="unlock" data-what="guided"', true) });
    if (fs.length < 10) rows.push({ n: fs.length ? 'Add a finger' : 'Teach one finger', k: 'fingerprint unlock',
      h: fs.length && fs.length < 3 ? 'Three or more make it much more reliable.' : '',
      c: WORD(fs.length ? 'Add' : 'Teach', 'data-act2="unlock" data-what="add-finger"', !!fs.length && fs.length < 3) });
    if (fs.length > 1) rows.push({ n: 'Forget all fingers', k: 'fingerprint unlock',
      h: unlockForget === 'all' ? 'Press again to forget all of them. The password still opens the lock.' : 'The password still opens the lock.',
      c: unlockForget === 'all' ? WORD('Forget all', 'data-act2="unlock" data-what="forget-fingers"', true) : WORD('Forget all', 'data-act2="unlock-ask"') });
    parts.push({ cap: 'Fingerprints', act: `<span>${fs.length >= 3 ? 'Any of these opens the lock' : 'The reader is the power button'}</span>`, rows });
  }
  parts.push({ cap: 'The AI', rows: [
    { n: 'May do', h: mode, k: 'permissions safety careful commands claude', c: WORD('Change', 'data-cat="ai"') },
    { n: 'The safety net', h: 'Whatever it may do, it never wipes disks, deletes whole folders outright, force-pushes over main, sends secrets over the network or adds login keys.', k: 'guard safety protection', c: '' },
    { n: 'Look through the phone', h: pv.eye ? 'When you ask, it takes one picture with your phone, if the camera page is open there. Nothing is streamed.' : 'Off. The AI cannot ask your phone for a picture.',
      k: 'camera phone eye picture', c: SWITCH(!!pv.eye, `data-switch="privacy_eye" data-v="${pv.eye ? 0 : 1}"`, 'Look through the phone') }] });
  parts.push({ cap: 'Kept on this computer', rows: [
    ask('forget-answers', 'Answers', 'Forget', pv.answers ? `${pv.answers} answers, the last 300 at most. They come back with Alt + Left.` : 'None kept.', 'Press again to forget every answer. This cannot be undone.'),
    pv.picture && ask('forget-picture', 'The phone’s last picture', 'Forget', 'Only the last one is kept.', 'Press again to delete it.')] });
  return { title: 'Privacy', sub: 'Who can open the lock, what the AI may do, and what is kept here.', parts };
}

// AI: what the home screen asks first, then the other apps, voice, and the accounts.
function aiPlace(p) {
  const sets = p.ai_sets || [], signing = p.ai_signing || [], accts = p.accounts || [],
    ready = accts.filter((x) => x.signedIn), home = accts.find((x) => x.active) || {},
    KIND = [['claude', 'Claude', 'Claude Code, by Anthropic.'], ['codex', 'OpenAI', 'Codex, by OpenAI.'], ['gemini', 'Gemini', 'Gemini CLI, by Google.'],
      ['grok', 'Grok', 'Grok Build, by xAI.'], ['copilot', 'Copilot', 'Copilot CLI, by GitHub.']];
  const slot = (set, k) => { const x = set.slots[k];
    if (x && x.signedIn) return '<span class="st-on">Signed in</span>';
    if (x && signing.includes(x.id)) return '<span class="st-wait"><span class="dot"></span>Signing in</span>';
    return WORD('Sign in', `data-act2="ai-signin" data-set="${esc(set.name)}" data-v="${k}"`); };
  const mode = p.permission_mode === 'default' ? 'acceptEdits' : p.permission_mode;
  const L = p.ai_local || {};
  const homeUses = (p.account_for || {}).home || '';
  const homeName = homeUses ? ((ready.find((x) => x.id === homeUses) || {}).label || 'another AI') : (home.set ? home.set + ' Claude' : 'Claude');
  const parts = [
    { cap: 'Home screen', rows: [
      { n: 'Home uses', h: 'The AI the home screen asks.', k: 'assistant provider openai gemini grok copilot',
        c: PICK('account_home', [['', 'Claude']].concat(ready.filter((x) => x.kind !== 'claude').map((x) => [x.id, esc(x.label)])), homeUses) },
      sets.length > 1 && { n: 'Claude account', k: 'personal work subscription', c: OPT('home_set', sets.map((x) => [x.name, esc(x.name)]), home.set) },
      { n: 'Model', k: 'opus sonnet haiku claude', c: OPT('model', [['default', 'Default'], ['opus', 'Opus'], ['sonnet', 'Sonnet'], ['haiku', 'Haiku']], p.model) },
      { n: 'May do', h: mode === 'acceptEdits' ? 'Reads and edits files, but runs no commands.' : 'Edits, runs and installs on its own. Asks before anything that cannot be undone.',
        k: 'permissions safety careful commands', c: OPT('permission_mode', [['bypassPermissions', 'Anything'], ['acceptEdits', 'Careful']], mode) },
      { n: 'Jobs at once', h: 'How many questions are worked on together.', k: 'parallel tabs memory',
        c: CHOICE([['auto', `Best for this computer (${p.jobs_auto || 2})`]].concat(['2', '3', '4', '6', '8'].filter((n) => n !== String(p.jobs_auto)).map((n) => [n, n]))
          .concat([['none', 'No limit, memory decides']]).map(([v, label]) => ({ label, sel: String(v) === String(p.max_jobs || 'auto'), attrs: `data-pref="max_jobs" data-v="${v}"` }))) }] },
    { cap: 'Other apps', rows: [['mail', 'Mail uses', 'Tegami: sorting, summaries and drafts.'], ['dev', 'Dev tools use', 'Tsukuru and the terminal.']].map(([k, n, h]) => ({ n, h, k: 'tegami tsukuru account',
      c: PICK('account_' + k, [['', 'Same as home']].concat(ready.map((x) => [x.id, esc(x.label)])), (p.account_for || {})[k] || '') })) },
    { cap: 'Voice', rows: [
      { n: 'The microphone button', k: 'voice speech talk', c: OPT('mic_button', [['talk', 'Talk with Claude'], ['dictate', 'Dictate']], p.mic_button) },
      { n: 'Dictation', h: 'Offline, on this computer.', k: 'voice speech whisper transcribe', c: OPT('dictation_model', [['auto', 'Best'], ['base', 'Fast'], ['small', 'Accurate']], p.dictation_model) }] },
  ];
  sets.forEach((set) => parts.push({ cap: esc(set.name) + ' accounts',
    act: set.removable ? WORD('Remove', `data-act2="ai-set-remove" data-v="${esc(set.name)}"`) : '',
    rows: KIND.map(([k, n, h]) => ({ n, h, k: 'account sign in subscription', c: slot(set, k) })) }));
  parts.push({ cap: 'More', rows: [
    { n: 'On this computer', k: 'local ollama offline model',
      h: L.running && L.models.length ? 'A model that runs here, through Ollama.' : L.running ? 'Ollama runs, without a model yet.'
        : L.installed ? 'Ollama is here but not running.' : 'A model that runs here, without the cloud, through Ollama.',
      c: L.running && L.models.length ? PICK('local_model', L.models.map((m) => [m, esc(m)]), L.model)
        : L.running ? '<a class="st-w" href="https://ollama.com/library">Pick a model</a>' : L.installed ? '' : '<a class="st-w" href="https://ollama.com/download">Get Ollama</a>' },
    { n: 'Add a set of accounts', h: 'Another Claude, OpenAI, Gemini, Grok and Copilot, like Work or Client.', k: 'account new',
      c: acctAdding ? FIELD('id="acctNew" placeholder="Name of the set"', '') : WORD('Add', 'data-act2="ai-set-add"') }] });
  return { title: 'AI', sub: `The home screen asks ${esc(homeName)}.`, parts };
}

function networkPlace() {
  const d = wifi.data, on = d ? d.enabled : true, cur = d && d.enabled ? (d.networks || []).find((n) => n.active) : null;
  return { title: 'Network', sub: !d ? 'Looking for networks.' : !d.enabled ? 'Wi‑Fi is off.' : cur ? `Connected to ${esc(cur.ssid)}.` : 'Not connected.', parts: [
    { rows: [
      { n: 'Wi‑Fi', h: cur ? `${cur.band} · ${quality(cur.signal)}` : '', k: 'wifi wireless internet', c: SWITCH(on !== !!wifi.toggling, 'data-act2="wifi-radio"', 'Wi‑Fi') },
      { n: 'Networks', h: 'Join one, change a password or forget it.', k: 'wifi wireless join password', c: WORD('Choose', 'data-act2="wifi"', on && !cur) },
      { n: 'VPN', k: 'private tunnel', c: WORD('Open', 'data-act2="wifi" data-v="vpn"') },
      { n: 'Bluetooth', k: 'headphones devices pair', c: WORD('Open', 'data-act2="wifi" data-v="bt"') }] }] };
}

// Battery (Power on a computer without one): the charge and where it will stop, then sleep and the lid.
function batteryPlace(p) {
  const b = battery, pw = p.power, has = p.about && p.about.battery != null, parts = [];
  let sub = 'Sleep, the screen and the lid.';
  if (has) {
    const lvl = Math.round((b && b.level) ?? p.about.battery), lim = b && b.limit, plugged = b && ['charging', 'pending-charge', 'fully-charged'].includes(b.state);
    const head = !b ? 'Charge' : b.state === 'charging' ? (lim && lim < 100 ? `Charging to ${lim}%` : 'Charging') : b.state === 'fully-charged' ? 'Full'
      : b.state === 'pending-charge' ? 'Plugged in, not charging' : 'On battery';
    const hint = b && b.state === 'charging' && b.toFull ? `Full in ${dur(b.toFull)}` : b && b.state === 'discharging' && b.toEmpty ? `${dur(b.toEmpty)} left` : '';
    const rows = [{ n: head, h: hint, k: 'battery charge level percent', c: VAL(lvl + '%'),
      below: METER(lvl, plugged && lim && lim < 100 && lvl < lim ? lim : 0, !plugged && lvl <= 10) }];
    sub = lim == null ? (b ? (plugged ? 'Plugged in.' : 'On battery.') : 'Reading the battery.')
      : b.care === 'full' ? 'Kūhaku fills it to 100% every time.'
      : b.care === 'once' ? 'Filling to 100% this once, then back to 80%.'
      : lim >= 100 && b.why?.why === 'trip' ? `Filling to 100% for ${b.why.trip ? '“' + esc(b.why.trip) + '”' : 'your trip'}, then back to 80%.`
      : lim >= 100 && b.why?.why === 'travelling' ? 'Filling to 100%: you seem to be travelling.'
      : lim >= 100 ? 'Filling to 100%: you usually head off around now.'
      : 'Kūhaku stops at 80% to protect it.';
    if (lim != null) {
      rows.push({ n: 'Protect the battery', h: 'Stops at 80%, and fills up before you usually head off.', k: 'charge limit care health 80',
        c: SWITCH(b.care !== 'full', `data-care="${b.care === 'full' ? 'care' : 'full'}"`, 'Protect the battery') });
      if (b.care !== 'full') rows.push({ n: 'Fill for a trip', h: b.care === 'once' ? 'Filling to 100% this once.' : 'To 100% once, then back to 80% after you head off.', k: 'charge full travel 100',
        c: b.care === 'once' ? WORD('Stop at 80%', 'data-care="care"') : WORD('Fill to 100%', 'data-care="once"') });
    }
    parts.push({ cap: 'Charge', rows });
  }
  parts.push({ cap: 'Power', rows: [
    p.profile && { n: 'Power mode', h: { 'power-saver': 'Quieter and longer on battery.', balanced: 'Fast when needed, frugal otherwise.', performance: 'As fast as it goes. Warmer and louder.' }[p.profile] || '',
      k: 'performance saver balanced fan speed', c: OPT('profile', [['power-saver', 'Saver'], ['balanced', 'Balanced'], ['performance', 'Performance']], p.profile) },
    pw && { n: 'Screen off after', k: 'display blank idle timeout', c: OPT('screen_off', [[2, '2 min'], [5, '5 min'], [10, '10 min'], [0, 'Never']], pw.screen_off) },
    pw && { n: 'Sleep after', k: 'suspend idle timeout', c: OPT('sleep_after', [[15, '15 min'], [30, '30 min'], [60, '1 hour'], [0, 'Never']], pw.sleep_after) },
    pw && { n: 'When the lid closes', k: 'lid suspend lock sleep', c: OPT('lid', [[1, 'Sleep'], [3, 'Lock'], [0, 'Screen off']], pw.lid) }] });
  if (has && b) {
    const c = chart(b.history, b.now);
    if (c) parts.push({ cap: 'Last hours', k: 'history chart', raw: c });
    const sm = (t) => `<small>${t}</small>`;
    parts.push({ cap: 'Health', rows: [
      b.health != null && ['Health', `${b.health}%${sm('of original capacity')}`],
      b.cycles && ['Charge cycles', b.cycles],
      b.rate != null && [b.state === 'charging' ? 'Charging at' : 'Using', `${b.rate.toFixed(1)} W`],
      b.energy != null && ['Energy', `${b.energy.toFixed(1)}${sm('of ' + (b.full?.toFixed(1) ?? '?') + ' Wh')}`],
      b.design && ['Designed for', `${b.design.toFixed(1)} Wh`],
      b.temperature && ['Temperature', `${b.temperature.toFixed(1)} °C`],
      b.name && ['Battery', esc(b.name) + (b.technology ? sm(esc(b.technology)) : '')],
    ].filter(Boolean).map(([n, v]) => ({ n, k: 'battery health wear cycles', c: VAL(v) })) });
  }
  return { title: has ? 'Battery' : 'Power', sub, parts };
}

const stripTags = (h) => String(h || '').replace(/<[^>]*>/g, ' ');
function rowHtml(r) {
  return `<div class="st-row${r.cls ? ' ' + r.cls : ''}"><div class="st-k"><span class="st-n">${r.n}</span>${r.h ? `<span class="st-h">${r.h}</span>` : ''}</div>` +
    `<div class="st-c">${r.c || ''}</div>${r.below ? `<div class="st-below">${r.below}</div>` : ''}</div>`;
}
function partHtml(pt) {
  const rows = (pt.rows || []).filter(Boolean);
  if (!rows.length && !pt.raw) return '';
  return `<section class="st-part">${pt.cap ? `<div class="st-cap"><span>${pt.cap}</span>${pt.act || ''}</div>` : ''}` +
    (pt.raw ? `<div class="st-raw">${pt.raw}</div>` : '') + rows.map(rowHtml).join('') + '</section>';
}

function renderSettings() {
  const pg = $('page'), p = prefs, act = document.activeElement;
  const keep = { top: pg.scrollTop, find: act && act.id === 'setFind' ? act.selectionStart : null,
    field: act && act.id === 'acctNew' ? act.value : null };
  const all = p ? settingsPlaces(p) : {};
  const places = PLACES.map(([g, list]) => [g, list.filter(([id]) => !p || all[id])]).filter(([, l]) => l.length);
  if (p && !all[settingsCat]) settingsCat = 'look';
  const nav = `<label class="sfind"><input id="setFind" placeholder="Find a setting" spellcheck="false" autocomplete="off" value="${esc(settingsQuery)}"></label>` +
    places.map(([g, list]) => `<div class="sgroup">${g}</div>` + list.map(([id, label]) =>
      `<button class="splace${id === settingsCat && !settingsQuery.trim() ? ' sel' : ''}" data-cat="${id}">${id === 'battery' && all.battery ? all.battery.title : label}</button>`).join('')).join('');
  let body;
  const q = settingsQuery.trim().toLowerCase();
  if (!p) body = '<h1>Settings</h1><div class="p-wait st-sub"><span class="dot"></span>Reading settings…</div>';
  else if (q) {
    // Every row whose name, hint or kind of thing matches, under the name of its place.
    const words = q.split(/\s+/);
    const hits = places.flatMap(([, list]) => list).map(([id, label]) => {
      const pl = all[id], rows = pl.parts.filter(Boolean).flatMap((pt) => (pt.rows || []).filter(Boolean).map((r) => ({ r, pt })))
        .filter(({ r, pt }) => { const t = stripTags([label, pl.title, pt.cap, r.n, r.h, r.k].join(' ')).toLowerCase(); return words.every((w) => t.includes(w)); });
      return [id, label, rows.map(({ r }) => r)];
    }).filter(([, , rows]) => rows.length);
    const n = hits.reduce((s, [, , r]) => s + r.length, 0);
    body = n ? `<h1>${n} ${n === 1 ? 'setting' : 'settings'}</h1><p class="st-sub">For “${esc(settingsQuery.trim())}”.</p>` +
      hits.map(([id, label, rows]) => partHtml({ cap: label, act: WORD('Open', `data-cat="${id}"`), rows })).join('')
      : `<h1>Nothing by that name</h1><p class="st-sub">Press Enter to ask Claude to change it.</p>`;
  } else {
    const pl = all[settingsCat];
    body = `<h1>${pl.title}</h1><p class="st-sub">${pl.sub}</p>` + pl.parts.filter(Boolean).map(partHtml).join('');
  }
  pg.innerHTML = `<div class="inner settings"><div class="scols"><nav class="snav">${nav}</nav><div class="sbody">${body}</div></div></div>`;
  pg.scrollTop = keep.top;
  if (keep.find != null) { const f = $('setFind'); f.focus(); f.setSelectionRange(keep.find, keep.find); }
  const nf = $('acctNew'); if (nf) { nf.value = keep.field || ''; nf.focus(); }
}

// Settings' own presses; the shared ones (a choice, a list, a level, a display) are in pages.js.
$('page').addEventListener('click', (e) => {
  if (page !== 'settings') return;
  const cat = e.target.closest('[data-cat]');
  if (cat) { e.stopPropagation(); settingsCat = cat.dataset.cat; settingsQuery = ''; acctAdding = false; unlockForget = null; settingsFetch(); renderSettings(); $('page').scrollTop = 0; return; }
  const sw = e.target.closest('[data-switch]');
  if (sw) { e.stopPropagation(); sw.classList.toggle('on'); sw.setAttribute('aria-checked', sw.classList.contains('on'));
    return native({ type: 'panel', action: 'pref', key: sw.dataset.switch, value: sw.dataset.v }); }
  const care = e.target.closest('[data-care]');
  if (care && battery) { e.stopPropagation(); battery.care = care.dataset.care; renderSettings(); return native({ type: 'panel', action: 'charge', care: care.dataset.care }); }
  const t = e.target.closest('[data-act2]'); if (!t) return;
  const a = t.dataset.act2;
  if (a === 'wifi-radio') { e.stopPropagation(); wifi.toggling = true; renderSettings(); return native({ type: 'panel', action: 'wifi-radio', on: !(wifi.data ? wifi.data.enabled : true) }); }
  if (a === 'wifi') { e.stopPropagation(); closePage(); openPanel('wifi'); if (t.dataset.v) { wifi.tab = t.dataset.v; renderWifi(); } return; }
  if (a === 'ai-set-add') { e.stopPropagation(); acctAdding = true; renderSettings(); }
  if (a === 'unlock-ask') { e.stopPropagation(); unlockForget = t.dataset.v || 'all'; renderSettings();
    setTimeout(() => { unlockForget = null; if (page === 'settings') renderSettings(); }, 5000); }
  if (a === 'unlock') unlockForget = null;
  if (a === 'ask') { e.stopPropagation(); settingsAsk = t.dataset.v; renderSettings();
    setTimeout(() => { settingsAsk = null; if (page === 'settings') renderSettings(); }, 5000); }
  if (a === 'do') { e.stopPropagation(); settingsAsk = null; native({ type: 'panel', action: 'pref', key: t.dataset.v, value: 1 }); }
  if (a === 'notices-open') { e.stopPropagation(); closePage(); openPanel('notices'); }
  if (a === 'notices-clear') { e.stopPropagation(); native({ type: 'panel', action: 'notices-clear' }); t.textContent = 'Cleared'; }
}, true);
$('page').addEventListener('input', (e) => {
  if (e.target.id === 'setFind') { settingsQuery = e.target.value; renderSettings(); }
});
$('page').addEventListener('keydown', (e) => {
  if (e.target.id === 'setFind') {
    if (e.key === 'Escape' && settingsQuery) { e.stopPropagation(); settingsQuery = ''; renderSettings(); }
    if (e.key === 'Enter' && settingsQuery.trim() && !$('page').querySelector('.st-row')) {
      const q = 'Change this setting: ' + settingsQuery.trim();
      closePage(); setAsk(''); show('working', q); native({ type: 'ask', text: q });
    }
  }
  if (e.target.id === 'acctNew') {
    if (e.key === 'Escape') { e.stopPropagation(); acctAdding = false; renderSettings(); }
    if (e.key === 'Enter' && e.target.value.trim()) { acctAdding = false; native({ type: 'panel', action: 'ai-set-add', name: e.target.value.trim() }); renderSettings(); }
  }
});
