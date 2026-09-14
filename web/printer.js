/*
 * printer.js — faithful JavaScript port of the PT-P300BT printer protocol.
 *
 * Ports:
 *   - ptcbp.py            (command building / serialization)
 *   - labelmaker_encode.py (raster transfer encoding + PackBits RLE)
 *   - ptstatus.py         (32-byte status register parsing)
 *   - labelmaker.py       (do_print_job / wait_for_print_completion)
 *
 * Transport: Web Serial API (navigator.serial). Chrome/Edge on Windows,
 * macOS and Linux can talk to Bluetooth Classic RFCOMM/SPP devices
 * directly through Web Serial (Chrome 117+), without an OS serial port.
 */

'use strict';

// ---------------------------------------------------------------------------
// ptcbp.py — command schema
// ---------------------------------------------------------------------------
// (op bytes, mnemonic, param schema, data-length hooks)
const CMD_SCHEMA = [
  [new Uint8Array([0x00]), 'nop', null, null],
  [new Uint8Array([0x1b, 0x40]), 'reset', null, null],
  [new Uint8Array([0x1b, 0x69, 0x53]), 'get_status', null, null],
  [new Uint8Array([0x1b, 0x69, 0x61]), 'use_command_set', 'B', null],
  [new Uint8Array([0x1b, 0x69, 0x7a]), 'set_print_parameters', '4BI2B', null],
  [new Uint8Array([0x1b, 0x69, 0x4d]), 'set_page_mode', 'B', null],
  [new Uint8Array([0x1b, 0x69, 0x4b]), 'set_page_mode_advanced', 'B', null],
  [new Uint8Array([0x1b, 0x69, 0x64]), 'set_page_margin', 'H', null],
  [new Uint8Array([0x4d]), 'compression', 'B', null],
  [new Uint8Array([0x0c]), 'print_page', null, null],
  [new Uint8Array([0x1a]), 'print', null, null],
  [new Uint8Array([0x67]), 'data2', 'H', 'len0'],
  [new Uint8Array([0x47]), 'data', 'H', 'len0'],
  [new Uint8Array([0x5a]), 'zerofill', null, null],
];

const MNEMONICS = {};
for (const e of CMD_SCHEMA) MNEMONICS[e[1]] = e;

// struct format -> [size, writer]
const STRUCT_FORMATS = {
  B: { size: 1, write: (dv, off, v) => dv.setUint8(off, v & 0xff) },
  H: { size: 2, write: (dv, off, v) => dv.setUint16(off, v & 0xffff, true) },
  I: { size: 4, write: (dv, off, v) => dv.setUint32(off, v >>> 0, true) },
};

// Parse a Python struct format string (little-endian) into a list of field
// codes, expanding repeat counts: "4BI2B" -> [B,B,B,B,I,B,B].
function parseSchema(schema) {
  const fields = [];
  let count = '';
  for (const ch of schema) {
    if (ch >= '0' && ch <= '9') {
      count += ch;
      continue;
    }
    const n = count ? parseInt(count, 10) : 1;
    for (let i = 0; i < n; i++) fields.push(ch);
    count = '';
  }
  return fields;
}

function schemaSize(schema) {
  let n = 0;
  for (const ch of parseSchema(schema)) n += STRUCT_FORMATS[ch].size;
  return n;
}

function packParams(schema, params) {
  const fields = parseSchema(schema);
  const size = schemaSize(schema);
  const buf = new ArrayBuffer(size);
  const dv = new DataView(buf);
  let off = 0;
  for (let i = 0; i < fields.length; i++) {
    const f = STRUCT_FORMATS[fields[i]];
    f.write(dv, off, params[i] || 0);
    off += f.size;
  }
  return new Uint8Array(buf);
}

// ---------------------------------------------------------------------------
// PackBits (TIFF) RLE — port of the `packbits` Python package used by ptcbp.
// ---------------------------------------------------------------------------
function packbitsEncode(data) {
  const out = [];
  const n = data.length;
  let i = 0;
  while (i < n) {
    // Count the run of identical bytes (max 128).
    let runLen = 1;
    while (i + runLen < n && data[i + runLen] === data[i] && runLen < 128) {
      runLen++;
    }
    if (runLen >= 2) {
      // Replicate run: header = 257 - runLen (as signed byte), then the byte.
      out.push((257 - runLen) & 0xff);
      out.push(data[i]);
      i += runLen;
    } else {
      // Literal run: gather until a run of >=3 identical bytes or 128 bytes.
      const start = i;
      let litLen = 0;
      while (i < n && litLen < 128) {
        // Look ahead: stop if the next 3 bytes are identical.
        if (i + 2 < n && data[i] === data[i + 1] && data[i] === data[i + 2]) {
          break;
        }
        i++;
        litLen++;
      }
      if (litLen === 0) {
        // Safety: emit a single literal to guarantee progress.
        litLen = 1;
        i = start + 1;
      }
      out.push((litLen - 1) & 0xff);
      for (let k = 0; k < litLen; k++) out.push(data[start + k]);
    }
  }
  return new Uint8Array(out);
}

