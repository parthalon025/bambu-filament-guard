# Bambu Filament Guard

Warns, for every filament of a print, when the spools on the printer do not hold enough of it. Works for any project sliced in Bambu Studio; nothing is set in the project.

Bambu Studio has no plugin interface (v02.09.01.70: the `plugins` folder holds its network libraries only), so the guard runs beside it:

1. Bambu Studio writes the G-code of each plate it slices to `%TEMP%\bamboo_model\<day>\<time>#<pid>#<id>\Metadata\.<pid>.<n>.gcode`. The guard watches that folder.
2. From the G-code it reads the grams each filament needs (purge and prime tower included), and its material, colour, product and nozzle.
3. It asks the printer the plate was sliced for what is left on every spool (read-only status over the LAN).
4. The answer appears as a "Filament check" card inside Bambu Studio's window, bottom right, under the slicing result. With enough of everything it is one line that goes away after 8 seconds; when a filament falls short it is the full table, and it stays until closed or until the next slice.
5. When "Send print job" opens, the guard checks every sliced plate of the project again. The worst answer shows beside that dialog when it is a warning, and names its plate.

The card belongs to Bambu Studio's window: it is above that window and no other, moves and minimises with it, follows its dark or light mode, and never takes the keyboard. It is still drawn by the guard, which must be running.

## Install

With Python 3 (`pip install paho-mqtt pystray pillow`):

```powershell
python filament_guard.py install      # start automatically, and now
```

Without Python: unzip `dist\BambuFilamentGuard-<version>.zip` (made by `python build.py`) where it can stay, and open `Install.cmd`. It runs from that folder; `Uninstall.cmd` removes it.

The guard starts with Windows and waits. Its icon, a spool, is in the notification area while Bambu Studio runs.

## The card

| Button | Does |
|---|---|
| Check again | Asks the printer again, for the same slice: after swapping a spool, no re-slice is needed. |
| Ignore for this project | This finding shows as one line from now on, until it changes. "Stop ignoring" on the full card (Details) undoes it. |
| Amount in A3… | For a spool the printer cannot measure: enter the grams left. See "Spools without a reading". |
| Details | On the one-line card: the full table. |

## The icon

Its colour and tooltip are the last result. A click shows that result again. The menu: check the newest slice now, pause, self-test, the log, and the settings:

- One line when there is enough (`ok_seconds`)
- Warn when a colour is not loaded / when a spool's amount is unknown (`popup_on`)
- Check again at "Send print job" (`at_send`)
- Reserve: 2, 3 or 5 % of the full spool (`spool_reserve_pct`)
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

## Spools without a reading

A spool without an RFID tag, or on an external holder, reports no amount. Enter it on the card ("Amount in …"): weigh the spool and take off the empty spool. The guard then counts down, erring to the low side:

- A print is taken off in full when it starts to run, also when it is cancelled later.
- A print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the amount "out of date": unknown again, until it is entered again.
- When the printer was not followed for ten minutes or more (the computer asleep) and has printed something else since, every amount entered for it is out of date.
- The entry ends when the printer reports another material or colour in that tray.

To count, the guard keeps a standing read-only connection to a printer for as long as it has an entered amount. Not seen: a print started and finished while the printer was not followed, when the printer names it exactly like the one before (LAN-only mode).

## Commands

```powershell
python filament_guard.py status       # running? where is the log?
python filament_guard.py check        # the newest slice of the running Bambu Studio, as text
python filament_guard.py check FILE.gcode.3mf --times 9   # nine prints of a sliced file
python filament_guard.py trays        # the spools each printer reports
python filament_guard.py selftest     # can the guard read this Bambu Studio and reach the printers?
python filament_guard.py uninstall    # remove the shortcut and stop
```

`check` exits 0 (enough), 1 (tight, two spools needed, or amount unknown), 2 (not enough, or the material or colour is not loaded), 3 (could not be checked), so a script can gate on it. `--json` prints the full answer; `--popup` also shows the card.

**After a Bambu Studio update** run `selftest` (or Self-test in the icon's menu). When Bambu Studio writes a slice the guard cannot read, a card says so once for that version, and every such file is in the log.

## Limits

- The check is against what is loaded **now**. A print that is running is still drawing from the same spools; the card says so.
- Each plate is checked on its own. "Print all plates" needs the sum; check the exported file with `check`.
- Which plate "Send print job" is sending, and which tray you pick there, cannot be read: the card beside it is the worst of the project's sliced plates, against every matching spool. Bambu Studio's own warning there ("The filament in … may be insufficient for this print") covers the chosen tray.
- Two slots of one colour draw on the same spools and are judged on what they need together.
- With two printers of one model, the one selected in Bambu Studio is asked.
- Printers are the ones Bambu Studio holds a LAN access code for. A plate sliced for another printer is logged and shows no card.
- Which nozzle an AMS feeds is read as on the H2D (firmware 01.04.20.00). Where that cannot be read, a spool counts for either nozzle.
- How a job names an external spool in its tray mapping has not been seen on a print; a tray the guard cannot name makes the entered amounts out of date, never wrong.
- The packaged guard is a folder, not one program. A single self-installing `.exe` was built first; Windows Defender removed it at its first start as `Trojan:Win32/Bearfoos.A!ml`, as it does with many unsigned packed programs that add themselves to Startup. The folder runs on python.org's signed `pythonw.exe` and is left alone.

## Files

| File | Holds |
|---|---|
| `filament_guard.py` | G-code, printer status, the assessment, the watcher, the commands |
| `guard_ui.py` | the card and the icon |
| `guard_ledger.py` | entered amounts and the prints that draw on them |
| `build.py` | makes the packaged folder and its zip |
| `test_filament_guard.py` | `python -m pytest test_filament_guard.py` (needs no printer and no Bambu Studio) |

Log and state: `%LOCALAPPDATA%\BambuFilamentGuard\`. The LAN access code is read from Bambu Studio's configuration each time and goes to the printer only.
