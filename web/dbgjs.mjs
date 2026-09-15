import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import { createCanvas } from 'canvas';

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, 'label.js'), 'utf8');
const sandbox = {
  window: {}, console, Uint8Array, ArrayBuffer, DataView, Math, Date, Promise, setTimeout,
  document: { createElement(tag) { return tag === 'canvas' ? createCanvas(1, 1) : {}; } },
  Image: class { set src(v) { this._src = v; this.onerror && this.onerror(); } },
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const L = sandbox.window.PTLabel;

const args = {
  fontname: 'Arial', text_to_print: ['Hello'], unicode: false, lines: false,
  merge: [], resize: 1.0, x_merge: 0, y_merge: 12, merge_gap: 0,
  no_feed: false, auto_cut: false, end_margin: 0, nocomp: false,
  fill: 'black', stroke_fill: null, stroke_width: 0, text_size: null,
  font_scale: null, h_padding: 5, v_shift: 0, line_spacing: 1.2,
  center_text: false, tape_width: 12.0, emoji_print_area: false,
  uniform_font: false, ligatures: null, mono_emoji: true,
  luma_lo: 140, luma_hi: 175, tab_width: 8, white_level: 240, threshold: 75,
  fixed_width: null, fixed_font_size: null,
};
const image = await L.buildLabel(args);
const padded = L.rasterizeLabel(image, args);
const w = padded.width, h = padded.height;
const d = padded.getContext('2d').getImageData(0, 0, w, h).data;
console.log(`JS ${w}x${h}`);
for (let y = 0; y < h; y += 4) {
  let line = '';
  for (let x = 0; x < w; x += 2) {
    const i = (y * w + x) * 4;
    line += d[i] >= 128 ? '#' : '.';
  }
  console.log(line);
}
