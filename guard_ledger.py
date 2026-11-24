"""Amounts entered by hand, for spools the printer cannot measure (no RFID tag, an external holder), and the prints
that draw on them.

An entry belongs to one tray of one printer and to the material and colour that tray held when it was entered. It
ends (ended) when the printer reports another filament there, starts to measure the tray itself, or reports the tray
empty after it was seen holding that filament: at a check (apply), and at the follower's look once two in a row agree.

Counting down errs to the low side, because a figure that is too low warns early and one that is too high loses a
print:
  - a print is counted in full when it starts to run, also when it is later cancelled;
  - what a print needs beyond the amount of its tray comes off the other loaded spools of the same filament, the
    fullest first (take_off): with AMS auto refill the print goes on from one of them. An entered spool's part comes
    off its entry. When the tray's own amount is not known, how much that is cannot be told, and the entries of that
    filament are out of date;
  - an amount entered while a print runs (or after it was counted) has what that print still needs of it taken off at
    the next look at the printer (catch_up): the amount weighed is what the spool held then, and the print goes on.
    Of a print whose slice has no per-layer profile all it needs comes off; a print the guard cannot count at all makes
    such an entry out of date, like any other entry of the trays it may draw on;
  - a print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the entries of
    the trays it used "out of date": unknown again, until the amount is entered again.
    So does a filament of a print that its slice has no weight for (fg.parse_plate), for the entry of its own tray.
  - when the printer was not followed for ten minutes or more (this computer asleep, the link down) and shows
    another job than the last one counted, every entry of that printer is out of date: more than one print may
    have come and gone.
  - an entry made ten minutes or more before a follower's first look at the printer, with nothing following the
    printer in between (no guard was running), is out of date at that look: it takes what has run for the baseline.
A printer in LAN-only mode gives every job the id 0, so a job sent again has the key of the run before it. Kept with
the key of the job last counted is therefore whether that job was last seen over ('ended', in the state file): found
on the printer again after that, it is another run, counted like a new job, or after ten minutes unfollowed making the
entries out of date. And a key whose id is 0, or that has none, does not tell one run from the next at all: after ten
minutes or more unfollowed, every entry is out of date whatever such a printer shows (the same job, finished, may be
a second run of it). Not seen is what is left of that: a print that starts and ends, or takes the place of one last
seen running, within less than ten minutes unfollowed, when the printer names it exactly like the print before it.

For the check of the next print, owed() tells what the job the printer runs still needs from each tray: the per-layer
profile of the slice it was made from, from the printer's layer to the end. filament_guard takes that off the trays the
printer measures, and not off an entered amount, which had the whole job taken off when the job started.

JobWatch, the standing read-only link to a printer, also serves learning (guard_learn.py): with the setting "learn" on,
a printer is followed while a check has looked at it within LOOKED, or while it prints, and each look goes to the
guard_learn.Recorder too. Entered amounts are counted exactly as without it."""
import re, threading, time
import filament_guard as fg

STARTED = ('RUNNING', 'PAUSE', 'FINISH', 'FAILED')        # states in which a job has drawn filament and its tray mapping is its own
RUN = ('RUNNING', 'PAUSE')                                 # states in which a job runs
NO_JOB = ['-', '-']
GAP = 600                                                  # seconds without a report from the printer after which jobs may have been missed
SILENT = 60                                                # seconds without a report after which the status held is no longer the printer's:
                                                           # the link is ended and made anew (up to 1.4.0 a link gone silent was read on as
                                                           # if live, and the prints of those hours were never taken off)
AWAY = 1800                                                # seconds an entry made with its tray empty waits for its spool: the spool on the
                                                           # scale is put back at once; one first seen later may be another of its kind
LOOKED = 1800                                              # seconds a printer a check looked at is followed for learning: a print sent
                                                           # after the check is seen from its start
PRINTING = 2 * 3600                                        # seconds a printer last seen printing is still followed for learning: while it
                                                           # answers, every look renews it; past this, a printer gone offline mid-print
                                                           # (or whose access code changed) is no longer tried every minute for ever


def identity(tray):
    return [tray['type'].upper(), tray['colour']]

def serials(state=None):
    """The printers that have an entered amount."""
    return [s for s, book in (fg.load_state() if state is None else state).get('spools', {}).items() if book]

