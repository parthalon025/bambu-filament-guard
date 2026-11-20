# Bambu Filament Guard

Warns, for every filament of a print, when the spools on the printer do not hold enough of it. Works for any project sliced in Bambu Studio; nothing is set in the project.

Bambu Studio has no plugin interface (v02.09.01.70: the `plugins` folder holds its network libraries only), so the guard runs beside it:

1. Bambu Studio writes the G-code of each plate it slices to `%TEMP%\bamboo_model\<day>\<time>#<pid>#<id>\Metadata\.<pid>.<n>.gcode`. The guard watches that folder.
2. From the G-code it reads the grams each filament needs (purge and prime tower included), and its material, colour, product and nozzle.
3. It asks the printer the plate was sliced for what is left on every spool (read-only status over the LAN). When the printer is running a print, it first takes off each spool what that print still needs from it: the answer is for the moment the new print could start. See "The running print".
4. The answer appears as a "Filament check" card inside Bambu Studio's window, bottom right, under the slicing result. With enough of everything it is one line that goes away after 8 seconds; when a filament falls short it is the full table, and it stays until closed or until the next slice.
5. When "Send print job" opens, the guard checks every sliced plate of the project again. The worst answer shows beside that dialog when it is a warning, and names its plate.

The card belongs to Bambu Studio's window: it is above that window and no other, moves and minimises with it, follows its dark or light mode, and never takes the keyboard. It is still drawn by the guard, which must be running.

## Install

With Python 3 (`pip install paho-mqtt pystray pillow`):

```powershell
python filament_guard.py install      # start automatically, and now
```

Without Python: download `BambuFilamentGuard-<version>.zip` from the repository's [Releases](https://github.com/parthalon025/bambu-filament-guard/releases), unzip it where it can stay, and open `Install.cmd`. It runs from that folder; `Uninstall.cmd` removes it. `python build.py` makes the same zip in `dist\`.

The guard starts with Windows and waits. Its icon, a spool, is in the notification area while Bambu Studio runs.

## The card

A check takes a second or three, because the guard asks the printer each time. While one runs that you are waiting for (a plate you just sliced, or one you asked for from the icon) the card says so at once in one line, "Checking the spools on the Bambu Lab H2D…", and the answer takes its place. It does not cover a full card that is still open, and beside "Send print job" nothing shows until there is a warning.

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
- An amount entered by hand is not reduced again: the whole job came off it when the job started (see below).

## Learning from previous prints

The AMS's reading is an estimate, and not equally good in every tray. On 2026-11-19 tray C3 read 105 g and ran dry after 84-85 g of a print: it read about 20 g when it was empty. In the same print tray B4 fell 49 g for a need of 49.1 g: that tray tracks well. A fixed reserve treats both alike. So the guard learns, per printer and tray, how far that tray's reading can be trusted.

**What is recorded.** While learning is on, the guard follows a printer it has checked in the last 30 minutes, or that is printing, over the same read-only link it uses for entered amounts: it reads the status every 3 seconds and sends nothing. For each job matched to a slice with a per-layer profile, it records per tray: the spool (its RFID tag when it has one), the reading before the first layer, at the start of each layer (at most 60 per print, thinned evenly), and a minute after the print finished; and how many grams the profile has that tray give over the same span, printed and purged apart. A record is used only when it is sound: the same spool throughout, a reading that never rose, no gap of ten minutes or more in following, the tray never empty, and the print seen to finish. A tray that empties out of the status while the print runs, having read 50 g (or 10 % of its spool) or less, is a run-dry, and its last reading is recorded; a fuller spool was taken out, which is logged and teaches nothing.

**What is learned**, per tray, each number only past its threshold:

| Number | What it is | Used when |
|---|---|---|
| dry | what the tray read when a spool ran dry in it: the largest of its last 3 run-dries | from the first run-dry |
| rate | how far the reading fell for each gram the profile says the tray gave, over its last 10 sound whole prints of 5 g or more | from 3 such prints with 300 g between them, and only below 0.99 (whole-gram readings make 1 % noise) and not below 0.5 (more likely readings that lagged) |
| pre | grams the tray lost before the first layer beyond what the profile has there: the largest of its last 5 sound prints | from 2 such prints |

