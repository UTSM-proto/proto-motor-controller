from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'programmer'))
from serial_monitor import SerialMonitor, parse_line


class MonitorTests(unittest.TestCase):
    def test_existing_firmware_csv(self):
        result = parse_line('-80,1000,2560,48000,3,2,invalid=4,transitions=0,adc=0')
        self.assertEqual(result['current_ma'], -80)
        self.assertEqual(result['motor'], 2)
        self.assertEqual(result['invalid'], 4)

    def test_calibration_diagnostics(self):
        result = parse_line('diag cal=duplicate_hall cal_sector=2 cal_code=3 raw_hall=3 hall=3 motor=255 armed=0 block=calibration_failed')
        self.assertEqual(result['cal'], 'duplicate_hall')
        self.assertEqual(result['cal_sector'], 2)
        self.assertEqual(result['block'], 'calibration_failed')
        self.assertEqual(result['motor'], 255)

    def test_table_and_fault(self):
        self.assertEqual(parse_line('hallToMotor array: 255 2 0 1 4 3 5 255')['table'], [255,2,0,1,4,3,5,255])
        self.assertEqual(parse_line('FAULT: hall calibration/table invalid; drive disabled.')['block'], 'calibration_failed')
        self.assertEqual(parse_line('cal_observed: 1 2 3 255 255 255')['cal_observed'], [1,2,3,255,255,255])

    def test_bounded_history_and_clear(self):
        with tempfile.TemporaryDirectory() as root:
            monitor = SerialMonitor(root)
            for i in range(1600): monitor.accept('build=' + str(i))
            self.assertEqual(len(monitor.snapshot()['lines']), 1500)
            self.assertEqual(monitor.snapshot()['latest']['build'], '1599')
            monitor.clear()
            self.assertEqual(monitor.snapshot()['lines'], [])
            self.assertEqual(monitor.snapshot()['latest'], {})

    def test_reject_bootloader_without_opening_port(self):
        monitor = SerialMonitor('.')
        with self.assertRaises(ValueError): monitor.start({'mode':'BOOTSEL','com':None})

    def test_pause_closes_port_for_programming(self):
        monitor = SerialMonitor('.'); monitor.status = 'connected'
        with patch.object(monitor, 'stop') as stop:
            self.assertTrue(monitor.pause_for_programming()); stop.assert_called_once()
        self.assertEqual(monitor.snapshot()['status'], 'paused for programming')


if __name__ == '__main__': unittest.main()
