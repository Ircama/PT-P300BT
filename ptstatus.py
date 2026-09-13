#!/usr/bin/env python3

import ctypes
import sys
import contextlib
import ptcbp
import serial

POWER = {
    0: 'Battery full',
    1: 'Battery half',
    2: 'Battery low',
    3: 'Battery critical',
    4: 'AC',
}

MODELS = {
    0x38: 'QL-800',
    0x39: 'QL-810W',
    0x41: 'QL-820NWB',
    0x66: 'PT-E550W',
    0x68: 'PT-P750W',
    0x6f: 'PT-P900W',
    0x70: 'PT-P950NW',
    0x72: 'PT-P300BT',
}

ERR_FLAGS = {
    0: 'Replace media',
    1: 'Expansion buffer full',
    2: 'Communication error',
    3: 'Communication buffer full',
    4: 'Cover opened',
    5: 'Overheat/Cancelled on printer side',
    6: 'Feed error',
    7: 'General system error',
    8: 'Media not loaded',
    9: 'End of media (Page too long)',
    10: 'Cutter jammed',
    11: 'Low battery',
    12: 'Printer in use',
    13: 'Printer not powered',
    14: 'Overvoltage',
    15: 'Fan error',
}

TAPE_TYPE = {
    0x00: 'Not loaded',
    0x01: 'Laminated (TZexxx)',
    0x03: 'Non-laminated (TZeNxxx)',
    0x11: 'Heat shrink tube (HSexxx)',
    0x4a: 'Continuous tape',
    0x4b: 'Die-cut labels',
    0xff: 'Unsupported',
}

PHASES = {
    0x000000: 'Ready',
    0x000001: 'Feed',
    0x010000: 'Printing',
    0x010014: 'Cover open while receiving',
}

TAPE_BGCOLORS = {
    0x00: 'None',
    0x01: 'White',
    0x02: 'Other',
    0x03: 'Clear',
    0x04: 'Red',
    0x05: 'Blue',
    0x06: 'Yellow',
    0x07: 'Green',
    0x08: 'Black',
    0x09: 'Clear (White text)',
    0x20: 'Matte white',
    0x21: 'Matte clear',
    0x22: 'Matte silver',
    0x23: 'Satin gold',
    0x24: 'Satin silver',
    0x30: 'Blue (D)',
    0x31: 'Red (D)',
    0x40: 'Fluorescent orange',
    0x41: 'Fluorescent yellow',
    0x50: 'Berry pink (S)',
    0x51: 'Light gray (S)',
    0x52: 'Lime green (S)',
    0x60: 'Yellow (F)',
    0x61: 'Pink (F)',
    0x62: 'Blue (F)',
    0x70: 'White (Heat shrink tube)',
    0x90: 'White (Flex ID)',
    0x91: 'Yellow (Flex ID)',
    0xf0: 'Printing head cleaner',
    0xf1: 'Stencil',
    0xff: 'Unsupported',
}

TAPE_FGCOLORS = {
    0x00: 'None',
    0x01: 'White',
    0x02: 'Other',
    0x04: 'Red',
    0x05: 'Blue',
    0x08: 'Black',
    0x0a: 'Gold',
    0x62: 'Blue (F)',
    0xf0: 'Printing head cleaner',
    0xf1: 'Stencil',
    0xff: 'Unsupported',
}

PRINT_FLAGS = {
    6: 'Auto cut',
    7: 'Hardware mirroring',
}

STATUS_TYPE = {
    0x00: "Reply to status request",
    0x01: "Printing completed",
    0x02: "Error occured",
    0x03: "IF mode finished",
    0x04: "Power off",
    0x05: "Notification",
    0x06: "Phase change",
}

NOTIFICATIONS = {
    0x00: 'N/A',
    0x01: 'Cover open',
    0x02: 'Cover close',
}

