"""What the guard learns from previous prints, per tray: how far that tray's readings can be trusted.

The AMS's reading of a spool is an estimate. Measured 2026-11-19 on an H2D: tray C3 read 105 g and ran dry after 84-85 g
of a print, so it read about 20 g when it was empty; in the same print tray B4 fell 49 g for a need of 49.1 g. A fixed
reserve treats both alike. With the per-layer profile of the job that runs (filament_guard.layer_profile) the guard knows
how many grams left each tray between any two layers, and compares that with the tray's readings.

Recorded (Recorder, fed every look of guard_ledger.JobWatch, every 3 seconds while it follows a printer), per job the
printer runs whose trays can be named:
  - per tray the job draws on, when the job was matched to a slice with a profile: the spool; the reading before layer
    1; the reading at the start of each layer seen to begin (at most SAMPLES, thinned evenly); the reading at the end;
    the grams the profile has the tray give over that span, printed and purge apart, the purge as the spool gave it
    (the header's own split charges the nozzle's content to the filament pushed out, see layer_profile); and whether
    the record is sound: the same spool throughout, a reading that never rose, no gap in following longer than
    guard_ledger.GAP, the tray never empty, the job seen to finish.
  - a run-dry: a tray the job draws on that is in one look and gone from the next while the job runs, with its last
    reading, when that reading was low enough for a spool that ran out (DRY_MOST); a fuller spool was taken out.
Kept in a file of its own, STATE_DIR/learned.json: the last KEEP_JOBS jobs and KEEP_DRY run-dries per (printer, tray).

Learned per (printer, tray label), from sound records only, each number used from its threshold on:
  dry   what the tray read when a spool ran dry in it: the largest of its last DRY_EVENTS run-dries.
  rate  reading fallen per gram the tray gave: over its last RATE_JOBS sound whole jobs of RATE_JOB_GRAMS or more.
  pre   grams the tray lost before layer 1, beyond the profile's layer 0: the largest of its last PRE_JOBS.
A check credits a tray the printer measures (never an amount entered by hand) with the smallest of: the reading G;
G - dry; W - (W - G) / rate (W the spool's full weight); never below 0. A filament judged on the tray needs pre more.
Seen as a straight line, reading = dry + rate x true amount: dry is measured directly; rate alone implies dry =
W x (1 - rate), a gauge that counts too little of what it feeds. The two are views of the same error: the smaller of the
two corrections, not their sum, is taken, and neither ever adds, so learning can only make a check stricter. With no
history, or learning off (setting "learn"), every check is as it was."""
import json, os, threading, time
import filament_guard as fg
import guard_ledger as gl

SAMPLES = 60                  # readings kept per tray and job: enough to see how a tray's reading follows the print
SAMPLE_SPAN = 10              # seconds: a layer change seen between two looks further apart does not say when it happened
SETTLE = 60                   # seconds after FINISH the end reading is taken: the end routine unloads the filament, and the
                              # AMS may correct its figure then; the first FINISH reading is kept beside it (end_first)
KEEP_JOBS, KEEP_DRY = 20, 5   # per (printer, tray): enough for every number below, and a trace to judge them by later
DRY_MOST = (50.0, 0.10)       # grams, share of the spool's full weight: a spool that leaves with more was taken out, not run dry
DRY_EVENTS = 3                # dry is the largest of the last 3: one odd event is not forgotten at once, nor kept for ever
RATE_JOBS = 10                # rate is taken over the last 10 sound whole jobs: recent, and many grams
RATE_JOB_GRAMS = 5.0          # a job of less, read in whole grams, says nothing about a rate
RATE_MIN_JOBS = 3             # rate is used from 3 such jobs on...
RATE_MIN_GRAMS = 300.0        # ...that took 300 g or more from the tray between them: the reading's whole grams are then 1 % at most
RATE_NOISE = 0.99             # a rate of 0.99 or more is used as 1: within 1 % is the noise of whole-gram readings
RATE_FLOOR = 0.5              # a rate below this is not used: a gauge that counts less than half of what it feeds is more
                              # likely readings that lagged behind the print than a gauge that far out
PRE_JOBS = 5                  # pre is the largest of the last 5 sound jobs that have both readings...
PRE_MIN_JOBS = 2              # ...and is used from 2 on: the loss before layer 1 is the same each print, so two agree on it
LOCK = threading.RLock()


