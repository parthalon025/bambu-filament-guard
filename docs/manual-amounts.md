# Spools without a reading

[← README](../README.md) · [Using the guard](using.md) · [The running print](running-print.md) · [Learning](learning.md) · **Spools without a reading** · [Limits](limits.md)

A spool without an RFID tag, or on an external holder, reports no amount. Enter it on the card ("Amount in …"): weigh the spool and take off the empty spool. The guard then counts down, erring to the low side:

- A print is taken off in full when it starts to run, also when it is cancelled later.
- An amount entered while a print runs is what the spool held then: what that print still needs of it comes off at the guard's next look at the printer. When that is not known (the print was not sliced on this computer), the card says it could not count the running print.
- A print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the amount "out of date": unknown again, until it is entered again.
- When the printer was not followed for ten minutes or more (the computer asleep) and has printed something else since, every amount entered for it is out of date.
- The entry ends when the printer reports another material or colour in that tray.

To count, the guard keeps a standing read-only connection to a printer for as long as it has an entered amount (and while learning has a reason to: see [Learning](learning.md#what-is-recorded)). Not seen: a print started and finished while the printer was not followed, when the printer names it exactly like the one before (LAN-only mode).
