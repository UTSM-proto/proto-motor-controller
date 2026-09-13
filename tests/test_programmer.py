import json
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "programmer"))
from config import defaults, header, validate
from app import make_server
from backend import Programmer, devices


class ConfigTests(unittest.TestCase):
    def test_default_header_is_current(self):
        self.assertEqual(header(defaults()), (Path(__file__).resolve().parents[1] / 'firmware/controller_config.h').read_text())

    def test_bad_values(self):
        for key, value in [('THROTTLE_LOW', 3300), ('F_PWM', 0), ('THROTTLE_SLEW_RATE', .5), ('CURRENT_SCALING', float('nan')), ('CURRENT_CONTROL', 'false'), ('AL_PIN', 18), ('HALL_1_PIN', 14), ('LED_PIN', 23)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({**defaults(), key: value})

    def test_manual_table(self):
        config = defaults(); config['IDENTIFY_HALLS_ON_BOOT'] = False
        with self.assertRaises(ValueError): validate(config)
        config['HALL_TABLE'] = [255, 2, 0, 1, 4, 3, 5, 255]
        validate(config)
        config['HALL_TABLE'][2] = 2
        with self.assertRaises(ValueError): validate(config)

    def test_unknown_field(self):
        with self.assertRaises(ValueError): validate({**defaults(), 'COMMAND': 'anything'})

    def test_selected_board_required(self):
        with self.assertRaises(ValueError): Programmer().start(defaults(), True, None)

    def test_busy_rejected(self):
        p = Programmer(); p.job = {'status': 'running'}
        with self.assertRaises(ValueError): p.start(defaults())


class WorkflowTests(unittest.TestCase):
    def workflow(self, found=True, flash_failure=False):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        p = Programmer()
        p.job = dict(id='test', status='running', stage='', log='', artifact=None)
        calls = []
        def fake_run(args, cwd, env=None, timeout=300):
            args = list(map(str, args)); calls.append(args)
            if '--build' in args:
                build = root / 'builds/test/build'; build.mkdir()
                (build / 'blink.uf2').write_bytes(b'test firmware artifact')
            if 'load' in args and flash_failure: raise RuntimeError('USB write failed')
        tools = {k: str(root / k) for k in ('cmake', 'ninja', 'gcc', 'picotool', 'sdk')}
        def fake_program(artifact, selected, build_id, inventory, log, stage):
            calls.append(['usb_program', selected['serial']])
            if flash_failure: raise RuntimeError('USB write failed')
        with patch('backend.STATE', root), patch('backend.toolchain', return_value=tools), patch('backend.devices', return_value=[{'serial': 'E660123456789ABC'}] if found else []), patch.object(p, 'run', side_effect=fake_run), patch('backend.usb_transport.program', side_effect=fake_program):
            p.work(defaults(), True, 'E660123456789ABC')
        return p.status(), calls, root

    def test_flash_targets_serial_and_verifies(self):
        job, calls, root = self.workflow()
        self.assertEqual(job['status'], 'complete')
        writes = [c for c in calls if 'usb_program' in c]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0], ['usb_program', 'E660123456789ABC'])
        self.assertTrue((root / 'builds/test/manifest.json').is_file())

    def test_disconnect_blocks_flash_but_preserves_build(self):
        job, calls, root = self.workflow(found=False)
        self.assertEqual(job['status'], 'failed')
        self.assertFalse(any('usb_program' in c for c in calls))
        self.assertTrue(Path(job['artifact']).is_file())

    def test_failed_flash_never_reports_success(self):
        job, _, _ = self.workflow(flash_failure=True)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('USB write failed', job['log'])

    def test_usb_parsing(self):
        rows = [{'InstanceId': r'USB\VID_2E8A&PID_000A\E660123456789ABC', 'FriendlyName': 'Pico'}, {'InstanceId': r'USB\VID_2E8A&PID_0003\E660000000000001', 'FriendlyName': 'RP2 Boot'}]
        result = subprocess.CompletedProcess([], 0, json.dumps(rows), '')
        with patch('backend.subprocess.run', return_value=result):
            parsed = devices()
        self.assertEqual([d['mode'] for d in parsed], ['USB firmware', 'BOOTSEL'])
        self.assertEqual(parsed[0]['serial'], 'E660123456789ABC')


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, url = make_server()
        cls.base, cls.token = url.split('/#token=')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def request(self, path, body=None, token=True, extra=None):
        headers = {'X-Programmer-Token': self.token} if token else {}
        headers.update(extra or {})
        return urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers, data=json.dumps(body).encode() if body is not None else None))

    def test_config(self):
        with self.request('/api/config') as r: self.assertEqual(json.load(r)['defaults'], defaults())

    def test_no_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error: self.request('/api/build', {}, False)
        self.assertEqual(error.exception.code, 403)

    def test_foreign_origin(self):
        with self.assertRaises(urllib.error.HTTPError) as error: self.request('/api/build', {}, extra={'Origin': 'https://example.com'})
        self.assertEqual(error.exception.code, 403)

    def test_invalid_build_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error: self.request('/api/build', {'config': {}})
        self.assertEqual(error.exception.code, 400)

    def test_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as error: self.request('/../blink.c')
        self.assertEqual(error.exception.code, 404)


if __name__ == '__main__': unittest.main()
