# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Counts the print that is running. From each spool the guard first takes off what the print on the printer still needs from it, from the layer the printer is on to the end, so the answer is for the moment the checked print could start. In batch printing the next plate is sliced while one prints; until now it was checked against spools the running print was still emptying.
- Keeps, for each plate it sees sliced, the grams of each filament every layer takes, read from the extrusion moves of the G-code (about a second for 100 MB). The purge the printer does by itself at each filament change is placed by Bambu Studio's own flush table, so each filament's layers add up to its total. The last 30 are kept in `%LOCALAPPDATA%\BambuFilamentGuard\profiles\`.
- The card, the text report and `--json` say whether the running print was counted, and a filament's note names the spool's reading and what the running print still needs of it.
- A line that says a check is under way ("Checking the spools on the Bambu Lab H2D…") as soon as one starts that you are waiting for: a new slice, or a check from the icon. The answer takes its place.
- A line when the guard starts, the first time Bambu Studio is on screen: "Bambu Filament Guard 1.3.0 is watching for sliced plates".

### Changed

- A job is matched to its slice by plate as well, when the printer names the file it prints, so two plates of one project with the same number of layers are told apart. The amounts entered by hand are counted down by the same match.
- A new slice is checked before it is remembered, so a plate sliced again while it prints is checked against the slice that print was made from.

### Fixed

- A click on the icon while its check was running started another check, five in one second in one log; now it shows the "Checking…" line and starts nothing.
- A plate whose check failed ended that round of the watcher, and plates sliced with it went unchecked.
- The printer's job showed "None %" when the printer gave no percentage.

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
