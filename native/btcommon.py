"""Cross-platform native Bluetooth (RFCOMM/SPP) transport for the PT-P300BT.

Provides a `serial.Serial`-compatible class (`BTSerial`) that connects to the
printer directly over a Bluetooth RFCOMM channel instead of a
Bluetooth-serial (/dev/cu.*, COM) bridge, selecting the platform backend
automatically:

- macOS:  IOBluetooth via PyObjC (pure Python, no Xcode) — the /dev/cu.* bridge
  is unreliable for this printer (pyserial's close() doesn't drain the macOS run
  loop, so the RFCOMM channel stays half-open and the next open() hangs).
- Windows: connect to the paired device's SPP service (default channel 1)
  through the OS Bluetooth stack via the same COM port that the device manager
  exposes — falls back to pyserial with a short connect timeout.
- Linux:  try the `rfcomm` utility first (`sudo rfcomm connect` -> /dev/rfcomm*
  node via pyserial), otherwise fall back to a native stdlib RFCOMM socket
  (socket.AF_BLUETOOTH / BTPROTO_RFCOMM) — no extra Python packages needed.

Usage: port string of the form "bt:NAME" where NAME is a substring of the
paired device's Bluetooth name (default "PT-P300").
"""

import atexit
import os
import subprocess
import sys
import time

__all__ = ["BTSerial", "BTError", "platform_backend"]

DEFAULT_NAME = "PT-P300"
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"  # Serial Port Profile


class BTError(Exception):
    pass


def _looks_like_mac(s):
    """True if `s` is a Bluetooth MAC address (12 hex digits, any separators)."""
    import re
    return bool(re.fullmatch(r"[0-9a-fA-F]{2}([-:]?)[0-9a-fA-F]{2}(?:(?:-|:)?[0-9a-fA-F]{2}){4}", s or ""))


def platform_backend():
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


# --------------------------------------------------------------------------
# macOS backend (IOBluetooth via PyObjC) — lives in native/btnative.py
# --------------------------------------------------------------------------

def _open_macos_by_name(name, timeout, open_timeout):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from btnative import BTSerial as _MacBTSerial
    return _MacBTSerial(name=name, timeout=timeout, open_timeout=open_timeout)


# --------------------------------------------------------------------------
# Windows backend
# --------------------------------------------------------------------------

def _open_windows(name, timeout, open_timeout):
    import serial  # local import so pyserial is only needed when actually used
    from serial.tools import list_ports

    # When a Bluetooth SPP device is paired on Windows, the stack exposes one
    # or two "Standard Serial over Bluetooth link" COM ports. The one whose
    # hardware description contains the device name is the SPP service
    # endpoint that works with this printer. Match on the friendly name, then
    # fall back to any Bluetooth SPP COM port.
    # `name` may also be a Bluetooth MAC address (with or without colons);
    # in that case only the device with that address is considered.
    # NOTE: skip placeholder ports whose hwid contains the null MAC
    # (000000000000) — Windows always exposes them and they "open" fine, but
    # they are not bound to any real device (writes hang).
    ports = [p for p in list_ports.comports()
             if "000000000000" not in (p.hwid or "").lower()]
    if _looks_like_mac(name):
        mac = name.replace(":", "").replace("-", "").lower()
        candidates = [p.device for p in ports
                      if mac in (p.hwid or "").replace(":", "").lower()]
    else:
        named = [p.device for p in ports
                 if "bth" in (p.hwid or "").lower() and
                 name.lower() in (p.description or "").lower()]
        fallback = [p.device for p in ports
                    if "standard serial over bluetooth" in
                    (p.description or "").lower()]
        candidates = named or fallback
    if not candidates:
        raise BTError(
            f"No Bluetooth SPP COM port matching '{name}'. Pair the printer "
            "in Windows Bluetooth settings first (Settings > Bluetooth & "
            "devices > Add device), then retry."
        )
    last_err = None
    for port in candidates:
        try:
            return serial.Serial(port, timeout=timeout, write_timeout=timeout)
        except serial.SerialException as e:
            last_err = e
    raise BTError(f"Cannot open Bluetooth COM port(s) {candidates}: {last_err}")


