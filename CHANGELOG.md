# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0] - 2026-11-19

The first public release.

### Added

- Reads the grams each filament needs from the G-code Bambu Studio writes for every sliced plate, purge and prime tower included.
- Asks the printer, read-only over the LAN, what is left on every spool, and shows a "Filament check" card inside Bambu Studio's window: one line when there is enough, the full table when a filament falls short.
- Results: enough, tight, needs two spools, not enough, not loaded, colour not loaded, unknown. "Enough" includes a reserve, because the AMS's figure is an estimate.
- Checks every sliced plate again when "Send print job" opens, and shows the worst answer beside that dialog.
- Amounts for spools the printer cannot measure, entered on the card and counted down as prints run.
- A notification-area icon with the last result and the settings; `check`, `status`, `trays`, `selftest`, `install` and `uninstall` commands, with exit codes a script can gate on.
- A packaged folder and zip with a Python of its own (`python build.py`), for a computer that has none.

[Unreleased]: https://github.com/parthalon025/bambu-filament-guard/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/parthalon025/bambu-filament-guard/releases/tag/v1.2.0
