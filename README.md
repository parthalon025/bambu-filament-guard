# Bambu Filament Guard

Warns, for every filament of a print sliced in Bambu Studio, when the spools on the printer do not hold enough of it.

[![tests](https://github.com/parthalon025/bambu-filament-guard/actions/workflows/tests.yml/badge.svg)](https://github.com/parthalon025/bambu-filament-guard/actions/workflows/tests.yml)
[![latest release](https://img.shields.io/github/v/release/parthalon025/bambu-filament-guard)](https://github.com/parthalon025/bambu-filament-guard/releases/latest)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
![platform: Windows](https://img.shields.io/badge/platform-Windows-blue)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue)

![The guard's "Filament check" card: "Not enough filament for this print", plate 1 on a Bambu Lab H2D. Five filaments, each with what it needs, its spool, what that spool has and the result: blue and red not enough, green tight, white and grey enough. Under each row a note gives the spool's reading and what the print running on the printer still needs of it.](docs/card-warning.png)

A plate checked while the same plate prints: after the running print, two of its filaments would not have enough, and one would be tight. The pictures are the guard's own card. The job in them is made up, with figures modelled on the print of 2026-11-19 described under "The running print".

- Checks every filament of every plate you slice, in any project, against what the spools on the printer hold, purge and prime tower included: as soon as the slice is done, and again for all the project's sliced plates when "Send print job" opens.
- Counts the print that is running, when it was sliced on this computer: a plate sliced while another prints is checked against what the spools will hold when it can start.
- Learns, per tray, how far the AMS's reading can be trusted, from the prints it follows. It can only make a check stricter.
- Read-only, on your LAN: it reads the printer's status and the files Bambu Studio writes while it slices, and sets nothing in the project.

## Does it work with my printer?

| Printer | Sliced files | On the printer |
|---|---|---|
| H2D | Tried | Tried, with an AMS (firmware 01.04.20.00): prints run with the guard |
| A1 mini | Tried | Answered a status request; it had no AMS, so no spool amounts were seen |
| A1, P1P, P1S, X1, X1 Carbon, X1E, P2S, H2S, H2D Pro, H2C, A2L, X2D, H3C | Tried | Not tried |

Sliced files: for every model above, the grams per layer the guard counts from Bambu Studio 02.09.01.70 slices add up to Bambu Studio's own totals, each filament to within 0.005 g (see "The running print"). Reading the spools has been tried on the H2D only; on the other models it has not been tried on a machine. A report from one, whether it works or not, is welcome: [Printer report](https://github.com/parthalon025/bambu-filament-guard/issues/new?template=printer_report.yml).

On any of them the guard needs Windows 10 or 11, Bambu Studio (not OrcaSlicer), and the printer reachable on the local network with its LAN access code in Bambu Studio. It reads the amounts of spools the printer can measure: Bambu spools with an RFID tag, in an AMS. For any other spool the amount is entered by hand (see "Spools without a reading"). Which nozzle an AMS feeds is decoded for the H2D; where that cannot be read, a spool counts for either nozzle.

## Install

Without Python: download `BambuFilamentGuard-<version>.zip` from the repository's [Releases](https://github.com/parthalon025/bambu-filament-guard/releases), unzip it where it can stay, and open `Install.cmd`. It runs from that folder; `Uninstall.cmd` removes it. `python build.py` makes the same zip in `dist\`.

From source, with Python 3.13 or later:

```powershell
pip install -r requirements.txt       # paho-mqtt, pystray and Pillow
python filament_guard.py install      # start automatically, and now
```

The guard starts with Windows and waits. Its icon, a spool, is in the notification area while Bambu Studio runs.

## How it works

Bambu Studio has no plugin interface (v02.09.01.70: the `plugins` folder holds its network libraries only), so the guard runs beside it:

1. Bambu Studio writes the G-code of each plate it slices to `%TEMP%\bamboo_model\<day>\<time>#<pid>#<id>\Metadata\.<pid>.<n>.gcode`. The guard watches that folder.
2. From the G-code it reads the grams each filament needs (purge and prime tower included), and its material, colour, product and nozzle.
3. It asks the printer the plate was sliced for what is left on every spool (read-only status over the LAN). When the printer is running a print, it first takes off each spool what that print still needs from it: the answer is for the moment the new print could start. See "The running print".
4. The answer appears as a "Filament check" card inside Bambu Studio's window, bottom right, under the slicing result. With enough of everything it is one line that goes away after 8 seconds; when a filament falls short it is the full table, and it stays until closed or until the next slice.
5. When "Send print job" opens, the guard checks every sliced plate of the project again. The worst answer shows beside that dialog when it is a warning, and names its plate.

The card belongs to Bambu Studio's window: it is above that window and no other, moves and minimises with it, follows its dark or light mode, and never takes the keyboard. It is still drawn by the guard, which must be running.

## The card

With enough of everything the card is one line:

![The one-line card: "Enough filament for this print", 5 filaments against the spools on the Bambu Lab H2D, with a Details button.](docs/card-enough.png)

A check takes a second or three, because the guard asks the printer each time. While one runs that you are waiting for (a plate you just sliced, or one you asked for from the icon) the card says so at once in one line, "Checking the spools on the Bambu Lab H2D…", and the answer takes its place.

![The one-line card while a check runs: "Checking the spools on the Bambu Lab H2D…".](docs/card-checking.png)

For a new slice the line does not cover a full card that is still open; asked for from the icon, it takes the place of any card, as its answer will. Beside "Send print job" nothing shows until there is a warning.

When the guard starts, the first time Bambu Studio runs it shows one line, "Bambu Filament Guard 1.3.0 is watching for sliced plates", which goes away by itself; after `Install.cmd` the install card says it instead. A one-line card that comes while Bambu Studio is minimised waits for its window, and its seconds count from when it shows. With "One line when there is enough" turned off, quiet was asked for: a new slice shows no "Checking…" line, and the guard no start line.

| Button | Does |
|---|---|
| Check again | Asks the printer again, for the same slice: after swapping a spool, no re-slice is needed. |
| Ignore for this project | This finding shows as one line from now on, until it changes. "Stop ignoring" on the full card (Details) undoes it. |
| Amount in A3… | For a spool the printer cannot measure: enter the grams left. See "Spools without a reading". |
| Details | On the one-line card: the full table. |

## The icon

Its colour and tooltip are the last result. A click shows that result again; before there is one, it checks the newest slice. A click while that check runs shows the "Checking…" line again and starts no second check. The menu: check the newest slice now, pause, self-test, the log, forget what was learned, and the settings:

- One line when there is enough (`ok_seconds`)
- Warn when a colour is not loaded / when a spool's amount is unknown (`popup_on`)
- Check again at "Send print job" (`at_send`)
- Reserve: 2, 3 or 5 % of the full spool (`spool_reserve_pct`)
- Learn from prints, tray by tray (`learn`, on; see "Learning from previous prints")
- Start automatically

Settings are saved to `config.json` beside the script, which holds only what was changed. The keys, their defaults and their meaning are `DEFAULTS` at the top of `filament_guard.py`.

## What the results mean

| Result | Meaning |
|---|---|
| Enough | One matching spool holds the need plus the reserve. |
| Tight | One spool holds the need, without the reserve. |
| Needs two spools | No single spool holds it; the matching spools together do. AMS auto refill must be on. |
| Not enough | The matching spools together hold less than the need. |
| Not loaded | No spool of that material is loaded where the print's nozzle can reach it. |
| Colour not loaded | The material is loaded, this colour of it is not. The nearest spool is named and counts for nothing. |
| Unknown | The matching spool reports no remaining amount. Shown as one line, like "enough", unless `popup_on` names it. |

A spool matches a filament when it is the same material, on the nozzle that prints it, in the project's colour; spools of the same product come first. A spool of similar colour but another product is named in the note and not counted.

**The reserve.** The AMS's figure is an estimate: it counts what it has fed, so its error grows as the spool empties. On 2026-11-19 a 1 kg spool reported at 105 g ran dry after 84-85 g, one gram before the end of a print that needed 85.3 g. So "enough" asks for the need plus the larger of 10 % of the need (`margin_pct`) and 3 % of the spool's full weight (`spool_reserve_pct`: 30 g on a 1 kg spool). Below that the result is "tight", and the full card shows.

## The running print

In batch printing the next plate is sliced while one prints. On 2026-11-19 a plate needing 85.3 g of blue was printing from a spool that read 105 g; the same plate, sliced again for the next run, was checked against those 105 g, although after the running print the spool would hold about 20 g. So every check counts the print that is running:

- For each plate it sees sliced, the guard keeps how many grams of each filament every layer takes, counted from the G-code the way Bambu Studio counts its own totals: the moves along X or Y that extrude, and the purge at each filament change. The purge is real moves between `; FLUSH_START` and `; FLUSH_END` (A1, P1, X1), or, where the printer purges by itself, the `;VG1` comments between `; VFLUSH_START` and `; VFLUSH_END` (H2, P2S, A2L, X2D, H3C). Of each purge, as much as the nozzle holds counts for the filament it pushes out, the rest for the one loaded. Moves of the filament alone (load, prime, retract) are not counted; Bambu Studio does not count them either. This was measured on Bambu Studio 02.09.01.70 slices for those printer families, where every filament came to within 0.005 g of Bambu Studio's total; only the H2D has been tried on a real printer. A filament further than 0.02 g (or 0.2 %) from its total gives the slice no profile, and the log says by how much: a Bambu Studio that counts differently shows there.
- The job the printer runs is matched to its slice (project name, layer count and plate) and to the trays it uses (the job's tray mapping). What it still needs is its grams from the layer the printer is on to the end, that layer counted whole; while the printer prepares a job, all of it.
- That is taken off each spool's reading before the check. The note of a filament says so: "C3 reads 105 g, of which the running print still needs 85 g". The card says whether the running print was counted.
- What the running print needs beyond what its tray holds comes, with AMS auto refill, from another loaded spool of the same filament (the same material and colour, the same product where both name one, for the same nozzle): it is taken off those, the fullest first, and the note says so ("C3 reads 20 g; the running print still needs 85 g: 65 g more than it holds, taken off C4"). When no such spool is loaded, the card says that the print running now needs that much more than the loaded spools hold: a warning about the running print, whatever the checked plate needs.
- An amount entered by hand is counted down by the guard as prints draw on it (see "Spools without a reading"). Until it has taken the running print off it, the check takes that print's need off it like off a reading; once it has, not again.

## Learning from previous prints

The AMS's reading is an estimate, and not equally good in every tray. On 2026-11-19 tray C3 read 105 g and ran dry after 84-85 g of a print: it read about 20 g when it was empty. In the same print tray B4 fell 49 g for a need of 49.1 g: that tray tracks well. A fixed reserve treats both alike. So the guard learns, per printer and tray, how far that tray's reading can be trusted.

**What is recorded.** While learning is on, the guard follows a printer it has checked in the last 30 minutes, or that is printing, over the same read-only link it uses for entered amounts: it asks once, at each connection, for the full status, then reads the status reports every 3 seconds, and sends nothing else. For each job matched to a slice with a per-layer profile, it records per tray: the spool (its RFID tag when it has one), the reading before the first layer, at the start of each layer (at most 60 per print, thinned evenly), and a minute after the print finished; and how many grams the profile has that tray give over the same span, printed and purged apart. A record is used only when it is sound: the same spool throughout, a reading that never rose, no gap of ten minutes or more in following, the tray never empty, and the print seen to finish. A run-dry is a tray the print is drawing on at that point (by its profile) that is reported without a spool in two looks in a row, having read 50 g or less: its last reading is recorded, with the printer's error and HMS codes at that moment, raw, to judge it by later. A fuller spool, or one the print was not drawing on then, was taken out: that is logged and teaches nothing. A print not sliced on this computer records no run-dry, as what it draws on and when cannot be told.

**What is learned**, per tray, each number only past its threshold:

| Number | What it is | Used when |
|---|---|---|
| dry | what the tray read when a spool ran dry in it: the largest of its last 3 run-dries still in use | from the first run-dry; one stops being used when a later print shows the tray still giving filament at a lower reading |
| rate | how far the reading fell for each gram the profile says the tray gave, over its last 10 sound whole prints of 5 g or more | from 3 such prints with 300 g between them, and only below 0.99 (whole-gram readings make 1 % noise) and not below 0.9 (a lower rate means readings that lagged behind the print: the one measurement implies about 0.98) |
| pre | grams the tray lost before the first layer beyond what the profile has there: the largest of its last 5 sound prints | from 2 such prints |

**What it changes.** A tray the printer measures is counted with the smallest of its reading G, G less dry, and W - (W - G) / rate (W the spool's full weight), never below 0; the running print's share is then taken off that. A filament judged on the tray needs pre more. The note says what was done and why: "C3 reads 105 g, counted as 85 g: it read 20 g when a spool ran dry in it on 19 Nov". Learning can only make a check stricter: it never counts a tray higher or a need lower, and an amount entered by hand is left alone. With nothing learned yet, or learning off, every check is as without it. It starts empty and needs prints the guard follows from start to finish. It was built from one measured incident and has not yet been confirmed over many prints; `learned` shows the evidence behind every number, so it can be judged.

`learned` also reports, never uses, how the reading fell per gram printed and per gram purged separately, once there are sound prints whose purge shares differ enough to tell. `learned --forget` and "Forget what was learned" in the icon's menu start again from nothing; `learned --forget C3` forgets one tray (with `--printer` where two printers have a C3).

## Spools without a reading

A spool without an RFID tag, or on an external holder, reports no amount. Enter it on the card ("Amount in …"): weigh the spool and take off the empty spool. The guard then counts down, erring to the low side:

- A print is taken off in full when it starts to run, also when it is cancelled later.
- An amount entered while a print runs is what the spool held then: what that print still needs of it comes off at the guard's next look at the printer. When that is not known (the print was not sliced on this computer), the card says it could not count the running print.
- A print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the amount "out of date": unknown again, until it is entered again.
- When the printer was not followed for ten minutes or more (the computer asleep) and has printed something else since, every amount entered for it is out of date.
- The entry ends when the printer reports another material or colour in that tray.

To count, the guard keeps a standing read-only connection to a printer for as long as it has an entered amount (and while learning has a reason to, see above). Not seen: a print started and finished while the printer was not followed, when the printer names it exactly like the one before (LAN-only mode).

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

`check` exits 0 (enough), 1 (tight, two spools needed, or amount unknown), 2 (not enough, or the material or colour is not loaded), 3 (could not be checked), so a script can gate on it. `--json` prints the full answer; `--popup` also shows the card.

**After a Bambu Studio update** run `selftest` (or Self-test in the icon's menu). When Bambu Studio writes a slice the guard cannot read, a card says so once for that version, and every such file is in the log.

## Limits

- A running print is counted only when it was sliced on this computer while the guard (1.3.0 or later) was watching, and its G-code adds up to Bambu Studio's totals (see "The running print"). Otherwise the card says it could not count it, and the amounts are what the spools hold now.
- A job is matched to its slice by project name and layer count, and by plate when the printer names the file it prints (`/data/Metadata/plate_2.gcode`, as the H2D does). A printer that does not (an A1 mini printing from its SD card reports nothing there) gets the newer of two plates of one project with the same number of layers. Re-slicing a plate replaces its slice: from then on, a print still running from the earlier slice is counted with the new slice's amounts, or not at all when the layer count changed.
- What the running print needs beyond its tray is taken off the loaded spools AMS auto refill would go on with, as the guard reads that rule (the same material and colour, the same product where both name one, the same nozzle). Whether auto refill is on, and which spool the AMS really takes, cannot be read.
- Each plate is checked on its own. "Print all plates" needs the sum; check the exported file with `check`.
- Which plate "Send print job" is sending, and which tray you pick there, cannot be read: the card beside it is the worst of the project's sliced plates, against every matching spool. Bambu Studio's own warning there ("The filament in … may be insufficient for this print") covers the chosen tray.
- Two slots of one colour draw on the same spools and are judged on what they need together.
- With two printers of one model, the one selected in Bambu Studio is asked.
- Printers are the ones Bambu Studio holds a LAN access code for. A plate sliced for another printer is logged and shows no card.
- Which nozzle an AMS feeds is read as on the H2D (firmware 01.04.20.00). Where that cannot be read, a spool counts for either nozzle.
- How a job names an external spool in its tray mapping has not been seen on a print; a tray the guard cannot name makes the entered amounts out of date, never wrong.
- Learning learns nothing from a print not sliced on this computer. From a print it followed only from its middle (the guard started during it) it learns a run-dry but no rate and no loss before the first layer, which need the print's start. A spool moved to another tray starts again there: what is learned belongs to the tray, not the spool.
- Learning takes the readings the printer reports to follow the spool between the guard's looks, 3 seconds apart. Not yet seen on a print, and recorded so that the history will show them: how often a tray's reading changes during a print (a reading that lags shows as a low rate, and a rate below 0.9 is not used); what a tray looks like in the status when its spool runs out (counted as a run-dry only when the tray is reported without a spool in two looks in a row; a tray still reported, reading 0 g, makes the print's record unsound instead, with the reading before it); whether the AMS, refilling from a second spool, changes the job's tray mapping (the record then says at which layer, and is not used); and whether the reading settles after the print ends (both the first reading at the end and the one a minute later are kept).
- The packaged guard is a folder, not one program. A single self-installing `.exe` was built first; Windows Defender removed it at its first start as `Trojan:Win32/Bearfoos.A!ml`, as it does with many unsigned packed programs that add themselves to Startup. The folder runs on python.org's signed `pythonw.exe` and is left alone.

## Files

| File | Holds |
|---|---|
| `filament_guard.py` | G-code (totals and grams per layer), printer status, the assessment, the watcher, the commands |
| `guard_ui.py` | the card and the icon |
| `guard_ledger.py` | the prints that draw on the spools: entered amounts counted down, what the running print still needs; the link that follows a printer |
| `guard_learn.py` | learning from previous prints, per tray: the record, the three numbers, what they change, `learned` |
| `build.py` | makes the packaged folder and its zip |
| `test_filament_guard.py` | `python -m pytest test_filament_guard.py` (needs no printer and no Bambu Studio) |
| `docs/` | the pictures in this README |

Log and state: `%LOCALAPPDATA%\BambuFilamentGuard\`, with the grams per layer of the last 30 slices in `profiles\` and what was learned in `learned.json`. The LAN access code is read from Bambu Studio's configuration each time and goes to the printer only.

## Privacy and security

The guard makes no internet connection, and it sends the printer no command that changes it: no print, pause, stop, temperature or motion command. What it reads, sends and keeps, and how to report a vulnerability, is in [SECURITY.md](SECURITY.md).

## Contributing

How to run the tests, the conventions a change follows and what a useful report holds are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Changes

What each version changed is in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT: see [LICENSE](LICENSE). The packaged zip also holds Python, Tcl/Tk, paho-mqtt, pystray and Pillow, each under its own licence. Those are in the zip: `LICENSE.txt` (Python), `tcl\tk8.6\license.terms` (Tcl/Tk) and the `dist-info` folders in `Lib\site-packages`.
