"""Local build and USB programming service. No shell commands from the page."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from config import header, validate

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".programmer"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_tool(env, command, patterns):
    override = os.environ.get(env)
    if override:
        return str(Path(override).resolve()) if Path(override).is_file() else None
    found = shutil.which(command)
    if found:
        return found
    for base, pattern in patterns:
        matches = sorted(base.glob(pattern), reverse=True)
        if matches:
            return str(matches[0].resolve())
    return None


def toolchain():
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    pico = Path.home() / ".pico-sdk"
    installed = local / "UTSM/PicoTools"
    packaged = list(local.glob("Packages/OpenAI.Codex_*/LocalCache/Local/UTSM/PicoTools"))
    sdk_candidates = [Path(os.environ["PICO_SDK_PATH"])] if os.environ.get("PICO_SDK_PATH") else [installed / "pico-sdk-2.1.0", pico / "sdk/2.1.0", *[p / "pico-sdk-2.1.0" for p in packaged]]
    sdk = next((str(p.resolve()) for p in sdk_candidates if (p / "pico_sdk_init.cmake").is_file() and (p / "lib/tinyusb/src/tusb.c").is_file()), None)
    return {
        "cmake": find_tool("UTSM_CMAKE", "cmake", [(Path("C:/Program Files"), "CMake/bin/cmake.exe")]),
        "ninja": find_tool("UTSM_NINJA", "ninja", [(pico, "ninja/*/ninja.exe"), (local, "stm32cube/bundles/ninja/*/bin/ninja.exe")]),
        "gcc": find_tool("UTSM_ARM_GCC", "arm-none-eabi-gcc", [(pico, "toolchain/*/bin/arm-none-eabi-gcc.exe"), (local, "stm32cube/bundles/gnu-tools-for-stm32/*/bin/arm-none-eabi-gcc.exe")]),
        "picotool": find_tool("UTSM_PICOTOOL", "picotool", [(installed, "picotool-*/picotool/picotool.exe"), (pico, "picotool/*/picotool.exe"), *[(p, "picotool-*/picotool/picotool.exe") for p in packaged]]),
        "sdk": sdk,
    }


def devices():
    if os.name != "nt":
        return []
    command = r"Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object { $_.InstanceId -match '^USB\\VID_2E8A&PID_(0003|000A)\\[A-Fa-f0-9]+$' } | Select-Object FriendlyName,InstanceId | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Windows USB enumeration failed: " + result.stderr.strip())
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows, dict): rows = [rows]
    output = {}
    for row in rows:
        match = re.fullmatch(r"USB\\VID_2E8A&PID_(0003|000A)\\([A-Fa-f0-9]+)", row["InstanceId"], re.I)
        if match:
            serial = match[2].upper()
            output[serial] = dict(serial=serial, mode="BOOTSEL" if match[1] == "0003" else "USB firmware", name=row.get("FriendlyName") or "Raspberry Pi Pico")
    return list(output.values())


class Programmer:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.job))

    def log(self, message):
        with self.lock:
            self.job["log"] = (self.job["log"] + message + "\n")[-80000:]

    def run(self, args, cwd, env=None, timeout=300):
        self.log(subprocess.list2cmdline([str(a) for a in args]))
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
            capture_output=True, text=True, errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        self.log(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{Path(args[0]).name} exited with code {result.returncode}. See the build log.")
        return result

    def start(self, values, flash=False, serial=None):
        values = json.loads(json.dumps(validate(values)))
        if flash and (not isinstance(serial, str) or not re.fullmatch(r"[A-Fa-f0-9]{8,32}", serial)):
            raise ValueError("Choose a detected Pico before programming.")
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("A build or programming operation is already running.")
            self.job = dict(id=uuid.uuid4().hex, status="running", stage="Preparing", log="", artifact=None, flash=flash, serial=serial, started=time.time())
        threading.Thread(target=self.work, args=(values, flash, serial), daemon=True).start()
        return self.status()

    def stage(self, value):
        with self.lock: self.job["stage"] = value

    def work(self, values, flash, serial):
        final_status = "failed"
        try:
            tools = toolchain()
            missing = [name for name, path in tools.items() if not path]
            if missing: raise RuntimeError("Missing tools: " + ", ".join(missing) + ". See programmer/README.md for setup and path overrides.")
            folder = STATE / "builds" / self.job["id"]
            source = folder / "source"
            source.mkdir(parents=True)
            for name in ("blink.c", "controller_logic.h", "CMakeLists.txt", "pico_sdk_import.cmake"):
                shutil.copy2(ROOT / name, source / name)
            config_dir = source / "firmware"
            config_dir.mkdir()
            (config_dir / "controller_config.h").write_text(header(values), encoding="utf-8")
            (folder / "config.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
            build = folder / "build"
            env = os.environ.copy()
            env["PICO_SDK_PATH"] = tools["sdk"]
            env["PICO_TOOLCHAIN_PATH"] = str(Path(tools["gcc"]).parent.parent)
            env["PATH"] = str(Path(tools["gcc"]).parent) + os.pathsep + env.get("PATH", "")
            self.stage("Configuring")
            self.run([tools["cmake"], "-S", source, "-B", build, "-G", "Ninja",
                "-DCMAKE_BUILD_TYPE=Release", "-DPICO_BOARD=pico", "-DPICO_COMPILER=pico_arm_cortex_m0plus_gcc",
                "-DCMAKE_MAKE_PROGRAM=" + tools["ninja"], "-DPICO_SDK_PATH=" + tools["sdk"],
                "-DPICO_TOOLCHAIN_PATH=" + env["PICO_TOOLCHAIN_PATH"],
                "-Dpicotool_DIR=" + str(Path(tools["picotool"]).parent)], folder, env)
            self.stage("Compiling firmware")
            self.run([tools["cmake"], "--build", build, "--parallel", "4"], folder, env, 600)
            artifact = folder / "controller.uf2"
            shutil.copy2(build / "blink.uf2", artifact)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = dict(config=values, uf2_sha256=digest, tools=tools,
                source_sha256={name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in ("blink.c", "controller_logic.h", "CMakeLists.txt", "pico_sdk_import.cmake")})
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            with self.lock:
                self.job["artifact"] = str(artifact)
                self.job["sha256"] = digest
            if flash:
                self.stage("Checking selected Pico")
                if serial.upper() not in {d["serial"] for d in devices()}:
                    raise RuntimeError("Selected Pico is no longer connected. Reconnect it, refresh devices, and retry. The built UF2 is saved.")
                self.stage("Programming and verifying flash")
                # -v verifies flash before -x restarts it. Serial selection prevents
                # accidentally programming a different connected board.
                self.run([tools["picotool"], "load", "-v", "-x", str(artifact), "--ser", serial, "-f"], folder, timeout=90)
                self.log("Flash verified by picotool; restart requested. Motor operation has not been validated.")
            self.stage("Flash verified; restart requested" if flash else "Build ready")
            final_status = "complete"
        except Exception as exc:
            self.log(str(exc))
            self.stage("Operation failed")
        finally:
            folder = STATE / "builds" / self.job["id"]
            if folder.exists():
                (folder / "operation.log").write_text(self.status()["log"], encoding="utf-8")
            with self.lock: self.job["status"] = final_status
