# PT-P300BT Label Designer — Web

Browser version of the printlabel tool for the Brother PT-P300BT
label printer. It provides a HTML/CSS/JavaScript application to print over Bluetooth using the **Web Serial API**
(Bluetooth Classic RFCOMM/SPP).

## Files in this directory

| File | Purpose |
| --- | --- |
| `index.html` | The application page. |
| `favicon.ico` | Application icon (label/tape motif, 16 and 32 px frames). |
| `style.css` | Modern dark theme. |
| `app.js` | GUI controller: builds the args object from the controls, drives the live preview, zoom/pan, merge list, printer connection, print flow, save PNG, dynamic ligature list, log Copy/Clear. |
| `label.js` | printlabel label algorithms: auto-fit font sizing, multiline layout, uniform font sizing, emoji raster overlay (in-line and print-area band), luminance-based B/W emoji, image merge/crop/resize, fixed width, text stretching, rulers, TAB expansion, Unicode escapes, OpenType ligature shaping via SVG `font-feature-settings`, and the rotate/invert/mirror/threshold/128-px rasterization. Exposed as `window.PTLabel`. |
| `printer.js` | printer protocol: PTCBP command serialization, PackBits RLE compression, status-register parsing, the full print job flow, and the Web Serial transport (`SerialTransport`) that opens Bluetooth RFCOMM/SPP devices. Exposed as `window.PTPrinter`. |
| `test_printer.mjs` | Node test suite for the protocol (40 tests): command bytes, PackBits, status parsing, printer configuration, print job flow with a fake transport. Run: `node web/test_printer.mjs`. |
| `test_label.mjs` | Node test suite for the label algorithms (35 tests, needs `npm i canvas`): auto-fit convergence, rasterization, emoji detection, TAB expansion, Unicode escapes. Run: `node web/test_label.mjs`. |
| `test_web.mjs` | Playwright end-to-end suite (49 tests) driving the real page in Chrome and WebKit: preview, every control, zoom, converted-raster view, dynamic ligature list, save PNG, print guard, image merge, full print flow with a mocked serial port, and the unsupported-browser banner. Run: `node web/test_web.mjs` (needs `npm run serve` + `npx playwright install chrome webkit`). |
| `test_parity.mjs` | Compares the web raster output with the Python reference byte-for-byte for many feature combinations (uses node-canvas, whose font metrics match Pillow). Run: `node web/test_parity.mjs`. |
| `ref_raster.py` | Emits reference raster bytes from `printlabel.py` as JSON (`{"width", "height", "bytes"}`) for the parity test. Usage: `python web/ref_raster.py <case>`. |
| `dbg.py` | Debug helper that prints the Python raster as ASCII art (for visual comparison with `dbgjs.mjs`). |
| `dbgjs.mjs` | Debug helper that prints the JavaScript raster as ASCII art (same format as `dbg.py`). |

## Running it

Web Serial requires a **secure context**, so serve the folder over HTTP
(`file://` works for the preview but **not** for the serial port):

```bash
cd web
python -m http.server 8000
# then open http://localhost:8000/ in Chrome or Edge
```

or, from the repository root:

```bash
npm run serve
```

## Browser support

| Browser | Preview & algorithms | Bluetooth printing |
| --- | --- | --- |
| Chrome / Edge (desktop, 117+) | ✅ | ✅ (Web Serial + RFCOMM/SPP) |
| Chrome on Android | ✅ | ✅ |
| Firefox 155+ (desktop) | ✅ | ⚠️ (Web Serial present; RFCOMM/SPP support may vary — Chrome/Edge recommended) |
| Older Firefox | ✅ | ❌ (no Web Serial) |
| Safari (macOS/iOS) | ✅ | ❌ (no Web Serial) |

The **"Web Serial available"** badge means the browser exposes the Web
Serial API with a working `requestPort()` (checked at runtime: Firefox
before v155 has no `navigator.serial` at all). Chrome and Edge are the
fully supported browsers for Bluetooth RFCOMM/SPP printing. On browsers
without Web Serial (older Firefox, Safari) the page shows the red
"unavailable" badge plus an informative banner — printing is disabled
there, but the preview and every label algorithm keep working.

When you press **Connect printer**, the browser shows its own permission
prompt ("… wants to connect to a serial port") listing the paired Bluetooth
devices that expose the SPP service; the page requests the standard SPP
service class UUID (`00001101-…`), which is how Chrome reaches Bluetooth
Classic RFCOMM devices without an OS serial port.

## Tests

```bash
npm test          # runs all three suites
npm run serve     # local server needed by test_web.mjs
```
