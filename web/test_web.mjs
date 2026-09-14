/*
 * test_web.mjs — end-to-end browser test of the PT-P300BT web app.
 *
 * Run:  node web/test_web.mjs
 *
 * Uses Playwright with the system Chrome (channel: 'chrome') to load the
 * page over HTTP and exercise every GUI feature: preview rendering, all
 * controls, zoom, converted-raster view, ligatures, emoji, multiline,
 * uniform font, fixed width, save PNG, print guard, and the support banner.
 *
 * Requires a local HTTP server on http://localhost:8000 serving web/.
 */

import { chromium, webkit } from 'playwright';

const BASE = process.env.WEB_URL || 'http://localhost:8000/';

let passed = 0, failed = 0;
function ok(cond, msg) {
  if (cond) { passed++; console.log(`  ok   ${msg}`); }
  else { failed++; console.log(`  FAIL ${msg}`); }
}
function eq(a, b, msg) { ok(JSON.stringify(a) === JSON.stringify(b), `${msg} (expected ${JSON.stringify(b)}, got ${JSON.stringify(a)})`); }

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

await page.goto(BASE, { waitUntil: 'load' });

// Open every collapsible group so all controls are interactable.
await page.evaluate(() => {
  document.querySelectorAll('details').forEach((d) => { d.open = true; });
});
await page.waitForTimeout(200);

// Helper: read the preview canvas ink + size.
async function preview() {
  return page.evaluate(() => {
    const c = document.querySelector('#preview-canvas');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let ink = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] < 128) ink++;
    return { w: c.width, h: c.height, ink };
  });
}
async function setText(t) {
  await page.fill('#text', t);
  await page.waitForTimeout(500);
}

// --- 1. Page load & support banner ----------------------------------------
console.log('page load');
ok(await page.title() !== '', 'page has a title');
eq(await page.isVisible('#support-badge'), true, 'support badge visible');
eq(await page.textContent('#support-badge'), 'Web Serial available', 'Web Serial detected in Chrome');
eq(await page.isHidden('#support-banner'), true, 'support banner hidden when supported');
eq(await page.$('#preview-canvas') !== null, true, 'preview canvas present');

// --- 2. Font list ----------------------------------------------------------
console.log('font list');
{
  const fonts = await page.$$eval('#fontname option', (o) => o.map((x) => x.value));
  ok(fonts.length >= 20, `font list populated (${fonts.length} fonts)`);
  ok(fonts.includes('Arial'), 'Arial present');
  ok(fonts.includes('DejaVu Sans Mono'), 'DejaVu Sans Mono present');
}

// --- 3. Basic preview ------------------------------------------------------
console.log('basic preview');
await setText('Hello');
{
  const p = await preview();
  ok(p.ink > 0, `preview renders ink (${p.ink} px)`);
  const status = await page.textContent('#status');
  ok(/raster/.test(status), `status shows raster info: "${status}"`);
}

// --- 4. Multiline ----------------------------------------------------------
console.log('multiline');
await setText('Line1\nLine2');
{
  const p = await preview();
  ok(p.ink > 0, `multiline renders (${p.ink} px)`);
  const status = await page.textContent('#status');
  ok(/raster/.test(status), 'multiline status ok');
}

// --- 5. Emoji --------------------------------------------------------------
console.log('emoji');
await setText('A\u{1F60C}B');
{
  const p = await preview();
  ok(p.ink > 0, `emoji label renders (${p.ink} px)`);
}

// --- 6. Uniform font -------------------------------------------------------
console.log('uniform font');
await setText('acca');
await page.check('#uniform_font');
await page.waitForTimeout(500);
{
  const p1 = await preview();
  await setText('accal');
  const p2 = await preview();
  ok(p1.ink > 0 && p2.ink > 0, 'uniform font renders both variants');
}
await page.uncheck('#uniform_font');
await page.waitForTimeout(300);

