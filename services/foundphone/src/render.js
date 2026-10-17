import { presets } from './presets.js';

function wrapMessage(ctx, text, width) {
  const graphemes = [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)];
  if (graphemes.length > 120) throw new Error('textError');
  const lines = [''];
  for (const { segment } of graphemes) {
    if (ctx.measureText(segment).width > width) throw new Error('textError');
    const last = lines.length - 1;
    if (segment === '\n') lines.push('');
    else if (ctx.measureText(lines[last] + segment).width > width) lines.push(segment);
    else lines[last] += segment;
  }
  if (lines.length > 4) throw new Error('textError');
  return lines;
}

function drawCode(ctx, phone, width, top) {
  const code = window.qrcode(0, 'M');
  code.addData('tel:' + phone);
  code.make();
  const count = code.getModuleCount();
  const cell = Math.floor(width * 0.34 / (count + 8));
  const size = cell * (count + 8);
  const left = Math.floor((width - size) / 2);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(left, top, size, size);
  ctx.fillStyle = '#172b2a';
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (code.isDark(row, col)) ctx.fillRect(left + (col + 4) * cell, top + (row + 4) * cell, cell, cell);
    }
  }
}

/** Draw a locally generated wallpaper; reject invalid contact or text before export. */
export function renderWallpaper(canvas, state) {
  const { model, contact, message, copy } = state;
  const phone = contact.replace(/[\s().-]/g, '');
  if (phone && !/^\+[1-9]\d{7,14}$/.test(phone)) throw new Error('phoneError');
  const [width, height] = presets[model];
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#172b2a';
  ctx.fillRect(0, 0, width, height);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#e5edc9';
  ctx.font = `600 ${width * 0.055}px system-ui, sans-serif`;
  ctx.fillText(copy.found, width / 2, height * 0.43);
  ctx.font = `${width * 0.041}px system-ui, sans-serif`;
  ctx.fillStyle = '#ffffff';
  const lines = wrapMessage(ctx, message || copy.call, width * 0.78);
  lines.forEach((line, index) => {
    ctx.direction = /[\u0590-\u08ff]/.test(line) ? 'rtl' : 'ltr';
    ctx.fillText(line, width / 2, height * 0.49 + index * width * 0.065);
  });
  ctx.direction = 'ltr';
  ctx.font = `600 ${width * 0.055}px system-ui, sans-serif`;
  ctx.fillText(phone || copy.placeholder, width / 2, height * 0.66, width * 0.84);
  if (phone) drawCode(ctx, phone, width, Math.floor(height * 0.72));
  return phone;
}
