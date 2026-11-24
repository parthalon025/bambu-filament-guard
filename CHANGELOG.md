# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.5.0] - 2026-11-24

### Added

- A weigh-in more than 50 g from the printer's reading, either way, is not saved at the first Save. The window says both numbers and how far apart they are; Save again takes the weight as right. A slipped key or another spool on the scale was kept as what the tray reads at empty, and counted every tray of the printer lower.
- `learned --forget-weigh TRAY` takes back that tray's latest weigh-in and leaves the rest of what was learned (`--printer` where two printers have the tray).

### Changed

- The measured reserve is off unless you turn it on: "Measure the reserve from past prints (experimental)" in the icon's settings (`measured_reserve`). 1.4.0 had it on. It loosens a check on evidence that cannot see a change in what spools read at empty: in an independent review's simulation, once spools read 25 g more at empty, 5 of the 31 answers it had loosened to "enough" ran dry, where 1.3.0's rules had no failure. Nothing of the learning has yet followed a real print. Off, no answer is looser than the fixed reserve and 1.3.0's rules give: learning only makes a check stricter. Where the printer's past forecasts fell further short than the fixed reserve covers, the larger figure is the reserve, on or off, and the card says so: against 66,000 random checks no answer is looser than 1.4.0's either, with the setting as it comes or turned on. `docs/learning.md` has the cases and the numbers, under "What the measured reserve cannot see".
- The reserve's share of a spool's full weight (3 %) is taken from the largest spool the filament could be printed from, not from the fullest one. With a 250 g and a 1 kg spool of one filament loaded, that share is 30 g whichever is the fullest, so a check can be "tight" where it was "enough".
- No weigh-in is offered while a job is on the printer: the readings are moving then.
- The window for a spool's amount ("Amount in …", "Weigh C3…") takes what the scale shows and what the empty spool weighs, and takes one from the other: the filament shows as you type. It starts with the empty spool's weight entered last (`empty_spool_g`, 250 g at first); 0 there is the filament weighed alone, as before.

### Fixed

- With learning on, a check could answer "enough" where 1.3.0's rules said "tight", before any reserve was measured. Which spool is the fullest changes with what was learned of the trays: a 1 kg spool counted lower for another tray's run-dry left a 250 g spool the fullest, and the reserve fell from 30 g to 7.5 g. The reserve no longer depends on which spool is the fullest.
- One spool weighed near empty three times, or weighed and then run dry, counted as three readings at empty. The measured reserve could then be earned on how a scale repeats, with no second spool seen. A spool now counts once, by its largest reading at empty; no tray is counted higher for it.
- The card could say "the reserve is measured" when the row had used the fixed one (scored prints in one tray, run-dries only in another). With the measured reserve on, it now names the reserve its rows used and, for the fixed one, what the measured one still lacks, in a sentence that can be read. With it off the card says nothing of the reserve.
- A `learned.json` with one odd record (a key missing, None or text where a number belongs, a list where a record belongs) made every check of that printer end as "could not be checked", and `learned` crash on the same file. Such a record is now left out and the rest of the history used; the log says once what was left out. Whatever else goes wrong in what was learned, the check goes on as with learning off, and the card and the text output say that what was learned could not be used.
- A warning that offered a weigh-in could fail to show. The card noted the offer in the state file before it was put on the screen; when another program held that file, the note failed and the card stayed unseen. The card is now shown first, and a note that cannot be written is logged: the offer may then come again.
- A filament that fell short could show as one line, and not at all beside "Send print job". The full card opened by the plate's worst result alone: with "Warn when a colour is not loaded" unticked, a filament whose colour was not loaded hid the shortfall of the one after it, and a `popup_on` written by hand without `SHORT` (`["UNKNOWN"]`, or names in small letters) turned the "not enough" card off. Any one filament whose result opens the card now opens it; "not enough" and "not loaded" do whatever `popup_on` holds, which decides only for colour, two spools, tight and unknown. Its names are read in capitals or small letters, and the log says once when one of them is no result.

## [1.4.0] - 2026-11-20

### Added

- Learning scores itself. When a print it followed ends, the guard keeps what it had forecast the tray's reading would fall by, by what it knew before that print, and what the reading fell. From 9 scored prints, and readings at empty that repeat within a tray, the reserve is measured from those errors (a one-sided 90 % conformal bound, per gram of print plus 20 g, never below 5 g or 2 % of the need) instead of the fixed one, and can be smaller: a tray that proves accurate is no longer called "tight" on 3 % of its spool. A new setting, "Measure the reserve from past prints" (`measured_reserve`, on), turns it off. Until it is earned, or with it off, no answer is looser than 1.3.0's.
- "Weigh C3…" on the card, when weighing a spool the printer measures would settle the answer: at most once a week per tray and once a day in all. The spool is then counted with no more than it held; weighed near empty, it tells what the tray reads at empty.
- A tray whose learned forecasts do clearly worse than the slicer's grams (a sign test over its last 10) has them set aside; a change in a tray's forecast errors (Bayesian online change-point detection) starts what is learned of it again.
- `learned` shows the scores, the rate's 90 % interval, the evidence for what each tray reads at empty, which reserve is in use, any change noticed, and the rate by filament product.

