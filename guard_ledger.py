"""Amounts entered by hand, for spools the printer cannot measure (no RFID tag, an external holder), and the prints
that draw on them.

An entry belongs to one tray of one printer and to the material and colour that tray held when it was entered. It
ends when the printer reports another filament there, or starts to measure the tray itself.

Counting down errs to the low side, because a figure that is too low warns early and one that is too high loses a
print:
  - a print is counted in full when it starts to run, also when it is later cancelled;
  - a print the guard cannot count (not sliced on this computer, or its tray cannot be told) makes the entries of
    the trays it used "out of date": unknown again, until the amount is entered again.
  - when the printer was not followed for ten minutes or more (this computer asleep, the link down) and shows
    another job than the last one counted, every entry of that printer is out of date: more than one print may
    have come and gone.
Not seen: a print started and finished while the printer was not followed, when the printer names it exactly like
the print before it (LAN-only mode gives every job the id 0).

For the check of the next print, owed() tells what the job the printer runs still needs from each tray: the per-layer
profile of the slice it was made from, from the printer's layer to the end. filament_guard takes that off the trays the
printer measures, and not off an entered amount, which had the whole job taken off when the job started."""
import re, threading, time
import filament_guard as fg

STARTED = ('RUNNING', 'PAUSE', 'FINISH', 'FAILED')        # states in which a job has drawn filament and its tray mapping is its own
NO_JOB = ['-', '-']
GAP = 600                                                  # seconds without a look at the printer after which jobs may have been missed


def identity(tray):
    return [tray['type'].upper(), tray['colour']]

def serials():
    """The printers that have an entered amount."""
    return [s for s, book in fg.load_state().get('spools', {}).items() if book]

def apply(trays, serial, complete=True):
    """Gives the trays the printer cannot measure the amount entered for them (grams, and 'entered': when), or marks
    them 'stale' when that amount is out of date. Drops the entries that have ended: another filament in the tray, a
    tray the printer now measures, or (only when the status is complete, i.e. has its spools) a tray that is empty."""
    def change(state):
        book = state.get('spools', {}).get(serial, {})
        for label in list(book):
            t, e = next((t for t in trays if t['label'] == label), None), book[label]
            if t is None:
                if complete: del book[label]
            elif t['grams'] is not None or identity(t) != [e['type'], e['colour']]: del book[label]
            elif e.get('stale'): t['stale'] = e['at']
            else: t['grams'], t['weight'], t['entered'] = e['grams'], e['first'], e['at']
    fg.update_state(change)
    return trays

def enter(serial, tray, grams):
    """Records grams for tray ({'label', 'type', 'colour'}); None removes the entry. The job the printer shows at
    that moment is taken as already counted: the amount is what is on the spool now."""
    def change(state):
        book = state.setdefault('spools', {}).setdefault(serial, {})
        if not book: state.setdefault('jobs', {}).pop(serial, None)          # the first entry: the next sighting of the printer is the baseline
        if grams is None: book.pop(tray['label'], None)
        else: book[tray['label']] = {'type': tray['type'].upper(), 'colour': tray['colour'], 'grams': float(grams), 'first': float(grams), 'at': time.time()}
    fg.update_state(change)
    fg.log(f"{tray['label']}: amount " + ('removed' if grams is None else f'entered, {float(grams):.0f} g'))

def job_key(status):
    return [str(status.get('task_id') or status.get('subtask_id') or status.get('job_id') or ''), status.get('subtask_name') or '']

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

def job_plate(status):
    """The plate number of the job the printer shows, from the file it prints ("/data/Metadata/plate_2.gcode" on the
    H2D), or None when the printer does not say (an A1 mini printing from its SD card reports '')."""
    m = re.search(r'plate_(\d+)\.gcode$', str(status.get('gcode_file') or ''))
    return int(m.group(1)) if m else None

def match_slice(name, layers, slices, plate=None):
    """The slice a job was made from: the newest one whose project title starts the job's name (Bambu Studio names a
    job "<project>" or "<project>_<plate name>") and whose layer count is the job's; of that plate, when the job's plate
    is known. Two plates of one project with the same layer count are told apart only by that."""
    for s in slices:
        if not (name == s['title'] or name.startswith(s['title'] + '_')): continue
        if layers and s.get('layers') and int(layers) != int(s['layers']): continue
        if plate is not None and s.get('plate') != plate: continue
        return s
    return None

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

def draws(status, slices, all_labels):
    """({tray label: grams} the job the printer shows draws, the trays whose draw cannot be told)."""
    _, pairs, unknown = paired(status, slices, all_labels)
    drawn = {}
    for label, f in pairs: drawn[label] = drawn.get(label, 0.0) + f['grams']
    return drawn, unknown

def owed(status, slices):
    """({tray label: grams the job the printer shows still needs from it}, counted). counted: None when no job is on
    the printer; False when one is and what it still needs cannot be told (not sliced on this computer while the guard
    watched, no profile, a tray that cannot be named), and then nothing is guessed; True with the grams. Still needed is
    the slice's per-layer profile from the printer's layer to the end, that layer counted whole: a figure too low loses
    a print, one too high only warns early. In PREPARE, or without a layer, all of it: a printer that prepares a job may
    still show the last layer of the job before."""
    if status.get('gcode_state') not in fg.BUSY: return {}, None
    trays, _ = fg.trays_of(status)
    s, pairs, _ = paired(status, slices, [t['label'] for t in trays])
    profile = fg.load_profile(s) if pairs else None
    if not profile or any(f['slot'] not in profile for _, f in pairs): return {}, False
    try: layer = int(status.get('layer_num'))
    except (TypeError, ValueError): layer = 0
    start = layer if status['gcode_state'] != 'PREPARE' and layer >= 1 else 0
    out = {}
    for label, f in pairs: out[label] = out.get(label, 0.0) + sum(profile[f['slot']][start:])
    return out, True

