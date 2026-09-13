# Windows Pico programmer

Double-click **Launch Programmer.cmd** in the repository root. Python starts a local service and opens the configuration page in your browser. Keep its terminal open while using the app. Python 3.10+ is required; the app itself has no pip dependencies.

1. Adjust settings using number fields, sliders or On/Off menus. Throttle thresholds also accept external volts; the default 5 V display scale corresponds to 4095 ADC counts, not permission to apply 5 V to a Pico pin.
2. Save/import JSON profiles to keep configurations. Defaults match the last 1.25 V throttle setting, with synchronous switching off. The sensor scale factors now retain the fractional part that the old integer constants discarded.
3. Connect a Pico and click **Refresh devices**. Select its USB serial number.
4. **Build UF2 only** compiles without touching hardware. **Build & program Pico** builds a snapshot of the settings, writes that firmware to the selected device, verifies it with picotool, and restarts the Pico. Automatic identification moves the motor on restart. Release throttle before operation.
5. If automatic USB reset fails, close serial monitors, reconnect while holding BOOTSEL, refresh devices and retry. Devices using nonstandard USB IDs are not currently enumerated. Only the original RP2040 Pico is supported by this build target.

Every build lives in `.programmer/builds/<id>/`, with `config.json`, the source snapshot, `controller.uf2`, `manifest.json` (including SHA-256), and `operation.log`. Download UF2 from the page or use the saved file. Builds do not modify the checked-in default header. Changes made while a build runs belong to the next build.

## Build tools

The page reports detected tool paths. It supports the Pico VS Code tool layout, the existing UTSM tools directory, STM32Cube Arm GCC/Ninja, or explicit environment variables:

| Variable | Value |
| --- | --- |
| `PICO_SDK_PATH` | Pico SDK 2.1.0 directory, including the TinyUSB submodule |
| `UTSM_CMAKE` | Full path to `cmake.exe` |
| `UTSM_NINJA` | Full path to `ninja.exe` |
| `UTSM_ARM_GCC` | Full path to `arm-none-eabi-gcc.exe` |
| `UTSM_PICOTOOL` | Full path to `picotool.exe`; keep its CMake package files alongside it |

Install Python, Git, CMake, Ninja, the Arm GNU embedded toolchain and [picotool](https://github.com/raspberrypi/picotool/releases). The tested laptop uses SDK 2.1.0, Arm GCC 14.3.1 and picotool 2.3.1. To install the SDK into the auto-detected directory from PowerShell:

```powershell
git clone --depth 1 --branch 2.1.0 https://github.com/raspberrypi/pico-sdk.git "$env:LOCALAPPDATA\UTSM\PicoTools\pico-sdk-2.1.0"
git -C "$env:LOCALAPPDATA\UTSM\PicoTools\pico-sdk-2.1.0" submodule update --init --depth 1 lib/tinyusb
```

Do not run the clone command over an existing SDK checkout. Restart the programmer after changing environment variables. On another laptop, missing tools block the build with an actionable error; the app does not silently install software.

## Firmware fixes in this branch

- Startup hall identification runs once. It gathers consecutive stable whole-state readings, rejects invalid/duplicate states, and publishes a table only after all six sectors pass. Failed identification or a bad manual table keeps the drive disabled.
- Hall inputs use one GPIO register snapshot per sample and vote on complete three-bit codes. Temporal filtering spans PWM cycles. Uncertain states blank the phases; normal settling does not reset the throttle ramp. Skipped-sector faults clear when throttle is released, allowing resynchronization without reboot.
- Three ordered blocking ADC conversions replace the FIFO race. They read explicitly selected current, bus-voltage and throttle channels. ADC errors turn drive off and require throttle release. The control-loop timing must still be measured on hardware, especially with current control enabled.
- PWM outputs receive their off levels after initialization and before the GPIO function is switched. This avoids initialization clearing the off compare value on inverted low-side outputs.
- Current control handles zero duty without division by zero, uses a wide intermediate for current-limit arithmetic, and cannot integrate positive duty at zero throttle.
- Synchronous switching is one explicit setting, off by default in both control modes. The GUI validates pin uniqueness, paired PWM channels, separate slices, ADC pins, throttle ordering and manual tables.
- Telemetry snapshots shared values before printing: `current_mA,target_mA,duty16,bus_mV,hall,motor_state,invalid=N,transitions=N,adc=N`. A hall value of 255 means the sampled state was rejected. Calibration/table failures print a fault repeatedly.

The PCB's 5 V hall pull-ups remain a hardware issue. These firmware fixes cannot level-shift the hall inputs. Inspect the interface before powered tests. Calibration can still fail if duty is too low to move the rotor; it will now report failure and disable drive instead of accepting an incomplete table.

## Verification and bench acceptance

```powershell
python -m unittest discover -s tests -v
gcc -std=c11 -Wall -Wextra -Werror tests\test_logic.c -o .programmer\test_logic.exe
.\.programmer\test_logic.exe
```

`gcc` in this test command is a Windows host compiler, not Arm GCC. Create `.programmer` first if the app has never run. `tests/browser_smoke.cjs` additionally exercises the actual page and build using Playwright; set `PROGRAMMER_URL` to the authenticated URL printed by the service.

Before accepting motor operation, verify the gate outputs and ISR timing with a scope, confirm hall pin voltages, capture a complete calibration table, and check slow forward/reverse hand rotation with the power stage disabled. Then test controlled low-power startup, zero-throttle shutoff and hall-fault recovery. A successful build or USB verification does not establish correct motor commutation.

The service listens only on 127.0.0.1 and uses a random session token plus host/origin checks. There is no remote control endpoint, shell input, or automatic flash on page load.