// --- 7. Fixed width --------------------------------------------------------
console.log('fixed width');
await setText('Hi');
await page.fill('#fixed_width', '30');
await page.waitForTimeout(500);
{
  const status = await page.textContent('#status');
  ok(/raster/.test(status), 'fixed width renders');
}
await page.fill('#fixed_width', '0');
await page.waitForTimeout(300);

// --- 8. Font scale ---------------------------------------------------------
console.log('font scale');
await setText('Scale');
await page.fill('#font_scale', '60');
await page.waitForTimeout(500);
{
  const p = await preview();
  ok(p.ink > 0, 'font scale renders');
}
await page.fill('#font_scale', '100');
await page.waitForTimeout(300);

// --- 9. Center text --------------------------------------------------------
console.log('center text');
await setText('Center');
await page.check('#center_text');
await page.waitForTimeout(500);
ok((await preview()).ink > 0, 'center text renders');
await page.uncheck('#center_text');
await page.waitForTimeout(300);

// --- 10. Rulers ------------------------------------------------------------
console.log('rulers');
await setText('Rulers');
await page.check('#lines');
await page.waitForTimeout(500);
ok((await preview()).ink > 0, 'rulers render');
await page.uncheck('#lines');
await page.waitForTimeout(300);

// --- 11. Ligatures ---------------------------------------------------------
console.log('ligatures');
await setText('fi');
await page.selectOption('#fontname', 'Georgia');
await page.waitForTimeout(500);
{
  const off = await preview();
  await page.selectOption('#ligatures', 'dlig');
  await page.waitForTimeout(700);
  const on = await preview();
  ok(on.ink !== off.ink, `dlig changes rendering (${off.ink} -> ${on.ink})`);
}
await page.selectOption('#ligatures', '');
await page.selectOption('#fontname', 'Arial');
await page.waitForTimeout(300);

// --- 12. Converted raster view --------------------------------------------
console.log('converted raster view');
await setText('Raster');
{
  const normal = await preview();
  await page.check('#preview_conv');
  await page.waitForTimeout(600);
  const conv = await preview();
  ok(conv.w === 128 * 2, `converted raster is 128px wide at 2x (${conv.w})`);
  ok(conv.w !== normal.w, 'converted view differs from normal view');
  await page.uncheck('#preview_conv');
  await page.waitForTimeout(400);
}

// --- 13. Zoom controls -----------------------------------------------------
console.log('zoom controls');
{
  const before = await page.textContent('#zoom-info');
  await page.click('#zoom-in');
  const afterIn = await page.textContent('#zoom-info');
  ok(before !== afterIn, `zoom-in changes zoom (${before} -> ${afterIn})`);
  await page.click('#zoom-fit');
  const afterFit = await page.textContent('#zoom-info');
  ok(afterFit !== afterIn, 'zoom-fit changes zoom');
  await page.click('#zoom-tape');
  const afterTape = await page.textContent('#zoom-info');
  ok(afterTape !== afterFit, 'zoom-tape changes zoom');
}

// --- 14. Save PNG ----------------------------------------------------------
console.log('save PNG');
{
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 5000 }).catch(() => null),
    page.click('#save-png'),
  ]);
  ok(download !== null, 'save PNG triggers a download');
  if (download) ok(/\.png$/i.test(download.suggestedFilename()), 'download is a .png');
}

// --- 15. Print guard (no connection) --------------------------------------
console.log('print guard');
{
  await page.click('#print-btn');
  await page.waitForTimeout(300);
  const status = await page.textContent('#status');
  ok(/Connect the printer first/.test(status), 'print without connection is guarded');
}

// --- 16. Refresh button ----------------------------------------------------
console.log('refresh button');
{
  await setText('Refresh');
  await page.click('#refresh-btn');
  await page.waitForTimeout(400);
  ok((await preview()).ink > 0, 'refresh re-renders the preview');
}

