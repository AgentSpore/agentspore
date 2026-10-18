import { copies } from './i18n.js';
import { renderWallpaper } from './render.js';

const language = document.querySelector('#language');
const model = document.querySelector('#model');
const mode = document.querySelector('#mode');
const contact = document.querySelector('#contact');
const message = document.querySelector('#message');
const canvas = document.querySelector('#wallpaper');
const download = document.querySelector('#download');
const error = document.querySelector('#error');

function recordEvent(event, snapshot = { model: model.value, lang: language.value }) {
  if (!['form_open', 'png_download'].includes(event)
      || !['iphone-x', 'iphone-xr', 'pixel-8'].includes(snapshot.model)
      || !['en', 'ru'].includes(snapshot.lang)) return;
  // D2.6: Metrics are best effort; failure must never interrupt local PNG export.
  try {
    void fetch('/_agentspore/events', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, model: snapshot.model, lang: snapshot.lang }),
    }).catch(() => {});
  } catch {}
}

function redraw() {
  const translation = copies[language.value];
  const copy = { ...translation, ...translation.modes[mode.value] };
  document.documentElement.lang = language.value;
  document.title = `FoundPhone — ${copy.title}`;
  language.setAttribute('aria-label', copy.language);
  canvas.setAttribute('aria-label', copy.preview);
  message.placeholder = copy.found;
  document.querySelectorAll('[data-copy]').forEach(node => { node.textContent = copy[node.dataset.copy]; });
  try {
    const phone = renderWallpaper(canvas, { model: model.value, contact: contact.value, message: message.value, copy });
    download.disabled = !phone;
    error.textContent = '';
    contact.setAttribute('aria-invalid', 'false');
    message.setAttribute('aria-invalid', 'false');
  } catch (failure) {
    error.textContent = copy[failure.message] || copy.exportError;
    contact.setAttribute('aria-invalid', String(failure.message === 'phoneError'));
    message.setAttribute('aria-invalid', String(failure.message === 'textError'));
    download.disabled = true;
  }
}

function downloadImage() {
  const snapshot = { model: model.value, lang: language.value };
  const filename = `foundphone-${snapshot.model}.png`;
  canvas.toBlob(blob => {
    if (!blob) { error.textContent = copies[language.value].exportError; return; }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    recordEvent('png_download', snapshot);
    error.textContent = '';
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, 'image/png');
}

[language, model, mode, contact, message].forEach(control => control.addEventListener('input', redraw));
[language, model].forEach(control => control.addEventListener('input', () => recordEvent('form_open')));
download.addEventListener('click', downloadImage);
redraw();
recordEvent('form_open');
