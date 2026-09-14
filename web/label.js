/*
 * label.js — faithful JavaScript port of printlabel.py's label builder.
 *
 * Ports the algorithms 1:1:
 *   - build_label()            (auto-fit loop, multiline, emoji, merge, rulers)
 *   - rasterize_label()        (rotate/invert/mirror + threshold + 128px pad)
 *   - process_image()          (white-crop + aspect resize)
 *   - calculate_multiline_dimensions() / draw_multiline_text()
 *   - _draw_text_mixed() / _MixedFont (emoji raster overlay)
 *   - _emoji_raster_to_height() / _luminance_mono() / is_emoji_char()
 *
 * Rendering uses the Canvas 2D API (FontFace / system fonts). Measurements
 * mirror Pillow's getbbox/getlength semantics so the auto-fit converges to
 * the same font size and the preview matches the printed tape.
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants (printlabel.py)
// ---------------------------------------------------------------------------
const MM_PER_DOT = 0.149;
const HEIGHT_OF_THE_TAPE = 86;
const HEIGHT_OF_THE_IMAGE = 88;
const UNIFORM_SAMPLE = 'Agiy';
const EMOJI_FONT_STACK =
  '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji","Twemoji Mozilla",sans-serif';

// ---------------------------------------------------------------------------
// Emoji detection (is_emoji_char)
// ---------------------------------------------------------------------------
function isEmojiChar(ch) {
  if (!ch) return false;
  const cp = ch.codePointAt(0);
  if (cp >= 0x1f000 && cp <= 0x1faff) return true;
  if (cp >= 0x2600 && cp <= 0x27bf) return true;
  if (cp >= 0x2b00 && cp <= 0x2bff) return true;
  if (cp === 0xfe0f) return false; // variation selector alone
  if (cp === 0x200d) return false; // ZWJ alone
  if (cp >= 0x2300 && cp <= 0x23ff && cp !== 0x23e9) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Measurement canvas (shared)
// ---------------------------------------------------------------------------
const _measureCanvas = document.createElement('canvas');
const _measureCtx = _measureCanvas.getContext('2d');

function fontString(family, size) {
  return `${size}px ${family}`;
}

// ---------------------------------------------------------------------------
// Ligature shaping (GSUB features) — mirrors printlabel.py's _lig_* helpers.
//
// Chrome/Edge shape SVG <text> with HarfBuzz (the same engine uharfbuzz
// wraps), so `font-feature-settings` reproduces the Python ligature
// substitution exactly. Measurement is synchronous (getComputedTextLength);
// rendering is async (SVG -> Image -> canvas) and cached per (text, font).
// ---------------------------------------------------------------------------
const _SVG_NS = 'http://www.w3.org/2000/svg';
let _svgRoot = null;
function _svgAvailable() {
  return typeof document !== 'undefined'
    && typeof document.createElementNS === 'function'
    && typeof document.body !== 'undefined'
    && typeof Blob !== 'undefined'
    && typeof URL !== 'undefined'
    && typeof URL.createObjectURL === 'function';
}
function _svgMeasureRoot() {
  if (!_svgRoot) {
    _svgRoot = document.createElementNS(_SVG_NS, 'svg');
    _svgRoot.setAttribute('width', '0');
    _svgRoot.setAttribute('height', '0');
    _svgRoot.style.position = 'absolute';
    _svgRoot.style.left = '-99999px';
    _svgRoot.style.top = '0';
    document.body.appendChild(_svgRoot);
  }
  return _svgRoot;
}

// Synchronous shaped advance width (Pillow getlength with the feature on).
function svgMeasure(text, family, size, feature) {
  if (!feature || !text || !_svgAvailable()) return null;
  const root = _svgMeasureRoot();
  const t = document.createElementNS(_SVG_NS, 'text');
  t.setAttribute('font-family', family);
  t.setAttribute('font-size', String(size));
  t.style.fontFeatureSettings = `'${feature}' 1`;
  t.textContent = text;
  root.appendChild(t);
  let len = 0;
  try { len = t.getComputedTextLength(); } catch (e) { len = 0; }
  root.removeChild(t);
  return len;
}

function _escapeXml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;',
  }[c]));
}

// Async shaped render -> { canvas, pad, baseline } (baseline y inside canvas).
const _ligRenderCache = new Map();
const _LIG_CACHE_MAX = 256;
function _ligKey(text, family, size, feature, fill, strokeWidth, strokeFill) {
  return `${text}\u0000${family}\u0000${size}\u0000${feature}\u0000${fill}\u0000${strokeWidth}\u0000${strokeFill || ''}`;
}
function svgRender(text, family, size, feature, fill, strokeWidth, strokeFill) {
  if (!_svgAvailable()) return Promise.resolve(null);
  const key = _ligKey(text, family, size, feature, fill, strokeWidth, strokeFill);
  if (_ligRenderCache.has(key)) return Promise.resolve(_ligRenderCache.get(key));
  return new Promise((resolve) => {
    const pad = Math.ceil(size * 0.6) + 4;
    const adv = svgMeasure(text, family, size, feature);
    const w = Math.ceil((adv || size * text.length) + pad * 2);
    const h = Math.ceil(size * 2.2) + pad * 2;
    const baseline = pad + size;
    const stroke = (strokeWidth > 0 && strokeFill)
      ? ` stroke="${strokeFill}" stroke-width="${strokeWidth * 2}" stroke-linejoin="round"`
      : '';
    const svg = `<svg xmlns="${_SVG_NS}" width="${w}" height="${h}">` +
      `<text x="${pad}" y="${baseline}" font-family="${family}" font-size="${size}" ` +
      `fill="${fill}"${stroke} style="font-feature-settings:'${feature}' 1">` +
      `${_escapeXml(text)}</text></svg>`;
    const img = new Image();
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d').drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      const entry = { canvas: c, pad, baseline };
      if (_ligRenderCache.size >= _LIG_CACHE_MAX) {
        _ligRenderCache.delete(_ligRenderCache.keys().next().value);
      }
      _ligRenderCache.set(key, entry);
      resolve(entry);
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    img.src = url;
  });
}

// Pre-render every non-emoji chunk so the synchronous draw path can blit them.
async function preRenderLigatures(font, text, feature, fill, strokeWidth, strokeFill) {
  if (!feature || !text) return;
  for (const [f, chunk] of font._split(text)) {
    if (f !== null && chunk && !chunk.includes('\n')) {
      await svgRender(chunk, font.font.family, font.font.size, feature,
        fill, strokeWidth, strokeFill);
    }
  }
}

// ---------------------------------------------------------------------------
// WebFont — Pillow-like font surface over Canvas 2D
// ---------------------------------------------------------------------------
class WebFont {
  constructor(family, size) {
    this.family = family;
    this.size = size;
    this.path = family; // used as a cache key by callers
  }

  _ctx() {
    _measureCtx.font = fontString(this.family, this.size);
    return _measureCtx;
  }

  // Advance width (Pillow getlength).
  getlength(text) {
    if (!text) return 0;
    const ctx = this._ctx();
    return ctx.measureText(text).width;
  }

  // Ink bounding box. anchor "lt" -> top-left of ink; "ls" -> baseline-left.
  getbbox(text, opts = {}) {
    const anchor = opts.anchor || null;
    if (!text) return [0, 0, 0, 0];
    const ctx = this._ctx();
    const m = ctx.measureText(text);
    const ascent = m.actualBoundingBoxAscent || 0;
    const descent = m.actualBoundingBoxDescent || 0;
    const left = m.actualBoundingBoxLeft || 0;
    const right = m.actualBoundingBoxRight || 0;
    if (anchor === 'lt') {
      // Top of the ink at y=0: width = ink width, height = ink height.
      return [0, 0, left + right, ascent + descent];
    }
    if (anchor === 'ls') {
      // Baseline-left: top is negative ascent.
      return [left, -ascent, right, descent];
    }
    return [left, -ascent, right, descent];
  }

  // Font metrics (ascent, descent) — approximated from a tall sample.
  getmetrics() {
    const ctx = this._ctx();
    const m = ctx.measureText('Mg');
    return [m.actualBoundingBoxAscent || this.size, m.actualBoundingBoxDescent || 0];
  }
}

// ---------------------------------------------------------------------------
// Emoji raster (emoji_thumbnail / _emoji_raster_to_height)
// ---------------------------------------------------------------------------
function emojiRasterToHeight(char, targetH, mono) {
  const big = Math.max(32, Math.round(targetH * 2));
  const pad = Math.round(big * 0.6);
  const w = big * 2 + pad * 2;
  const h = big * 2 + pad * 2;
  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  ctx.font = fontString(EMOJI_FONT_STACK, big);
  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'left';
  // mono: force text presentation (monochrome outline) with U+FE0E.
  const glyph = mono ? char + '\uFE0E' : char;
  ctx.fillStyle = '#000';
  ctx.fillText(glyph, pad, pad + big);
  const b = inkBBox(ctx, w, h);
  if (!b) return { raster: null, width: 0 };
  const [x0, y0, x1, y1] = b;
  const cw = x1 - x0;
  const chh = y1 - y0;
  if (chh <= 0) return { raster: null, width: 0 };
  const newW = Math.max(1, Math.round(cw * targetH / chh));
  const out = document.createElement('canvas');
  out.width = newW;
  out.height = targetH;
  const octx = out.getContext('2d');
  octx.imageSmoothingEnabled = true;
  octx.imageSmoothingQuality = 'high';
  octx.drawImage(cv, x0, y0, cw, chh, 0, 0, newW, targetH);
  return { raster: out, width: newW };
}

// Ink bounding box of a canvas (alpha > 0), or null when empty.
function inkBBox(ctx, w, h) {
  const data = ctx.getImageData(0, 0, w, h).data;
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] > 0) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
    }
  }
  if (x1 < 0) return null;
  return [x0, y0, x1 + 1, y1 + 1];
}

// _luminance_mono — in-place B/W derivation from a colour emoji raster.
function luminanceMono(canvas, lo = 140.0, hi = 175.0) {
  if (lo > hi) [lo, hi] = [hi, lo];
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const img = ctx.getImageData(0, 0, w, h);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3];
    if (!a) continue;
    const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    if (lum <= lo) {
      d[i] = 0; d[i + 1] = 0; d[i + 2] = 0; // solid ink
    } else if (lum >= hi) {
      d[i + 3] = 0; // clear
    } else {
      d[i] = 0; d[i + 1] = 0; d[i + 2] = 0;
      d[i + 3] = Math.round(a * (hi - lum) / (hi - lo));
    }
  }
  ctx.putImageData(img, 0, 0);
}

// ---------------------------------------------------------------------------
// MixedFont — emoji-aware wrapper (port of _MixedFont)
// ---------------------------------------------------------------------------
class MixedFont {
  constructor(family, size, opts = {}) {
    this.font = new WebFont(family, size);
    this.size = size;
    this._uniform = !!opts.uniform;
    this._mono = opts.mono !== false;
    this._lumaLo = opts.lumaLo != null ? opts.lumaLo : 140.0;
    this._lumaHi = opts.lumaHi != null ? opts.lumaHi : 175.0;
    this._emojiTargetH = null;
    this._emojiBand = false;
    this._emojiBandY = 0;
    this._emojiCache = new Map();
    this._path = family;
    this._ligatures = opts.ligatures || null;
  }

  isEmoji(ch) {
    return isEmojiChar(ch);
  }

  _split(text) {
    const chunks = [];
    let buf = '';
    for (const ch of text) {
      if (this.isEmoji(ch)) {
        if (buf) { chunks.push([this.font, buf]); buf = ''; }
        chunks.push([null, ch]);
      } else {
        buf += ch;
      }
    }
    if (buf) chunks.push([this.font, buf]);
    return chunks;
  }

  _emojiWidth(ch) {
    if (this._emojiBand) return this.font.getlength(' ');
    if (this._emojiTargetH) {
      const { width } = this._raster(ch);
      return width;
    }
    return Math.max(1, Math.round(this.font.getlength('M')));
  }

  _raster(ch) {
    const key = `${ch}|${this._emojiTargetH}|${this._mono}`;
    if (this._emojiCache.has(key)) return this._emojiCache.get(key);
    const { raster, width } = emojiRasterToHeight(
      ch, this._emojiTargetH || 32, this._mono);
    if (raster && !this._mono) {
      luminanceMono(raster, this._lumaLo, this._lumaHi);
    }
    const entry = { raster, width };
    this._emojiCache.set(key, entry);
    return entry;
  }

  setEmojiLineHeight(lineHeight) {
    this._emojiTargetH = Math.max(4, Math.round(lineHeight));
    this._emojiBand = false;
    this._emojiCache.clear();
  }

  setEmojiBand(bandY) {
    this._emojiTargetH = 64;
    this._emojiBand = true;
    this._emojiBandY = Math.round(bandY);
    this._emojiCache.clear();
  }

  bandWidth(text) {
    if (!this._emojiBand) return 0;
    let total = 0;
    for (const ch of text) {
      if (this.isEmoji(ch)) total += this._raster(ch).width;
    }
    return total;
  }

  _cleanText(text) {
    let out = '';
    for (const ch of text) out += this.isEmoji(ch) ? ' ' : ch;
    return out;
  }

  getlength(text) {
    let total = 0;
    for (const [font, chunk] of this._split(text)) {
      if (font === null) {
        for (const ch of chunk) total += this._emojiWidth(ch);
      } else {
        total += font.getlength(chunk);
      }
    }
    return total;
  }

  getbbox(text, opts = {}) {
    const anchor = opts.anchor || null;
    if (!text) return [0, 0, 0, 0];
    const hasEmojiTarget = this._emojiTargetH !== null;
    let clean = this._cleanText(text);
    if (!clean.trim()) clean = 'Ag';
    const hasEmoji = [...text].some((c) => this.isEmoji(c));

    if (this._uniform && clean.trim()) {
      const hB = this.font.getbbox(UNIFORM_SAMPLE, { anchor });
      const wB = this.font.getbbox(clean, { anchor });
      if (hasEmojiTarget && hasEmoji && !this._emojiBand) {
        const w = Math.round(this.getlength(text));
        if (anchor === 'lt') return [0, 0, w, hB[3] - hB[1]];
        return [wB[0], hB[1], wB[0] + w, hB[3]];
      }
      if (anchor === 'lt') return [wB[0], hB[1], wB[2], hB[3] - hB[1]];
      return [wB[0], hB[1], wB[2], hB[3]];
    }

    const b = this.font.getbbox(clean, { anchor });
    if (hasEmojiTarget && hasEmoji && !this._emojiBand) {
      const w = Math.round(this.getlength(text));
      if (anchor === 'lt') return [0, 0, w, b[3] - b[1]];
      return [b[0], b[1], b[0] + w, b[3]];
    }
    return b;
  }

  getmetrics() {
    return this.font.getmetrics();
  }
}

// ---------------------------------------------------------------------------
// calculate_multiline_dimensions
// ---------------------------------------------------------------------------
function calculateMultilineDimensions(lines, font, lineSpacing) {
  let maxWidth = 0;
  const lineHeights = [];
  const sampleB = font.getbbox(lines.join(''), { anchor: 'lt' });
  const baseLineHeight = sampleB[3] - sampleB[1];
  const lineSpacingPixels = baseLineHeight * lineSpacing;
  const n = lines.length;
  for (const line of lines) {
    const b = font.getbbox(line, { anchor: 'lt' });
    maxWidth = Math.max(maxWidth, b[2] - b[0]);
    lineHeights.push(baseLineHeight);
  }
  let totalHeight = 0;
  for (let i = 0; i < n; i++) {
    totalHeight += baseLineHeight;
    if (n > 1 && i < n - 1) totalHeight += (lineSpacingPixels - baseLineHeight);
  }
  return { width: maxWidth, height: Math.round(totalHeight), lineHeights };
}

// ---------------------------------------------------------------------------
// _draw_text_mixed
// ---------------------------------------------------------------------------
function drawTextMixed(ctx, xy, text, font, opts = {}) {
  const fill = opts.fill || '#000';
  const strokeWidth = opts.strokeWidth || 0;
  const strokeFill = opts.strokeFill || null;
  if (!text) return;
  if (!(font instanceof MixedFont)) {
    drawPlain(ctx, xy, text, font, fill, strokeWidth, strokeFill);
    return;
  }
  let [x0, y0] = xy;
  const clean = font._cleanText(text);

  if (font._emojiBand) {
    if (clean.trim()) {
      drawPlain(ctx, [x0, y0], clean, font.font, fill, strokeWidth, strokeFill);
    }
    return;
  }

  let asc;
  if (font._uniform) {
    asc = -font.font.getbbox(UNIFORM_SAMPLE, { anchor: 'ls' })[1];
  } else {
    asc = clean.trim()
      ? -font.font.getbbox(clean, { anchor: 'ls' })[1]
      : font.font.getmetrics()[0];
  }
  const baseline = y0 + asc;

  for (const [fontUsed, chunk] of font._split(text)) {
    if (fontUsed === null) {
      for (const ch of chunk) {
        const { raster, width } = font._raster(ch);
        if (raster) ctx.drawImage(raster, Math.round(x0), Math.round(y0));
        x0 += width;
      }
    } else if (!chunk.includes('\n')) {
      let drawn = false;
      if (font._ligatures) {
        const entry = _ligRenderCache.get(
          _ligKey(chunk, fontUsed.family, fontUsed.size, font._ligatures,
            fill, strokeWidth, strokeFill));
        if (entry) {
          ctx.drawImage(entry.canvas,
            Math.round(x0 - entry.pad), Math.round(baseline - entry.baseline));
          const adv = svgMeasure(chunk, fontUsed.family, fontUsed.size, font._ligatures);
          x0 += adv != null ? adv : fontUsed.getlength(chunk);
          drawn = true;
        }
      }
      if (!drawn) {
        drawPlain(ctx, [x0, baseline], chunk, fontUsed, fill, strokeWidth, strokeFill, 'alphabetic');
        x0 += fontUsed.getlength(chunk);
      }
    } else {
      let bl = baseline;
      for (const ln of chunk.split('\n')) {
        if (ln) {
          drawPlain(ctx, [x0, bl], ln, fontUsed, fill, strokeWidth, strokeFill, 'alphabetic');
          x0 += fontUsed.getlength(ln);
        }
        bl += fontUsed.getlength('A');
      }
    }
  }
}

function drawPlain(ctx, xy, text, font, fill, strokeWidth, strokeFill, baseline) {
  ctx.save();
  ctx.font = fontString(font.family, font.size);
  ctx.textBaseline = baseline || 'top';
  ctx.textAlign = 'left';
  ctx.fillStyle = fill;
  if (strokeWidth > 0 && strokeFill) {
    ctx.lineWidth = strokeWidth * 2;
    ctx.strokeStyle = strokeFill;
    ctx.lineJoin = 'round';
    ctx.strokeText(text, xy[0], xy[1]);
  }
  ctx.fillText(text, xy[0], xy[1]);
  ctx.restore();
}

// ---------------------------------------------------------------------------
// draw_multiline_text
// ---------------------------------------------------------------------------
function drawMultilineText(ctx, textLines, x, y, font, opts) {
  if (!textLines.length) return;
  const sampleB = font.getbbox(textLines.join(''), { anchor: 'lt' });
  const baseLineHeight = sampleB[3];
  const lineSpacingPixels = baseLineHeight * opts.lineSpacing;
  let currentY = y;
  for (const line of textLines) {
    if (line.trim()) {
      let xPos = x;
      if (opts.centerText) {
        const b = font.getbbox(line, { anchor: 'lt' });
        const lineWidth = b[2] - b[0];
        xPos = Math.floor((opts.imageWidth - lineWidth) / 2);
      }
      drawTextMixed(ctx, [xPos, currentY], line, font, {
        fill: opts.fill,
        strokeWidth: opts.strokeWidth,
        strokeFill: opts.strokeFill,
      });
    }
    currentY += lineSpacingPixels;
  }
}

// ---------------------------------------------------------------------------
// process_image — white-crop + aspect resize (async image load)
// ---------------------------------------------------------------------------
async function loadImageElement(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Invalid image "${src}"`));
    img.src = src;
  });
}

async function processImage(src, resize, whiteLevel, targetHeight) {
  const img = await loadImageElement(src);
  const w = img.naturalWidth, h = img.naturalHeight;
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  const ctx = cv.getContext('2d');
  // Flatten transparency onto white.
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(img, 0, 0);
  const data = ctx.getImageData(0, 0, w, h).data;
  let left = w, top = h, right = 0, bottom = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      // Grayscale value (Pillow converts to "L").
      const g = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
      if (g < whiteLevel) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  if (right > left && bottom > top) {
    const cw = right - left + 1;
    const chh = bottom - top + 1;
    const aspect = cw / chh;
    const newWidth = Math.round(targetHeight * aspect);
    const outW = Math.round(newWidth * resize);
    const outH = Math.round(targetHeight * resize);
    const out = document.createElement('canvas');
    out.width = Math.max(1, outW);
    out.height = Math.max(1, outH);
    const octx = out.getContext('2d');
    octx.imageSmoothingEnabled = true;
    octx.imageSmoothingQuality = 'high';
    octx.drawImage(cv, left, top, cw, chh, 0, 0, out.width, out.height);
    return out;
  }
  return null;
}

// ---------------------------------------------------------------------------
// build_label
// ---------------------------------------------------------------------------
async function buildLabel(args) {
  const tapeMm = Math.max(3.5, Math.min(12.0, Number(args.tape_width) || 12.0));
  const heightOfPrintable = Math.round(64.0 * tapeMm / 12.0);
  const printBorder = (HEIGHT_OF_THE_IMAGE - heightOfPrintable) / 2;
  const emojiMode = args.emoji_print_area ? 'print' : 'line';
  const uniform = !!args.uniform_font;
  const mono = args.mono_emoji !== false;
  const fontFamily = args.fontname || 'Arial';

  const makeFont = (size) => new MixedFont(fontFamily, size, {
    uniform, mono,
    lumaLo: args.luma_lo, lumaHi: args.luma_hi,
    ligatures: args.ligatures || null,
  });

  let text = (args.text_to_print || []).join(' ');
  if (text && args.unicode) {
    text = decodeUnicodeEscapes(text);
  }
  const tabW = Math.max(1, parseInt(args.tab_width, 10) || 8);
  if (text.includes('\t')) text = expandTabs(text, tabW);

  let fontSize = 0;
  let fontHeight = 0;
  let font = null;
  let fontWidth = 0;
  let lineHeights = [];
  let textLines = null;
  let image = null;
  let ctx = null;

  if (text) {
    const hasNewlines = text.includes('\\n');
    if (hasNewlines) {
      textLines = text.replace(/\\n/g, '\n').split('\n');
      if (args.fixed_font_size) {
        fontSize = args.fixed_font_size;
        font = makeFont(fontSize);
        const d = calculateMultilineDimensions(textLines, font, args.line_spacing);
        fontWidth = d.width; fontHeight = d.height; lineHeights = d.lineHeights;
      } else {
        let stop = false;
        while (fontHeight !== heightOfPrintable) {
          if (fontHeight > heightOfPrintable) {
            if (textLines.length > 1) {
              const minSpacing = args.line_spacing * 0.9;
              const step = 0.01;
              let newSpacing = args.line_spacing;
              let foundFit = false;
              while (newSpacing > minSpacing) {
                newSpacing -= step;
                const d2 = calculateMultilineDimensions(textLines, font, newSpacing);
                if (d2.height === heightOfPrintable) {
                  args.line_spacing = newSpacing;
                  fontWidth = d2.width; fontHeight = d2.height;
                  foundFit = true;
                  break;
                }
              }
              if (foundFit) break;
            }
            fontSize -= 1;
            stop = true;
          } else {
            fontSize += 1;
          }
          font = makeFont(fontSize);
          const d = calculateMultilineDimensions(textLines, font, args.line_spacing);
          fontWidth = d.width; fontHeight = d.height; lineHeights = d.lineHeights;
          if (stop) break;
        }
      }
      if (!args.font_scale) {
        font = makeFont(fontSize);
        if (emojiMode === 'line') font.setEmojiLineHeight(lineHeights[0]);
        else font.setEmojiBand(printBorder);
        const d = calculateMultilineDimensions(textLines, font, args.line_spacing);
        fontWidth = d.width; fontHeight = d.height; lineHeights = d.lineHeights;
      }
      let yPosition = printBorder;
      if (args.font_scale) {
        const scaled = Math.round(fontSize * (args.font_scale / 100.0));
        font = makeFont(scaled);
        let d = calculateMultilineDimensions(textLines, font, args.line_spacing);
        if (emojiMode === 'line') font.setEmojiLineHeight(d.lineHeights[0]);
        else font.setEmojiBand(printBorder);
        d = calculateMultilineDimensions(textLines, font, args.line_spacing);
        fontWidth = d.width; fontHeight = d.height; lineHeights = d.lineHeights;
        yPosition = printBorder + Math.floor((heightOfPrintable - fontHeight) / 2);
      }
      let emojiStripW = 0;
      if (font._emojiBand) {
        emojiStripW = Math.max(0, ...textLines.map((l) => font.bandWidth(l)));
      }
      const imgW = emojiStripW + fontWidth + args.h_padding * 2 + 1 + args.end_margin;
      image = newCanvas(imgW, HEIGHT_OF_THE_IMAGE, '#fff');
      ctx = image.getContext('2d');
      if (emojiStripW) {
        let xStrip = args.h_padding;
        for (const l of textLines) {
          for (const ch of l) {
            if (font.isEmoji(ch)) {
              const { raster, width } = font._raster(ch);
              if (raster) ctx.drawImage(raster, Math.round(xStrip), Math.round(printBorder + args.v_shift));
              xStrip += width;
            }
          }
        }
      }
      const textX = args.h_padding + emojiStripW;
      if (args.ligatures) {
        for (const l of textLines) {
          await preRenderLigatures(font, l, args.ligatures,
            args.fill, args.stroke_width, args.stroke_fill);
        }
      }
      drawMultilineText(ctx, textLines, textX, yPosition + args.v_shift, font, {
        fill: args.fill, strokeWidth: args.stroke_width, strokeFill: args.stroke_fill,
        lineSpacing: args.line_spacing, centerText: args.center_text, imageWidth: image.width,
      });
      if (args.text_size) {
        const textSize = Math.round(args.text_size / MM_PER_DOT) - args.h_padding - args.end_margin;
        const scaleFactor = fontWidth / textSize;
        image = affineScaleX(image, textSize + args.end_margin + args.h_padding, scaleFactor);
        ctx = image.getContext('2d');
      }
    } else {
      // Single-line
      if (args.fixed_font_size) {
        fontSize = args.fixed_font_size;
        font = makeFont(fontSize);
        const b = font.getbbox(text, { anchor: 'lt' });
        fontWidth = b[2]; fontHeight = b[3];
      } else {
        let stop = false;
        while (fontHeight !== heightOfPrintable) {
          if (fontHeight > heightOfPrintable) { fontSize -= 1; stop = true; }
          else fontSize += 1;
          font = makeFont(fontSize);
          const b = font.getbbox(text, { anchor: 'lt' });
          fontWidth = b[2]; fontHeight = b[3];
          if (stop) break;
        }
      }
      if (!args.font_scale) {
        font = makeFont(fontSize);
        if (emojiMode === 'line') font.setEmojiLineHeight(fontHeight);
        else font.setEmojiBand(printBorder);
        const b = font.getbbox(text, { anchor: 'lt' });
        fontWidth = b[2]; fontHeight = b[3];
      }
      let yPosition = printBorder;
      if (args.font_scale) {
        const scaled = Math.round(fontSize * (args.font_scale / 100.0));
        font = makeFont(scaled);
        if (emojiMode === 'line') font.setEmojiLineHeight(fontHeight);
        else font.setEmojiBand(printBorder);
        const b = font.getbbox(text, { anchor: 'lt' });
        fontWidth = b[2]; fontHeight = b[3];
        yPosition = printBorder + Math.floor((heightOfPrintable - fontHeight) / 2);
      }
      let emojiStripW = 0;
      if (font._emojiBand) emojiStripW = font.bandWidth(text);
      const imgW = emojiStripW + fontWidth + args.h_padding * 2 + 1 + args.end_margin;
      image = newCanvas(imgW, HEIGHT_OF_THE_IMAGE, '#fff');
      ctx = image.getContext('2d');
      if (emojiStripW) {
        let xStrip = args.h_padding;
        for (const ch of text) {
          if (font.isEmoji(ch)) {
            const { raster, width } = font._raster(ch);
            if (raster) ctx.drawImage(raster, Math.round(xStrip), Math.round(printBorder + args.v_shift));
            xStrip += width;
          }
        }
      }
      const textX = args.h_padding + emojiStripW;
      if (args.ligatures) {
        await preRenderLigatures(font, text, args.ligatures,
          args.fill, args.stroke_width, args.stroke_fill);
      }
      drawTextMixed(ctx, [textX, yPosition + args.v_shift], text, font, {
        fill: args.fill, strokeWidth: args.stroke_width, strokeFill: args.stroke_fill,
      });
      if (args.text_size) {
        const textSize = Math.round(args.text_size / MM_PER_DOT) - args.h_padding - args.end_margin;
        const b = font.getbbox(text, { anchor: 'lt' });
        const textWidth = b ? b[2] - b[0] : 0;
        const scaleFactor = textWidth / textSize;
        image = affineScaleX(image, textSize + args.end_margin + args.h_padding, scaleFactor);
        ctx = image.getContext('2d');
      }
    }
  } else {
    image = newCanvas(0, HEIGHT_OF_THE_IMAGE, '#fff');
    ctx = image.getContext('2d');
  }

  // Merge images (in reverse order, like the original).
  if (args.merge && args.merge.length) {
    for (let i = args.merge.length - 1; i >= 0; i--) {
      const loaded = await processImage(args.merge[i], args.resize, args.white_level, heightOfPrintable);
      if (!loaded) throw new Error(`Invalid image "${args.merge[i]}"`);
      const gap = image.width ? args.merge_gap : 0;
      const dst = newCanvas(loaded.width + gap + image.width, HEIGHT_OF_THE_IMAGE, '#fff');
      const dctx = dst.getContext('2d');
      dctx.drawImage(loaded, args.x_merge, args.y_merge);
      dctx.drawImage(image, loaded.width + gap, 0);
      image = dst;
      ctx = image.getContext('2d');
    }
  }

  // Fixed width padding.
  if (args.fixed_width) {
    const targetWidthDots = Math.round(args.fixed_width / MM_PER_DOT);
    if (image.width < targetWidthDots) {
      const padded = newCanvas(targetWidthDots, image.height, '#fff');
      const pctx = padded.getContext('2d');
      const xOffset = args.center_text ? Math.floor((targetWidthDots - image.width) / 2) : 0;
      pctx.drawImage(image, xOffset, 0);
      image = padded;
      ctx = image.getContext('2d');
    }
  }

  // Rulers / guide lines.
  if (args.lines) {
    drawRulers(ctx, image, printBorder);
  }

  if (image.width === 0 || image.height === 0) {
    throw new Error('Null image generated.');
  }
  return image;
}

function drawRulers(ctx, image, printBorder) {
  ctx.save();
  ctx.font = '10px sans-serif';
  ctx.fillStyle = 'magenta';
  ctx.textBaseline = 'top';
  ctx.fillText('in', 0, 1);
  let x = -1, i = 0;
  while (x < image.width) {
    if (x > 0) {
      ctx.fillRect(Math.round(x), printBorder - (i % 4 ? 4 : 9), 2, (i % 4 ? 4 : 9) - 2);
    }
    x += 43.18; i++;
  }
  ctx.fillText('cm', 0, 76);
  x = -1; i = 0;
  while (x < image.width) {
    if (x > 0) {
      const len = i % 10 ? 5 : 9;
      ctx.fillRect(Math.round(x), HEIGHT_OF_THE_IMAGE - printBorder + 1, 2, len);
    }
    x += 68; i++;
  }
  ctx.fillStyle = 'red';
  for (let xx = 0; xx < image.width; xx += 5) {
    ctx.fillRect(xx, printBorder - 1, 1, 1);
    ctx.fillRect(xx, HEIGHT_OF_THE_IMAGE - printBorder, 1, 1);
  }
  const tapeBorder = Math.floor((HEIGHT_OF_THE_IMAGE - HEIGHT_OF_THE_TAPE) / 2);
  if (tapeBorder > 0) {
    ctx.fillStyle = 'cyan';
    ctx.fillRect(0, tapeBorder - 1, image.width, 1);
    ctx.fillRect(0, HEIGHT_OF_THE_IMAGE - tapeBorder, image.width, 1);
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// rasterize_label — rotate/invert/mirror + threshold + 128px pad
// ---------------------------------------------------------------------------
function rasterizeLabel(image, args) {
  const w = image.width, h = image.height;
  // Rotate -90 (expand): new canvas is h x w.
  const rot = newCanvas(h, w, '#fff');
  const rctx = rot.getContext('2d');
  rctx.translate(0, w);
  rctx.rotate(-Math.PI / 2);
  rctx.drawImage(image, 0, 0);
  // Mirror horizontally.
  const mir = newCanvas(h, w, '#fff');
  const mctx = mir.getContext('2d');
  mctx.translate(h, 0);
  mctx.scale(-1, 1);
  mctx.drawImage(rot, 0, 0);
  // Invert + threshold -> binary.
  const img = mctx.getImageData(0, 0, h, w);
  const d = img.data;
  const threshold = args.threshold;
  for (let i = 0; i < d.length; i += 4) {
    const g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    const inv = 255 - g;
    const v = inv > threshold ? 255 : 0;
    d[i] = d[i + 1] = d[i + 2] = v;
    d[i + 3] = 255;
  }
  mctx.putImageData(img, 0, 0);
  // Pad width to 128 (centered).
  const padded = newCanvas(128, w, '#fff');
  const pctx = padded.getContext('2d');
  const x = Math.floor((128 - h) / 2);
  pctx.drawImage(mir, x, 0);
  return padded;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function newCanvas(w, h, bg) {
  const cv = document.createElement('canvas');
  cv.width = Math.max(0, Math.round(w));
  cv.height = Math.max(0, Math.round(h));
  if (bg) {
    const ctx = cv.getContext('2d');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, cv.width, cv.height);
  }
  return cv;
}

function affineScaleX(image, newWidth, scaleFactor) {
  const out = newCanvas(newWidth, image.height, '#fff');
  const ctx = out.getContext('2d');
  ctx.drawImage(image, 0, 0, image.width, image.height, 0, 0, image.width / scaleFactor, image.height);
  return out;
}

function expandTabs(text, tabW) {
  // Mirror Python str.expandtabs: advance to the next multiple of tabW.
  let out = '';
  let col = 0;
  for (const ch of text) {
    if (ch === '\t') {
      const spaces = tabW - (col % tabW);
      out += ' '.repeat(spaces);
      col += spaces;
    } else if (ch === '\n') {
      out += ch;
      col = 0;
    } else {
      out += ch;
      col++;
    }
  }
  return out;
}

function decodeUnicodeEscapes(text) {
  // Mirror Python's str.encode().decode('unicode_escape'): handle \uXXXX,
  // \UXXXXXXXX, \xXX and the common single-character escapes.
  const simple = {
    n: '\n', t: '\t', r: '\r', '\\': '\\', "'": "'", '"': '"',
    a: '\x07', b: '\b', f: '\f', v: '\v', 0: '\0',
  };
  return text.replace(
    /\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|.)/g,
    (m, esc) => {
      const kind = esc[0];
      if (kind === 'u') return String.fromCharCode(parseInt(esc.slice(1), 16));
      if (kind === 'U') return String.fromCodePoint(parseInt(esc.slice(1), 16));
      if (kind === 'x') return String.fromCharCode(parseInt(esc.slice(1), 16));
      return Object.prototype.hasOwnProperty.call(simple, esc) ? simple[esc] : m;
    },
  );
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
window.PTLabel = {
  buildLabel,
  rasterizeLabel,
  processImage,
  calculateMultilineDimensions,
  drawMultilineText,
  drawTextMixed,
  isEmojiChar,
  MixedFont,
  WebFont,
  expandTabs,
  decodeUnicodeEscapes,
  MM_PER_DOT,
  HEIGHT_OF_THE_IMAGE,
  HEIGHT_OF_THE_TAPE,
};