def count(serial, status):
    """Takes the job the printer shows off the entered amounts of the trays it uses."""
    def change(state):
        book = state.get('spools', {}).get(serial, {})
        if not book: return
        trays, _ = fg.trays_of(status)
        if trays: drawn, unknown = draws(status, state.get('slices', []), [t['label'] for t in trays])
        else: drawn, unknown = {}, set(book)                                 # a status without its spools: nothing is certain
        for label, e in book.items():
            if label in unknown: e['stale'] = True
            elif label in drawn: e['grams'] = max(0.0, e['grams'] - drawn[label])
        fg.log(f"job \"{status.get('subtask_name')}\" counted: " + (', '.join(f'{l} -{g:.0f} g' for l, g in drawn.items() if l in book) or 'no entered spool drawn on')
               + (f"; out of date: {', '.join(sorted(l for l in unknown if l in book))}" if unknown & set(book) else ''))
    fg.update_state(change)

def all_out_of_date(serial, why):
    def change(state):
        for e in state.get('spools', {}).get(serial, {}).values(): e['stale'] = True
    fg.update_state(change); fg.log(f'entered amounts out of date: {why}')


class JobWatch(threading.Thread):
    """Follows one printer for as long as it has entered amounts: a standing, read-only subscription to its status,
    looked at every 3 seconds. Each job is counted once, when it is first seen running (or already over)."""
    def __init__(self, serial, cfg):
        super().__init__(daemon=True)
        self.serial, self.cfg, self.stopped = serial, cfg, threading.Event()
        state = fg.load_state()
        self.counted = state.get('jobs', {}).get(serial)                     # the key of the last job counted; None = take the first sighting as the baseline
        self.followed = state.get('followed', {}).get(serial)                # when the printer was last looked at (kept in the state file, to the minute)
        self.saved = 0.0
        self.seen = (None, None)                                             # (state, key) at the last look that was dealt with
        self.active = None                                                   # was the printer running then? None = not looked yet
        self.lost = False                                                    # the last link got no report: look for the printer's address again

    def look(self, status, gap=False):
        """status: the printer's whole status now. gap: it was not followed for a while before this look. Returns True
        when it counted a job (or, failing that, made the entries out of date). What cannot be dealt with now is left
        for the next look."""
        state, key = status.get('gcode_state'), job_key(status)
        if (state, key) == self.seen and not gap: return False
        running, was = state in ('RUNNING', 'PAUSE'), None if gap else self.active
        done = False
        if self.counted is None:                                             # baseline: what runs or has run is not counted, what is being prepared will be
            self.remember(key if state in STARTED else NO_JOB)
        elif gap and key != self.counted:                                    # prints may have come and gone unseen: this one, and how many before it?
            all_out_of_date(self.serial, 'the printer was not followed for a while and has printed since'); self.remember(key if state in STARTED else NO_JOB); done = True
        elif state in STARTED and (key != self.counted or (running and was is False)):   # a new job, or the same one started anew before our eyes
            try: count(self.serial, status)
            except Exception as e:
                all_out_of_date(self.serial, f'a job could not be counted ({e.__class__.__name__}: {e})')
            self.remember(key); done = True
        self.seen, self.active = (state, key), running
        return done

    def remember(self, key):
        self.counted = key
        fg.update_state(lambda st: st.setdefault('jobs', {}).__setitem__(self.serial, key))

    def mark_followed(self):
        self.followed = time.time()
        if self.followed - self.saved > 60:
            self.saved = self.followed
            fg.update_state(lambda st: st.setdefault('followed', {}).__setitem__(self.serial, self.followed))

    def run(self):
        while not self.stopped.is_set():
            link = None
            try:
                conf = fg.bs_conf(); ip = fg.printer_ip(self.serial, self.cfg, refresh=self.lost)
                if ip and self.serial in conf.get('access_code', {}):
                    status, lock, first, opened = {}, threading.Lock(), [], time.time()
                    def on_report(d):
                        with lock: fg.merge(status, d); first.append(time.time()) if not first else None
                    def on_refused(rc): fg.log(f'following the printer for entered amounts: {ip} refused the access code Bambu Studio holds for it')
                    link = fg.printer_link(ip, self.serial, conf['access_code'][self.serial], f'bambu-filament-guard-jobs-{fg.os.getpid()}', on_report, on_refused)
                    while not self.stopped.wait(3):
                        if self.serial not in serials(): self.stopped.set(); break   # the last entry is gone: nothing left to follow
                        if not first:
                            if time.time() - opened > 40: self.lost = True; break    # no report: the address or the access code has changed
                            continue
                        if time.time() - first[0] < 3: continue                      # the printer sends its first report in pieces
                        with lock: snap = fg.json.loads(fg.json.dumps(status))
                        if 'gcode_state' not in snap: continue
                        self.lost = False
                        self.look(snap, gap=bool(self.followed) and time.time() - self.followed > GAP); self.mark_followed()
            except Exception as e:
                fg.log(f'following the printer for entered amounts: {e.__class__.__name__}: {e}')
            finally:
                if link: link.loop_stop(); link.disconnect()
            self.stopped.wait(60)