# ---------------------------------------------------------------- the history file

def path():
    return os.path.join(fg.STATE_DIR, 'learned.json')

def load():
    """{serial: {tray label: {'jobs': [record], 'dry': [event]}}}. No file is no history; a damaged one is set aside."""
    for attempt in range(6):
        try:
            with open(path(), encoding='utf-8') as f: return json.load(f)
        except FileNotFoundError: return {}
        except PermissionError:                                # being replaced just now
            if attempt == 5: raise
            time.sleep(0.05)
        except ValueError:
            try: os.replace(path(), path() + '.damaged')
            except OSError: pass
            fg.log('learned.json was damaged: set aside as learned.json.damaged, learning starts again')
            return {}

def save(history):
    os.makedirs(fg.STATE_DIR, exist_ok=True)
    with open(path() + '.new', 'w', encoding='utf-8') as f: json.dump(history, f, separators=(',', ':'))
    for attempt in range(6):
        try: os.replace(path() + '.new', path()); return
        except PermissionError:                                # a check reading it just then (Windows)
            if attempt == 5: raise
            time.sleep(0.05)

def keep(serial, label, kind, item, most):
    """Adds a job record or a run-dry to a tray's history, keeping the last `most`."""
    with LOCK:
        history = load()
        items = history.setdefault(serial, {}).setdefault(label, {}).setdefault(kind, [])
        items.append(item); del items[:-most]
        save(history)

def forget():
    with LOCK: save({})
    fg.log('what was learned is forgotten')


# ---------------------------------------------------------------- what is learned

def day(at):
    return time.strftime('%d %b', time.localtime(at)).lstrip('0')

def model(tray_history):
    """The three numbers of one tray: {'dry' | 'rate' | 'pre': {'value', 'used', 'why'}}. value None: no evidence yet.
    used: past its threshold. why: the evidence in words when used, what is missing when not."""
    jobs = [j for j in tray_history.get('jobs', []) if j.get('sound')]
    dries = tray_history.get('dry', [])[-DRY_EVENTS:]
    out = {}
    if dries:
        worst = max(dries, key=lambda d: d['reading'])
        out['dry'] = {'value': worst['reading'], 'used': True, 'why': f"it read {worst['reading']:.0f} g when a spool ran dry in it on {day(worst['at'])}"}
    else: out['dry'] = {'value': None, 'used': False, 'why': 'no spool has run dry in it yet'}
    whole = [j for j in jobs if j.get('whole') and j['sliced']['printed'] + j['sliced']['purge'] >= RATE_JOB_GRAMS][-RATE_JOBS:]
    given = sum(j['sliced']['printed'] + j['sliced']['purge'] for j in whole)
    rate = sum(j['start'] - j['end'] for j in whole) / given if given else None
    missing = [x for x in ((f'{len(whole)} of {RATE_MIN_JOBS} prints' if len(whole) < RATE_MIN_JOBS else ''),
                           (f'{given:.0f} of {RATE_MIN_GRAMS:.0f} g' if given < RATE_MIN_GRAMS else '')) if x]
    if rate is not None and not missing and rate >= RATE_NOISE: missing = [f'{rate:.3f} g per gram is within the reading\'s own noise']
    elif rate is not None and not missing and rate < RATE_FLOOR: missing = [f'{rate:.2f} g per gram is too far out to believe']
    out['rate'] = {'value': rate, 'used': not missing, 'jobs': len(whole), 'grams': given,
                   'why': f"its reading fell {rate:.2f} g for each gram it gave in its last {len(whole)} prints" if not missing else ', '.join(missing)}
    lost = [max(0.0, j['before'] - j['start'] - j['layer0']) for j in jobs if j.get('before') is not None and j.get('start') is not None][-PRE_JOBS:]
    pre = max(lost) if lost else None
    out['pre'] = {'value': pre, 'used': len(lost) >= PRE_MIN_JOBS, 'jobs': len(lost),
                  'why': (f"lost up to {pre:.1f} g before the first layer of its last {len(lost)} prints" if len(lost) >= PRE_MIN_JOBS
                          else f'{len(lost)} of {PRE_MIN_JOBS} prints')}
    return out

