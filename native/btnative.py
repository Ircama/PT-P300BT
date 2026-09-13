"""Pure-Python (PyObjC) native Bluetooth transport for the PT-P300BT.

A `serial.Serial`-compatible class that talks to the printer over a macOS
IOBluetooth RFCOMM channel, entirely in-process — no Swift, no subprocess.
Requires only:  pip install pyobjc-framework-IOBluetooth

Why this exists: the macOS /dev/cu.* Bluetooth-serial bridge is unreliable for
this printer (pyserial's close() never drains the macOS run loop, so bluetoothd
leaves the RFCOMM channel half-open and the next open() hangs). Owning the
IOBluetooth channel directly — and draining the run loop on close — avoids that.
"""

import atexit
import time

import objc
from Foundation import NSObject, NSDefaultRunLoopMode, NSRunLoop, NSDate
import IOBluetooth


class BTError(Exception):
    pass


def _spin(cond, deadline):
    """Pump the run loop (delivering IOBluetooth callbacks) until cond() or deadline."""
    rl = NSRunLoop.currentRunLoop()
    while not cond() and time.time() < deadline:
        rl.runMode_beforeDate_(NSDefaultRunLoopMode,
                               NSDate.dateWithTimeIntervalSinceNow_(0.05))


class _Delegate(NSObject):
    def init(self):
        self = objc.super(_Delegate, self).init()
        self.open_done = False
        self.open_status = -1
        self.closed = False
        self.rx = bytearray()
        return self

    def rfcommChannelOpenComplete_status_(self, channel, status):
        self.open_status = status
        self.open_done = True

    def rfcommChannelData_data_length_(self, channel, data, length):
        # PyObjC bridges (void *data, size_t length) to a buffer object.
        self.rx += bytes(data[:length])

    def rfcommChannelClosed_(self, channel):
        self.closed = True


class BTSerial:
    """Minimal serial.Serial stand-in over IOBluetooth RFCOMM."""

    def __init__(self, name="PT-P300", timeout=3.0, open_timeout=15.0):
        self.name = name
        self.timeout = timeout
        self._open_timeout = open_timeout
        self._device = None
        self._channel = None
        self._d = None
        self.is_open = False
        self.open()
        atexit.register(self.close)

    # ----- lifecycle -------------------------------------------------------
    def open(self):
        if self.is_open:
            return
        paired = IOBluetooth.IOBluetoothDevice.pairedDevices()
        if not paired:
            raise BTError("No paired Bluetooth devices (Bluetooth off, or this "
                          "terminal lacks Bluetooth permission in System Settings "
                          "> Privacy & Security > Bluetooth).")
        dev = next((d for d in paired if (d.name() or "").find(self.name) >= 0), None)
        if dev is None:
            names = ", ".join(d.name() or "?" for d in paired)
            raise BTError(f'No paired device whose name contains "{self.name}". '
                          f"Paired: {names}")
        self._device = dev

        channel_id = self._resolve_channel(dev)
        self._d = _Delegate.alloc().init()
        res, channel = dev.openRFCOMMChannelAsync_withChannelID_delegate_(
            None, channel_id, self._d)
        if res != 0:
            raise BTError(f"openRFCOMMChannelAsync failed: {res:#x}")
        _spin(lambda: self._d.open_done, time.time() + self._open_timeout)
        if not self._d.open_done or self._d.open_status != 0 or channel is None:
            raise BTError(f"RFCOMM open failed (status={self._d.open_status}); "
                          "the link did not come up.")
        self._channel = channel
        self.is_open = True

    @staticmethod
    def _resolve_channel(dev):
        spp = IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x1101)  # Serial Port Profile
        rec = dev.getServiceRecordForUUID_(spp)
        if rec is None:
            dev.performSDPQuery_(None)
            _spin(lambda: dev.getServiceRecordForUUID_(spp) is not None,
                  time.time() + 6)
            rec = dev.getServiceRecordForUUID_(spp)
        if rec is not None:
            res, cid = rec.getRFCOMMChannelID_(None)
            if res == 0 and cid:
                return cid
        return 1  # SPP channel fallback

    def close(self):
        if self._channel is not None and self.is_open:
            try:
                self._channel.closeChannel()
            except Exception:
                pass
            # Drain the run loop so bluetoothd tears down the RFCOMM channel
            # (the crux of the macOS reconnect bug).
            _spin(lambda: self._d.closed, time.time() + 1.0)
        self.is_open = False
        self._channel = None

    # ----- pyserial-compatible I/O -----------------------------------------
    def write(self, data):
        if not self.is_open:
            raise BTError("port not open")
        data = bytes(data)
        mtu = int(self._channel.getMTU()) or 320
        for i in range(0, len(data), mtu):
            seg = data[i:i + mtu]
            self._channel.writeSync_length_(seg, len(seg))
        return len(data)

    def read(self, n=1):
        if not self.is_open:
            raise BTError("port not open")
        _spin(lambda: len(self._d.rx) >= n, time.time() + self.timeout)
        take = min(len(self._d.rx), n)
        out = bytes(self._d.rx[:take])
        del self._d.rx[:take]
        return out

    def reset_input_buffer(self):
        if self._d is not None:
            del self._d.rx[:]

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
