/*
 * app.js — GUI controller for the web port of printlabel.py.
 *
 * Mirrors ptgui.py: builds an args namespace from the controls, renders a
 * live preview (build_label + rasterize_label), and prints over Web Serial.
 */

'use strict';

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  merge: [],          // list of {name, url}
  selectedMerge: -1,
  transport: null,
  connected: false,
  busy: false,
  previewScheduled: false,
  zoom: null,         // null => Fit
  pilImage: null,     // current preview canvas (native size)
  fontFamily: 'Arial',
};

// ---------------------------------------------------------------------------
// Browser support check
// ---------------------------------------------------------------------------
function checkSupport() {
  const badge = $('support-badge');
  const banner = $('support-banner');
  const bannerText = $('support-banner-text');
  const supported = window.PTPrinter.SerialTransport.isSupported();
  if (supported) {
    badge.textContent = 'Web Serial available';
    badge.className = 'badge badge-ok';
    banner.classList.add('hidden');
  } else {
    badge.textContent = 'Web Serial unavailable';
    badge.className = 'badge badge-err';
    banner.classList.remove('hidden');
    bannerText.textContent =
      'This browser does not expose the Web Serial API, so printing to the ' +
      'PT-P300BT is not possible here. Use Chrome or Edge (desktop) on ' +
      'Windows, macOS or Linux. The preview and all label algorithms still ' +
      'work in any modern browser.';
  }
  return supported;
}

// ---------------------------------------------------------------------------
// Font list (system fonts are not enumerable in the browser; offer a curated
// list plus user-loaded font files via the FontFace API).
// ---------------------------------------------------------------------------
const SYSTEM_FONTS = [
  'Arial', 'Arial Black', 'Calibri', 'Cambria', 'Candara', 'Comic Sans MS',
  'Consolas', 'Courier New', 'Georgia', 'Impact', 'Lucida Console',
  'Segoe UI', 'Tahoma', 'Times New Roman', 'Trebuchet MS', 'Verdana',
  'DejaVu Sans', 'DejaVu Sans Mono', 'Liberation Sans', 'Noto Sans',
  'Roboto', 'Ubuntu', 'Helvetica', 'Menlo', 'Monaco',
];

function populateFonts() {
  const sel = $('fontname');
  sel.innerHTML = '';
  for (const f of SYSTEM_FONTS) {
    const o = document.createElement('option');
    o.value = f;
    o.textContent = f;
    sel.appendChild(o);
  }
  sel.value = 'Arial';
}

async function loadFontFile(file) {
  try {
    const buf = await file.arrayBuffer();
    const family = file.name.replace(/\.[^.]+$/, '');
    const face = new FontFace(family, buf);
    await face.load();
    document.fonts.add(face);
    const sel = $('fontname');
    const o = document.createElement('option');
    o.value = family;
    o.textContent = `${family} (loaded)`;
    sel.appendChild(o);
    sel.value = family;
    $('font-status').textContent = `Loaded ${file.name}`;
    schedulePreview();
  } catch (e) {
    $('font-status').textContent = `Failed: ${e.message}`;
  }
}

// ---------------------------------------------------------------------------
// Args builder (mirrors ptgui._make_args)
// ---------------------------------------------------------------------------
function num(id, cast = Number, def = 0) {
  const v = $(id).value;
  if (v === '' || v == null) return def;
  const n = cast(v);
  return Number.isNaN(n) ? def : n;
}
function bool(id) { return $(id).checked; }
function str(id) { return $(id).value; }

function makeArgs(noPrint) {
  const text = $('text').value;
  const cliText = text ? text.split('\n').join('\\n') : '';
  const fontScale = num('font_scale', Number, 100);
  return {
    comport: 'web-serial',
    fixed_width: num('fixed_width', Number, 0) || null,
    fixed_font_size: num('fixed_font_size', Number, 0) || null,
    fontname: state.fontFamily,
    text_to_print: cliText ? [cliText] : [],
    unicode: bool('unicode'),
    lines: bool('lines'),
    image: null,
    merge: state.merge.map((m) => m.url),
    resize: num('resize', Number, 1.0),
    x_merge: num('x_merge', Number, 0),
    y_merge: num('y_merge', Number, 12),
    merge_gap: num('merge_gap', Number, 0),
    no_print: noPrint,
    no_feed: bool('no_feed'),
    auto_cut: bool('auto_cut'),
    end_margin: num('end_margin', Number, 0),
    raw: bool('raw'),
    nocomp: bool('nocomp'),
    fill: str('fill') || 'black',
    stroke_fill: str('stroke_fill').trim() || null,
    stroke_width: num('stroke_width', Number, 0),
    text_size: num('text_size', Number, 0) || null,
    font_scale: fontScale === 100 ? null : fontScale,
    h_padding: num('h_padding', Number, 5),
    v_shift: num('v_shift', Number, 0),
    line_spacing: num('line_spacing', Number, 1.2),
    center_text: bool('center_text'),
    tape_width: num('tape_width', Number, 12.0),
    emoji_print_area: bool('emoji_print_area'),
    uniform_font: bool('uniform_font'),
    ligatures: str('ligatures') || null,
    mono_emoji: bool('mono_emoji'),
    luma_lo: num('luma_lo', Number, 140.0),
    luma_hi: num('luma_hi', Number, 175.0),
    tab_width: num('tab_width', Number, 8),
    white_level: num('white_level', Number, 240),
    threshold: num('threshold', Number, 75),
  };
}

