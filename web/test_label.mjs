/*
 * test_label.mjs — verifies the JavaScript label-builder port (web/label.js)
 * using node-canvas for headless rendering.
 *
 * Run:  node web/test_label.mjs
 *
 * Fidelity checks:
 *  - auto-fit converges so the rendered text height equals the printable area
 *  - rasterizeLabel rotates/mirrors/pads to 128px and produces binary output
 *  - emoji detection, tab expansion, unicode escapes, multiline dimensions
 *  - Python reference (printlabel.py) is invoked to confirm the target height
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';
import vm from 'node:vm';
import { createCanvas } from 'canvas';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..');

// --- Load label.js with a canvas-backed window shim ------------------------
const src = readFileSync(join(__dirname, 'label.js'), 'utf8');
const sandbox = {
  window: {},
  console,
  Uint8Array, ArrayBuffer, DataView, Math, Date, Promise, setTimeout,
  document: {
    createElement(tag) {
      if (tag === 'canvas') return createCanvas(1, 1);
      return {};
    },
  },
  Image: class {
    set src(v) { this._src = v; this.onerror && this.onerror(); }
  },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const L = sandbox.window.PTLabel;

// --- Tiny test framework ---------------------------------------------------
let passed = 0, failed = 0;
function ok(cond, msg) {
  if (cond) { passed++; console.log(`  ok   ${msg}`); }
  else { failed++; console.log(`  FAIL ${msg}`); }
}
function eq(actual, expected, msg) {
  ok(JSON.stringify(actual) === JSON.stringify(expected),
    `${msg} (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`);
}

// --- Base args factory -----------------------------------------------------
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

// --- Python reference ------------------------------------------------------
console.log('Python reference (printlabel.py)');
try {
  const out = execFileSync('python', ['-c', `
import printlabel as pl, io, contextlib
a = pl.set_args().parse_args(['-n','x','arial.ttf','Hello'])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    img = pl.build_label(a)
print(img.size[0], img.size[1])
`], { cwd: repoRoot, encoding: 'utf8' }).trim();
  const [pw, ph] = out.split(/\s+/).map(Number);
  console.log(`  Python 'Hello' -> ${pw}x${ph}`);
  ok(ph === 88, 'Python reference height is 88');
} catch (e) {
  console.log(`  (skipped: ${e.message.split('\n')[0]})`);
}

// --- Emoji detection -------------------------------------------------------
console.log('isEmojiChar');
eq(L.isEmojiChar('\u{1F600}'), true, 'grinning face is emoji');
eq(L.isEmojiChar('A'), false, 'letter A is not emoji');
eq(L.isEmojiChar('\uFE0F'), false, 'variation selector is not emoji');
eq(L.isEmojiChar('\u200D'), false, 'ZWJ is not emoji');

// --- Font measurement sanity ----------------------------------------------
console.log('WebFont measurement');
{
  const f = new L.WebFont('Arial', 40);
  const len = f.getlength('Hello');
  ok(len > 0, `getlength('Hello') = ${len.toFixed(1)} > 0`);
  const b = f.getbbox('Hello', { anchor: 'lt' });
  ok(b[2] > 0 && b[3] > 0, `getbbox lt = ${JSON.stringify(b)}`);
}

// --- calculateMultilineDimensions -----------------------------------------
console.log('calculateMultilineDimensions');
{
  const f = new L.MixedFont('Arial', 40, {});
  const d = L.calculateMultilineDimensions(['A', 'B', 'C'], f, 1.2);
  ok(d.height > 0 && d.lineHeights.length === 3,
    `height=${d.height} lines=${d.lineHeights.length}`);
  const single = L.calculateMultilineDimensions(['A'], f, 1.2);
  ok(single.height === Math.round(single.lineHeights[0]),
    'single line height == rounded line height');
}

// --- buildLabel: single line auto-fit -------------------------------------
console.log('buildLabel (single line)');
{
  const args = baseArgs();
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'image height is 88');
  ok(img.width > 0, `image width = ${img.width} > 0`);
  const padded = L.rasterizeLabel(img, args);
  eq(padded.width, 128, 'raster padded to 128 wide');
  eq(padded.height, img.width, 'raster height == source width (rotated)');
}

// --- buildLabel: multiline auto-fit ---------------------------------------
console.log('buildLabel (multiline)');
{
  const args = baseArgs({ text_to_print: ['Line1\\nLine2'] });
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'multiline height is 88');
  ok(img.width > 0, `multiline width = ${img.width} > 0`);
}

// --- buildLabel: three lines ----------------------------------------------
console.log('buildLabel (three lines)');
{
  const args = baseArgs({ text_to_print: ['A\\nB\\nC'] });
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'three-line height is 88');
  ok(img.width > 0, `three-line width = ${img.width} > 0`);
}

// --- buildLabel: emoji -----------------------------------------------------
console.log('buildLabel (emoji)');
{
  const args = baseArgs({ text_to_print: ['A\u{1F60C}B'] });
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'emoji label height is 88');
  ok(img.width > 0, `emoji width = ${img.width} > 0`);
}

// --- buildLabel: uniform font ---------------------------------------------
console.log('buildLabel (uniform font)');
{
  const args = baseArgs({ text_to_print: ['acca'], uniform_font: true });
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'uniform height is 88');
  const args2 = baseArgs({ text_to_print: ['accal'], uniform_font: true });
  const img2 = await L.buildLabel(args2);
  eq(img2.height, 88, 'uniform (accal) height is 88');
}

// --- buildLabel: fixed font size ------------------------------------------
console.log('buildLabel (fixed font size)');
{
  const args = baseArgs({ fixed_font_size: 40 });
  const img = await L.buildLabel(args);
  eq(img.height, 88, 'fixed-size height is 88');
  ok(img.width > 0, `fixed-size width = ${img.width} > 0`);
}

// --- buildLabel: fixed width ----------------------------------------------
console.log('buildLabel (fixed width)');
{
  const args = baseArgs({ fixed_width: 30.0 });
  const img = await L.buildLabel(args);
  const target = Math.round(30.0 / L.MM_PER_DOT);
  ok(img.width >= target, `fixed width ${img.width} >= ${target}`);
}

// --- buildLabel: empty text -----------------------------------------------
console.log('buildLabel (empty text)');
{
  const args = baseArgs({ text_to_print: [''] });
  let threw = false;
  try { await L.buildLabel(args); } catch { threw = true; }
  ok(threw, 'empty text throws "Null image generated."');
}

// --- rasterizeLabel: binary output ----------------------------------------
console.log('rasterizeLabel (binary)');
{
  const args = { threshold: 75 };
  const img = createCanvas(20, 88);
  const ctx = img.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 20, 88);
  ctx.fillStyle = '#000'; ctx.fillRect(5, 20, 10, 40);
  const padded = L.rasterizeLabel(img, args);
  eq(padded.width, 128, 'padded width 128');
  const data = padded.getContext('2d').getImageData(0, 0, 128, 20).data;
  let binary = true;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i] !== 0 && data[i] !== 255) { binary = false; break; }
  }
  ok(binary, 'output is binary (0 or 255)');
}

// --- buildLabel: ligatures (graceful fallback) ----------------------------
console.log('buildLabel (ligatures)');
{
  // node-canvas has no SVG font-feature-settings, so the ligature render
  // path falls back to the plain draw. The label must still build, and the
  // width must never be NARROWER than the plain one (the shaped-advance
  // widening that prevents right-side truncation can only grow it).
  const off = await L.buildLabel(baseArgs({ text_to_print: ['fi'] }));
  const on = await L.buildLabel(baseArgs({ text_to_print: ['fi'], ligatures: 'dlig' }));
  eq(on.height, 88, 'ligature label height is 88');
  ok(on.width >= off.width, `ligature width >= plain (${on.width} >= ${off.width})`);
}

// --- expandTabs ------------------------------------------------------------
console.log('expandTabs');
{
  eq(L.expandTabs('a\tb', 4), 'a   b', 'tab expands to next multiple of 4');
  eq(L.expandTabs('abcd\te', 4), 'abcd    e', 'tab at column 4 -> 4 spaces');
  eq(L.expandTabs('no tabs', 8), 'no tabs', 'no tabs unchanged');
}

// --- decodeUnicodeEscapes --------------------------------------------------
console.log('decodeUnicodeEscapes');
{
  eq(L.decodeUnicodeEscapes('\\u0041'), 'A', '\\u0041 -> A');
  eq(L.decodeUnicodeEscapes('\\U0001F600'), '\u{1F600}', '\\U0001F600 -> emoji');
  eq(L.decodeUnicodeEscapes('plain'), 'plain', 'plain unchanged');
}

// --- Summary ---------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
