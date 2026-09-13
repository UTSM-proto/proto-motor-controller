# UTSM motor controller

RP2040 Pico firmware and StinkyController KiCad hardware.

On Windows, double-click **Launch Programmer.cmd** to edit the firmware configuration and build/program a connected Pico. The editor includes 40 settings plus the manual hall table, descriptions, sliders, numeric inputs, voltage conversion, profile import/export and a build log.

See [programmer setup and operation](programmer/README.md) for dependencies, firmware changes and testing. See [hardware files](hardware/StinkyController/README.md) for PCB revisions and manufacturing outputs. `blink.c` is the active firmware target; `temp.c` is a legacy copy and is not built.

The GUI generates an isolated configuration header for each build. The checked-in firmware defaults are in `firmware/controller_config.h`; `programmer/config.py` defines the editor schema and validation. Keep both synchronized when changing defaults.