def followed(cfg, state=None):
    """The printers to follow: those with an entered amount; with learning on, also one a check looked at within LOOKED
    (in this process or another: it goes through the state file), or that was printing at its last look (a guard
    started during a print follows it on)."""
    state = fg.load_state() if state is None else state
    out = {s for s, book in state.get('spools', {}).items() if book}
    if cfg.get('learn'):
        out |= {s for s, at in state.get('looked', {}).items() if time.time() - at < LOOKED}
        out |= {s for s, at in state.get('printing', {}).items() if time.time() - (at or 0) < PRINTING}
    return sorted(out)

def lapse(e, why):
    """Makes an entry out of date and keeps why, for the note of the filament that rests on it (fg.assess): up to 1.5.0
    every such note blamed "a print the guard could not count". The first reason stands. Returns True when it made
    the entry out of date."""
    if e.get('stale'): return False
    e['stale'], e['why'] = True, why; return True

def ended(e, t, empty):
    """Why an entry has ended, or '' while it stands. t: its tray among the loaded ones, else None; empty: the status
    says that tray is without a spool. An empty tray ends an entry only once the tray was seen holding the entry's
    filament since the entry was made ('loaded'): the weight is typed while the spool is still on the scale, and up to
    1.4.0 the check that follows took the entry away again."""
    if t is None: return 'the tray is empty' if empty and e.get('loaded') else ''
    if t['grams'] is not None: return 'the printer measures the tray now'
    return '' if identity(t) == [e['type'], e['colour']] else f"the tray holds {t['type'].upper()} {t['colour']} now"

def settle(book, trays, empty, only=None, since=None):
    """One status against a printer's entries (book): an entry whose tray holds its filament is marked 'loaded', and one
    that has ended (ended) is dropped, with a line in the log. An entry whose tray is first seen holding its filament
    more than AWAY after it was made is out of date: typed with the spool on the scale, it would else wait without limit
    for any spool of its material and colour. empty: the trays without a spool; only: {label: why}, the endings that
    may be carried out (all, when not given); since: when the status was reported (now, when not given): an entry made
    after that is left as it is."""
    by, now = {t['label']: t for t in trays}, time.time() if since is None else since
    for label, e in list(book.items()):
        if e['at'] > now: continue
        why = ended(e, by.get(label), label in empty)
        if why and (only is None or only.get(label) == why): del book[label]; fg.log(f'{label}: amount ended: {why}')
        elif not why and label in by and not e.get('loaded'):
            e['loaded'] = True
            if now - e['at'] > AWAY and lapse(e, f'its spool was not seen back in its tray within {AWAY // 60} minutes'):
                fg.log(f'{label}: amount out of date: its spool was first seen in the tray more than {AWAY // 60} minutes after it was entered')

def apply(trays, serial, complete=True):
    """Gives the trays the printer cannot measure the amount entered for them (grams, and 'entered': when), or marks
    them 'stale' when that amount is out of date. Drops the entries that have ended (ended): another filament in the
    tray, a tray the printer now measures, or a tray that is empty. Empty is a tray that is not among the loaded ones
    when the status is complete, i.e. has its spools, and shows a spool in an AMS: a report still in pieces, with an
    "ams" part and no tray in it yet, would end every entry."""
    def change(state):
        book = state.get('spools', {}).get(serial, {})
        settle(book, trays, set(book) if complete and any(not t['label'].startswith('Ext') for t in trays) else ())
        for t in trays:
            e = book.get(t['label'])
            if e and e.get('stale'): t['stale'], t['why'] = e['at'], e.get('why')
            elif e:
                t['grams'], t['weight'], t['entered'], t['accounted'], t['missed'] = e['grams'], e['first'], e['at'], e.get('job'), e.get('missed')
                t['over'], t['gave'] = e.get('over'), e.get('gave')
    fg.update_state(change)
    return trays

def enter(serial, tray, grams):
    """Records grams for tray ({'label', 'type', 'colour'}); None removes the entry. The amount is what is on the spool
    now. The job the printer shows then has not been taken off it ('job' is not set): if that job has started, what it
    still needs of the tray comes off at the next look (catch_up); if not, the whole job comes off when it starts."""
    def change(state):
        book = state.setdefault('spools', {}).setdefault(serial, {})
        if not book: state.setdefault('jobs', {}).pop(serial, None); state.get('ended', {}).pop(serial, None)   # the first entry: the next sighting of the printer is the baseline
        if grams is None: book.pop(tray['label'], None)
        else: book[tray['label']] = {'type': tray['type'].upper(), 'colour': tray['colour'], 'grams': float(grams), 'first': float(grams), 'at': time.time()}
    fg.update_state(change)
    fg.log(f"{tray['label']}: amount " + ('removed' if grams is None else f'entered, {float(grams):.0f} g'))