# macOS: resolve a bt:MAC address directly against pairedDevices() before
# falling back to the name substring match in btnative.BTSerial.
def _open_macos(name, timeout, open_timeout):
    if _looks_like_mac(name):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import IOBluetooth
        mac = name.replace(":", "").replace("-", "").lower()
        for d in IOBluetooth.IOBluetoothDevice.pairedDevices():
            if (d.addressString() or "").replace(":", "").lower() == mac:
                from btnative import BTSerial as _MacBTSerial
                return _MacBTSerial(name=d.name(), timeout=timeout,
                                    open_timeout=open_timeout)
        raise BTError(f"No paired Bluetooth device with address {name}")
    return _open_macos_by_name(name, timeout, open_timeout)


# --------------------------------------------------------------------------
# Linux backend
# --------------------------------------------------------------------------

def _open_linux(name, timeout, open_timeout):
    import serial

    addr = name if _looks_like_mac(name) else _find_bluez_device(name)
    if addr is None:
        raise BTError(
            f"No paired Bluetooth device matching '{name}'. Pair it first: "
            f"bluetoothctl pair <MAC> (then trust + connect)."
        )
    # Preferred: bind an RFCOMM node with the standard rfcomm tool, then use
    # pyserial on /dev/rfcommN (this path is what most distros support out of
    # the box; needs root the first time).
    node = _rfcomm_connect(addr, open_timeout)
    if node:
        return serial.Serial(node, timeout=timeout)
    return _rfcomm_socket_connect(addr, timeout, open_timeout)


def _rfcomm_socket_connect(addr, timeout, open_timeout):
    import socket
    try:
        sock = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(open_timeout)
        sock.connect((addr, 1))  # 1 = SPP RFCOMM channel
        sock.settimeout(timeout)
        return _SocketSerialAdapter(sock, timeout)
    except OSError as e:
        raise BTError(
            f"Cannot open RFCOMM channel to {addr}: {e}. Try the 'rfcomm' "
            "utility (bluez package): sudo rfcomm connect 0 " + addr + " 1"
        )


class _SocketSerialAdapter:
    """Minimal serial.Serial-compatible adapter over a Bluetooth socket."""

    def __init__(self, sock, timeout=3.0):
        self._sock = sock
        self.timeout = timeout
        self.is_open = True
        atexit.register(self.close)

    def write(self, data):
        self._sock.sendall(bytes(data))
        return len(data)

    def read(self, n=1):
        self._sock.settimeout(self.timeout)
        try:
            return self._sock.recv(n)
        except Exception:
            return b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _find_bluez_device(name):
    """Return the MAC address of the paired device whose name contains `name`."""
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        # "Device AA:BB:CC:DD:EE:FF Some Name"
        parts = line.split(maxsplit=2)
        if len(parts) >= 3 and parts[0] == "Device" and \
                name.lower() in parts[2].lower():
            return parts[1]
    return None