// ---------------------------------------------------------------------------
// Preview
// ---------------------------------------------------------------------------
function schedulePreview() {
  if (!bool('auto_preview')) return;
  if (state.busy) { state.previewScheduled = true; return; }
  state.previewScheduled = true;
  setTimeout(runPreview, 150);
}

async function runPreview() {
  state.previewScheduled = false;
  if (state.busy) return;
  state.busy = true;
  try {
    const args = makeArgs(true);
    if (!args.text_to_print.length && !args.merge.length) {
      clearPreview();
      setStatus('Type some text to see the preview.');
      return;
    }
    const image = await window.PTLabel.buildLabel(args);
    const padded = window.PTLabel.rasterizeLabel(image, args);
    const showConv = bool('preview_conv');
    state.pilImage = showConv ? padded : image;
    renderPreview();
    const cm = (padded.height * window.PTLabel.MM_PER_DOT / 10).toFixed(1);
    setStatus(`${padded.width}×${padded.height} raster, ${cm} cm of tape.`);
  } catch (e) {
    if (String(e.message).includes('Null image')) {
      clearPreview();
      setStatus('Type some text to see the preview.');
    } else {
      setStatus(`Error: ${e.message}`);
      log(`ERROR: ${e.message}`);
    }
  } finally {
    state.busy = false;
    if (state.previewScheduled) schedulePreview();
  }
}

function clearPreview() {
  state.pilImage = null;
  const cv = $('preview-canvas');
  cv.width = 0; cv.height = 0;
  $('preview-empty').classList.remove('hidden');
  $('zoom-info').textContent = '';
}

function renderPreview() {
  const img = state.pilImage;
  if (!img) return;
  $('preview-empty').classList.add('hidden');
  const wrap = $('canvas-wrap');
  let scale;
  if (state.zoom === null) {
    const avail = Math.max(wrap.clientWidth - 40, 100);
    scale = Math.min(avail / img.width, 2.0);
  } else {
    scale = state.zoom;
  }
  const cv = $('preview-canvas');
  cv.width = Math.max(1, Math.round(img.width * scale));
  cv.height = Math.max(1, Math.round(img.height * scale));
  const ctx = cv.getContext('2d');
  ctx.imageSmoothingEnabled = scale < 2;
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.drawImage(img, 0, 0, cv.width, cv.height);
  $('zoom-info').textContent = `${cv.width}×${cv.height} (${Math.round(scale * 100)}%)`;
}

function setZoom(mode) {
  if (mode === 'fit') state.zoom = null;
  else if (mode === 'in') state.zoom = Math.min((state.zoom || 1) * 1.25, 4);
  else if (mode === 'out') state.zoom = Math.max((state.zoom || 1) / 1.25, 0.1);
  else if (mode === 'tape') {
    // 12 mm tape at 203 dpi ≈ 96 dots; show at true physical size.
    const dpi = 96; // CSS px per inch assumption
    const tapeDots = Math.round(12 * 203 / 25.4);
    state.zoom = Math.min((12 * dpi / 25.4) / tapeDots, 2.0);
  }
  renderPreview();
}

