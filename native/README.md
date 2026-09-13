# Native macOS Bluetooth transport for the PT-P300BT

On macOS the default `/dev/cu.*` Bluetooth-serial bridge is unreliable for the
PT-P300BT: opening the port hangs or connects "half-open" (data out, nothing
back), and after one open/close cycle subsequent opens succeed but silently
carry no data, hanging forever on "Querying printer status…".

The root cause (diagnosed by @cehbz in Ircama/PT-P300BT#3): pyserial's `close()`
doesn't drain the macOS run loop, so `bluetoothd` never tears down the RFCOMM
channel and leaves it half-open. The fix is to talk to the printer over a
**native IOBluetooth RFCOMM channel** and drain the run loop on close.

This is done **in pure Python via [PyObjC](https://pyobjc.readthedocs.io/)** —
no Swift, no Xcode, no compile step:

```bash
pip install pyobjc-framework-IOBluetooth   # already in requirements.txt (macOS only)
```

## Files

- `btnative.py` — a `serial.Serial`-compatible class (`BTSerial`) that opens an
  IOBluetooth RFCOMM channel to the printer and exposes
  `write()/read()/reset_input_buffer()/open()/close()`. `printlabel.py` uses it
  automatically when the port argument starts with `bt:`. Connect/transfer all
  happen in-process; `close()` drains the run loop so the channel tears down
  cleanly (the crux of the reconnect bug).
- `ptprobe.py` — a standalone diagnostic: connect + query status + print the
  result. Handy for verifying the link without printing.

## Use

```bash
# Quick connection/status check:
python3 native/ptprobe.py PT-P300

# Print (bt:NAME matches the paired device whose Bluetooth name contains NAME;
# NAME defaults to PT-P300):
python3 printlabel.py "bt:PT-P300" "/System/Library/Fonts/Supplemental/Arial.ttf" "Hello"
```

Notes:
- The phone/other host must not hold the printer — it allows one host at a time.
- The terminal app may need Bluetooth permission (System Settings → Privacy &
  Security → Bluetooth).
- If macOS prompts to "finish setting up this printer" in Printers & Scanners,
  cancel it — we only need the Bluetooth bond, not a CUPS print queue.
- On macOS, give a full font path (bare `arial.ttf` won't resolve); e.g.
  `/System/Library/Fonts/Supplemental/Arial.ttf`.
