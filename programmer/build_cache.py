"""Cache complete firmware artifacts, retaining their original boot identity."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

SOURCE_FILES = ('blink.c', 'controller_logic.h', 'CMakeLists.txt', 'pico_sdk_import.cmake')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_stamp(root):
    """Track SDK/toolchain replacements and edits without rehashing large libraries."""
    root = Path(root)
    rows = []
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if f not in ('.git', '__pycache__'))
        for name in sorted(files):
            path = Path(directory) / name
            stat = path.stat()
            rows.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()


def fingerprint(source, config_header, tools):
    source = Path(source)
    inputs = dict(version=1, source={name: sha256(source / name) for name in SOURCE_FILES},
        header=config_header, recipe=sha256(Path(__file__).with_name('backend.py')),
        executables={name: dict(path=str(Path(path).resolve()), sha256=sha256(path)) for name, path in tools.items() if name != 'sdk'},
        sdk=dict(path=tools['sdk'], stamp=tree_stamp(tools['sdk'])),
        compiler_libraries=tree_stamp(Path(tools['gcc']).parent.parent),
        python=dict(path=sys.executable, version=sys.version),
        environment={key: value for key, value in os.environ.items() if key.startswith(('PICO_', 'CMAKE_')) or key in ('PATH', 'CFLAGS', 'CXXFLAGS', 'ASMFLAGS', 'CPPFLAGS', 'LDFLAGS', 'CC', 'CXX')})
    encoded = json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def lookup(state, key):
    try:
        entry = json.loads((Path(state) / 'cache' / (key + '.json')).read_text())
        build_id = entry['firmware_build_id']
        if not isinstance(build_id, str) or not re.fullmatch(r'[a-f0-9]{32}', build_id): return None
        folder = Path(state) / 'builds' / build_id
        manifest = json.loads((folder / 'manifest.json').read_text())
        if not isinstance(manifest, dict): return None
        artifact = folder / 'controller.uf2'
        if manifest.get('cache_key') != key or manifest.get('firmware_build_id') != build_id: return None
        if sha256(artifact) != manifest['uf2_sha256']: return None
        return artifact, manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None


def publish(state, key, build_id):
    cache = Path(state) / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + '.json')
    temporary = cache / (key + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(dict(firmware_build_id=build_id)), encoding='utf-8')
    temporary.replace(target)