def job_key(status):
    return [str(status.get('task_id') or status.get('subtask_id') or status.get('job_id') or ''), status.get('subtask_name') or '']

def taken_off(status, state, serial):
    """Is the job the printer shows the run that the entries carrying its key have had taken off them? Not while the
    printer prepares it: a job is counted when it runs. Nor when the job counted was last seen over: the one on the
    printer is then another run under the same key, which the follower has yet to count."""
    return status.get('gcode_state') in RUN and not state.get('ended', {}).get(serial)

def mapped_labels(status):
    """The tray of each filament of the job the printer shows: its label, '' for a filament that is not used, None for
    a tray that cannot be named. None in all when the printer reports no mapping. A tray is numbered AMS id * 256 +
    tray: 514 = AMS C, tray 3 = "C3" (read on the H2D, firmware 01.04). External spools: 254 and 255, alone or as the
    AMS id; that numbering has not been seen on a print."""
    mapping = status.get('mapping')
    if not isinstance(mapping, list) or not mapping: return None
    external = status.get('vir_slot') or ([status['vt_tray']] if isinstance(status.get('vt_tray'), dict) else [])
    ext = {str(t.get('id')): ('Ext' if i == 0 else f'Ext{i + 1}') for i, t in enumerate(external)}
    out = []
    for code in mapping:
        if not isinstance(code, int): out.append(None); continue
        ams, tray = code >> 8, code & 0xFF
        if code in (65535, -1): out.append('')
        elif code in (254, 255): out.append(ext.get(str(code)))
        elif ams in (254, 255): out.append(ext.get(str(ams)))
        elif ams >= 128: out.append(f'HT-{chr(65 + ams - 128)}')
        elif ams < 26 and tray < 4: out.append(f'{chr(65 + ams)}{tray + 1}')
        else: out.append(None)
    return out

def drawn_on(status):
    """The trays the job the printer shows may draw on: the ones its tray mapping names; every loaded tray when the
    mapping cannot be read, or names a tray that is not loaded: the print has then gone on from another spool, and
    which one cannot be read."""
    labels, loaded = mapped_labels(status), {t['label'] for t in fg.trays_of(status)[0]}
    return loaded if labels is None or None in labels or not {l for l in labels if l} <= loaded else {l for l in labels if l}

def job_plate(status):
    """The plate number of the job the printer shows, from the file it prints ("/data/Metadata/plate_2.gcode" on the
    H2D), or None when the printer does not say (an A1 mini printing from its SD card reports '')."""
    m = re.search(r'plate_(\d+)\.gcode$', str(status.get('gcode_file') or ''))
    return int(m.group(1)) if m else None

def fits(s, name, layers, plate=None):
    """Could a job of this name, layer count and plate (None: the printer does not say) have been made from slice s?
    Bambu Studio names a job "<project>" or "<project>_<plate name>"."""
    if not (name == s['title'] or name.startswith(s['title'] + '_')): return False
    if layers and s.get('layers') and int(layers) != int(s['layers']): return False
    return plate is None or s.get('plate') == plate

def match_slice(name, layers, slices, plate=None):
    """The slice a job was made from: the newest that fits it (fits: the job's layer count; its plate, when that is
    known) among the slices of the project it is named after. That project is the one whose title is the job's name;
    failing that, the one with the longest title that, followed by "_", starts it: by the newest title that starts
    it, a job "Bracket_v2" would be counted by a newer slice of the project "Bracket". When no slice of that project
    fits, the job is not matched to another project's: None, and it is not counted. Two plates of one project with the same layer count are
    told apart only by the plate."""
    titles = {s['title'] for s in slices if name == s['title'] or name.startswith(s['title'] + '_')}
    title = name if name in titles else max(titles, key=len, default=None)
    return next((s for s in slices if s['title'] == title and fits(s, name, layers, plate)), None)

def versions(status, slices):
    """The slices the job the printer shows may have been made from: the newest that fits it (match_slice) and the
    earlier slices of the same plate that fit it too, which fg.remember_slice keeps: with the newest alone, a plate
    sliced again while it prints (fewer copies for the next run) has the running job counted by the new slice, 91 g
    where it needs 205 g. Which of them the printer runs cannot be read, so owed() and draws() take the largest need
    among them."""
    name, layers, plate = status.get('subtask_name') or '', status.get('total_layer_num'), job_plate(status)
    s = match_slice(name, layers, slices, plate)
    return [x for x in slices if (x['title'], x.get('plate')) == (s['title'], s.get('plate')) and fits(x, name, layers, plate)] if s else []