def corrections(serial, trays, history=None):
    """{tray label: {'credited', 'evidence', 'pre', 'pre_note'}} for the trays the printer measures (not an amount entered
    by hand), from what was learned of them. credited: the smallest of the reading and what each number in use makes of
    it, never below 0; evidence: the number that gave it, in words. Trays nothing was learned of are left out."""
    history = load() if history is None else history
    out = {}
    for t in trays:
        if t['grams'] is None or t.get('entered') or t.get('stale'): continue
        m, g, w = model(history.get(serial, {}).get(t['label'], {})), t['grams'], t.get('weight')
        options = [(g, '')]
        if m['dry']['used']: options.append((g - m['dry']['value'], m['dry']['why']))
        if m['rate']['used'] and w: options.append((w - (w - g) / m['rate']['value'], m['rate']['why']))
        credited, evidence = min(options, key=lambda o: o[0])
        fix = {'credited': max(0.0, credited), 'evidence': evidence} if credited < g else {}
        if m['pre']['used'] and m['pre']['value'] > 0: fix.update(pre=m['pre']['value'], pre_note=m['pre']['why'])
        if fix: out[t['label']] = fix
    return out


# ---------------------------------------------------------------- recording

def same_spool(a, b):
    """By the tag when both have one; otherwise by material, colour, product and full weight: two spools that agree on
    all four cannot be told apart, and a refill of one shows as a reading that rose."""
    if a.get('id') and b.get('id'): return a['id'] == b['id']
    return all(a.get(k) == b.get(k) for k in ('type', 'colour', 'fid', 'weight'))

def spool(t):
    return {'id': t.get('spool'), 'type': t['type'], 'colour': t['colour'], 'fid': t['fid'], 'weight': t['weight']}

def emptied(status):
    """The trays the status reports with no spool in them (a tray entry without a material), by label. A tray the
    status does not mention at all is not among them: a report may carry only part of the AMS."""
    out = set()
    for a in status.get('ams', {}).get('ams', []) or []:
        aid = int(a.get('id', 0))
        for t in a.get('tray', []) or []:
            if not t.get('tray_type'): out.add(f'HT-{chr(65 + aid - 128)}' if aid >= 128 else f"{chr(65 + aid)}{int(t.get('id', 0)) + 1}")
    return out

def layer_of(status):
    """The layer the printer is on; 0 before layer 1 and while it prepares (it may still show the job before's)."""
    if status.get('gcode_state') == 'PREPARE': return 0
    try: return max(0, int(status.get('layer_num')))
    except (TypeError, ValueError): return 0


