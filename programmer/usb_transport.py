"""Windows automatic reboot, port-correlated UF2 upload and boot confirmation."""
import os
from pathlib import Path
import re
import struct
import subprocess
import time

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def serial_command(com, script, timeout=15, extra=None):
    if not re.fullmatch(r'COM\d+', com or ''):
        raise RuntimeError('The selected Pico has no usable USB serial port.')
    env = os.environ.copy()
    env['UTSM_PICO_COM'] = com
    env.update(extra or {})
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env=env, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Pico serial operation failed. Close other serial monitors. ' + result.stderr.strip())
    return result.stdout


def request_bootloader(com):
    serial_command(com, """
$ErrorActionPreference = 'Stop'
$port = New-Object System.IO.Ports.SerialPort $env:UTSM_PICO_COM,1200,None,8,One
try { $port.DtrEnable = $true; $port.Open(); Start-Sleep -Milliseconds 150 }
finally { if ($port.IsOpen) { $port.Close() }; $port.Dispose() }
""")


def confirm_build(com, build_id):
    output = serial_command(com, """
$ErrorActionPreference = 'Stop'
$port = New-Object System.IO.Ports.SerialPort $env:UTSM_PICO_COM,115200,None,8,One
$port.ReadTimeout = 500
$port.DtrEnable = $true
$buffer = ''
try {
    $port.Open()
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ([DateTime]::UtcNow -lt $deadline) {
        $buffer += $port.ReadExisting()
        if ($buffer.Contains('build=' + $env:UTSM_BUILD_ID)) { Write-Output ('build=' + $env:UTSM_BUILD_ID); exit 0 }
        if ($buffer.Length -gt 16000) { $buffer = $buffer.Substring($buffer.Length - 8000) }
        Start-Sleep -Milliseconds 100
    }
    throw 'The firmware did not report the expected build identity.'
} finally { if ($port.IsOpen) { $port.Close() }; $port.Dispose() }
""", timeout=70, extra={'UTSM_BUILD_ID': build_id})
    if 'build=' + build_id not in output:
        raise RuntimeError('Firmware identity was not confirmed after transfer.')


def same_port(rows, location, mode):
    matches = [d for d in rows if d.get('location') == location and d['mode'] == mode]
    if len(matches) > 1:
        raise RuntimeError('More than one device matches the selected USB port; programming stopped.')
    return matches[0] if matches else None


def wait_for_device(inventory, location, mode, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        device = same_port(inventory(), location, mode)
        if device and (device.get('volumes') if mode == 'BOOTSEL' else device.get('com')):
            return device
        time.sleep(0.5)
    raise RuntimeError('Timed out waiting for the selected Pico to return as ' + mode + '. Check its USB connection. If firmware is unresponsive, manual BOOTSEL recovery may be necessary.')


def copy_uf2(artifact, root):
    root = Path(root)
    info = root / 'INFO_UF2.TXT'
    if not info.is_file() or 'RPI-RP2' not in info.read_text(errors='replace'):
        raise RuntimeError('Selected USB volume is not an RP2040 UF2 bootloader.')
    data = Path(artifact).read_bytes()
    if not data or len(data) % 512:
        raise RuntimeError('Build output is not a complete UF2 file.')
    for offset in range(0, len(data), 512):
        first, second = struct.unpack_from('<II', data, offset)
        end, = struct.unpack_from('<I', data, offset + 508)
        if (first, second, end) != (0x0A324655, 0x9E5D5157, 0x0AB16F30):
            raise RuntimeError('Build output has an invalid UF2 block.')
    with (root / 'UTSM.UF2').open('wb') as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def program(artifact, selected, build_id, inventory, log, stage):
    location = selected.get('location')
    if not location:
        raise RuntimeError('Windows did not provide a USB location for this Pico. Cannot safely follow it across reboot.')
    log('Selected USB port: ' + location)
    reset_error = None
    if selected['mode'] != 'BOOTSEL':
        stage('Rebooting selected Pico for programming')
        try:
            request_bootloader(selected.get('com'))
        except RuntimeError as exc:
            # Setting 1200 baud can reset the device before Windows finishes
            # Open(), producing an I/O error even when the request succeeded.
            reset_error = exc
            log('Serial port disconnected during reset; checking for the bootloader on the selected USB port.')
    stage('Waiting for Pico programming interface')
    try:
        boot = wait_for_device(inventory, location, 'BOOTSEL')
    except RuntimeError as exc:
        if reset_error:
            raise RuntimeError(str(reset_error) + ' ' + str(exc)) from exc
        raise
    log('Bootloader detected as ' + boot['serial'] + ' on the same USB port.')
    volumes = boot.get('volumes', [])
    if len(volumes) != 1:
        raise RuntimeError('Expected exactly one USB drive belonging to the selected Pico.')
    # Recheck correlation immediately before writing, never choose a drive by label alone.
    current = same_port(inventory(), location, 'BOOTSEL')
    if not current or current.get('volumes') != volumes or current['serial'] != boot['serial']:
        raise RuntimeError('USB target changed before the write. Programming stopped.')
    stage('Transferring firmware to Pico')
    copy_uf2(artifact, volumes[0])
    log('UF2 transferred. Waiting for the expected firmware to start; transfer alone is not success.')
    stage('Confirming firmware startup')
    runtime = wait_for_device(inventory, location, 'USB firmware')
    confirm_build(runtime['com'], build_id)
    log('Confirmed running firmware build=' + build_id + ' on ' + runtime['com'])
    return runtime
