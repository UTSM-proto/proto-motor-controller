from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'programmer'))
import usb_transport as usb


class UsbTests(unittest.TestCase):
    def setUp(self):
        self.app = dict(serial='APP123', location='PORT1', mode='USB firmware', com='COM3', volumes=[])
        self.boot = dict(serial='DIFFERENT', location='PORT1', mode='BOOTSEL', com=None, volumes=['D:/'])

    def test_changed_serial_same_port(self):
        with patch.object(usb, 'request_bootloader') as reset, patch.object(usb, 'wait_for_device', side_effect=[self.boot, self.app]), patch.object(usb, 'copy_uf2') as copy, patch.object(usb, 'confirm_build') as confirm:
            usb.program('image.uf2', self.app, 'build1', lambda: [self.boot], lambda _: None, lambda _: None)
            reset.assert_called_once_with('COM3')
            copy.assert_called_once_with('image.uf2', 'D:/')
            confirm.assert_called_once_with('COM3', 'build1')

    def test_other_board_cannot_be_selected_after_reboot(self):
        other = {**self.boot, 'location': 'PORT2'}
        self.assertIsNone(usb.same_port([other], 'PORT1', 'BOOTSEL'))
        with patch.object(usb, 'request_bootloader'), patch.object(usb, 'wait_for_device', return_value=self.boot), patch.object(usb, 'copy_uf2') as copy:
            with self.assertRaisesRegex(RuntimeError, 'target changed'):
                usb.program('image.uf2', self.app, 'build1', lambda: [other], lambda _: None, lambda _: None)
            copy.assert_not_called()

    def test_missing_identity_is_failure(self):
        with patch.object(usb, 'request_bootloader'), patch.object(usb, 'wait_for_device', side_effect=[self.boot, self.app]), patch.object(usb, 'copy_uf2'), patch.object(usb, 'confirm_build', side_effect=RuntimeError('wrong build')):
            with self.assertRaisesRegex(RuntimeError, 'wrong build'):
                usb.program('image.uf2', self.app, 'build1', lambda: [self.boot], lambda _: None, lambda _: None)

    def test_existing_bootloader_skips_reset(self):
        with patch.object(usb, 'request_bootloader') as reset, patch.object(usb, 'wait_for_device', side_effect=[self.boot, self.app]), patch.object(usb, 'copy_uf2'), patch.object(usb, 'confirm_build'):
            usb.program('image.uf2', self.boot, 'build1', lambda: [self.boot], lambda _: None, lambda _: None)
            reset.assert_not_called()

    def test_no_location_blocks_programming(self):
        with self.assertRaisesRegex(RuntimeError, 'USB location'):
            usb.program('image.uf2', {**self.app, 'location': None}, 'build1', lambda: [], lambda _: None, lambda _: None)

    def test_reset_disconnect_is_allowed_only_if_bootloader_arrives(self):
        with patch.object(usb, 'request_bootloader', side_effect=RuntimeError('device disconnected')), patch.object(usb, 'wait_for_device', side_effect=[self.boot, self.app]), patch.object(usb, 'copy_uf2'), patch.object(usb, 'confirm_build'):
            usb.program('image.uf2', self.app, 'build1', lambda: [self.boot], lambda _: None, lambda _: None)
        with patch.object(usb, 'request_bootloader', side_effect=RuntimeError('port busy')), patch.object(usb, 'wait_for_device', side_effect=RuntimeError('timeout')), patch.object(usb, 'copy_uf2') as copy:
            with self.assertRaisesRegex(RuntimeError, 'port busy.*timeout'):
                usb.program('image.uf2', self.app, 'build1', lambda: [], lambda _: None, lambda _: None)
            copy.assert_not_called()


if __name__ == '__main__': unittest.main()