class Recorder:
    """Records the jobs one printer prints, for learning. look() is given the printer's whole status at every look of
    the follower. Each job's records are written when it ends: seen to finish (SETTLE seconds after FINISH), failed or
    cancelled, or replaced by another job."""
    def __init__(self, serial):
        self.serial, self.job, self.last, self.closed = serial, None, None, None   # last: (when, state, layer, {label: tray}) of the look before

    def look(self, status, now=None):
        now = time.time() if now is None else now
        state, key, layer = status.get('gcode_state'), gl.job_key(status), layer_of(status)
        trays, empty = {t['label']: t for t in fg.trays_of(status)[0]}, emptied(status)
        last, self.last = self.last, (now, state, layer, trays)
        if self.job and self.job['key'] != key: self.close('the printer showed another job before this one was seen to finish')
        if self.job is None and state in fg.BUSY and not (key == self.closed and last and last[1] in fg.BUSY):
            self.job = self.open(status, key, now)
        job = self.job
        if job is None: return
        if last and now - last[0] > gl.GAP: self.unsound(f'not followed for {(now - last[0]) / 60:.0f} minutes')
        if status.get('mapping') != job['mapping'] and not job.get('remapped'):        # an AMS that refills from another tray may do this: the trace shows it
            job['remapped'] = [layer, status.get('mapping')]; self.unsound(f'its trays changed at layer {layer}')
        stepped = last and last[1] in fg.BUSY and state in ('RUNNING', 'PAUSE') and layer == last[2] + 1 and now - last[0] <= SAMPLE_SPAN
        for label, tr in job['trays'].items():
            t = trays.get(label)
            if t is None:
                if label in empty:
                    if not tr.get('gone') and state in ('RUNNING', 'PAUSE') and last and label in last[3]: self.gone(label, tr, layer, now)
                    tr['gone'] = True
                continue
            g = t['grams']
            if g is None: continue
            if not same_spool(tr['spool'], spool(t)): self.unsound(f'{label} holds another spool')
            if tr['last'] is not None and g > tr['last']: self.unsound(f'{label} read {tr["last"]:.0f} g, then {g:.0f} g')
            if g <= 0: self.unsound(f'{label} read 0 g' + (f' (it read {tr["last"]:.0f} g before)' if tr['last'] is not None else ''))
            tr['last'] = g
            if tr['before'] is None and layer < 1 and state in fg.BUSY: tr['before'] = g
            if stepped:
                if layer == 1: tr['start'] = g
                first = tr['samples'][0][0] if tr['samples'] else layer
                if (layer - first) % tr['stride'] == 0:                       # thinned evenly: every stride-th layer from the first sample
                    tr['samples'].append([layer, g])
                    if len(tr['samples']) > SAMPLES: tr['stride'] *= 2; tr['samples'] = [x for x in tr['samples'] if (x[0] - first) % tr['stride'] == 0]
            if state == 'FINISH':
                if tr['end_first'] is None: tr['end_first'] = g
                tr['end'] = g
        if state == 'FINISH':
            job['finished'] = job['finished'] or now
            if now - job['finished'] >= SETTLE: self.close(None)
        elif job['finished']: self.close(None)                         # finished, and the printer has moved on before the reading settled
        elif state not in fg.BUSY: self.close('failed or cancelled' if state == 'FAILED' else f'it stopped without finishing ({state})')

    def open(self, status, key, now):
        """A job record, or None when the job's trays cannot be named: then nothing of it can be told by tray."""
        trays = fg.trays_of(status)[0]
        s, pairs, _ = gl.paired(status, fg.load_state().get('slices', []), [t['label'] for t in trays])
        labels = gl.mapped_labels(status)
        drawn = {l for l, _ in pairs} or ({l for l in labels if l} if labels and None not in labels else set())
        if not drawn: return None
        parts = {part: fg.load_profile(s, part) for part in ('layers', 'purge', 'pushed')} if pairs else {}
        if not parts.get('layers') or any(f['slot'] not in parts['layers'] for _, f in pairs): parts = None
        by_label = {t['label']: t for t in trays}
        return {'key': key, 'name': status.get('subtask_name') or '', 'opened': now, 'mapping': status.get('mapping'), 'profile': parts,
                'slots': {l: [f['slot'] for m, f in pairs if m == l] for l in drawn}, 'sound': True, 'why': [], 'finished': None,
                'trays': {l: {'spool': spool(by_label[l]), 'before': None, 'start': None, 'samples': [], 'stride': 1, 'end_first': None, 'end': None,
                              'last': None} for l in drawn if l in by_label and by_label[l]['grams'] is not None}}

    def unsound(self, why):
        if why not in self.job['why']: self.job['sound'] = False; self.job['why'].append(why)

    @staticmethod
    def given(job, label, first, last=None):
        """(printed, purge) grams the job's profile has the tray give from layer `first` up to layer `last` (not included;
        None: to the end), the purge as the spool gave it (the header's split moved back)."""
        p, printed, purge = job['profile'], 0.0, 0.0
        for slot in job['slots'][label]:
            pushed = (p.get('pushed') or {}).get(slot) or [0.0] * len(p['layers'][slot])
            for n in range(first, len(p['layers'][slot]) if last is None else last):
                printed += p['layers'][slot][n] - p['purge'][slot][n]; purge += p['purge'][slot][n] - pushed[n]
        return printed, purge

    def gone(self, label, tr, layer, now):
        """A tray the job draws on has left the status while the job runs: a run-dry when its last reading was low enough."""
        reading, w = tr['last'], (tr['spool'] or {}).get('weight')
        if reading is None: return
        if reading > max(DRY_MOST[0], DRY_MOST[1] * (w or 0)):
            fg.log(f'{label}: a spool reading {reading:.0f} g was taken out during "{self.job["name"]}": not a run-dry, nothing learned from it')
            self.unsound(f'{label}: its spool was taken out'); return
        needed = sum(self.given(self.job, label, layer)) if self.job['profile'] else None
        keep(self.serial, label, 'dry', {'at': now, 'job': self.job['name'], 'reading': reading, 'layer': layer, 'needed': needed, 'spool': tr['spool']}, KEEP_DRY)
        fg.log(f'{label} ran dry at layer {layer} of "{self.job["name"]}" reading {reading:.0f} g' + (f'; the job still needed {needed:.1f} g of it' if needed is not None else ''))
        self.unsound(f'{label} ran dry')

    def close(self, why):
        job, self.job = self.job, None
        self.closed = job['key']
        if why: job['sound'] = False; job['why'].append(why)
        if not job['profile']: return                                  # nothing to measure the readings against: only its run-dries count
        notes = []
        for label, tr in job['trays'].items():
            if not tr['samples'] and tr['before'] is None: continue
            first = tr['samples'][0][0] if tr['samples'] else 1
            to = None if job['finished'] else (tr['samples'][-1][0] if tr['samples'] else first)
            printed, purge = self.given(job, label, first, to)
            layer0 = sum(self.given(job, label, 0, 1))
            whole = bool(job['finished'] and tr['start'] is not None and first == 1 and tr['end'] is not None)
            keep(self.serial, label, 'jobs', {
                'at': job['opened'], 'job': job['name'], 'spool': tr['spool'], 'before': tr['before'], 'start': tr['start'], 'samples': tr['samples'],
                'end_first': tr['end_first'], 'end': tr['end'], 'layer0': layer0, 'whole': whole,
                'sliced': {'from': first, 'to': to, 'printed': printed, 'purge': purge}, 'remapped': job.get('remapped'),
                'sound': job['sound'], 'why': list(job['why'])}, KEEP_JOBS)
            notes.append(label)
        if notes: fg.log(f'recorded "{job["name"]}" for learning: {", ".join(notes)}' + ('' if job['sound'] else f' (not used: {"; ".join(job["why"])})'))