class StatusRegister(ctypes.BigEndianStructure):
    _pack_ = 1  # The wire format is exactly 32 bytes, with no native alignment.
    _fields_ = (
        ('magic', ctypes.c_char * 4),
        ('model', ctypes.c_uint8),
        ('country', ctypes.c_uint8),
        ('_err2', ctypes.c_uint8),
        ('_power', ctypes.c_uint8),
        ('err', ctypes.c_uint16),
        ('tape_width', ctypes.c_uint8),
        ('tape_type', ctypes.c_uint8),
        ('colors', ctypes.c_uint8),
        ('fonts', ctypes.c_uint8),
        ('_sbz0', ctypes.c_uint8),
        ('mode', ctypes.c_uint8),
        ('density', ctypes.c_uint8),
        ('tape_length', ctypes.c_uint8),
        ('status_type', ctypes.c_uint8),
        ('phase_type', ctypes.c_uint8),
        ('phase', ctypes.c_uint16),
        ('notification', ctypes.c_uint8),
        ('expansion_area', ctypes.c_uint8),
        ('tape_bgcolor', ctypes.c_uint8),
        ('tape_fgcolor', ctypes.c_uint8),
        ('hw_settings', ctypes.c_uint32),
        ('_sbz1', ctypes.c_uint8 * 2),
    )

describe_code = lambda code, table: f'{table.get(code, "Unknown")} (0x{code:02x})'

def describe_flag(flagset, descset):
    flags = []
    ctr = 0
    if flagset == 0:
        return 'None'
    while flagset != 0:
        flag = flagset & 1
        if flag:
            flags.append(descset.get(ctr, 'bit{}'.format(ctr)))
        ctr += 1
        flagset >>= 1
    return ', '.join(flags)

def print_status(stat, verbose=False):
    # 0:4
    if bytes(stat.magic) != b'\x80\x20B0':
        raise RuntimeError('Invalid magic')
    # 4
    print(f'Model: {describe_code(stat.model, MODELS)}')

    # 5:8
    if (verbose):
        print(f'Country: 0x{stat.country:02x}')
        print(f'Extended error: 0x{stat._err2:02x}')
        print(f'Power: {describe_code(stat._power, POWER)}')
    # 8:12
    print(f'Errors: {describe_flag(stat.err, ERR_FLAGS)}')
    print(f'Tape width: {stat.tape_width}mm')
    print(f'Tape type: {describe_code(stat.tape_type, TAPE_TYPE)}')
    # 12:15
    if (verbose):
        pass
    # 15
    print(f'Print flags: {describe_flag(stat.mode, PRINT_FLAGS)}')
    # 16
    if (verbose):
        pass
    # 17
    # This would be uglier if written as an f string, so just leave as-is
    print('Fixed label length: {}'.format('{}mm'.format(stat.tape_length) if stat.tape_length != 0 else 'N/A'))
    # 18
    print(f'Status: {describe_code(stat.status_type, STATUS_TYPE)}')
    # 19:22
    print(f'Phase: {describe_code(stat.phase_type << 16 | stat.phase, PHASES)}')
    # 22
    print(f'Notification: {describe_code(stat.notification, NOTIFICATIONS)}')
    # 23
    if (verbose):
        print(f'Expansion size: 0x{stat.expansion_area:02x}')
    # 24:26
    print(f'Tape background: {describe_code(stat.tape_bgcolor, TAPE_BGCOLORS)}')
    print(f'Tape foreground: {describe_code(stat.tape_fgcolor, TAPE_FGCOLORS)}')
    # 26
    if (verbose):
        print(f'Hardware settings: 0x{stat.hw_settings:08x}')

def unpack_status(bytes_):
    if len(bytes_) != 32:
        raise ValueError('Status must be exactly 32 bytes long.')
    status = StatusRegister()
    ctypes.memmove(ctypes.addressof(status), bytes_, ctypes.sizeof(status))
    return status

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} <COM port>')
        exit(1)

    addr = sys.argv[1]
    ser = serial.Serial(addr)

    ser.write(b'\x00'*64)
    ser.write(ptcbp.serialize_control('reset'))
    ser.write(ptcbp.serialize_control('get_status'))
    resp = StatusRegister()
    buf = ser.read(32)
    ctypes.memmove(ctypes.addressof(resp), buf, ctypes.sizeof(resp))
    print(buf)
    print_status(resp, verbose=True)
