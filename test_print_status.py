import contextlib
import ctypes
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import labelmaker
import ptstatus


def status_frame(kind=0, error=0, phase_type=0):
    # Construct the documented wire format independently of the ctypes layout.
    data = bytearray(32)
    data[:4] = b'\x80\x20B0'
    data[4] = 0x72
    data[8:10] = error.to_bytes(2, 'big')
    data[10:12] = bytes([12, 1])
    data[18:20] = bytes([kind, phase_type])
    return bytes(data)


class FakeSerial:
    def __init__(self, frames, fragment_size=32):
        self.pending = bytearray(b''.join(frames))
        self.fragment_size = fragment_size
        self.timeout = 10
        self.writes = []

    def read(self, size):
        count = min(size, self.fragment_size, len(self.pending))
        result = bytes(self.pending[:count])
        del self.pending[:count]
        return result

    def write(self, data):
        self.writes.append(data)
        return len(data)


class PrintStatusTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_status_layout_is_exactly_32_bytes(self):
        self.assertEqual(ctypes.sizeof(ptstatus.StatusRegister), 32)
        self.assertEqual(ptstatus.StatusRegister.hw_settings.offset, 26)
        frame = bytearray(status_frame())
        frame[26:30] = b'\x01\x02\x03\x04'
        status = ptstatus.unpack_status(bytes(frame))
        self.assertEqual(status.hw_settings, 0x01020304)

    def test_waits_past_printing_and_handles_fragmented_replies(self):
        ser = FakeSerial([status_frame(6, phase_type=1), status_frame(1)], 7)
        labelmaker.wait_for_print_completion(ser)
        self.assertFalse(ser.pending)
        self.assertEqual(ser.timeout, 10)

    def test_low_battery_after_printing_started_fails_job(self):
        ser = FakeSerial([
            status_frame(), status_frame(6, phase_type=1),
            status_frame(2, error=0x0800, phase_type=1),
        ])
        args = SimpleNamespace(no_feed=False, auto_cut=False, end_margin=0,
                               nocomp=False, no_print=False)
        with self.assertRaisesRegex(RuntimeError, 'Low battery'):
            labelmaker.do_print_job(ser, args, b'\x00' * 16)
        self.assertNotIn('All done', self.output.getvalue())
        self.assertEqual(ser.timeout, 10)

    def test_success_is_only_reported_after_completion(self):
        ser = FakeSerial([status_frame(), status_frame(6), status_frame(1)])
        args = SimpleNamespace(no_feed=False, auto_cut=False, end_margin=0,
                               nocomp=False, no_print=False)
        labelmaker.do_print_job(ser, args, b'\x00' * 16)
        self.assertFalse(ser.pending)
        self.assertIn('All done', self.output.getvalue())

    def test_timeout_restores_serial_timeout(self):
        ser = FakeSerial([])
        with patch('labelmaker.time.monotonic', side_effect=[0, 0, 61]):
            with self.assertRaises(TimeoutError):
                labelmaker.wait_for_print_completion(ser)
        self.assertEqual(ser.timeout, 10)

    def test_power_off_fails_job(self):
        with self.assertRaisesRegex(RuntimeError, 'powered off'):
            labelmaker.wait_for_print_completion(FakeSerial([status_frame(4)]))

    def test_no_print_does_not_wait_for_completion(self):
        ser = FakeSerial([status_frame()])
        args = SimpleNamespace(no_feed=False, auto_cut=False, end_margin=0,
                               nocomp=False, no_print=True)
        with patch('labelmaker.wait_for_print_completion') as wait:
            labelmaker.do_print_job(ser, args, b'\x00' * 16)
        wait.assert_not_called()
        self.assertNotIn(b'\x1a', ser.writes)


if __name__ == '__main__':
    unittest.main()
