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
python -m pytest test_filament_guard.py
```

They need no printer and no Bambu Studio. The same command runs on every push and pull request.

## Code layout

| File | Holds |
|---|---|
| `filament_guard.py` | G-code (totals and grams per layer), printer status, the assessment, the watcher, the commands |
| `guard_ui.py` | the card and the icon |
| `guard_ledger.py` | the prints that draw on the spools: entered amounts counted down, what the running print still needs; the link that follows a printer |
| `guard_learn.py` | learning from previous prints, per tray: the record, the model and its scores, what they change, weigh-ins, `learned` |
| `guard_stats.py` | the estimators learning uses: shrinkage, the conformal bound, Kaplan-Meier, change points, Fieller's interval; plain arithmetic |
| `build.py` | makes the packaged folder and its zip |
| `test_filament_guard.py` | `python -m pytest test_filament_guard.py` (needs no printer and no Bambu Studio) |
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