def _rfcomm_connect(addr, open_timeout):
    """Bind /dev/rfcommN via the `rfcomm` tool; return the node path or None."""
    import serial
    rfcomm = None
    for cand in ("/usr/bin/rfcomm", "/bin/rfcomm"):
        if os.path.exists(cand):
            rfcomm = cand
            break
    if rfcomm is None:
        return None
    node = "/dev/rfcomm0"
    subprocess.run(
        [rfcomm, "connect", "0", addr, "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=open_timeout,
    )
    # rfcomm connect normally forks to background once the link is up.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            s = serial.Serial(node, timeout=0.2)
            s.close()
            return node
        except Exception:
            time.sleep(0.3)
    return None


# --------------------------------------------------------------------------
# Device discovery (paired Bluetooth devices with an RFCOMM/SPP channel)
# --------------------------------------------------------------------------

def _list_devices_macos():
    """[(name, address, channel_id), ...] via IOBluetooth (PyObjC)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import IOBluetooth
    devices = []
    for d in IOBluetooth.IOBluetoothDevice.pairedDevices():
        name = d.name() or "?"
        addr = d.addressString() or "?"
        channel = 1
        spp = IOBluetooth.IOBluetoothSDPUUID.uuid16_(0x1101)
        rec = d.getServiceRecordForUUID_(spp)
        if rec is not None:
            res, cid = rec.getRFCOMMChannelID_(None)
            if res == 0 and cid:
                channel = cid
        devices.append((name, addr, channel))
    return devices


def _list_devices_windows():
    """[(name, address, channel_id), ...] for paired SPP devices.

    Windows maps each paired SPP device to one or two 'Standard Serial over
    Bluetooth link' COM ports, so we report the COM port instead of the raw
    RFCOMM channel. Names come from the BTHPORT registry (Name is stored as
    UTF-16LE bytes).
    """
    import re
    import winreg
    from serial.tools import list_ports

    def _read_name(sub):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters"
                r"\Devices" + "\\" + sub
            ) as k:
                raw = winreg.QueryValueEx(k, "Name")[0]
            if isinstance(raw, (bytes, bytearray)):
                # Name is stored as null-terminated bytes (typically ASCII).
                return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
            return raw
        except OSError:
            return None

    # MAC (no colons, lowercase) -> friendly name
    names = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
        ) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                n = _read_name(sub)
                if n:
                    names[sub.lower()] = n
    except OSError:
        pass

    # 'Standard Serial over Bluetooth link (COMxx)' entries live under the
    # SPP service keys '{00001101-...}*' in Enum\BTHENUM; each instance key
    # path ends with the device MAC (e.g. ...&70DDA8C9970D_C00000000) and
    # carries the COM port in its FriendlyName value.
    devices = []
    try:
        base = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\BTHENUM")
        with base:
            i = 0
            while True:
                try:
                    dev_key = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                if not dev_key.lower().startswith("{00001101-0000-1000-8000-00805f9b34fb}"):
                    continue  # only the SPP (Serial Port Profile) service keys
                try:
                    dk = winreg.OpenKey(base, dev_key)
                except OSError:
                    continue
                with dk:
                    j = 0
                    while True:
                        try:
                            inst = winreg.EnumKey(dk, j)
                        except OSError:
                            break
                        j += 1
                        m = re.search(r"([0-9a-fA-F]{12})_", inst)
                        if not m or m.group(1).strip("0") == "":
                            continue  # skip placeholder ports without a real device
                        mac = m.group(1).lower()
                        try:
                            with winreg.OpenKey(dk, inst) as ik:
                                friendly = winreg.QueryValueEx(ik, "FriendlyName")[0]
                        except OSError:
                            continue
                        mport = re.search(r"\((COM\d+)\)$", friendly)
                        if not mport:
                            continue
                        addr = ":".join(mac[x:x + 2] for x in range(0, 12, 2))
                        devices.append((
                            names.get(mac, addr),
                            addr,
                            mport.group(1),
                        ))
    except OSError:
        pass

    if not devices:
        # Fallback: pyserial's own view of Bluetooth COM ports.
        for p in list_ports.comports():
            desc = p.description or ""
            if "standard serial over bluetooth" in desc.lower():
                devices.append((desc, p.hwid, p.device))
    return devices


def _list_devices_linux():
    """[(name, address, channel_id), ...] for paired devices via bluetoothctl."""
    devices = []
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return devices
    for line in out.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 3 and parts[0] == "Device":
            addr, name = parts[1], parts[2]
            devices.append((name, addr, 1))  # SPP channel 1 assumed
    return devices


_LIST_BACKENDS = {
    "macos": _list_devices_macos,
    "windows": _list_devices_windows,
    "linux": _list_devices_linux,
}


def list_devices():
    """Return paired Bluetooth devices supporting SPP/RFCOMM.

    Returns a list of (name, address, channel) tuples where:
    - macOS:    channel = RFCOMM channel id from SDP
    - Windows:  "address" is the hwid and "channel" is the COM port name
                (the OS does not expose the raw channel)
    - Linux:    address = Bluetooth MAC, channel = assumed SPP channel 1
    """
    backend = platform_backend()
    fn = _LIST_BACKENDS.get(backend)
    if fn is None:
        raise BTError(f"Unsupported platform for Bluetooth discovery: {backend}")
    return fn()


def find_device(name=DEFAULT_NAME):
    """Return the first (name, address, channel) whose name contains `name`."""
    for dev_name, addr, channel in list_devices():
        if name.lower() in dev_name.lower():
            return dev_name, addr, channel
    return None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

_BACKENDS = {
    "macos": _open_macos,
    "windows": _open_windows,
    "linux": _open_linux,
}


def BTSerial(name=DEFAULT_NAME, timeout=3.0, open_timeout=15.0):
    """Open a serial-like connection to the printer over native Bluetooth.

    Returns a pyserial-compatible object on any of macOS / Windows / Linux.
    """
    backend = platform_backend()
    opener = _BACKENDS.get(backend)
    if opener is None:
        raise BTError(f"Unsupported platform for native Bluetooth: {backend}")
    ser = opener(name, timeout, open_timeout)
    atexit.register(ser.close)
    return ser
