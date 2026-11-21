# Bambu Filament Guard

Warns, for every filament of a print sliced in Bambu Studio, when the spools on the printer do not hold enough of it.

[![tests](https://github.com/parthalon025/bambu-filament-guard/actions/workflows/tests.yml/badge.svg)](https://github.com/parthalon025/bambu-filament-guard/actions/workflows/tests.yml)
[![latest release](https://img.shields.io/github/v/release/parthalon025/bambu-filament-guard)](https://github.com/parthalon025/bambu-filament-guard/releases/latest)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
![platform: Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)

![The guard's "Filament check" card: "Not enough filament for this print", plate 1 on a Bambu Lab H2D. Five filaments, each with what it needs, its spool, what that spool has and the result: blue and red not enough, green tight, white and grey enough. Under each row a note gives the spool's reading and what the print running on the printer still needs of it.](docs/card-warning.png)

*A plate checked while the same plate prints: after the running print, two of its filaments would not have enough, and one would be tight. The picture is the guard's own card. The job in it is made up, with figures modelled on the print of 2026-11-19 described in [The running print](docs/running-print.md).*

## Features

- **Checks every filament of every plate you slice**, in any project, against what the spools on the printer hold, purge and prime tower included: as soon as the slice is done, and again for all the project's sliced plates when "Send print job" opens.
- **Counts the print that is running**, when it was sliced on this computer: a plate sliced while another prints is checked against what the spools will hold when it can start. See [The running print](docs/running-print.md).
- **Learns, per tray, how far the AMS's reading can be trusted**, from the prints it follows, and scores its own forecasts against what each print took. Until they have earned it, it only makes a check stricter; then the reserve is measured instead of fixed, and is smaller for a tray that proves accurate. See [Learning from previous prints](docs/learning.md).
- **Read-only, on your LAN**: it reads the printer's status and the files Bambu Studio writes while it slices, and sets nothing in the project. See [Privacy and security](#privacy-and-security).

## Contents

- [Requirements](#requirements)
- [Does it work with my printer?](#does-it-work-with-my-printer)
- [Install](#install)
- [Usage](#usage)
- [Commands](#commands)
- [Documentation](#documentation)
- [Privacy and security](#privacy-and-security)
- [Contributing](#contributing)
- [Changelog](#changelog)
- [Licence](#licence)

## Requirements

- Windows 10 or 11.
- Bambu Studio (not OrcaSlicer).
- The printer reachable on the local network, with its LAN access code in Bambu Studio.
- Spools the printer can measure (Bambu spools with an RFID tag, in an AMS) for amounts read automatically. For any other spool the amount is entered by hand: see [Spools without a reading](docs/manual-amounts.md).
- From source only: Python 3.13 or later.

## Does it work with my printer?

| Printer | Sliced files | On the printer |
|---|---|---|
| H2D | Tried | Tried, with an AMS (firmware 01.04.20.00): prints run with the guard |
| A1 mini | Tried | Answered a status request; it had no AMS, so no spool amounts were seen |
| A1, P1P, P1S, X1, X1 Carbon, X1E, P2S, H2S, H2D Pro, H2C, A2L, X2D, H3C | Tried | Not tried |

**Sliced files:** for every model above, the grams per layer the guard counts from Bambu Studio 02.09.01.70 slices add up to Bambu Studio's own totals, each filament to within 0.005 g (see [The running print](docs/running-print.md)).

**On the printer:** reading the spools has been tried on the H2D only. Which nozzle an AMS feeds is decoded for the H2D; where that cannot be read, a spool counts for either nozzle.

A report from any other model, whether it works or not, is welcome: [Printer report](https://github.com/parthalon025/bambu-filament-guard/issues/new?template=printer_report.yml).

## Install

### Packaged (no Python needed)

1. Download `BambuFilamentGuard-<version>.zip` from the [latest release](https://github.com/parthalon025/bambu-filament-guard/releases/latest).
2. Unzip it where it can stay: it runs from that folder.
3. Open `Install.cmd`.

`Uninstall.cmd` removes it. `python build.py` makes the same zip in `dist\`.

### From source

With Python 3.13 or later:

```powershell
pip install -r requirements.txt       # paho-mqtt, pystray and Pillow
python filament_guard.py install      # start automatically, and now
```

`python filament_guard.py uninstall` removes the shortcut and stops it.

Either way, the guard starts with Windows and waits. Its icon, a spool, is in the notification area while Bambu Studio runs.

## Usage

There is nothing to do beyond slicing as usual. When a plate is sliced, the guard reads from its G-code the grams each filament needs, asks the printer what is left on every spool, takes off what a running print still needs, and shows the answer as a **Filament check** card inside Bambu Studio's window, bottom right, under the slicing result.

With enough of everything the card is one line that goes away after 8 seconds:

![The one-line card: "Enough filament for this print", 5 filaments against the spools on the Bambu Lab H2D, with a Details button.](docs/card-enough.png)

When a filament falls short it is the full table, as in the picture at the top, and it stays until closed or until the next slice. From it you can check again after swapping a spool, ignore a finding for the project, or enter the amount of a spool the printer cannot measure.

| Result | Meaning |
|---|---|
| Enough | One matching spool holds the need plus the reserve. |
| Tight | One spool holds the need, without the reserve. |
| Needs two spools | No single spool holds it; the matching spools together do. AMS auto refill must be on. |
| Not enough | The matching spools together hold less than the need. |
| Not loaded | No spool of that material is loaded where the print's nozzle can reach it. |
| Colour not loaded | The material is loaded, this colour of it is not. The nearest spool is named and counts for nothing. |
| Unknown | The matching spool reports no remaining amount. Shown as one line, like "enough", unless `popup_on` names it. |

The card's buttons, the icon's menu and settings, how spools are matched and how the reserve is set are in [Using the guard](docs/using.md).

## Commands

```powershell
python filament_guard.py status       # running? where is the log?
python filament_guard.py check        # the newest slice of the running Bambu Studio, as text
python filament_guard.py check FILE.gcode.3mf --times 9   # nine prints of a sliced file
python filament_guard.py trays        # the spools each printer reports
python filament_guard.py learned      # what was learned from previous prints, per tray; --forget [TRAY] starts again
python filament_guard.py selftest     # can the guard read this Bambu Studio and reach the printers?
python filament_guard.py uninstall    # remove the shortcut and stop
```

`check` exits with a code a script can gate on: `--json` prints the full answer; `--popup` also shows the card.

| Exit code | Meaning |
|---|---|
| 0 | Enough |
| 1 | Tight, two spools needed, or amount unknown |
| 2 | Not enough, or the material or colour is not loaded |
| 3 | Could not be checked |

> [!TIP]
> **After a Bambu Studio update**, run `selftest` (or Self-test in the icon's menu). When Bambu Studio writes a slice the guard cannot read, a card says so once for that version, and every such file is in the log.

**Log and state** are in `%LOCALAPPDATA%\BambuFilamentGuard\`, with the grams per layer of the last 30 slices in `profiles\` and what was learned in `learned.json`. Settings are saved to `config.json` beside the script.

## Documentation

| Page | What it covers |
|---|---|
| [Using the guard](docs/using.md) | How it works step by step, the card and its buttons, the icon and its settings, what the results mean, the reserve |
| [The running print](docs/running-print.md) | How the print on the printer is counted before a new plate is checked |
| [Learning from previous prints](docs/learning.md) | What is recorded, the model, what is learned and what it changes, the measured reserve, weigh-ins, the simulation |
| [Spools without a reading](docs/manual-amounts.md) | Entering the amount of a spool the printer cannot measure, and how it is counted down |
| [Limits](docs/limits.md) | What the guard cannot read or does not yet know, and what it does in each case |

## Privacy and security

The guard makes no internet connection, and it sends the printer no command that changes it: no print, pause, stop, temperature or motion command. The LAN access code is read from Bambu Studio's configuration each time and goes to the printer only. What it reads, sends and keeps, and how to report a vulnerability, is in [SECURITY.md](SECURITY.md).

## Contributing

How to run the tests, the conventions a change follows, the layout of the code and what a useful report holds are in [CONTRIBUTING.md](CONTRIBUTING.md). Bugs: [open an issue](https://github.com/parthalon025/bambu-filament-guard/issues/new/choose).

## Changelog

What each version changed is in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT: see [LICENSE](LICENSE). The packaged zip also holds Python, Tcl/Tk, paho-mqtt, pystray and Pillow, each under its own licence. Those are in the zip: `LICENSE.txt` (Python), `tcl\tk8.6\license.terms` (Tcl/Tk) and the `dist-info` folders in `Lib\site-packages`.
