#!/usr/bin/env python

from labelmaker_encode import encode_raster_transfer, read_png

import argparse
import sys
import contextlib
import ctypes
import ptcbp
import ptstatus
import serial

BARS = '123456789'

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('comport', help='Printer COM port.')
    p.add_argument('-i', '--image', help='Image file to print.')
    p.add_argument('-n', '--no-print', help='Only configure the printer and send the image but do not send print command.', action='store_true')
    p.add_argument('-F', '--no-feed', help='Disable feeding at the end of the print (chaining).')
    p.add_argument('-a', '--auto-cut', help='Enable auto-cutting (or print label boundary on e.g. PT-P300BT).')
    p.add_argument('-m', '--end-margin', help='End margin (in dots).', default=0, type=int)
    p.add_argument('-r', '--raw', help='Send the image to printer as-is without any pre-processing.', action='store_true')
    p.add_argument('-C', '--nocomp', help='Disable compression.', action='store_true')
    return p, p.parse_args()

def reset_printer(ser):
    # Flush print buffer
    ser.write(b"\x00" * 64)

    # Initialize
    ser.write(ptcbp.serialize_control('reset'))

    # Enter raster graphics (PTCBP) mode
    ser.write(ptcbp.serialize_control('use_command_set', ptcbp.CommandSet.ptcbp))

def configure_printer(ser, raster_lines, tape_dim, compress=True, chaining=False, auto_cut=False, end_margin=0):
    reset_printer(ser)

    type_, width, length = tape_dim
    # Set media & quality
    ser.write(ptcbp.serialize_control_obj('set_print_parameters', ptcbp.PrintParameters(
        active_fields=(ptcbp.PrintParameterField.width |
                       ptcbp.PrintParameterField.quality |
                       ptcbp.PrintParameterField.recovery),
        media_type=type_,
        width_mm=width, # Tape width in mm
        length_mm=length, # Label height in mm (0 for continuous roll)
        length_px=raster_lines, # Number of raster lines in image data
        is_follow_up=0, # Unused
        sbz=0, # Unused
    )))

    pm, pm2 = 0, 0
    if not chaining:
        pm2 |= ptcbp.PageModeAdvanced.no_page_chaining
    if auto_cut:
        pm |= ptcbp.PageMode.auto_cut

    # Set print chaining off (0x8) or on (0x0)
    ser.write(ptcbp.serialize_control('set_page_mode_advanced', pm2))

    # Set no mirror, no auto tape cut
    ser.write(ptcbp.serialize_control('set_page_mode', pm))

    # Set margin amount (feed amount)
    ser.write(ptcbp.serialize_control('set_page_margin', end_margin))

    # Set compression mode: TIFF
    ser.write(ptcbp.serialize_control('compression', ptcbp.CompressionType.rle if compress else ptcbp.CompressionType.none))

def do_print_job(ser, args, data):
    print('=> Querying printer status...')

    # The Bluetooth serial link (esp. on macOS) can be idle/half-open on first
    # access, so a single status read may hang or return nothing. Re-init and
    # re-read until we get a full 32-byte status reply or run out of attempts.
    status = None
    for attempt in range(1, 7):
        reset_printer(ser)
        ser.reset_input_buffer()
        ser.write(ptcbp.serialize_control('get_status'))
        resp = ser.read(32)
        if len(resp) == 32:
            status = ptstatus.unpack_status(resp)
            break
        print(f"   ...no status yet (attempt {attempt}/6, got "
              f"{len(resp)} bytes); retrying...")
    if status is None:
        print('** Printer did not respond to status query. Make sure it is '
              'connected and active in Bluetooth, then try again.')
        sys.exit(1)
    ptstatus.print_status(status)

    if status.err != 0x0000 or status.phase_type != 0x00 or status.phase != 0x0000:
        print('** Printer indicates that it is not ready. Refusing to continue.')
        sys.exit(1)

    print('=> Configuring printer...')

    raster_lines = len(data) // 16
    configure_printer(ser, raster_lines, (status.tape_type,
                                          status.tape_width,
                                          status.tape_length),
                      chaining=args.no_feed,
                      auto_cut=args.auto_cut,
                      end_margin=args.end_margin,
                      compress=not args.nocomp)

    # Send image data
    print(f"=> Sending image data ({raster_lines} lines)...")
    sys.stdout.write('[')
    for line in encode_raster_transfer(data, args.nocomp):
        if line[0:1] == b'G':
            sys.stdout.write(BARS[min((len(line) - 3) // 2, 7) + 1])
        elif line[0:1] == b'Z':
            sys.stdout.write(BARS[0])
        sys.stdout.flush()
        ser.write(line)
    sys.stdout.write(']')

    print()
    print("=> Image data was sent successfully. Printing will begin soon.")

    if not args.no_print:
        # Print and feed
        ser.write(ptcbp.serialize_control('print'))

        # Dump status that the printer returns (best-effort; the print has
        # already been sent, so don't crash if the reply is slow/short).
        resp = ser.read(32)
        if len(resp) == 32:
            ptstatus.print_status(ptstatus.unpack_status(resp))

    print("=> All done.")

def main():
    p, args = parse_args()

    data = None
    if args.image is None:
        p.error('An image must be specified for printing job.')
    else:
        # Read input image into memory
        if args.raw:
            data = read_png(args.image, False, False, False)
        else:
            data = read_png(args.image)

    ser = serial.Serial(args.comport)

    try:
        assert data is not None
        do_print_job(ser, args, data)
    finally:
        # Initialize
        reset_printer(ser)

if __name__ == '__main__':
    main()