def note_job(serial, status, state=None):
    """Keeps in the state file the job the printer has on it (name, layers, plate), or that it has none:
    fg.remember_slice does not let go of a slice such a job may be printed from. A state file that cannot be written
    now leaves the note as it was."""
    job = {'name': status.get('subtask_name') or '', 'layers': status.get('total_layer_num'), 'plate': job_plate(status)} if status.get('gcode_state') in fg.BUSY else None
    try:
        if (fg.load_state() if state is None else state).get('on', {}).get(serial) != job:
            fg.update_state(lambda st: st.setdefault('on', {}).__setitem__(serial, job) if job else st.get('on', {}).pop(serial, None))
    except OSError: pass

def paired(status, slices, all_labels):
    """The job the printer shows, filament by filament: (the slice it was made from, [(tray label, filament of that
    slice)], the trays whose draw cannot be told). The pairs are [] when they cannot be told, and the trays in doubt are
    then every loaded tray (the mapping cannot be read) or the mapped ones (the slice or a filament's tray is unknown)."""
    labels = mapped_labels(status)
    if labels is None and len(all_labels) != 1: return None, [], set(all_labels)   # no mapping and several trays: any of them may be in use
    if labels is not None and None in labels: return None, [], set(all_labels)     # a tray that cannot be named: none is certain
    used = set(all_labels) if labels is None else {l for l in labels if l}
    s = match_slice(status.get('subtask_name') or '', status.get('total_layer_num'), slices, job_plate(status))
    if not s: return None, [], used                                                # not sliced on this computer: the trays are known, the grams are not
    fils = s['filaments']
    if labels is None: pairs = [(all_labels[0], f) for f in fils]
    elif all(labels) and len(labels) == len(fils): pairs = list(zip(labels, fils))                     # one entry per filament in use
    else: pairs = [(labels[f['slot'] - 1] if 0 < f['slot'] <= len(labels) else '', f) for f in fils]  # one entry per filament of the project
    if not all(label for label, _ in pairs): return s, [], used                     # a filament without a tray: the mapping is not read right
    return s, pairs, set()

def filaments_of(status, slices):
    """{tray label: what the slice says of the filament the job the printer shows draws from that tray}, in a tray's
    shape for fg.same_filament: for a tray that is no longer loaded, it tells which spools the print may have gone on
    from. A slice kept by a guard before 1.5.0 does not say, and its trays are not in it."""
    _, pairs, _ = paired(status, slices, [t['label'] for t in fg.trays_of(status)[0]])
    return {label: {'label': label, 'type': f['type'], 'colour': f['colour'], 'fid': f.get('fid') or '', 'nozzle': f.get('nozzle')} for label, f in pairs if f.get('type') and f.get('colour')}

def draws(status, slices, all_labels):
    """({tray label: grams} the job the printer shows draws, the trays whose draw cannot be told). Of a plate kept in
    several slices that fit the job (versions), per tray the largest; and when one of them leaves a tray in doubt, no
    draw is told. A filament the slice has no weight for (fg.parse_plate) puts its own tray among those in doubt, and
    leaves the other trays their draws: dropped with it, an entered spool the job also draws on was neither counted
    down nor made out of date."""
    drawn, doubt, told = {}, set(), True
    for v in versions(status, slices) or [None]:
        _, pairs, unknown = paired(status, [v] if v else [], all_labels)
        if not pairs: told = False                                           # this slice pairs no tray with a filament: every tray it names is in doubt
        one = {}
        for label, f in pairs:
            if f['grams'] is None: unknown = unknown | {label}
            else: one[label] = one.get(label, 0.0) + f['grams']
        doubt |= unknown; drawn.update({label: max(g, drawn.get(label, 0.0)) for label, g in one.items()})
    return ({label: g for label, g in drawn.items() if label not in doubt} if told else {}), doubt

def owed(status, slices):
    """({tray label: grams the job the printer shows still needs from it}, counted). counted: None when no job is on
    the printer; False when one is and what it still needs cannot be told (not sliced on this computer while the guard
    watched, no profile, a tray that cannot be named), and then nothing is guessed; True with the grams. Still needed is
    the slice's per-layer profile from the printer's layer to the end, that layer counted whole: a figure too low loses
    a print, one too high only warns early. In PREPARE, or without a layer, all of it: a printer that prepares a job may
    still show the last layer of the job before. Of a plate kept in several slices that fit the job (versions), per tray
    the largest: never less than the job needs; and when one of them cannot be told, none is."""
    if status.get('gcode_state') not in fg.BUSY: return {}, None
    trays, _ = fg.trays_of(status)
    try: layer = int(status.get('layer_num'))
    except (TypeError, ValueError): layer = 0
    start, out = layer if status['gcode_state'] != 'PREPARE' and layer >= 1 else 0, {}
    for v in versions(status, slices) or [None]:
        s, pairs, _ = paired(status, [v] if v else [], [t['label'] for t in trays])
        profile = fg.load_profile(s) if pairs else None
        if not profile or any(f['slot'] not in profile for _, f in pairs): return {}, False
        one = {}
        for label, f in pairs: one[label] = one.get(label, 0.0) + sum(profile[f['slot']][start:])
        out.update({label: max(g, out.get(label, 0.0)) for label, g in one.items()})
    return out, True

