# Spools without a reading

[← README](../README.md) · [Using the guard](using.md) · [The running print](running-print.md) · [Learning](learning.md) · **Spools without a reading** · [Limits](limits.md)

A spool without an RFID tag, or on an external holder, reports no amount. Enter it on the card ("Amount in …"): put the spool on a scale and type what the scale shows and what the empty spool weighs. The filament is the difference, shown as you type. The window starts with the empty spool's weight you entered last (`empty_spool_g`; 250 g at first, about a Bambu spool); enter 0 there when you weighed the filament alone or already took the spool off. The result then says that nothing is taken off for the spool, and 0 is not kept: the next window starts with the last weight above 0. Save stores the amount; if it cannot be stored just then, the window stays open and says so. The guard then counts down, erring to the low side:

- A print is taken off in full when it starts to run, also when it is cancelled later.
- An amount entered while a print runs is what the spool held then: what that print still needs of it comes off at the guard's next look at the printer. When that is not known (the print was not sliced on this computer), the card says it could not count the running print.
- A print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the amount "out of date": unknown again, until it is entered again. So does a print of a filament that Bambu Studio gives no weight (a profile with density 0), for the spool that filament prints from.
- When the printer was not followed for ten minutes or more (the computer asleep) and has printed something else since, every amount entered for it is out of date.
- The entry ends when the printer reports another material or colour in that tray.

The count-down is the running guard's: an amount entered on the card of `check --popup` while no guard runs is kept, and the window says that nothing counts it down until one runs. To count, the guard keeps a standing read-only connection to a printer for as long as it has an entered amount (and while learning has a reason to: see [Learning](learning.md#what-is-recorded)). Not seen: a print started and finished while the printer was not followed, when the printer names it exactly like the one before (LAN-only mode).
