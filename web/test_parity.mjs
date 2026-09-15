/*
 * test_parity.mjs — compares the web port's raster output with the Python
 * reference (printlabel.py) byte-for-byte, for many feature combinations.
 *
 * Run:  node web/test_parity.mjs
 *
 * Uses node-canvas (same FreeType/Pillow metrics as Python) so the only
 * expected differences are the text-shaping engine's ink extents. Reports
 * the pixel difference ratio per case; a case passes when the rasters have
 * the same dimensions and the difference is below a small tolerance.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import vm from 'node:vm';
import { createCanvas } from 'canvas';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..');

// --- Load label.js with a canvas shim -------------------------------------
const src = readFileSync(join(__dirname, 'label.js'), 'utf8');
const sandbox = {
  window: {}, console,
  Uint8Array, ArrayBuffer, DataView, Math, Date, Promise, setTimeout,
  document: {
    createElement(tag) { return tag === 'canvas' ? createCanvas(1, 1) : {}; },
  },
  Image: class { set src(v) { this._src = v; this.onerror && this.onerror(); } },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const L = sandbox.window.PTLabel;

// --- Args factory (mirrors ref_raster.py CASES) ---------------------------
function baseArgs(over = {}) {
  return Object.assign({
    fontname: 'Arial', text_to_print: ['Hello'], unicode: false, lines: false,
    merge: [], resize: 1.0, x_merge: 0, y_merge: 12, merge_gap: 0,
    no_feed: false, auto_cut: false, end_margin: 0, nocomp: false,
    fill: 'black', stroke_fill: null, stroke_width: 0, text_size: null,
    font_scale: null, h_padding: 5, v_shift: 0, line_spacing: 1.2,
    center_text: false, tape_width: 12.0, emoji_print_area: false,
    uniform_font: false, ligatures: null, mono_emoji: true,
    luma_lo: 140, luma_hi: 175, tab_width: 8, white_level: 240, threshold: 75,
    fixed_width: null, fixed_font_size: null,
  }, over);
}

const CASES = {
  hello: baseArgs(),
  multiline: baseArgs({ text_to_print: ['Line1\\nLine2'] }),
  three_lines: baseArgs({ text_to_print: ['A\\nB\\nC'] }),
  symbols: baseArgs({ text_to_print: ['\u2192\u2190\u2191\u2193\u2605\u2665\u00a9\u00ae\u2122'] }),
  emoji: baseArgs({ text_to_print: ['A\u{1F60C}B'] }),
  uniform: baseArgs({ text_to_print: ['acca'], uniform_font: true }),
  fixed_size: baseArgs({ fixed_font_size: 40 }),
  center: baseArgs({ text_to_print: ['Hi'], center_text: true }),
  rulers: baseArgs({ text_to_print: ['Hi'], lines: true }),
  tab: baseArgs({ text_to_print: ['a\tb'] }),
  unicode_esc: baseArgs({ text_to_print: ['\\u0041\\u0042'], unicode: true }),
  tape6: baseArgs({ tape_width: 6.0 }),
  tape9: baseArgs({ tape_width: 9.0 }),
  hpad: baseArgs({ text_to_print: ['Hi'], h_padding: 20 }),
  vshift: baseArgs({ text_to_print: ['Hi'], v_shift: 5 }),
  endmargin: baseArgs({ text_to_print: ['Hi'], end_margin: 30 }),
  linespacing: baseArgs({ text_to_print: ['A\\nB'], line_spacing: 1.5 }),
  fontscale: baseArgs({ font_scale: 60 }),
  textsize: baseArgs({ text_to_print: ['Hello'], text_size: 40 }),
  fixedwidth: baseArgs({ text_to_print: ['Hi'], fixed_width: 30.0 }),
  stroke: baseArgs({ text_to_print: ['Hi'], stroke_width: 2, stroke_fill: 'black' }),
};

// --- Python reference ------------------------------------------------------
function pyRef(caseName) {
  const out = execFileSync('python', [join(__dirname, 'ref_raster.py'), caseName],
    { cwd: repoRoot, encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(out);
}

// --- JS raster -> 1 byte/pixel --------------------------------------------
function jsRaster(args) {
  return L.buildLabel(args).then((image) => {
    const padded = L.rasterizeLabel(image, args);
    const d = padded.getContext('2d').getImageData(0, 0, padded.width, padded.height).data;
    const out = new Uint8Array(padded.width * padded.height);
    // In the JS raster the text is white (255) and the background black (0);
    // in the Python '1'-mode raster the text is 1. Map text -> 1.
    for (let i = 0; i < out.length; i++) out[i] = d[i * 4] >= 128 ? 1 : 0;
    return { width: padded.width, height: padded.height, bytes: out };
  });
}

// --- Compare ---------------------------------------------------------------
let passed = 0, failed = 0;
const rows = [];
for (const [name, args] of Object.entries(CASES)) {
  let ref;
  try { ref = pyRef(name); } catch (e) {
    failed++; console.log(`  FAIL ${name}: python error ${e.message.split('\n')[0]}`);
    continue;
  }
  const js = await jsRaster(args);
  const refBytes = Buffer.from(ref.bytes, 'hex');
  const sameDims = js.width === ref.width && js.height === ref.height;
  let diff = -1, ratio = -1;
  if (sameDims) {
    diff = 0;
    for (let i = 0; i < refBytes.length; i++) if (refBytes[i] !== js.bytes[i]) diff++;
    ratio = diff / refBytes.length;
  }
  const okCase = sameDims && ratio <= 0.02;
  if (okCase) passed++; else failed++;
  rows.push({ name, ok: okCase, pyW: ref.width, pyH: ref.height, jsW: js.width, jsH: js.height, ratio });
  const tag = okCase ? 'ok  ' : 'FAIL';
  console.log(`  ${tag} ${name.padEnd(14)} py=${ref.width}x${ref.height} js=${js.width}x${js.height} diff=${(ratio * 100).toFixed(2)}%`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