// --- 17. Live preview toggle ----------------------------------------------
console.log('live preview toggle');
{
  await page.uncheck('#auto_preview');
  await page.fill('#text', 'NoLive');
  await page.waitForTimeout(400);
  const before = await preview();
  await page.click('#refresh-btn');
  await page.waitForTimeout(400);
  const after = await preview();
  ok(before.ink !== after.ink, 'manual refresh needed when live preview off');
  await page.check('#auto_preview');
  await page.waitForTimeout(300);
}

// --- 18. Merge image controls ---------------------------------------------
console.log('merge controls');
{
  ok(await page.isVisible('#merge-add'), 'merge add button present');
  ok(await page.isVisible('#merge-del'), 'merge remove button present');
  ok(await page.$('#merge-list') !== null, 'merge list present');
}

// --- 19. Advanced controls present ----------------------------------------
console.log('advanced controls');
{
  const ids = ['font_scale', 'line_spacing', 'h_padding', 'v_shift', 'text_size',
    'fixed_width', 'tape_width', 'fixed_font_size', 'luma_lo', 'luma_hi',
    'fill', 'stroke_fill', 'stroke_width', 'end_margin', 'unicode', 'lines',
    'resize', 'x_merge', 'y_merge', 'merge_gap', 'white_level', 'threshold',
    'no_print', 'auto_cut', 'no_feed', 'raw', 'nocomp', 'tab_width'];
  const missing = [];
  for (const id of ids) {
    if (!(await page.$(`#${id}`))) missing.push(id);
  }
  eq(missing, [], 'all advanced controls present');
}

// --- 20. Unicode escapes ---------------------------------------------------
console.log('unicode escapes');
await setText('\\u0041\\u0042');
await page.check('#unicode');
await page.waitForTimeout(500);
ok((await preview()).ink > 0, 'unicode escape text renders');
await page.uncheck('#unicode');
await page.waitForTimeout(300);

// --- 21. TAB expansion -----------------------------------------------------
console.log('TAB expansion');
await setText('a\tb');
await page.waitForTimeout(500);
ok((await preview()).ink > 0, 'TAB text renders');

// --- 22. No JS errors ------------------------------------------------------
console.log('runtime errors');
{
  const real = errors.filter((e) => !/willReadFrequently/.test(e) && !/favicon/.test(e) && !/404/.test(e));
  eq(real, [], 'no JavaScript errors during the session');
}

// --- 23. Unsupported browser banner ---------------------------------------
console.log('unsupported browser banner');
{
  // WebKit (Safari engine) has no Web Serial API.
  const wk = await webkit.launch({ headless: true });
  const p2 = await wk.newPage();
  await p2.goto(BASE, { waitUntil: 'load' });
  await p2.waitForTimeout(400);
  const badge = await p2.textContent('#support-badge');
  const bannerVisible = await p2.isVisible('#support-banner');
  const bannerText = await p2.textContent('#support-banner-text');
  ok(/unavailable/i.test(badge), `badge reports unavailable ("${badge}")`);
  ok(bannerVisible, 'informative banner is shown');
  ok(/Chrome or Edge/.test(bannerText), 'banner names the supported browsers');
  // Preview must still work without Web Serial.
  await p2.fill('#text', 'NoSerial');
  await p2.waitForTimeout(600);
  const ink = await p2.evaluate(() => {
    const c = document.querySelector('#preview-canvas');
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] < 128) n++;
    return n;
  });
  ok(ink > 0, 'preview still works without Web Serial');
  await wk.close();
}

