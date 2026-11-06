// Drive a Mado tab over Chromium DevTools: node cdp.mjs <targetId|url-substring> <method> [json-params]
// e.g. node cdp.mjs tagmanager Runtime.evaluate '{"expression":"document.title","returnByValue":true}'
const [, , which, method, params = '{}'] = process.argv;
const tabs = await (await fetch('http://127.0.0.1:9222/json')).json();
const t = tabs.find((x) => x.id === which) || tabs.find((x) => x.type === 'page' && x.url.includes(which));
if (!t) { console.error('no tab', which); process.exit(1); }
const ws = new WebSocket(t.webSocketDebuggerUrl);
ws.onopen = () => ws.send(JSON.stringify({ id: 1, method, params: JSON.parse(params) }));
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id === 1) {
  if (method === 'Page.captureScreenshot' && m.result) { (await_write(m.result.data)); }
  else console.log(JSON.stringify(m.result ?? m.error, null, 1).slice(0, 20000));
  ws.close(); } };
function await_write(b64) { import('fs').then((fs) => { fs.writeFileSync('/tmp/cdp.png', Buffer.from(b64, 'base64')); console.log('/tmp/cdp.png'); }); }