# ---------------------------------------------------------------- the command

def printed_and_purged(jobs):
    """(per gram printed, per gram purged) the reading fell by, least squares over sound whole jobs, or None when their
    purge shares do not differ enough to tell the two apart (at least 3 jobs, 10 points between the shares)."""
    rows = [(j['sliced']['printed'], j['sliced']['purge'], j['start'] - j['end']) for j in jobs if j.get('sound') and j.get('whole')]
    shares = [q / (p + q) for p, q, _ in rows if p + q > 0]
    if len(rows) < 3 or not shares or max(shares) - min(shares) < 0.10: return None
    a, b, c = sum(p * p for p, _, _ in rows), sum(p * q for p, q, _ in rows), sum(q * q for _, q, _ in rows)
    y1, y2, det = sum(p * y for p, _, y in rows), sum(q * y for _, q, y in rows), a * c - b * b
    if det <= 1e-9 * a * c: return None
    return (c * y1 - b * y2) / det, (a * y2 - b * y1) / det

def report(history, names=None, learning=True):
    """learned: the text, line by line."""
    names = names or {}
    lines = [f"Learning from prints is {'on' if learning else 'off'}" + ('' if history else ': nothing recorded yet')]
    for serial, by_tray in sorted(history.items()):
        lines.append(names.get(serial, serial))
        for label, h in sorted(by_tray.items()):
            jobs, dries, m = h.get('jobs', []), h.get('dry', []), model(h)
            lines.append(f"  {label}: {len(jobs)} print{'s' if len(jobs) != 1 else ''} recorded, {sum(1 for j in jobs if j.get('sound'))} sound"
                         + (f"; {len(dries)} run-dr{'ies' if len(dries) != 1 else 'y'}" if dries else ''))
            for name, unit in (('dry', ' g'), ('rate', ' g per gram'), ('pre', ' g')):
                x = m[name]
                value = '-' if x['value'] is None else f"{x['value']:.{3 if name == 'rate' else 1}f}{unit}"
                lines.append(f"    {name:<5} {value:<16} " + (f"used: {x['why']}" if x['used'] else f"not used: {x['why']}"))
            for d in dries:
                lines.append(f"    ran dry {time.strftime('%d %b %H:%M', time.localtime(d['at']))} at layer {d['layer']} of \"{d['job']}\", reading {d['reading']:.0f} g"
                             + (f", {d['needed']:.1f} g still to go" if d.get('needed') is not None else ''))
            split = printed_and_purged(jobs)
            lines.append('    per gram printed / purged: ' + (f'{split[0]:.3f} / {split[1]:.3f} g (a report only, not used)' if split else 'not enough to tell'))
    return lines