### Changed

- The rate and the loss before the first layer are used from the first print, each tray's drawn toward the printer's own figure as far as its trays cannot be told apart, recent prints weighing more than old ones, and each print's ratio clipped first so that one odd print cannot set it. A rate above 1 now adds to the need.
- What a tray reads with its spool empty is an upper bound from run-dries, spools weighed near empty, and spools seen still feeding at a reading (Kaplan-Meier), in place of the largest of the last 3 run-dries and the rule that dropped a run-dry at once when a later spool fed lower.
- A filament's need takes the largest learned rate and loss of the trays it could be printed from.
- `learned.json` keeps 100 records per tray (the newest 20 with their readings layer by layer), 20 run-dries and 20 weigh-ins. A 1.3.0 file reads as it is; its records are not scored, and scoring starts with the next print.

## [1.3.0] - 2026-11-20

### Added

- Counts the print that is running. From each spool the guard first takes off what the print on the printer still needs from it, from the layer the printer is on to the end, so the answer is for the moment the checked print could start. What it needs beyond its tray comes off the other loaded spools of that filament, as AMS auto refill would; when none holds it, the card warns that the running print needs more than is loaded. In batch printing the next plate is sliced while one prints; until now it was checked against spools the running print was still emptying.
- Keeps, for each plate it sees sliced, the grams of each filament every layer takes, counted the way Bambu Studio counts its totals: the moves along X or Y that extrude, and the purge at each filament change (moves in `FLUSH` blocks, or `;VG1` comments in `VFLUSH` blocks), of which as much as the nozzle holds counts for the filament pushed out. Measured on Bambu Studio 02.09.01.70 slices for the A1, P1, X1, H2, P2S, A2L, X2D and H3C families: every filament within 0.005 g of Bambu Studio's total. A slice that does not add up gets no profile, and the log says by how much. The last 30 are kept in `%LOCALAPPDATA%\BambuFilamentGuard\profiles\`, the purge apart from the rest.
- The card, the text report and `--json` say whether the running print was counted, and a filament's note names the spool's reading and what the running print still needs of it.
- A line that says a check is under way ("Checking the spools on the Bambu Lab H2D…") as soon as one starts that you are waiting for: a new slice, or a check from the icon. The answer takes its place; a newest slice that cannot be read ends in a card that says so.
- Learns from previous prints, per printer and tray, how far that tray's reading can be trusted: what it read when a spool ran dry in it, how far its reading falls per gram the tray gives, and what it loses before the first layer. A check counts the tray with less, or the need with more, where that history shows it; it can only make a check stricter, and an amount entered by hand is left alone. A run-dry counts only for a print sliced here, from a tray the print was drawing on, reported empty twice, having read 50 g or less; a later print that shows the tray still feeding at a lower reading retires it. While learning is on (setting `learn`, on by default) the guard follows a printer it has checked, or that prints, over the same read-only link as for entered amounts. `learned` shows what was learned and on what evidence; `learned --forget` and the icon's "Forget what was learned" start again.
- A line when the guard starts, the first time Bambu Studio runs: "Bambu Filament Guard 1.3.0 is watching for sliced plates". After `Install.cmd` its own card says it instead.

### Changed

- A job is matched to its slice by plate as well, when the printer names the file it prints, so two plates of one project with the same number of layers are told apart. The amounts entered by hand are counted down by the same match.
- A new slice is checked before it is remembered, so a plate sliced again while it prints is checked against the slice that print was made from.

### Fixed

- A click on the icon while its check was running started another check, five in one second in one log; now it shows the "Checking…" line and starts nothing.
- A plate whose check failed ended that round of the watcher, and plates sliced with it went unchecked.
- The printer's job showed "None %" when the printer gave no percentage.
- An amount entered by hand while a print ran never had the rest of that print taken off it; now it has, at the guard's next look at the printer.
- The watcher and a command run beside it could undo each other's changes to the state file, or have it set aside as damaged, losing entered amounts. The state folder's files are now read and written by one at a time, across processes, and a write that cannot be made now is tried again rather than lost.
- At "Send print job", one sliced plate of the project that could not be read stopped the check of all of them; it is now left out, and logged.
- A one-line card that came while Bambu Studio was minimised counted its seconds from then, and could go before it was ever seen; it now counts from when it shows.

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

[Unreleased]: https://github.com/parthalon025/bambu-filament-guard/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/parthalon025/bambu-filament-guard/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/parthalon025/bambu-filament-guard/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/parthalon025/bambu-filament-guard/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/parthalon025/bambu-filament-guard/releases/tag/v1.2.0