def take_off(book, trays, need, now, fils=None):
    """Takes what a job needs of each tray (need: {label: grams}) off the entries counted in this step (now: their
    labels), and what it needs beyond a tray's amount off the other loaded spools of the same filament
    (fg.same_filament), the fullest first: with AMS auto refill the print goes on from one of them, and fg.credit takes
    it off them the same way while the job prepares. An entered spool's part comes off its entry; that of a spool the
    printer measures is left to its reading. A tray's amount is its entry's grams, or the reading of a tray the
    printer measures (trays: fg.trays_of). Where it is not known (no tag and no entry, or an entry out of date), what
    goes beyond it cannot be told, and the entered spools of its filament become out of date. So they do for a tray
    that is no longer loaded: the print has gone on from another spool; of its filament as the slice names it (fils:
    filaments_of), or, when the slice does not, any spool.
    Kept with the entries while the job runs, for fg.credit: 'over', what the job needs beyond an entry that no entered
    spool gave (credit takes it off the spools the printer measures until their readings have fallen by it); 'gave',
    {tray: grams} an entry gave for a tray the printer measures (credit does not take that off again).
    Returns ([(label, grams taken off its entry, the tray they were needed beyond or None)], [(label made out of date,
    the tray whose amount is not known)])."""
    by, took, doubt = {t['label']: t for t in trays}, [], []
    for label, n in need.items():
        t, e = by.get(label), book.get(label)
        own, who = e if e and not e.get('stale') else None, t or (fils or {}).get(label)
        mates = [o['label'] for o in trays if o['label'] in now and o['label'] != label and not book[o['label']].get('stale') and (fg.same_filament(who, o) if who else not own)]
        if own and label in now: excess = over = max(0.0, n - e['grams']); took.append((label, n - excess, None)); e['grams'] = max(0.0, e['grams'] - n)
        elif own: over = e.get('over') or 0.0; excess = min(over, n)         # counted before: what was then left to the spools the printer measures
        elif t and t['grams'] is not None: excess = max(0.0, n - t['grams'] - sum((x.get('gave') or {}).get(label, 0.0) for x in book.values()))
        else:
            for l in mates: lapse(book[l], f'a print on {label}, whose amount is not known, may have gone on from it'); doubt.append((l, label))
            continue
        have = {o['label']: book[o['label']]['grams'] if o['label'] in mates else o['grams'] for o in trays       # in the trays' order, as credit takes equals
                if o['label'] in mates or (who and o['grams'] is not None and o['label'] not in book and fg.same_filament(who, o))}
        for l in sorted(have, key=lambda l: -have[l]):
            g = min(have[l], excess); excess -= g
            if g <= 0 or l not in mates: continue                           # a measured spool's part shows in its reading: nothing to keep
            book[l]['grams'] -= g; took.append((l, g, label))
            if own: over -= g
            else: gave = book[l].setdefault('gave', {}); gave[label] = gave.get(label, 0.0) + g
        if own and over > 1e-6: e['over'] = over
        elif own: e.pop('over', None)
    return took, doubt

def count(serial, status):
    """Takes the job the printer shows off the entered amounts of the trays it uses (take_off), and notes it counted (for
    each entry and for the printer) in the same write: a count is never half done."""
    key = job_key(status)
    def change(state):
        book = state.get('spools', {}).get(serial, {})
        if not book: return
        trays, _ = fg.trays_of(status)
        if trays: drawn, unknown = draws(status, state.get('slices', []), [t['label'] for t in trays])
        else: drawn, unknown = {}, set(book)                                 # a status without its spools: nothing is certain
        state.setdefault('jobs', {})[serial] = key; state.setdefault('ended', {})[serial] = status.get('gcode_state') not in RUN
        for label, e in book.items():
            e['job'] = key; e.pop('over', None); e.pop('gave', None)          # what the job before left to other spools is history
            if label in unknown: lapse(e, 'a print the guard could not count has drawn on it')
        took, doubt = take_off(book, trays, drawn, set(book), filaments_of(status, state.get('slices', [])))
        fg.log(f"job \"{status.get('subtask_name')}\" counted: " + (', '.join(f'{l} -{g:.0f} g' + (f' that it needs beyond {b}' if b else '') for l, g, b in took) or 'no entered spool drawn on')
               + (f"; out of date: {', '.join(sorted(l for l in unknown if l in book))}" if unknown & set(book) else '')
               + ''.join(f"; out of date: {l}, which the print may go on from: {b}'s amount is not known" for l, b in doubt))
    fg.update_state(change)