// --- 24. Merge a real image ------------------------------------------------
console.log('merge image');
{
  // Build a small PNG (black square on white) as a data URL and inject it
  // into the merge list via the app's own state, then verify the label grows.
  const before = await page.evaluate(async () => {
    const L = window.PTLabel;
    const args = {
      fontname: 'Arial', text_to_print: ['Hi'], unicode: false, lines: false,
      merge: [], resize: 1.0, x_merge: 0, y_merge: 12, merge_gap: 0,
      no_feed: false, auto_cut: false, end_margin: 0, nocomp: false,
      fill: 'black', stroke_fill: null, stroke_width: 0, text_size: null,
      font_scale: null, h_padding: 5, v_shift: 0, line_spacing: 1.2,
      center_text: false, tape_width: 12.0, emoji_print_area: false,
      uniform_font: false, ligatures: null, mono_emoji: true,
      luma_lo: 140, luma_hi: 175, tab_width: 8, white_level: 240, threshold: 75,
      fixed_width: null, fixed_font_size: null,
    };
    const img = await L.buildLabel(args);
    return img.width;
  });
  const after = await page.evaluate(async () => {
    const L = window.PTLabel;
    // Create a 40x40 black square PNG data URL.
    const c = document.createElement('canvas');
    c.width = 40; c.height = 40;
    const cx = c.getContext('2d');
    cx.fillStyle = '#fff'; cx.fillRect(0, 0, 40, 40);
    cx.fillStyle = '#000'; cx.fillRect(10, 10, 20, 20);
    const url = c.toDataURL('image/png');
    const args = {
      fontname: 'Arial', text_to_print: ['Hi'], unicode: false, lines: false,
      merge: [url], resize: 1.0, x_merge: 0, y_merge: 12, merge_gap: 0,
      no_feed: false, auto_cut: false, end_margin: 0, nocomp: false,
      fill: 'black', stroke_fill: null, stroke_width: 0, text_size: null,
      font_scale: null, h_padding: 5, v_shift: 0, line_spacing: 1.2,
      center_text: false, tape_width: 12.0, emoji_print_area: false,
      uniform_font: false, ligatures: null, mono_emoji: true,
      luma_lo: 140, luma_hi: 175, tab_width: 8, white_level: 240, threshold: 75,
      fixed_width: null, fixed_font_size: null,
    };
    const img = await L.buildLabel(args);
    return img.width;
  });
  ok(after > before, `merge image widens the label (${before} -> ${after})`);
}

// --- 25. Print flow with a mocked serial port ------------------------------
console.log('print flow (mocked serial)');
{
  const result = await page.evaluate(async () => {
    const L = window.PTLabel, P = window.PTPrinter;
    const args = {
      fontname: 'Arial', text_to_print: ['Print'], unicode: false, lines: false,
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
    const d = padded.getContext('2d').getImageData(0, 0, padded.width, padded.height).data;
    const bytes = new Uint8Array(padded.width * padded.height);
    for (let i = 0; i < bytes.length; i++) bytes[i] = d[i * 4] < 128 ? 1 : 0;
    function mkStatus(type) {
      const s = new Uint8Array(32);
      s.set([0x80, 0x20, 0x42, 0x30], 0);
      s[18] = type; s[19] = 0x00;
      return s;
    }
    let reads = 0;
    const written = [];
    const fake = {
      async write(b) { written.push(b.slice()); },
      async read() { reads++; return reads === 1 ? mkStatus(0x00) : mkStatus(0x01); },
      async resetInputBuffer() {},
    };
    const logs = [];
    await P.doPrintJob(fake, args, bytes, (l) => logs.push(l), () => {});
    const last = written[written.length - 1];
    return {
      writes: written.length,
      totalBytes: written.reduce((a, b) => a + b.length, 0),
      lastByte: last[last.length - 1],
      logs,
    };
  });
  ok(result.writes > 0, `print job wrote ${result.writes} chunks`);
  eq(result.lastByte, 0x1a, 'print job ends with the print command');
  ok(result.logs.some((l) => /Querying printer status/.test(l)), 'logged status query');
  ok(result.logs.some((l) => /Configuring printer/.test(l)), 'logged configuration');
}

// --- Summary ---------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
await browser.close();
process.exit(failed ? 1 : 0);
