# Contributing

## Run from source

You need Windows and Python 3.13 or later.

```powershell
pip install -r requirements-dev.txt
python filament_guard.py selftest
```

`selftest` says whether the guard can read your Bambu Studio and reach your printers. If you already use an installed guard, do not run `watch`, `install` or `setup` from a checkout: they act on that install.

## Run the tests

From the repository root:

```powershell
python -m pytest                          # everything, as CI runs it
python -m pytest -m "not simulation"      # without the simulated printer: a second or two
python -m pytest tests/test_learning.py   # one area
```

They need no printer and no Bambu Studio. The full run is on every push and pull request, on Windows.

Tests marked `windows` drive the card, the icon or the state file's named mutex, which call the Windows API. On another system they are skipped, with the reason, and the rest run.

| File | Tests |
|---|---|
| `tests/test_gcode.py` | reading a sliced plate: the header, and the grams each layer takes with the purge; where slices are looked for; `check` on a file: its plates, what it cannot read, its exit codes |
| `tests/test_assessment.py` | one filament against the spools that could print it: enough, tight, two spools, colour, nozzle |
| `tests/test_running_print.py` | what the running print still needs, matched to its slice, taken off its spools |
| `tests/test_entered_amounts.py` | amounts entered by hand, counted down, and the link that follows a printer |
| `tests/test_learning.py` | learning from previous prints: the record, the model, what it changes, weigh-ins |
| `tests/test_stats.py` | the estimators learning uses, against known values |
| `tests/test_simulation.py` | the simulated printer behind the tables in [docs/learning.md](docs/learning.md) (marked `simulation`); `python tests/test_simulation.py` prints those tables, in some minutes |
| `tests/test_guard.py` | the watcher, the card and the commands, as the running guard drives them |
| `tests/test_state.py` | settings and the state file, with two processes writing it at once |
| `tests/helpers.py`, `tests/conftest.py` | what several files share: G-code, trays and printer statuses, and the `state` fixture |

A new test goes in the file of what it tests. A helper used by one file stays in that file.

## Code layout

| File | Holds |
|---|---|
| `filament_guard.py` | G-code (totals and grams per layer), printer status, the assessment, the watcher, the commands |
| `guard_ui.py` | the card and the icon |
| `guard_ledger.py` | the prints that draw on the spools: entered amounts counted down, what the running print still needs; the link that follows a printer |
| `guard_learn.py` | learning from previous prints, per tray: the record, the model and its scores, what they change, weigh-ins, `learned` |
| `guard_stats.py` | the estimators learning uses: shrinkage, the conformal bound, Kaplan-Meier, change points, Fieller's interval; plain arithmetic |
| `build.py` | makes the packaged folder and its zip |
| `tests/` | the tests: see [Run the tests](#run-the-tests) |
| `pytest.ini` | where the tests are, and their markers |
| `docs/` | the documentation beyond the README, and its pictures |

## Conventions

- Every claim the README and the pages in `docs/` make about what the guard does is true of the code. If a change makes a claim false, change them in the same pull request.
- A comment states a constraint and why it holds. It does not say what the next line does.
- A fix comes with a test, and the test's name states the behaviour it checks.
- No new dependency without a reason, given in the pull request. The packaged zip carries every dependency, so each one adds to it.
- Settings go in `DEFAULTS` at the top of `filament_guard.py`, with their meaning beside them.

## Bug reports

A useful report holds:

- the guard's version (`VERSION` in `filament_guard.py`, or the number in the zip's name)
- the Bambu Studio version
- the printer model and its firmware version
- the output of `python filament_guard.py selftest`
- the lines of `%LOCALAPPDATA%\BambuFilamentGuard\guard.log` around the event

**Remove printer serial numbers and IP addresses** from everything you paste, and never post a LAN access code. The log and the output of `selftest` and `trays` can hold serials and IP addresses.

Reports from printers other than the H2D are especially welcome. The H2D is the one the guard was built on, and how other models report their spools and nozzles has not been seen.
