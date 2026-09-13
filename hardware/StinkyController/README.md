# StinkyController hardware

This directory contains the KiCad PCB projects and manufacturing outputs from
the supplied `StinkyController 0.5.zip` archive.

## Project revisions

- `StinkyController 0.5`
- `StinkyController 1`
- `StinkyController 1.1`
- `StinkyController MK2` (newest PCB and Gerber revision in the archive)

The `gerb*` directories and ZIP files contain the supplied manufacturing
outputs. Generated KiCad user preferences, caches, backups, and macOS metadata
are excluded from version control.

## Missing source

The supplied archive does not contain a `.kicad_sch` schematic file. The PCB
files retain component values, footprints, net names, and routed connectivity,
but the editable schematic source will need to be added separately if it still
exists.