// ---------------------------------------------------------------------------
// ptcbp.py — Opcode serialization
// ---------------------------------------------------------------------------
function serializeControl(mnemonic, ...params) {
  const entry = MNEMONICS[mnemonic];
  if (!entry) throw new Error(`Unknown mnemonic ${mnemonic}`);
  const op = entry[0];
  const schema = entry[2];
  const parts = [op];
  if (schema) parts.push(packParams(schema, params));
  return concatBytes(parts);
}

function serializeData(data, compress = 'none', useData2 = false) {
  const mnemonic = useData2 && compress === 'none' ? 'data2' : 'data';
  const entry = MNEMONICS[mnemonic];
  const op = entry[0];
  const payload = compress === 'rle' ? packbitsEncode(data) : data;
  const lenBuf = new ArrayBuffer(2);
  new DataView(lenBuf).setUint16(0, payload.length, true);
  return concatBytes([op, new Uint8Array(lenBuf), payload]);
}

function concatBytes(parts) {
  let total = 0;
  for (const p of parts) total += p.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Enums (ptcbp.py)
// ---------------------------------------------------------------------------
const CompressionType = { none: 0, rle: 2 };
const CommandSet = { escp: 0, ptcbp: 1, ptouch_template: 3 };
const PageMode = { auto_cut: 1 << 6, mirror: 1 << 7 };
const PageModeAdvanced = {
  half_cut: 1 << 2,
  no_page_chaining: 1 << 3,
  no_cutting_on_special_tape: 1 << 4,
  cut_on_last_label: 1 << 5,
  high_resolution: 1 << 6,
  preserve_buffer: 1 << 7,
};
const PrintParameterField = {
  media_type: 1 << 1,
  width: 1 << 2,
  length: 1 << 3,
  quality: 1 << 6,
  recovery: 1 << 7,
};

// ---------------------------------------------------------------------------
// labelmaker_encode.py — encode_raster_transfer
// ---------------------------------------------------------------------------
function* encodeRasterTransfer(data, nocomp = false) {
  const chunkSize = 16;
  const zeroLine = new Uint8Array(chunkSize);
  for (let i = 0; i < data.length; i += chunkSize) {
    const chunk = data.subarray(i, i + chunkSize);
    if (chunk.length === chunkSize && bytesEqual(chunk, zeroLine)) {
      yield serializeControl('zerofill');
    } else {
      yield serializeData(chunk, nocomp ? 'none' : 'rle');
    }
  }
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// ---------------------------------------------------------------------------
// ptstatus.py — status register parsing (32 bytes, big-endian)
// ---------------------------------------------------------------------------
const POWER = { 0: 'Battery full', 1: 'Battery half', 2: 'Battery low', 3: 'Battery critical', 4: 'AC' };
const MODELS = {
  0x38: 'QL-800', 0x39: 'QL-810W', 0x41: 'QL-820NWB', 0x66: 'PT-E550W',
  0x68: 'PT-P750W', 0x6f: 'PT-P900W', 0x70: 'PT-P950NW', 0x72: 'PT-P300BT',
};
const ERR_FLAGS = {
  0: 'Replace media', 1: 'Expansion buffer full', 2: 'Communication error',
  3: 'Communication buffer full', 4: 'Cover opened',
  5: 'Overheat/Cancelled on printer side', 6: 'Feed error',
  7: 'General system error', 8: 'Media not loaded',
  9: 'End of media (Page too long)', 10: 'Cutter jammed', 11: 'Low battery',
  12: 'Printer in use', 13: 'Printer not powered', 14: 'Overvoltage', 15: 'Fan error',
};
const TAPE_TYPE = {
  0x00: 'Not loaded', 0x01: 'Laminated (TZexxx)', 0x03: 'Non-laminated (TZeNxxx)',
  0x11: 'Heat shrink tube (HSexxx)', 0x4a: 'Continuous tape',
  0x4b: 'Die-cut labels', 0xff: 'Unsupported',
};
const PHASES = {
  0x000000: 'Ready', 0x000001: 'Feed', 0x010000: 'Printing',
  0x010014: 'Cover open while receiving',
};
const TAPE_BGCOLORS = {
  0x00: 'None', 0x01: 'White', 0x02: 'Other', 0x03: 'Clear', 0x04: 'Red',
  0x05: 'Blue', 0x06: 'Yellow', 0x07: 'Green', 0x08: 'Black',
  0x09: 'Clear (White text)', 0x20: 'Matte white', 0x21: 'Matte clear',
  0x22: 'Matte silver', 0x23: 'Satin gold', 0x24: 'Satin silver',
  0x30: 'Blue (D)', 0x31: 'Red (D)', 0x40: 'Fluorescent orange',
  0x41: 'Fluorescent yellow', 0x50: 'Berry pink (S)', 0x51: 'Light gray (S)',
  0x52: 'Lime green (S)', 0x60: 'Yellow (F)', 0x61: 'Pink (F)',
  0x62: 'Blue (F)', 0x70: 'White (Heat shrink tube)', 0x90: 'White (Flex ID)',
  0x91: 'Yellow (Flex ID)', 0xf0: 'Printing head cleaner', 0xf1: 'Stencil',
  0xff: 'Unsupported',
};
const TAPE_FGCOLORS = {
  0x00: 'None', 0x01: 'White', 0x02: 'Other', 0x04: 'Red', 0x05: 'Blue',
  0x08: 'Black', 0x0a: 'Gold', 0x62: 'Blue (F)', 0xf0: 'Printing head cleaner',
  0xf1: 'Stencil', 0xff: 'Unsupported',
};
const PRINT_FLAGS = { 6: 'Auto cut', 7: 'Hardware mirroring' };
const STATUS_TYPE = {
  0x00: 'Reply to status request', 0x01: 'Printing completed',
  0x02: 'Error occured', 0x03: 'IF mode finished', 0x04: 'Power off',
  0x05: 'Notification', 0x06: 'Phase change',
};
const NOTIFICATIONS = { 0x00: 'N/A', 0x01: 'Cover open', 0x02: 'Cover close' };

function describeCode(code, table) {
  const name = table[code] !== undefined ? table[code] : 'Unknown';
  return `${name} (0x${code.toString(16).padStart(2, '0')})`;
}

function describeFlag(flagset, descset) {
  if (flagset === 0) return 'None';
  const flags = [];
  let ctr = 0;
  while (flagset !== 0) {
    if (flagset & 1) flags.push(descset[ctr] !== undefined ? descset[ctr] : `bit${ctr}`);
    ctr++;
    flagset >>>= 1;
  }
  return flags.join(', ');
}

function unpackStatus(bytes) {
  if (bytes.length !== 32) throw new Error('Status must be exactly 32 bytes long.');
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return {
    magic: bytes.subarray(0, 4),
    model: dv.getUint8(4),
    country: dv.getUint8(5),
    _err2: dv.getUint8(6),
    _power: dv.getUint8(7),
    err: dv.getUint16(8, false),
    tape_width: dv.getUint8(10),
    tape_type: dv.getUint8(11),
    colors: dv.getUint8(12),
    fonts: dv.getUint8(13),
    _sbz0: dv.getUint8(14),
    mode: dv.getUint8(15),
    density: dv.getUint8(16),
    tape_length: dv.getUint8(17),
    status_type: dv.getUint8(18),
    phase_type: dv.getUint8(19),
    phase: dv.getUint16(20, false),
    notification: dv.getUint8(22),
    expansion_area: dv.getUint8(23),
    tape_bgcolor: dv.getUint8(24),
    tape_fgcolor: dv.getUint8(25),
    hw_settings: dv.getUint32(26, false),
  };
}

function printStatusLines(stat, verbose = false) {
  const lines = [];
  if (!bytesEqual(stat.magic, new Uint8Array([0x80, 0x20, 0x42, 0x30]))) {
    throw new Error('Invalid magic');
  }
  lines.push(`Model: ${describeCode(stat.model, MODELS)}`);
  if (verbose) {
    lines.push(`Country: 0x${stat.country.toString(16).padStart(2, '0')}`);
    lines.push(`Extended error: 0x${stat._err2.toString(16).padStart(2, '0')}`);
    lines.push(`Power: ${describeCode(stat._power, POWER)}`);
  }
  lines.push(`Errors: ${describeFlag(stat.err, ERR_FLAGS)}`);
  lines.push(`Tape width: ${stat.tape_width}mm`);
  lines.push(`Tape type: ${describeCode(stat.tape_type, TAPE_TYPE)}`);
  lines.push(`Print flags: ${describeFlag(stat.mode, PRINT_FLAGS)}`);
  lines.push(`Fixed label length: ${stat.tape_length !== 0 ? stat.tape_length + 'mm' : 'N/A'}`);
  lines.push(`Status: ${describeCode(stat.status_type, STATUS_TYPE)}`);
  lines.push(`Phase: ${describeCode((stat.phase_type << 16) | stat.phase, PHASES)}`);
  lines.push(`Notification: ${describeCode(stat.notification, NOTIFICATIONS)}`);
  if (verbose) lines.push(`Expansion size: 0x${stat.expansion_area.toString(16).padStart(2, '0')}`);
  lines.push(`Tape background: ${describeCode(stat.tape_bgcolor, TAPE_BGCOLORS)}`);
  lines.push(`Tape foreground: ${describeCode(stat.tape_fgcolor, TAPE_FGCOLORS)}`);
  if (verbose) lines.push(`Hardware settings: 0x${stat.hw_settings.toString(16).padStart(8, '0')}`);
  return lines;
}

// ---------------------------------------------------------------------------
// labelmaker.py — printer configuration and print job
// ---------------------------------------------------------------------------
function resetPrinterBytes() {
  return [
    new Uint8Array(64), // flush print buffer
    serializeControl('reset'),
    serializeControl('use_command_set', CommandSet.ptcbp),
  ];
}

function configurePrinterBytes(rasterLines, tapeDim, opts = {}) {
  const { compress = true, chaining = false, autoCut = false, endMargin = 0 } = opts;
  const [type_, width, length] = tapeDim;
  const out = [];
  out.push(...resetPrinterBytes());

  const activeFields =
    PrintParameterField.width | PrintParameterField.quality | PrintParameterField.recovery;
  // set_print_parameters: '4BI2B' = active_fields(B), media_type(B),
  // width_mm(B), length_mm(B), length_px(I), is_follow_up(B), sbz(B)
  out.push(serializeControl('set_print_parameters',
    activeFields, type_, width, length, rasterLines, 0, 0));

  let pm = 0, pm2 = 0;
  if (!chaining) pm2 |= PageModeAdvanced.no_page_chaining;
  if (autoCut) pm |= PageMode.auto_cut;

  out.push(serializeControl('set_page_mode_advanced', pm2));
  out.push(serializeControl('set_page_mode', pm));
  out.push(serializeControl('set_page_margin', endMargin));
  out.push(serializeControl('compression', compress ? CompressionType.rle : CompressionType.none));
  return out;
}

// ---------------------------------------------------------------------------
// Web Serial transport
// ---------------------------------------------------------------------------
class SerialTransport {
  constructor(port) {
    this.port = port;
    this.reader = null;
    this.writer = null;
    this._readBuffer = new Uint8Array(0);
  }

  static isSupported() {
    return typeof navigator !== 'undefined' && 'serial' in navigator;
  }

  static async requestPort() {
    // The PT-P300BT exposes the standard Bluetooth SPP (Serial Port Profile)
    // service. Chrome can access RFCOMM/SPP devices directly through Web
    // Serial; passing the SPP service class id makes the chooser list the
    // paired printer even when the OS did not create a serial port.
    const SPP_SERVICE_CLASS_ID = '00001101-0000-1000-8000-00805f9b34fb';
    try {
      return await navigator.serial.requestPort({
        allowedBluetoothServiceClassIds: [SPP_SERVICE_CLASS_ID],
      });
    } catch (e) {
      // Older Chrome builds may not accept the option: retry without it.
      return await navigator.serial.requestPort();
    }
  }

  async open(baudRate = 9600) {
    await this.port.open({ baudRate });
    this.writer = this.port.writable.getWriter();
  }

  async write(bytes) {
    if (!this.writer) throw new Error('Port not open');
    await this.writer.write(bytes);
  }

  async read(n, timeoutMs = 1000) {
    // Read exactly n bytes (or fewer on timeout), buffering leftovers.
    const deadline = Date.now() + timeoutMs;
    while (this._readBuffer.length < n && Date.now() < deadline) {
      if (!this.reader) this.reader = this.port.readable.getReader();
      const remaining = deadline - Date.now();
      const result = await Promise.race([
        this.reader.read(),
        new Promise((res) => setTimeout(() => res({ timeout: true }), Math.max(1, remaining))),
      ]);
      if (result.timeout) break;
      if (result.done) break;
      if (result.value && result.value.length) {
        this._readBuffer = concatBytes([this._readBuffer, result.value]);
      }
    }
    const take = Math.min(n, this._readBuffer.length);
    const out = this._readBuffer.subarray(0, take);
    this._readBuffer = this._readBuffer.subarray(take);
    return out;
  }

  async resetInputBuffer() {
    // Drain whatever is pending without blocking.
    try {
      if (!this.reader) this.reader = this.port.readable.getReader();
      const result = await Promise.race([
        this.reader.read(),
        new Promise((res) => setTimeout(() => res({ timeout: true }), 50)),
      ]);
      if (!result.timeout && !result.done && result.value) {
        this._readBuffer = new Uint8Array(0);
      }
    } catch (e) {
      /* ignore */
    }
  }

  async close() {
    try { if (this.reader) { await this.reader.cancel(); this.reader.releaseLock(); this.reader = null; } } catch (e) {}
    try { if (this.writer) { this.writer.releaseLock(); this.writer = null; } } catch (e) {}
    try { await this.port.close(); } catch (e) {}
  }
}

// ---------------------------------------------------------------------------
// labelmaker.py — do_print_job / wait_for_print_completion
// ---------------------------------------------------------------------------
async function waitForPrintCompletion(transport, timeout = 60, onLog = () => {}) {
  const deadline = Date.now() + timeout * 1000;
  let buffer = new Uint8Array(0);
  while (Date.now() < deadline) {
    const chunk = await transport.read(32 - buffer.length, 1000);
    buffer = concatBytes([buffer, chunk]);
    if (buffer.length < 32) continue;
    const status = unpackStatus(buffer.subarray(0, 32));
    buffer = buffer.subarray(32);
    for (const line of printStatusLines(status)) onLog(line);
    if (status.err || status.status_type === 0x02) {
      throw new Error('Printer reported an error: ' + describeFlag(status.err, ERR_FLAGS));
    }
    if (status.status_type === 0x01) return;
    if (status.status_type === 0x04) {
      throw new Error('Printer powered off before completing the job.');
    }
  }
  throw new Error(`Printer did not confirm print completion within ${timeout} seconds.`);
}

async function doPrintJob(transport, args, data, onLog = () => {}, onProgress = () => {}) {
  onLog('=> Querying printer status...');

  let status = null;
  for (let attempt = 1; attempt <= 6; attempt++) {
    for (const b of resetPrinterBytes()) await transport.write(b);
    await transport.resetInputBuffer();
    await transport.write(serializeControl('get_status'));
    const resp = await transport.read(32, 1000);
    if (resp.length === 32) {
      status = unpackStatus(resp);
      break;
    }
    onLog(`   ...no status yet (attempt ${attempt}/6, got ${resp.length} bytes); retrying...`);
  }
  if (status === null) {
    throw new Error('Printer did not respond to status query. Make sure it is connected and active in Bluetooth, then try again.');
  }
  for (const line of printStatusLines(status)) onLog(line);

  if (status.err !== 0x0000 || status.phase_type !== 0x00 || status.phase !== 0x0000) {
    throw new Error('Printer indicates that it is not ready. Refusing to continue.');
  }

  onLog('=> Configuring printer...');
  const rasterLines = Math.floor(data.length / 16);
  const cfg = configurePrinterBytes(
    rasterLines,
    [status.tape_type, status.tape_width, status.tape_length],
    {
      chaining: args.no_feed,
      autoCut: args.auto_cut,
      endMargin: args.end_margin,
      compress: !args.nocomp,
    },
  );
  for (const b of cfg) await transport.write(b);

  onLog(`=> Sending image data (${rasterLines} lines)...`);
  const BARS = '123456789';
  let bar = '';
  for (const line of encodeRasterTransfer(data, args.nocomp)) {
    if (line[0] === 0x47) bar += BARS[Math.min(Math.floor((line.length - 3) / 2), 7) + 1];
    else if (line[0] === 0x5a) bar += BARS[0];
    onProgress(bar);
    await transport.write(line);
  }
  onLog('=> Image data was sent successfully. Printing will begin soon.');

  if (!args.no_print) {
    await transport.write(serializeControl('print'));
    await waitForPrintCompletion(transport, 60, onLog);
  }
  onLog('=> All done.');
}

// Expose the API on the global object (no bundler needed).
window.PTPrinter = {
  serializeControl,
  serializeData,
  encodeRasterTransfer,
  packbitsEncode,
  unpackStatus,
  printStatusLines,
  resetPrinterBytes,
  configurePrinterBytes,
  SerialTransport,
  doPrintJob,
  waitForPrintCompletion,
  CompressionType,
  CommandSet,
  PageMode,
  PageModeAdvanced,
  PrintParameterField,
};