def catch_up(serial, status, state=None):
    """Entries the job the printer shows has not been taken off, although it was counted (or taken as the baseline) for
    the printer: made while it ran. What it still needs of their trays comes off now, as a print is counted in full
    (take_off: what it needs beyond a tray's amount included). When that cannot be told, the low side, as for a job
    that starts after the entry was made: with the slice known and only its per-layer profile missing, all the job
    needs comes off; otherwise (not sliced here, a tray that cannot be named) the entries of the trays the job may draw
    on are out of date. An entry of a tray such a job does not use is marked 'missed': a check takes the running print
    off it as far as it can tell then. Returns True when it changed an entry."""
    key, name = job_key(status), status.get('subtask_name')
    if not any(e.get('job') != key for e in ((fg.load_state() if state is None else state).get('spools', {}).get(serial, {}) or {}).values()): return False
    def change(st):
        book = st.get('spools', {}).get(serial, {})
        late, (trays, _) = {l: e for l, e in book.items() if e.get('job') != key}, fg.trays_of(status)
        need, counted = owed(status, st.get('slices', []))
        if counted is False:
            need, unknown = draws(status, st.get('slices', []), [t['label'] for t in trays]) if trays else ({}, set(late))
            for l, e in late.items():
                if l in unknown: lapse(e, 'a print the guard could not count has drawn on it'); fg.log(f'{l}: entered during "{name}", a print the guard cannot count: out of date')
                elif l not in need: e['missed'] = key
        took, doubt = take_off(book, trays, need, set(late), filaments_of(status, st.get('slices', [])))
        for l, g, b in took:
            fg.log(f'{l}: entered during "{name}": ' + (f'what that print still needs beyond {b}, {g:.0f} g' if b else f'what that print still needs of it, {need[l]:.0f} g' if counted else
                                                           f'all that print needs of it, {need[l]:.0f} g (how far it is cannot be told)') + f", taken off: {book[l]['grams']:.0f} g")
        for l, b in doubt: fg.log(f"{l}: entered during \"{name}\": out of date, as the print may go on from it: {b}'s amount is not known")
        for e in late.values(): e['job'] = key
        return bool(late)
    return fg.update_state(change)

def unfollowed(serial, before, followed):
    """Makes out of date, at a follower's first look, the entries made before `before` that nothing has followed the
    printer since (followed: when it was last followed, None: never). An amount entered with no guard running, on the
    card of "check --popup", would else be taken for the spool's amount by a guard started a day and two prints later:
    the first look counts nothing of what has run."""
    def change(state):
        old = [l for l, e in state.get('spools', {}).get(serial, {}).items() if e['at'] < before and not (followed and followed >= e['at']) and not e.get('stale')]
        for l in old: lapse(state['spools'][serial][l], 'it was entered long before the printer was first followed, which may have printed since')
        return old
    old = fg.update_state(change)
    if old: fg.log(f"entered amounts out of date: {', '.join(old)}: entered more than ten minutes before the printer was first followed, and it may have printed since")

def all_out_of_date(serial, why):
    def change(state):
        for e in state.get('spools', {}).get(serial, {}).values(): lapse(e, why)
    fg.update_state(change); fg.log(f'entered amounts out of date: {why}')


