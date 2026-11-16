// Pictures for Claude: the Kūhaku picker (pick/picker.js) over the home screen, for pictures only.
// Chosen pictures are copied into ~/Pictures/ForClaude and named in the line (core.js).
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages, pictures.
const pictures = makePicker({
  root: $('picks'),
  peek: $('peek'),
  start: 'Screenshots',
  send: (m) => native({ type: 'picture', ...m }),
  onClose: () => { document.body.classList.remove('picking'); focusInput(); },
});
function openPictures() {
  if (panel) closePanel();
  closeMenu();
  document.body.classList.add('picking');
  ask.blur();
  pictures.open();
}
window.setPictures = (listing) => pictures.set(listing);
window.addEventListener('keydown', pictures.key, true);
window.addEventListener('keyup', pictures.keyup, true);
