// Files for Claude: the Kūhaku picker (pick/picker.js) over the home screen, any kind of file.
// Chosen pictures are copied into ~/Pictures/ForClaude, other files named where they are, in the line (core.js).
// Classic scripts sharing one page, loaded in order by index.html: core, tabs, panels, pages, settings, pictures.
const pictures = makePicker({
  root: $('picks'),
  peek: $('peek'),
  title: 'Add a file',
  start: 'Recent',
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
window.setPicturePeek = (data) => pictures.peekData(data);
window.addEventListener('keydown', pictures.key, true);
window.addEventListener('keyup', pictures.keyup, true);
