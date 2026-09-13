#!/usr/bin/env python3
"""Pure-Python (PyObjC) IOBluetooth RFCOMM probe for the PT-P300BT.

Proves the native-Bluetooth approach works from Python with no Swift/Xcode:
    pip install pyobjc-framework-IOBluetooth

Finds the paired printer, opens an RFCOMM channel via IOBluetooth, sends a
status query, prints the 32-byte reply. Mirrors native/ptprobe.swift.
"""
import sys
import time

import objc
from Foundation import NSObject, NSDefaultRunLoopMode, NSRunLoop, NSDate
import IOBluetooth

NAME = sys.argv[1] if len(sys.argv) > 1 else "PT-P300"
TIMEOUT = 10.0


def spin(cond, deadline):
    """Pump the run loop (servicing IOBluetooth callbacks) until cond() or deadline."""
    rl = NSRunLoop.currentRunLoop()
    while not cond() and time.time() < deadline:
        rl.runMode_beforeDate_(NSDefaultRunLoopMode,
                               NSDate.dateWithTimeIntervalSinceNow_(0.05))


class Delegate(NSObject):
    def init(self):
        self = objc.super(Delegate, self).init()
        self.open_done = False
        self.open_status = -1
        self.rx = bytearray()
        return self

    def rfcommChannelOpenComplete_status_(self, channel, status):
        self.open_status = status
        self.open_done = True

    def rfcommChannelData_data_length_(self, channel, data, length):
        # PyObjC bridges (void *data, size_t length) to a bytes-like buffer.
        self.rx += bytes(data[:length]) if not isinstance(data, (bytes, bytearray)) else bytes(data[:length])

    def rfcommChannelClosed_(self, channel):
        pass


def main():
    t0 = time.time()
    paired = IOBluetooth.IOBluetoothDevice.pairedDevices()
    if not paired:
        print("** No paired devices (Bluetooth off or no permission).", file=sys.stderr)
        return 2
    printer = next((d for d in paired if (d.name() or "").find(NAME) >= 0), None)
    if printer is None:
        names = ", ".join(d.name() or "?" for d in paired)
        print(f'** No paired device matching "{NAME}". Paired: {names}', file=sys.stderr)
        return 3
    print(f"Device: {printer.name()}  addr={printer.addressString()}  "
          f"connected={bool(printer.isConnected())}", file=sys.stderr)

    # Resolve the SPP (Serial Port Profile, UUID 0x1101) RFCOMM channel via SDP.
    spp = IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x1101)
    channel_id = 0
    rec = printer.getServiceRecordForUUID_(spp)
    if rec is None:
        printer.performSDPQuery_(None)
        spin(lambda: printer.getServiceRecordForUUID_(spp) is not None,
             time.time() + 6)
        rec = printer.getServiceRecordForUUID_(spp)
    if rec is not None:
        res, cid = rec.getRFCOMMChannelID_(None)
        if res == 0 and cid:
            channel_id = cid
    if channel_id == 0:
        channel_id = 1
    print(f"SDP: SPP RFCOMM channel = {channel_id}", file=sys.stderr)

    delegate = Delegate.alloc().init()
    res, channel = printer.openRFCOMMChannelAsync_withChannelID_delegate_(
        None, channel_id, delegate)
    if res != 0:
        print(f"** openRFCOMMChannelAsync failed: {res:#x}", file=sys.stderr)
        return 4
    spin(lambda: delegate.open_done, time.time() + TIMEOUT)
    if not delegate.open_done or delegate.open_status != 0 or channel is None:
        print(f"** RFCOMM open failed (status={delegate.open_status}).", file=sys.stderr)
        return 5
    print(f"RFCOMM open OK in {time.time()-t0:.2f}s (mtu={channel.getMTU()})",
          file=sys.stderr)

    # Send flush + reset + get_status (ESC i S), then await the 32-byte reply.
    for payload in (b"\x00" * 64, b"\x1b\x40", b"\x1b\x69\x53"):
        channel.writeSync_length_(payload, len(payload))
    t_send = time.time()
    spin(lambda: len(delegate.rx) >= 32, time.time() + TIMEOUT)
    channel.closeChannel()

    if len(delegate.rx) >= 32:
        status = bytes(delegate.rx[:32])
        err = status[8] | (status[9] << 8)
        print(f"STATUS OK: 32 bytes in {time.time()-t_send:.2f}s after query",
              file=sys.stderr)
        print(f"  model=0x{status[4]:02x}  errors={'none' if err == 0 else hex(err)}"
              f"  tape_width={status[10]}mm", file=sys.stderr)
        print(status.hex())
        return 0
    print(f"** No status reply (got {len(delegate.rx)} bytes).", file=sys.stderr)
    return 6


if __name__ == "__main__":
    sys.exit(main())
