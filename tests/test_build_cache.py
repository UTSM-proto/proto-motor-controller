import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'programmer'))
import backend
import build_cache
from config import defaults


class CacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'repo'; self.source.mkdir()
        for name in build_cache.SOURCE_FILES:
            (self.source / name).write_text(name)
        self.state = self.root / 'state'
        self.tools = {name: str(self.root / 'tools/bin' / name) for name in ('cmake', 'ninja', 'gcc', 'picotool')}
        for path in self.tools.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True); Path(path).write_bytes(b'executable')
        self.tools['sdk'] = str(self.root / 'sdk'); Path(self.tools['sdk']).mkdir()
        self.sdk_file = Path(self.tools['sdk']) / 'sdk.h'; self.sdk_file.write_text('v1')
        self.compilations = 0
        self.confirm_ids = []

    def run_job(self, values=None, flash=False, fail=False, usb_fail=False):
        p = backend.Programmer(); job_id = uuid.uuid4().hex
        p.job = dict(id=job_id, status='running', stage='', log='', artifact=None)
        def run(args, cwd, env=None, timeout=300):
            if '--build' in args:
                self.compilations += 1
                if fail: raise RuntimeError('Compiler failed')
                folder = self.state / 'builds' / job_id / 'build'; folder.mkdir()
                (folder / 'blink.uf2').write_bytes(b'firmware-' + job_id.encode())
        def program(artifact, selected, build_id, *rest):
            if usb_fail: raise RuntimeError('USB disconnected')
            self.confirm_ids.append(build_id)
        with patch.object(backend, 'ROOT', self.source), patch.object(backend, 'STATE', self.state), patch.object(backend, 'toolchain', return_value=self.tools), patch.object(p, 'run', side_effect=run), patch.object(backend, 'devices', return_value=[{'serial': 'E660123456789ABC'}]), patch.object(backend.usb_transport, 'program', side_effect=program):
            p.work(values or defaults(), flash, 'E660123456789ABC')
        return p.status()

    def test_reuses_build_across_service_instances_and_confirms_original_id(self):
        first = self.run_job()
        second = self.run_job(flash=True)
        self.assertEqual(second['status'], 'complete')
        self.assertTrue(second['cache_hit'])
        self.assertEqual(self.compilations, 1)
        self.assertEqual(self.confirm_ids, [first['id']])
        self.assertEqual(Path(first['artifact']).read_bytes(), Path(second['artifact']).read_bytes())

    def test_config_change_and_return_to_previous_config(self):
        self.run_job()
        changed = self.run_job({**defaults(), 'THROTTLE_LOW': 1200})
        self.assertFalse(changed['cache_hit'])
        self.assertTrue(self.run_job()['cache_hit'])
        self.assertEqual(self.compilations, 2)

    def test_source_and_sdk_changes_invalidate(self):
        self.run_job()
        (self.source / 'blink.c').write_text('new source')
        self.assertFalse(self.run_job()['cache_hit'])
        self.sdk_file.write_text('new SDK contents')
        self.assertFalse(self.run_job()['cache_hit'])
        self.assertEqual(self.compilations, 3)

    def test_corrupt_or_missing_uf2_is_rebuilt(self):
        first = self.run_job(); Path(first['artifact']).write_bytes(b'corrupt')
        second = self.run_job(); self.assertFalse(second['cache_hit'])
        Path(second['artifact']).unlink()
        self.assertFalse(self.run_job()['cache_hit'])

    def test_failed_build_is_not_cached(self):
        self.assertEqual(self.run_job(fail=True)['status'], 'failed')
        self.assertFalse(self.run_job()['cache_hit'])
        self.assertEqual(self.compilations, 2)

    def test_failed_usb_retry_does_not_recompile(self):
        self.assertEqual(self.run_job(flash=True, usb_fail=True)['status'], 'failed')
        result = self.run_job(flash=True)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(result['cache_hit'])
        self.assertEqual(self.compilations, 1)


if __name__ == '__main__': unittest.main()
