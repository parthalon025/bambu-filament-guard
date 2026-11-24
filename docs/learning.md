# Learning from previous prints

[← README](../README.md) · [Using the guard](using.md) · [The running print](running-print.md) · **Learning** · [Spools without a reading](manual-amounts.md) · [Limits](limits.md)

The AMS's reading is an estimate, and not equally good in every tray. On 2026-11-19 tray C3 read 105 g and ran dry after 84-85 g of a print: it read about 20 g when it was empty. In the same print tray B4 fell 49 g for a need of 49.1 g: that tray tracks well. A fixed reserve treats both alike. So the guard learns, per printer and tray, how far that tray's reading can be trusted.

## What is recorded

While learning is on, the guard follows a printer it has checked in the last 30 minutes, or that is printing, over the same read-only link it uses for entered amounts: it asks once, at each connection, for the full status, then reads the status reports every 3 seconds, and sends nothing else. For each job matched to a slice with a per-layer profile, it records per tray: the spool (its RFID tag when it has one), the reading before the first layer, at the start of each layer (at most 60 per print, thinned evenly), and a minute after the print finished; and how many grams the profile has that tray give over the same span, printed and purged apart. A record is used only when it is sound: the same spool throughout, a reading that never rose, no gap of ten minutes or more in following, the tray never empty, and the print seen to finish. A run-dry is a tray the print is drawing on at that point (by its profile) that is reported without a spool in two looks in a row, having read 50 g or less: its last reading is recorded, with the printer's error and HMS codes at that moment, raw, to judge it by later. A fuller spool, or one the print was not drawing on then, was taken out: that is logged and teaches nothing. A print not sliced on this computer records no run-dry, as what it draws on and when cannot be told.

With each sound whole print followed from before its first layer it also keeps a score: what the guard, as it stood before that print, forecast the tray's reading would fall by, and what it fell. With every print, whatever became of it, it keeps the lowest reading each tray was seen still feeding at.

## The model

In the reading's own grams, so that every part of it can be seen: a print of N sliced grams makes the tray's reading fall by about rho x N, plus pre before the first layer; the spool is empty when the reading reaches a. A print fits when the reading is at least rho x N + pre + a + a margin.

## What is learned