// ---------------------------------------------------------------------------
// Status / log
// ---------------------------------------------------------------------------
function setStatus(s) { $('status').textContent = s; }
function log(s) {
  const el = $('log');
  el.textContent += s + '\n';
  el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------------------
// Merge images
// ---------------------------------------------------------------------------
function renderMergeList() {
  const ul = $('merge-list');
  ul.innerHTML = '';
  state.merge.forEach((m, i) => {
    const li = document.createElement('li');
    li.textContent = m.name;
    if (i === state.selectedMerge) li.classList.add('selected');
    li.onclick = () => { state.selectedMerge = i; renderMergeList(); };
    ul.appendChild(li);
  });
}

function addMergeFile(file) {
  const url = URL.createObjectURL(file);
  state.merge.push({ name: file.name, url });
  renderMergeList();
  schedulePreview();
}

function delMerge() {
  if (state.selectedMerge >= 0) {
    state.merge.splice(state.selectedMerge, 1);
    state.selectedMerge = -1;
    renderMergeList();
    schedulePreview();
  }
}

// ---------------------------------------------------------------------------
// Printer connection & print
// ---------------------------------------------------------------------------
async function connectPrinter() {
  if (!checkSupport()) return;
  try {
    const port = await window.PTPrinter.SerialTransport.requestPort();
    const transport = new window.PTPrinter.SerialTransport(port);
    await transport.open(9600);
    state.transport = transport;
    state.connected = true;
    $('port-status').textContent = 'Connected';
    $('connect-btn').textContent = 'Disconnect';
    log('=> Printer connected.');
  } catch (e) {
    log(`Connection failed: ${e.message}`);
    setStatus(`Connection failed: ${e.message}`);
  }
}

async function disconnectPrinter() {
  if (state.transport) {
    await state.transport.close();
    state.transport = null;
  }
  state.connected = false;
  $('port-status').textContent = 'Not connected';
  $('connect-btn').textContent = 'Connect printer';
  log('=> Printer disconnected.');
}

async function doPrint() {
  if (!state.connected || !state.transport) {
    setStatus('Connect the printer first.');
    return;
  }
  if (state.busy) return;
  if (!confirm('Send the label to the printer?')) return;
  state.busy = true;
  $('print-btn').disabled = true;
  try {
    const args = makeArgs(false);
    log('=> Building label…');
    const image = await window.PTLabel.buildLabel(args);
    const padded = window.PTLabel.rasterizeLabel(image, args);
    const data = imageDataToBytes(padded);
    await window.PTPrinter.doPrintJob(
      state.transport, args, data,
      (line) => log(line),
      (bar) => setStatus(`Sending [${bar}]`),
    );
    setStatus('Print job sent.');
  } catch (e) {
    log(`ERROR: ${e.message}`);
    setStatus(`Error: ${e.message}`);
  } finally {
    state.busy = false;
    $('print-btn').disabled = false;
  }
}

// Convert the padded 1-bit canvas to the raw 16-bytes-per-line raster.
function imageDataToBytes(canvas) {
  const w = canvas.width, h = canvas.height;
  const ctx = canvas.getContext('2d');
  const img = ctx.getImageData(0, 0, w, h).data;
  const bytesPerLine = w / 8;
  const out = new Uint8Array(bytesPerLine * h);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      // Black pixel -> 1 bit (the raster is inverted: ink = 1).
      const black = img[i] < 128 ? 1 : 0;
      if (black) out[y * bytesPerLine + (x >> 3)] |= (0x80 >> (x & 7));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Save PNG
// ---------------------------------------------------------------------------
function savePng() {
  if (!state.pilImage) { setStatus('Nothing to save.'); return; }
  const a = document.createElement('a');
  a.download = 'label.png';
  a.href = state.pilImage.toDataURL('image/png');
  a.click();
}

// ---------------------------------------------------------------------------
// Ligature features (best-effort: list common GSUB tags)
// ---------------------------------------------------------------------------
function populateLigatures() {
  const sel = $('ligatures');
  const tags = ['', 'calt', 'liga', 'dlig', 'clig', 'rlig', 'kern', 'ss01', 'ss02'];
  sel.innerHTML = '';
  for (const t of tags) {
    const o = document.createElement('option');
    o.value = t;
    o.textContent = t || '(off)';
    sel.appendChild(o);
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------
function wire() {
  // Live preview on any control change.
  document.querySelectorAll('input, select, textarea').forEach((el) => {
    el.addEventListener('input', schedulePreview);
    el.addEventListener('change', schedulePreview);
  });
  $('fontname').addEventListener('change', (e) => {
    state.fontFamily = e.target.value;
    schedulePreview();
  });
  $('font-upload-btn').onclick = () => $('font-upload').click();
  $('font-upload').onchange = (e) => { if (e.target.files[0]) loadFontFile(e.target.files[0]); };

  $('merge-add').onclick = () => $('merge-file').click();
  $('merge-file').onchange = (e) => { if (e.target.files[0]) addMergeFile(e.target.files[0]); };
  $('merge-del').onclick = delMerge;

  $('connect-btn').onclick = () => state.connected ? disconnectPrinter() : connectPrinter();
  $('print-btn').onclick = doPrint;
  $('refresh-btn').onclick = runPreview;
  $('save-png').onclick = savePng;

  $('zoom-in').onclick = () => setZoom('in');
  $('zoom-out').onclick = () => setZoom('out');
  $('zoom-fit').onclick = () => setZoom('fit');
  $('zoom-tape').onclick = () => setZoom('tape');

  window.addEventListener('resize', () => { if (state.zoom === null) renderPreview(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F5') { e.preventDefault(); runPreview(); }
    if (e.ctrlKey && e.key === 'p') { e.preventDefault(); doPrint(); }
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function init() {
  checkSupport();
  populateFonts();
  populateLigatures();
  wire();
  clearPreview();
  log('PT-P300BT Label Designer (web) ready.');
  if (!window.PTPrinter.SerialTransport.isSupported()) {
    log('Web Serial unavailable: printing disabled, preview still works.');
  }
}

document.addEventListener('DOMContentLoaded', init);
