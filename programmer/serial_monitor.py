"""Read-only USB telemetry with bounded browser output and saved session logs."""
from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import uuid


def parse_line(line):
    if line.startswith('diag '):
        result = {}
        for key, value in re.findall(r'(\w+)=([^\s]+)', line):
            try: result[key] = int(value)
            except ValueError: result[key] = value
        return result
    if re.match(r'^-?\d+,-?\d+,-?\d+,-?\d+,\d+,\d+,', line):
        items = line.split(',')
        result = dict(zip(('current_ma', 'target_ma', 'duty', 'bus_mv', 'hall', 'motor'), map(int, items[:6])))
        for item in items[6:]:
            if '=' in item:
                key, value = item.split('=', 1)
                if value.isdigit(): result[key] = int(value)
        return result
    if line.startswith('build='): return {'build': line[6:].strip()}
    if line.startswith('FAULT:'): return {'block': 'calibration_failed', 'fault': line}
    if line.startswith('hallToMotor array:'):
        values = [int(v) for v in re.findall(r'\d+', line.split(':', 1)[1])]
        if len(values) == 8: return {'table': values}
    if line.startswith('cal_observed:'):
        return {'cal_observed': [int(v) for v in re.findall(r'\d+', line)]}
    return {}


class SerialMonitor:
    def __init__(self, state):
        self.state = Path(state)
        self.lock = threading.RLock()
        self.process = None
        self.thread = None
        self.lines = deque(maxlen=1500)
        self.latest = {}
        self.status = 'disconnected'
        self.com = None
        self.log_path = None
        self.sequence = 0

    def snapshot(self):
        with self.lock:
            return dict(status=self.status, com=self.com, lines=list(self.lines), latest=dict(self.latest), log_path=str(self.log_path) if self.log_path else None, sequence=self.sequence)

    def accept(self, line):
        line = line[:8192]
        with self.lock:
            self.sequence += 1
            record = dict(seq=self.sequence, time=datetime.now().isoformat(timespec='milliseconds'), text=line)
            self.lines.append(record)
            self.latest.update(parse_line(line))
        return record

    def start(self, device, preserve=False):
        com = device.get('com')
        if device.get('mode') != 'USB firmware' or not re.fullmatch(r'COM\d+', com or ''):
            raise ValueError('Select a Pico running USB firmware to open its serial monitor.')
        self.stop()
        with self.lock:
            if not preserve:
                self.lines.clear(); self.latest.clear()
            self.com = com
            self.status = 'connecting'
            folder = self.state / 'serial'; folder.mkdir(parents=True, exist_ok=True)
            self.log_path = folder / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + '.log')
            env = os.environ.copy(); env['UTSM_PICO_COM'] = com
            self.process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('serial_reader.ps1'))],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.thread = threading.Thread(target=self._read, args=(self.process, self.log_path), daemon=True)
            self.thread.start()
        return self.snapshot()

    def _read(self, process, path):
        saved = 0
        try:
            with path.open('w', encoding='utf-8') as log:
                for raw in process.stdout:
                    line = raw.rstrip('\r\n')
                    if line == '__MONITOR_READY__':
                        with self.lock:
                            if self.process is process: self.status = 'connected'
                        continue
                    if line.startswith('__MONITOR_ERROR__'):
                        line = 'Serial error: ' + line.removeprefix('__MONITOR_ERROR__')
                        with self.lock:
                            if self.process is process: self.status = 'error'
                    record = self.accept(line)
                    if saved < 10_000_000:
                        text = record['time'] + ' ' + record['text'] + '\n'
                        log.write(text); log.flush(); saved += len(text.encode('utf-8'))
        finally:
            process.stdout.close()
            with self.lock:
                if self.process is process and self.status != 'error': self.status = 'disconnected'

    def stop(self):
        with self.lock:
            process, thread = self.process, self.thread
            self.process = None; self.thread = None; self.status = 'disconnected'
        if process:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        if thread: thread.join(timeout=5)

    def pause_for_programming(self):
        with self.lock: active = self.status in ('connected', 'connecting')
        if active:
            self.stop()
            with self.lock: self.status = 'paused for programming'
            self.accept('Monitor paused to release the USB serial port for programming.')
        return active

    def resume_after_programming(self, device):
        self.start(device, preserve=True)
        self.accept('Monitor resumed after programming.')

    def clear(self):
        with self.lock: self.lines.clear(); self.latest.clear(); self.sequence += 1