**What it changes.** A tray the printer measures is counted with the smallest of its reading G, G less dry, and W - (W - G) / rate (W the spool's full weight), never below 0; the running print's share is then taken off that. A filament judged on the tray needs pre more. The note says what was done and why: "C3 reads 105 g, counted as 85 g: it read 20 g when a spool ran dry in it on 19 Nov". Learning can only make a check stricter: it never counts a tray higher or a need lower, and an amount entered by hand is left alone. With nothing learned yet, or learning off, every check is as without it. It starts empty and needs prints the guard follows from start to finish. It was built from one measured incident and has not yet been confirmed over many prints; `learned` shows the evidence behind every number, so it can be judged.

`learned` also reports, never uses, how the reading fell per gram printed and per gram purged separately, once there are sound prints whose purge shares differ enough to tell. `learned --forget` and "Forget what was learned" in the icon's menu start again from nothing.

## Spools without a reading

A spool without an RFID tag, or on an external holder, reports no amount. Enter it on the card ("Amount in …"): weigh the spool and take off the empty spool. The guard then counts down, erring to the low side:

- A print is taken off in full when it starts to run, also when it is cancelled later.
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
python filament_guard.py learned      # what was learned from previous prints, per tray; --forget starts again
python filament_guard.py selftest     # can the guard read this Bambu Studio and reach the printers?
python filament_guard.py uninstall    # remove the shortcut and stop
```

`check` exits 0 (enough), 1 (tight, two spools needed, or amount unknown), 2 (not enough, or the material or colour is not loaded), 3 (could not be checked), so a script can gate on it. `--json` prints the full answer; `--popup` also shows the card.

**After a Bambu Studio update** run `selftest` (or Self-test in the icon's menu). When Bambu Studio writes a slice the guard cannot read, a card says so once for that version, and every such file is in the log.

## Limits

- A running print is counted only when it was sliced on this computer while the guard (1.3.0 or later) was watching, and its G-code adds up to Bambu Studio's totals (see "The running print"). Otherwise the card says it could not count it, and the amounts are what the spools hold now.
- A job is matched to its slice by project name and layer count, and by plate when the printer names the file it prints (`/data/Metadata/plate_2.gcode`, as the H2D does). A printer that does not (an A1 mini printing from its SD card reports nothing there) gets the newer of two plates of one project with the same number of layers. Re-slicing a plate replaces its slice: from then on, a print still running from the earlier slice is counted with the new slice's amounts, or not at all when the layer count changed.
- What the running print still needs from a tray that has run out (taken out, or left for the AMS to refill from another spool) is not taken off any other spool.
- Each plate is checked on its own. "Print all plates" needs the sum; check the exported file with `check`.
- Which plate "Send print job" is sending, and which tray you pick there, cannot be read: the card beside it is the worst of the project's sliced plates, against every matching spool. Bambu Studio's own warning there ("The filament in … may be insufficient for this print") covers the chosen tray.
- Two slots of one colour draw on the same spools and are judged on what they need together.
- With two printers of one model, the one selected in Bambu Studio is asked.
- Printers are the ones Bambu Studio holds a LAN access code for. A plate sliced for another printer is logged and shows no card.
- Which nozzle an AMS feeds is read as on the H2D (firmware 01.04.20.00). Where that cannot be read, a spool counts for either nozzle.
- How a job names an external spool in its tray mapping has not been seen on a print; a tray the guard cannot name makes the entered amounts out of date, never wrong.
- Learning learns nothing from a print not sliced on this computer, or followed only from its middle (the guard started during it). A spool moved to another tray starts again there: what is learned belongs to the tray, not the spool.
- Learning takes the readings the printer reports to follow the spool between the guard's looks, 3 seconds apart. Not yet seen on a print, and recorded so that the history will show them: how often a tray's reading changes during a print (a reading that lags shows as a low rate, and a rate below 0.5 is not used); what a tray looks like in the status when its spool runs out (counted as a run-dry only when the tray is reported without a spool; a tray still reported, reading 0 g, makes the print's record unsound instead, with the reading before it); whether the AMS, refilling from a second spool, changes the job's tray mapping (the record then says at which layer, and is not used); and whether the reading settles after the print ends (both the first reading at the end and the one a minute later are kept).
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

Log and state: `%LOCALAPPDATA%\BambuFilamentGuard\`, with the grams per layer of the last 30 slices in `profiles\` and what was learned in `learned.json`. The LAN access code is read from Bambu Studio's configuration each time and goes to the printer only.

## Licence

MIT: see `LICENSE`. The packaged zip also holds Python, Tcl/Tk, paho-mqtt, pystray and Pillow, each under its own licence. Those are in the zip: `LICENSE.txt` (Python), `tcl\tk8.6\license.terms` (Tcl/Tk) and the `dist-info` folders in `Lib\site-packages`.