class JobWatch(threading.Thread):
    """Follows one printer while it is to be followed (followed()): a standing, read-only subscription to its status,
    looked at every 3 seconds. Each job is counted once, when it is first seen running (or already over), for the
    entered amounts; and every look goes to the learning's Recorder while learning is on. A job counted and since seen
    over, found on the printer again, is another run of it (`ended`, kept in the state file: the next guard knows it
    too)."""
    def __init__(self, serial, cfg):
        import guard_learn
        super().__init__(daemon=True)
        self.serial, self.cfg, self.stopped = serial, cfg, threading.Event()
        state = fg.load_state()
        self.recorder, self.entered, self.said = guard_learn.Recorder(serial), serial in serials(state), set()
        self.printing = state.get('printing', {}).get(serial) or 0           # when a look last saw it printing (kept in the state file); 0: not printing
        self.counted = state.get('jobs', {}).get(serial)                     # the key of the last job counted; None = take the first sighting as the baseline
        self.ended = bool(state.get('ended', {}).get(serial))                # was that job last seen not running? Then the same key on the printer is another run
        self.followed = state.get('followed', {}).get(serial)                # when the printer last reported, at the last look (kept in the state file, to the minute)
        self.saved = 0.0
        self.seen = (None, None)                                             # (state, key) at the last look that was dealt with
        self.going = {}                                                      # {tray: why} the entries the last look found ended
        self.lost = False                                                    # the last link got no report: look for the printer's address again

    def look(self, status, gap=False, state=None, at=None):
        """status: the printer's whole status now. gap: it was not followed for a while before this look. at: when the
        printer reported it (now, when not given). Returns True when it counted a job (or, failing that, made the
        entries out of date). An entry made while this job ran is caught up with it (catch_up). What cannot be dealt
        with now (fg.StateBusy: the state file could not be written) is left for the next look."""
        gstate, key = status.get('gcode_state'), job_key(status)
        running, done = gstate in RUN, False
        if (gstate, key) != self.seen or gap:
            again = key == self.counted and gstate in fg.BUSY and (self.ended or not running)   # the job counted is on the printer anew: another run of it
            if self.counted is None:                                         # baseline: what runs or has run is not counted, what is being prepared will be
                self.remember(key if gstate in STARTED else NO_JOB, not running); unfollowed(self.serial, (time.time() if at is None else at) - GAP, self.followed)
            elif gap and (key != self.counted or again or key[0] in ('0', '')):   # prints may have come and gone unseen: this one, and how many before it?
                away = f'{((time.time() if at is None else at) - self.followed) / 60:.0f} minutes' if self.followed else 'a while'
                all_out_of_date(self.serial, f'the printer was not followed for {away} and ' + ('has printed since' if key != self.counted or again else
                                                                                                 'may have printed since: it names every job alike'))
                self.remember(key if gstate in STARTED else NO_JOB, not running); done = True
            elif gstate in STARTED and (key != self.counted or (again and running)):   # a new job, or the same one started anew
                try: count(self.serial, status)
                except fg.StateBusy: raise                                   # not counted: the next look tries again
                except Exception as e:
                    all_out_of_date(self.serial, f'a job could not be counted ({e.__class__.__name__}: {e})')
                self.remember(key, not running); done = True
            elif again: self.remember(NO_JOB, True)                          # prepared anew: another run in whatever state it is seen next, also cancelled
            elif not self.ended and not (running and key == self.counted): self.remember(self.counted, True)   # the job counted is over
            self.seen = (gstate, key)
        if gstate in STARTED and key == self.counted: catch_up(self.serial, status, state)
        return done

    def see(self, status, state=None, at=None):
        """One look at the printer's whole status: for the entered amounts (look) while there are any, and for learning.
        state: the state file as read for this look, else read here. at: when the printer last reported (now, when not
        given): GAP or more since the report of the look before is a gap, in which jobs may have come and gone. A state
        file that cannot be written now is said in the log (once) and tried again at the next look: no count is taken as
        done until it is written."""
        state, at = fg.load_state() if state is None else state, time.time() if at is None else at
        entered = self.serial in serials(state)
        try:
            if entered and not self.entered: self.restart(state)
            self.entered = entered
            if entered: self.look(status, gap=bool(self.followed) and at - self.followed > GAP, state=state, at=at); self.end(status, state, at)
            self.mark_followed(at); self.mark_printing(status.get('gcode_state') in fg.BUSY); note_job(self.serial, status, state)
        except fg.StateBusy as e: self.say(f'following the printer: {e}; tried again at the next look')
        if self.cfg.get('learn'):
            try: self.recorder.look(status)
            except Exception as e: self.say(f'learning: {e.__class__.__name__}: {e}')   # learning never stops the entered amounts' count

    def end(self, status, state, at):
        """Ends the entries whose spool is gone, as a check does (apply), on a status that has its spools: ended at
        checks alone, an entry outlives the change to another spool of its material and colour whenever no plate is
        sliced while the tray is empty. Only what two looks in a row agree on is ended, as one report may carry a piece of the AMS; and a tray is
        empty when the status says so (the external holder never does)."""
        import guard_learn
        if 'ams' not in status: return
        book, trays, empty = state.get('spools', {}).get(self.serial, {}), fg.trays_of(status)[0], guard_learn.emptied(status)
        by = {t['label']: t for t in trays}
        gone = {l: why for l, e in book.items() if e['at'] <= at for why in [ended(e, by.get(l), l in empty)] if why}
        agreed, self.going = {l: why for l, why in gone.items() if self.going.get(l) == why}, gone
        if agreed or any(l in by and l not in gone and not e.get('loaded') and e['at'] <= at for l, e in book.items()):
            fg.update_state(lambda st: settle(st.get('spools', {}).get(self.serial, {}), trays, empty, agreed, at))

    def restart(self, state):
        """The printer has its first entered amount while followed for learning: as a follower started now would, the
        next sighting is the baseline (enter() has cleared the job counted)."""
        self.counted, self.ended, self.seen = state.get('jobs', {}).get(self.serial), bool(state.get('ended', {}).get(self.serial)), (None, None)

    def wanted(self, state=None):
        """Still to be followed: an entered amount, or learning has a reason (followed()), or a job it is recording."""
        return self.serial in followed(self.cfg, state) or bool(self.cfg.get('learn') and self.recorder.job is not None)

    def say(self, text):
        """Logs a failure once per follower and reason: a printer that stays away would otherwise fill the log every minute."""
        if text not in self.said: self.said.add(text); fg.log(text)

    def mark_printing(self, on):
        """Keeps in the state file when a look last saw the printer printing (to the minute), or 0 when it did not."""
        now = time.time()
        if (on and now - self.printing > 60) or (not on and self.printing):
            self.printing = now if on else 0; fg.update_state(lambda st: st.setdefault('printing', {}).__setitem__(self.serial, self.printing))

    def remember(self, key, ended):
        """Keeps the job counted, and whether it was last seen over, for this follower and the next."""
        self.counted, self.ended = key, ended
        fg.update_state(lambda st: (st.setdefault('jobs', {}).__setitem__(self.serial, key), st.setdefault('ended', {}).__setitem__(self.serial, ended)))

    def mark_followed(self, at):
        self.followed = at
        if self.followed - self.saved > 60:
            self.saved = self.followed
            fg.update_state(lambda st: st.setdefault('followed', {}).__setitem__(self.serial, self.followed))

    def run(self):
        """Follows the printer on a link of its own, made anew whenever it falls silent. A status is looked at only while
        the printer reports: one with no report in the last SILENT seconds is the printer's status no longer (the Wi-Fi
        gone, the printer at another address), and nor is the one held while this thread stood still that long (the
        computer asleep), whatever arrives on waking. The link is then ended and made again, the address looked up
        afresh, from an empty status; and as only a look renews 'followed', the first status after a silence of GAP or
        more is one after a gap."""
        while not self.stopped.is_set() and self.wanted():
            link = None
            try:
                conf = fg.bs_conf(); ip = fg.printer_ip(self.serial, self.cfg, refresh=self.lost)
                if ip and self.serial in conf.get('access_code', {}):
                    status, lock, heard, opened = {}, threading.Lock(), [], time.time()   # heard: when the first and the latest report came
                    def on_report(d):
                        with lock: fg.merge(status, d); heard[1:] = [time.time()]
                    def on_refused(rc): self.say(f'following the printer: {ip} refused the access code Bambu Studio holds for it')
                    link = fg.printer_link(ip, self.serial, conf['access_code'][self.serial], f'bambu-filament-guard-jobs-{fg.os.getpid()}', on_report, on_refused)
                    passed = opened                                                  # when this loop last came round
                    while not self.stopped.wait(3):
                        state = fg.load_state()                                      # read once a look: who is followed, and what is entered
                        if not self.wanted(state): self.stopped.set(); break        # no entered amount, and nothing for learning to watch
                        now, before = time.time(), passed; passed = now
                        with lock: first, latest = (heard[0], heard[-1]) if heard else (None, None)
                        if first is None:
                            if now - opened > 40: self.lost = True; break            # no report: the address or the access code has changed
                            continue
                        if now - latest > SILENT or now - before > SILENT:
                            self.lost = True; self.say('following the printer: no report for a minute: the link is made anew'); break
                        if now - first < 3: continue                                 # the printer sends its first report in pieces
                        with lock: snap = fg.json.loads(fg.json.dumps(status))
                        if 'gcode_state' not in snap: continue
                        self.lost = False
                        self.see(snap, state, latest)
            except Exception as e:
                self.say(f'following the printer: {e.__class__.__name__}: {e}')
            finally:
                if link: link.loop_stop(); link.disconnect()
            self.stopped.wait(60)