Per tray, over its prints since any change was noticed in it (see [Checked against itself](#checked-against-itself)), each print weighted 0.95 for every print since, so that one 14 prints old counts half:

| Number | What it is | Used from |
|---|---|---|
| rho | how far the reading fell for each sliced gram: each print's ratio, clipped to 0.9..1.15 (to the printer's own 5 % and 95 % quantiles once it has 20), the tray's own drawn toward the printer's as far as the trays cannot be told apart from each other (shrinkage), and the printer's drawn toward 1 as if by 50 g of prints that fell true | the first sound whole print on the printer; a tray with none takes the printer's |
| pre | the grams lost before the first layer beyond what the profile has there, drawn toward the printer's the same way | the first print followed from before its first layer |
| a | what the tray reads with its spool empty, from run-dries and spools weighed near empty (seen exactly) and from spools seen still feeding at a reading (their a lies below it), by Kaplan-Meier: the largest seen exactly while there are 10 or fewer, and with more the reading that a tenth of them reach or pass (an estimate of their 90th percentile, not a bound). Once readings at empty of different spools repeat within a tray, also the 90 % bound for one more spool from their spread, where it is the larger. A spool counts once, by its largest reading at empty, however often it was weighed. A tray with fewer than 3 of its own takes the printer's, but never less than its own largest | the first run-dry or weigh-in near empty |
| margin | how far the guard's own forecasts fell short of what the reading fell, per gram of print plus 20 g: the one-sided 90 % conformal bound of the scored prints, the printer's, or the tray's own once it has 9 (the larger of the two) | 9 scored prints on the printer, and only with [the measured reserve](#the-measured-reserve) turned on |

## What it changes

A tray the printer measures is counted with its reading less a, and with no more than W - (W - G) / rho while rho is below 0.99 (W the spool's full weight, G its reading: a gauge that counts too little reads more than the spool holds), never below 0 and never above its reading. A filament needs max(1, rho) x N + pre, with the largest rho and pre of the trays it could be printed from, never less than the slicer's N. The note says what was done and why: "C3 reads 105 g, counted as 85 g: it read 20 g when a spool ran dry in it on 19 Nov". An amount entered by hand is left alone. With nothing learned yet, or learning off, every check is as without it.

## The reserve

The reserve is the fixed one (see [What the results mean](using.md#the-reserve)), and each tray is counted with the stricter of what is described above and of 1.3.0's rules: the largest reading of its last 3 run-dries (less any that a later print showed the tray still feeding below), its rate over its last 10 prints when that is between 0.90 and 0.99, and the largest loss before the first layer of its last 5. So no answer is looser than 1.3.0's rules and the fixed reserve give: learning only makes a check stricter.

### The measured reserve

This is experimental, and off unless you turn it on: "Measure the reserve from past prints (experimental)" in the icon's settings (`measured_reserve`). 1.4.0 had it on.

With it on, the reserve stays the fixed one until the printer has 9 scored prints and readings at empty of different spools that repeat within a tray (3 spools in one tray, or 2 in each of two: their spread from spool to spool is then known), and the tray judged has one of its own. Then it is the margin x (N + 20 g), never below 5 g or 2 % of the need, and the tray is counted by the model alone, without 1.3.0's rules. It can be smaller than the fixed one: a tray that proves accurate is no longer called "tight" on 3 % of its spool. The card then names the reserve its rows used: the measured one and what it rests on, or the fixed one and what the measured one still lacks. With the setting off the card has no line about the reserve.

### What the measured reserve cannot see

It is off by default because it can say "enough" for a print that then runs dry, in two cases that this version does not solve. An independent review of 1.4.0 found both, by running the guard's own recorder and check against a simulated printer of the reviewer's making. It counted the answers the measured reserve loosened: "enough" where 1.3.0's rules said tight or worse.

- **Spools begin to read more at empty.** A new batch or product whose spools hold less than their tag says, or a change in the AMS's firmware, moves what a tray reads with its spool empty. The guard looks for a change in a tray in its forecast errors, and this one never shows there: the reading still falls as far per gram as before. What a tray reads at empty is learned only from run-dries and weigh-ins near empty, and old ones count as much as new ones. In the review's runs (12 seeds of 300 prints, the reading at empty going from about 0 g to 25 g at print 150), 5 of the 31 answers loosened in the 60 prints after the change ran dry (16 %); before it, none of 66. After a change of 15 g it was 3 of 43 (7 %), and after one of 40 g (5 seeds) 3 of 9. 1.3.0's rules had no failure in the runs with 15 g and 25 g. A tray that has seen 19 run-dries near 0 g counts 9 g for an empty spool after one run-dry at 25 g, 13 g after two and 24 g after three, and each of those run-dries is a print that ran out.
- **One tray unlike the others.** How far spools differ at empty is measured over all trays of the printer together. With three trays whose spools agree to 2 g and a fourth whose spools are anything from 0 to 50 g short of their tag (mixed products), the spread comes mostly from the three, and is too small for the fourth. In the review's runs (8 seeds of 300 prints), 2 of the 18 answers loosened on that tray ran dry (11 %).

With the setting off neither can loosen an answer: the reserve is the fixed one, and 1.3.0's rules stand.

## Checked against itself

A tray whose learned forecasts were further from what fell than the plain sliced grams, in more of its last 10 scored prints than chance allows (a one-sided sign test, p < 0.05), has its rho and pre set aside until that is no longer so (p of 0.5 or more). A change in a tray's forecast errors (Bayesian online change-point detection, Adams and MacKay 2007: more likely than not to have begun 2 to 5 prints ago, with one odd print in twenty allowed for) ends what was learned of the tray before it, as when an AMS is serviced: the tray goes back to the printer's figures and builds again. `learned` names the print it began with.

## Weigh-ins

When weighing a spool the printer measures would settle a check (short of enough as counted, enough if its reading were true), the card offers "Weigh C3…": what the scale shows and what the empty spool weighs, as for [a spool without a reading](manual-amounts.md). It asks at most once a week per tray and once a day in all, and never when "One line when there is enough" is off. From then on that spool is counted with no more than it held, until another spool is in the tray; a spool weighed at a quarter of its full weight or less is also a reading at empty for the tray. One spool gives one such reading: weighed near empty twice, or weighed and then run dry, it counts once, by the largest. That shows how a scale and a gauge repeat, not how spools differ, and it is the difference between spools that the measured reserve rests on. A tray is still counted no higher than with every weigh-in and run-dry taken for itself, as 1.4.0 took them. Weighed fuller, it says what that spool holds, not what the tray reads when a spool runs out: a gauge that counts a little short goes on adding to that over the rest of the spool.

## On a simulated printer

A simulation checks this arithmetic against gauges invented for it, not against a real printer: four trays whose spools hold their tag's weight less an offset, with a gauge that falls a set amount per gram fed, prints of 5 to 250 g or printing a spool down, 5 seeds of 300 prints each, through the guard's own recorder and check. "Enough, then ran dry" is the share of "enough" answers after which the print would have run out; "fitted, but warned" the share of prints that would have fitted that were called tight or not enough.

| Scenario | Enough, then ran dry: 1.3.0 | 1.4.0 | Fitted, but warned: 1.3.0 | 1.4.0 | 1.4.0, measured reserve in use | Measured reserve from print |
|---|---|---|---|---|---|---|
| a true gauge | 0 of 1720 | 0 of 1776 | 10.1 % | 7.2 % | 6.0 % | 104 |
| reads 20 g at empty | 0 of 1620 | 0 of 1647 | 8.8 % | 7.3 % | 7.4 % | 49 |
| counts 0.97 g per gram fed | 0 of 1612 | 0 of 1673 | 9.0 % | 5.5 % | 5.5 % | 45 |
| spools that differ (8 g) and prints that vary (3 %) | 0 of 1718 | 0 of 1734 | 9.9 % | 9.0 % | 8.8 % | 104 |
| the gauge changes halfway (to 0.95) | 0 of 1681 | 0 of 1698 | 9.0 % | 8.1 % | 6.8 % | 104 |
| one false run-dry | 0 of 1715 | 0 of 1733 | 11.5 % | 10.5 % | 9.9 % | 84 |
| four trays: true, 20 g at empty, 0.97, spools that differ | 0 of 1690 | 0 of 1727 | 9.6 % | 7.6 % | 7.6 % | 56 |

rho and pre are in use from the second print, the margin from the eighth (9 scored prints), what a tray reads at empty from the 16th to the 41st (median over the 5 seeds). Two things the simulation found are fixed in what is described above: with only the largest of a few readings at empty, spools that differ ran dry after "enough" (3 of 755 answers with the measured reserve in use, where 1.3.0 had none); and once a reading at empty was known, a gauge that changed to count short was no longer counted lower for it (3 of 597).

## What `learned` shows

None of it has yet been confirmed on many real prints. `learned` shows, per printer and tray, the evidence behind every number: prints scored, how far the learned and the plain forecasts were off, rho with its 90 % interval (Fieller's, for a ratio of two sums), pre, what the tray reads at empty and on what, the margin and which reserve is in use, any change noticed, and what each number still needs. It also reports, never uses, rho by filament product across trays (a gauge that is off, against a filament whose density is not what the slicer assumes), and how the reading fell per gram printed and per gram purged, once prints differ enough to tell. `learned --forget` and "Forget what was learned" in the icon's menu start again from nothing; `learned --forget C3` forgets one tray (with `--printer` where two printers have a C3).
